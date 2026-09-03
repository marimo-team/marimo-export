"""Compute export producer identity without starting a notebook process."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import importlib.util
import os
import platform
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marimo_export._format import digest
from marimo_export._identity import require_implementation_stable
from marimo_export._json import JsonObject, canonical_bytes, sha256_bytes
from marimo_export._notebook import _notebook_path, _read_stable_source, document_sha256
from marimo_export._portable import validate_portable_basename
from marimo_export.errors import CompatibilityError, ExecutionError
from marimo_export.index import ProducerProvenance
from marimo_export.spec import StrPath


@dataclass(frozen=True, slots=True)
class ProducerIdentity:
    """Stable producer facts used for repository reuse and export provenance."""

    source: Path | None
    filename: str | None
    source_sha256: str
    document_sha256: str
    producer_sha256: str
    marimo_version: str
    marimo_export_version: str
    implementation_sha256: str
    environment_sha256: str

    def __post_init__(self) -> None:
        if self.source is not None:
            if not isinstance(self.source, Path) or not self.source.is_absolute():
                raise ValueError("producer source must be an absolute pathlib.Path or None")
            try:
                metadata = self.source.lstat()
            except OSError as error:
                raise ValueError("producer source must be available") from error
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("producer source must be a regular file")
            if self.filename != self.source.name:
                raise ValueError("producer filename must match its source basename")
        if self.filename is not None:
            validate_portable_basename(self.filename, "producer filename")
        for name in (
            "source_sha256",
            "document_sha256",
            "producer_sha256",
            "implementation_sha256",
            "environment_sha256",
        ):
            digest(getattr(self, name), f"producer {name}")
        for name in ("marimo_version", "marimo_export_version"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 255
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"producer {name} must be a bounded nonempty string")

    @property
    def provenance(self) -> ProducerProvenance:
        return ProducerProvenance(
            marimo=self.marimo_version,
            marimo_export=self.marimo_export_version,
            implementation_sha256=self.implementation_sha256,
        )


def producer_identity(source: StrPath) -> ProducerIdentity:
    """Return exact source and runtime identity without executing notebook code."""

    notebook = _notebook_path(source)
    _, source_sha256, source_revision = _read_stable_source(notebook)
    document = document_sha256(notebook)
    _, after_sha256, after_revision = _read_stable_source(notebook)
    if after_sha256 != source_sha256 or after_revision != source_revision:
        raise RuntimeError("notebook source changed while producer identity was computed")

    return _identity_from_facts(
        document_sha256=document,
        source_sha256=source_sha256,
        source=notebook,
        filename=notebook.name,
    )


def runtime_producer_identity(
    *,
    document_sha256: str,
    source: StrPath | None = None,
    source_sha256: str | None = None,
    filename: str | None = None,
) -> ProducerIdentity:
    """Return producer identity from runtime facts available inside a kernel."""

    if source is not None:
        stable = producer_identity(source)
        if stable.document_sha256 != document_sha256:
            raise ExecutionError(
                "the runtime source does not match the inspected notebook document",
                code="parent_document_changed",
                details={
                    "expected": document_sha256,
                    "actual": stable.document_sha256,
                },
            )
        return stable
    return _identity_from_facts(
        document_sha256=document_sha256,
        source_sha256=document_sha256 if source_sha256 is None else source_sha256,
        source=None,
        filename=filename,
    )


def _identity_from_facts(
    *,
    document_sha256: str,
    source_sha256: str,
    source: Path | None,
    filename: str | None,
) -> ProducerIdentity:
    marimo_version = _required_distribution_version("marimo")
    export_version = _distribution_version("marimo-export", fallback="0.0.0")
    implementation = require_implementation_stable()
    environment = _environment_identity(source)
    producer = sha256_bytes(
        canonical_bytes(
            {
                "document_sha256": document_sha256,
                "environment_sha256": environment,
                "implementation_sha256": implementation,
                "marimo": marimo_version,
                "marimo_export": export_version,
                "platform": {
                    "machine": platform.machine(),
                    "system": platform.system(),
                },
                "python": {
                    "abi": sys.implementation.cache_tag or "unknown",
                    "implementation": sys.implementation.name,
                    "version": platform.python_version(),
                },
                "source_sha256": source_sha256,
            }
        )
    )
    return ProducerIdentity(
        source=source,
        filename=filename,
        source_sha256=source_sha256,
        document_sha256=document_sha256,
        producer_sha256=producer,
        marimo_version=marimo_version,
        marimo_export_version=export_version,
        implementation_sha256=implementation,
        environment_sha256=environment,
    )


def producer_sha256(source: StrPath) -> str:
    """Return the spec-independent producer identity for one notebook source."""

    return producer_identity(source).producer_sha256


def managed_runtime_source(runtime_path: str | None) -> str | None:
    """Return the authored source path selected for an attached runtime."""

    return os.environ.get("MARIMO_EXPORT_MANAGED_SOURCE") or runtime_path


def _environment_identity(source: Path | None) -> str:
    distributions = _installed_distributions()
    sources = {} if source is None else _local_source_record(source)
    if _installed_distributions() != distributions:
        raise RuntimeError("installed distributions changed while identity was computed")
    return sha256_bytes(
        canonical_bytes(
            {
                "distributions": distributions,
                "local_sources": sources,
            }
        )
    )


def _installed_distributions() -> list[JsonObject]:
    distributions: list[JsonObject] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not name:
            continue
        distributions.append(
            {
                "name": name.lower().replace("_", "-"),
                "version": distribution.version,
            }
        )
    distributions.sort(key=lambda item: canonical_bytes(item))
    return distributions


def _local_source_record(source: Path) -> JsonObject:
    try:
        resolved = source.resolve(strict=True)
        root, search_roots = _local_source_roots(resolved)
        before = _local_source_manifest(root, search_roots)
        records: JsonObject = {}
        for relative, *_revision_parts in before:
            records[relative] = _stable_file_sha256(root / relative)
        after_root, after_search_roots = _local_source_roots(resolved)
        after = _local_source_manifest(root, search_roots)
        after_records = {
            relative: _stable_file_sha256(root / relative) for relative, *_revision_parts in after
        }
        if (
            after_root != root
            or after_search_roots != search_roots
            or after != before
            or after_records != records
        ):
            raise RuntimeError("local runtime sources changed while identity was computed")
    except RuntimeError:
        raise
    except OSError as error:
        raise RuntimeError("local runtime sources could not be inspected") from error
    return records


def _local_source_manifest(
    root: Path,
    search_roots: tuple[Path, ...] | None = None,
) -> tuple[tuple[str, int, int, int, int, int], ...]:
    selected = (root,) if search_roots is None else search_roots
    paths: set[Path] = set()
    for search_root in selected:
        if search_root.is_file():
            if _source_file(search_root) and not _excluded_source(search_root, root):
                paths.add(search_root)
            continue
        if not search_root.is_dir() or search_root.is_symlink():
            continue
        for current, directories, filenames in os.walk(search_root, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name
                for name in sorted(directories)
                if not _excluded_source(current_path / name, root)
                and not (current_path / name).is_symlink()
            ]
            for name in sorted(filenames):
                path = current_path / name
                if _source_file(path) and not _excluded_source(path, root):
                    paths.add(path)
    return tuple(
        (
            path.relative_to(root).as_posix(),
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        for path in sorted(paths)
        for metadata in (path.stat(),)
    )


def _local_source_roots(source: Path) -> tuple[Path, tuple[Path, ...]]:
    if source.is_dir():
        return source, (source,)
    root = _project_root(source)
    try:
        tree = ast.parse(source.read_bytes(), filename=str(source))
    except (OSError, SyntaxError, ValueError):
        return source.parent, (source.parent,)
    names = _import_names(tree)
    roots: set[Path] = {source}
    for path in source.parent.iterdir():
        if path.is_file() and _source_file(path):
            roots.add(path)
    search_bases = {source.parent, root}
    conventional = root / "src"
    if conventional.is_dir() and not conventional.is_symlink():
        search_bases.add(conventional)
    for base in search_bases:
        if not base.is_dir() or base.is_symlink():
            continue
        for child in base.iterdir():
            if child.is_dir() and not child.is_symlink() and (child / "__init__.py").is_file():
                roots.add(child)
        for name in names:
            module = base / f"{name}.py"
            package = base / name
            if module.is_file():
                roots.add(module)
            if package.is_dir() and not package.is_symlink():
                roots.add(package)
    roots.update(_external_import_roots(names))
    try:
        record_root = Path(os.path.commonpath((root, *roots)))
    except ValueError:
        roots = {path for path in roots if path.is_relative_to(root)}
        record_root = root
    return record_root, tuple(sorted(roots))


def _project_root(source: Path) -> Path:
    for candidate in (source.parent, *source.parents):
        if any((candidate / marker).exists() for marker in ("pyproject.toml", "setup.cfg")):
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return source.parent


def _import_names(tree: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.partition(".")[0])
    return frozenset(names)


def _external_import_roots(names: frozenset[str]) -> set[Path]:
    roots: set[Path] = set()
    runtime_roots = tuple(Path(value).resolve() for value in {sys.prefix, sys.base_prefix} if value)
    for name in names:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
        if spec is None:
            continue
        candidates = [Path(value) for value in spec.submodule_search_locations or ()]
        origin = spec.origin
        if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
            candidates.append(Path(origin))
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if any(resolved.is_relative_to(runtime_root) for runtime_root in runtime_roots):
                continue
            roots.add(resolved)
    return roots


def _source_file(path: Path) -> bool:
    return path.suffix in {".py", ".pyi", ".so", ".pyd", ".dll", ".dylib"}


def _excluded_source(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(
        part.startswith(".") or part in {"__pycache__", "node_modules", "site-packages", "venv"}
        for part in relative.parts
    )


def _stable_file_sha256(path: Path) -> str:
    try:
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise RuntimeError(f"runtime dependency {path.name!r} could not be read") from error
    if _revision(before) != _revision(after) or len(payload) != after.st_size:
        raise RuntimeError(f"runtime dependency {path.name!r} changed while it was read")
    return hashlib.sha256(payload).hexdigest()


def _revision(value: Any) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _required_distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise CompatibilityError(
            f"required runtime distribution {name!r} is unavailable",
            code="runtime_distribution_unavailable",
        ) from error


def _distribution_version(name: str, *, fallback: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return fallback


__all__ = [
    "ProducerIdentity",
    "managed_runtime_source",
    "producer_identity",
    "producer_sha256",
    "runtime_producer_identity",
]
