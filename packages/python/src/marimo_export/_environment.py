"""Fingerprint the installed distributions and local modules a notebook imports."""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import os
import re
import sys
import warnings
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
import yaml
from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from tomlkit.exceptions import TOMLKitError

from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._notebook import _script_metadata
from marimo_export.errors import ExecutionError

# Distributions that execute every state, with the extras to follow. `None`
# follows every declared extra, which covers the packages bundled exporters
# import. marimo's `sql` extra supplies the engines that run SQL cells.
_RUNTIME_DISTRIBUTIONS: tuple[tuple[str, frozenset[str] | None], ...] = (
    ("marimo", frozenset({"sql"})),
    ("marimo-export", None),
)
_SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".so", ".pyd", ".dll", ".dylib"})
_INSTALLED_PARTS = frozenset({"site-packages", "dist-packages"})
_PROJECT_MARKERS = ("pyproject.toml", "setup.cfg", ".git")
# Python and `{.marimo}` code fences in a Markdown notebook. A superset of
# marimo cell fences only widens the scope.
_PYTHON_FENCE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*(?:python|\.marimo)[^\n]*\n"
    r"(?P<code>.*?)^(?P=fence)[`~]*[ \t]*\r?$"
)
_FRONT_MATTER = re.compile(r"(?ms)\A---[ \t]*\r?\n(?P<yaml>.*?)^---[ \t]*\r?$")

_Revision = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _Scope:
    distributions: tuple[tuple[str, str], ...]
    sources: tuple[tuple[str, str, _Revision], ...]


def environment_identity(source: Path | None) -> str:
    """Hash the distributions and local modules that one notebook can import.

    Module resolution searches the notebook directory, then the project's `src`
    directory and root, then the interpreter path. A runtime without a saved
    notebook has no readable imports and keeps every installed distribution in
    scope.
    """

    try:
        scope = _scope(source)
        if _scope(source) != scope:
            raise RuntimeError("runtime dependencies changed while identity was computed")
    except OSError as error:
        raise RuntimeError("local runtime sources could not be inspected") from error
    return sha256_bytes(
        canonical_bytes(
            {
                "distributions": [
                    {"name": name, "version": version} for name, version in scope.distributions
                ],
                "local_sources": {key: digest for key, digest, _revision in scope.sources},
            }
        )
    )


def _scope(source: Path | None) -> _Scope:
    importlib.invalidate_caches()
    installed = _installed_distributions()
    if source is None:
        return _Scope(
            distributions=tuple(
                sorted((name, distribution.version) for name, distribution in installed.items())
            ),
            sources=(),
        )
    notebook = source.resolve(strict=True)
    search_path = _search_path(notebook)
    providers = _providers(installed)
    roots: list[tuple[str, frozenset[str] | None]] = list(_RUNTIME_DISTRIBUTIONS)
    sources: dict[str, tuple[str, _Revision]] = {}
    pending = sorted(_notebook_imports(notebook), reverse=True)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        if name in sys.stdlib_module_names or name in sys.builtin_module_names:
            continue
        locations = _module_locations(name, search_path)
        if not locations:
            continue
        distributions = providers.get(name)
        if distributions and all(_installed(path) for path in locations):
            roots.extend((distribution, frozenset()) for distribution in distributions)
            continue
        for key, path in _module_files(locations):
            if path == notebook or key in sources:
                continue
            payload, revision = _read_stable(path)
            sources[key] = (hashlib.sha256(payload).hexdigest(), revision)
            if path.suffix == ".py":
                pending.extend(sorted(_parsed_imports(payload) - visited, reverse=True))
    return _Scope(
        distributions=_distribution_closure(roots, installed),
        sources=tuple(
            (key, digest, revision) for key, (digest, revision) in sorted(sources.items())
        ),
    )


def _notebook_imports(notebook: Path) -> frozenset[str]:
    payload, _revision = _read_stable(notebook)
    try:
        script = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExecutionError(
            "the notebook source is not UTF-8 text",
            code="notebook_invalid",
        ) from error
    names = set(_declared_imports(_metadata_content(script, notebook.suffix)))
    if notebook.suffix == ".py":
        names |= _parsed_imports(payload)
    else:
        for fence in _PYTHON_FENCE.finditer(script):
            names |= _parsed_imports(fence["code"])
    return frozenset(names)


def _metadata_content(script: str, suffix: str) -> str:
    if suffix == ".py":
        return _script_metadata(script)[1]
    # marimo keeps a Markdown notebook's script metadata in front matter, as
    # TOML under `pyproject` or as a PEP 723 preamble under `header`.
    match = _FRONT_MATTER.match(script)
    try:
        front_matter = None if match is None else yaml.safe_load(match["yaml"])
    except yaml.YAMLError:
        return ""
    if not isinstance(front_matter, dict):
        return ""
    pyproject = front_matter.get("pyproject")
    header = front_matter.get("header")
    if isinstance(pyproject, str):
        return pyproject
    return _script_metadata(header)[1] if isinstance(header, str) else ""


def _declared_imports(content: str) -> tuple[str, ...]:
    try:
        metadata = tomlkit.parse(content).unwrap()
    except TOMLKitError as error:
        raise ExecutionError(
            "the notebook script metadata is not valid TOML",
            code="notebook_invalid",
            details={"exception_type": type(error).__name__},
        ) from error
    tool = metadata.get("tool")
    table = tool.get("marimo-export") if isinstance(tool, dict) else None
    if table is None:
        return ()
    imports = table.get("imports") if isinstance(table, dict) else None
    if (
        not isinstance(table, dict)
        or set(table) != {"imports"}
        or not isinstance(imports, list)
        or not all(
            isinstance(name, str) and all(part.isidentifier() for part in name.split("."))
            for name in imports
        )
    ):
        raise ExecutionError(
            "the notebook script metadata must set tool.marimo-export.imports "
            "to a list of module names",
            code="notebook_invalid",
        )
    return tuple(name.partition(".")[0] for name in imports)


def _parsed_imports(code: str | bytes) -> frozenset[str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.partition(".")[0])
    return frozenset(names)


def _search_path(notebook: Path) -> list[str]:
    # The kernel extends `sys.path` from marimo's `runtime.pythonpath` setting
    # and from cells. Leading with the notebook and project directories lets the
    # client and the kernel resolve project modules to the same files.
    directory = notebook.parent
    roots = [directory]
    project = next(
        (
            candidate
            for candidate in (directory, *directory.parents)
            if any((candidate / marker).exists() for marker in _PROJECT_MARKERS)
        ),
        None,
    )
    if project is not None:
        roots.extend((project / "src", project))
    return [*(str(root) for root in roots if root.is_dir()), *sys.path]


def _module_locations(name: str, search_path: list[str]) -> tuple[Path, ...]:
    try:
        spec = importlib.machinery.PathFinder.find_spec(
            name, search_path
        ) or importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return ()
    if spec is None:
        return ()
    if spec.submodule_search_locations is not None:
        return tuple(Path(location) for location in spec.submodule_search_locations)
    if spec.has_location and isinstance(spec.origin, str):
        return (Path(spec.origin),)
    return ()


def _installed(path: Path) -> bool:
    return not _INSTALLED_PARTS.isdisjoint(path.parts)


def _module_files(locations: Iterable[Path]) -> Iterator[tuple[str, Path]]:
    for location in locations:
        if location.is_file():
            if location.suffix in _SOURCE_SUFFIXES:
                yield location.name, location.resolve(strict=True)
            continue
        if not location.is_dir():
            continue
        package = location.resolve(strict=True)
        for current, directories, filenames in os.walk(package, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name
                for name in sorted(directories)
                if not _excluded_source(current_path / name, package)
                and not (current_path / name).is_symlink()
            ]
            for name in sorted(filenames):
                path = current_path / name
                if path.suffix in _SOURCE_SUFFIXES and not _excluded_source(path, package):
                    yield f"{location.name}/{path.relative_to(package).as_posix()}", path


def _excluded_source(path: Path, root: Path) -> bool:
    return any(
        part.startswith(".") or part in {"__pycache__", "node_modules", "site-packages", "venv"}
        for part in path.relative_to(root).parts
    )


def _installed_distributions() -> dict[str, importlib.metadata.Distribution]:
    # The first distribution on the interpreter path is the one Python imports.
    distributions: dict[str, importlib.metadata.Distribution] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if isinstance(name, str) and name:
            distributions.setdefault(canonicalize_name(name), distribution)
    return distributions


def _providers(
    installed: Mapping[str, importlib.metadata.Distribution],
) -> dict[str, list[str]]:
    # Python 3.10 `packages_distributions()` reads only `top_level.txt`, which
    # many wheel builders omit. The installed file list names the same modules.
    providers: dict[str, list[str]] = {}
    for name, distribution in installed.items():
        text = distribution.read_text("top_level.txt")
        if text:
            modules = {line.strip() for line in text.splitlines() if line.strip()}
        else:
            modules = set()
            for file in distribution.files or ():
                top = file.parts[0] if file.parts else ""
                if top in {"", "..", "__pycache__"} or top.endswith((".dist-info", ".data")):
                    continue
                module = top if len(file.parts) > 1 else inspect.getmodulename(top)
                if module:
                    modules.add(module)
        for module in modules:
            providers.setdefault(module, []).append(name)
    return providers


def _distribution_closure(
    roots: Iterable[tuple[str, frozenset[str] | None]],
    installed: Mapping[str, importlib.metadata.Distribution],
) -> tuple[tuple[str, str], ...]:
    versions: dict[str, str] = {}
    followed: set[tuple[str, str]] = set()
    pending = list(roots)
    while pending:
        requested, extras = pending.pop()
        name = canonicalize_name(requested)
        distribution = installed.get(name)
        if distribution is None:
            continue
        versions[name] = distribution.version
        declared = distribution.metadata.get_all("Provides-Extra") or []
        selected = {"", *(declared if extras is None else extras)}
        new = {extra for extra in selected if (name, extra) not in followed}
        if not new:
            continue
        followed.update((name, extra) for extra in new)
        for line in distribution.requires or ():
            try:
                requirement = Requirement(line)
            except InvalidRequirement:
                continue
            marker = requirement.marker
            try:
                required = marker is None or any(marker.evaluate({"extra": extra}) for extra in new)
            except (UndefinedComparison, UndefinedEnvironmentName):
                required = True
            if required:
                pending.append((requirement.name, frozenset(requirement.extras)))
    return tuple(sorted(versions.items()))


def _read_stable(path: Path) -> tuple[bytes, _Revision]:
    try:
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise RuntimeError(f"runtime dependency {path.name!r} could not be read") from error
    if _revision(before) != _revision(after) or len(payload) != after.st_size:
        raise RuntimeError(f"runtime dependency {path.name!r} changed while it was read")
    return payload, _revision(after)


def _revision(value: Any) -> _Revision:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


__all__ = ["environment_identity"]
