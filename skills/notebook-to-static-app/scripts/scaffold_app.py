#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version as distribution_version
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

LOADER_DEPENDENCIES = {
    "anywidget": {"@anywidget/types": "0.4.0"},
    "arrow": {"@uwdata/flechette": "2.5.0", "lz4js": "0.2.0"},
    "html": {},
    "json": {},
    "marimo-cell": {},
    "marimo-output": {},
    "numpy": {},
    "parquet": {"hyparquet": "1.29.2"},
    "text": {},
    "vegalite": {"vega-embed": "7.2.0"},
}
_SUPPORTED_PYTHON = SpecifierSet(">=3.10,<3.15")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a self-contained uv and Vite workspace for one marimo export."
    )
    parser.add_argument("--notebook", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--marimo-export-root", type=Path)
    parser.add_argument("--browser-package", type=Path)
    parser.add_argument("--python-package", type=Path)
    parser.add_argument(
        "--loader", action="append", choices=sorted(LOADER_DEPENDENCIES), default=[]
    )
    parser.add_argument("--name")
    args = parser.parse_args()

    notebook = args.notebook.expanduser().resolve()
    output = args.output.expanduser().resolve()
    candidate_root = (
        args.marimo_export_root.expanduser().resolve()
        if args.marimo_export_root is not None
        else Path(__file__).resolve().parents[3]
    )
    _require_file(notebook, "notebook")
    _require_empty_destination(output)
    export_root = _export_root(candidate_root, required=args.marimo_export_root is not None)
    package_version = distribution_version("marimo-export")

    source_digest = hashlib.sha256(notebook.read_bytes()).hexdigest()
    metadata = _script_metadata(notebook)
    slug = _slug(args.name or notebook.stem)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.scaffold-",
        dir=output.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        staging = temporary_root / "app"
        package_stage = temporary_root / "packages"
        staging.mkdir()
        package_stage.mkdir()
        browser_source = (
            args.browser_package.expanduser().resolve()
            if args.browser_package is not None
            else _pack_browser(export_root, package_stage)
            if export_root is not None
            else None
        )
        python_source = (
            args.python_package.expanduser().resolve()
            if args.python_package is not None
            else _build_python(export_root, package_stage)
            if export_root is not None
            else None
        )
        browser_package: Path | None = None
        python_package: Path | None = None
        if browser_source is not None or python_source is not None:
            vendor = staging / "vendor"
            vendor.mkdir()
            if browser_source is not None:
                _require_file(browser_source, "packed browser package")
                browser_package = vendor / "marimo-export.tgz"
                shutil.copy2(browser_source, browser_package)
            if python_source is not None:
                _require_file(python_source, "Python wheel")
                python_package = vendor / python_source.name
                shutil.copy2(python_source, python_package)

        template = Path(__file__).resolve().parents[1] / "assets/app"
        shutil.copytree(template, staging, dirs_exist_ok=True)
        (staging / "public").mkdir()
        _replace(staging / "index.html", "__APP_TITLE__", _title(slug))
        _write_pyproject(
            staging / "pyproject.toml",
            slug=slug,
            requires_python=metadata.get("requires-python", ">=3.10"),
            dependencies=metadata.get("dependencies", []),
            package_version=package_version,
            python_package=(
                python_package.relative_to(staging) if python_package is not None else None
            ),
        )
        _write_package_json(
            staging / "package.json",
            slug=slug,
            browser_package=(
                browser_package.relative_to(staging) if browser_package is not None else None
            ),
            package_version=package_version,
            loaders=args.loader,
        )
        provenance = {
            "filename": notebook.name,
            "sha256": source_digest,
        }
        (staging / ".notebook-source.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if hashlib.sha256(notebook.read_bytes()).hexdigest() != source_digest:
            raise SystemExit("notebook source changed while creating the app workspace")
        _commit(staging, output)
    print(output)


def _script_metadata(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == "# /// script"), None
    )
    if start is None:
        return {"dependencies": [], "requires-python": ">=3.10,<3.15"}
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip() == "# ///"),
        None,
    )
    if end is None:
        raise SystemExit(f"unterminated PEP 723 script metadata: {path}")
    body: list[str] = []
    for line in lines[start + 1 : end]:
        if not line.startswith("#"):
            raise SystemExit(f"invalid PEP 723 metadata line: {line!r}")
        value = line[1:]
        body.append(value[1:] if value.startswith(" ") else value)
    parsed = tomllib.loads("\n".join(body))
    dependencies = parsed.get("dependencies", [])
    requires_python = parsed.get("requires-python", ">=3.10")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, str) for item in dependencies
    ):
        raise SystemExit("PEP 723 dependencies must be an array of strings")
    if not isinstance(requires_python, str):
        raise SystemExit("PEP 723 requires-python must be a string")
    effective_requires_python = _validate_requires_python(requires_python)
    return {
        "dependencies": dependencies,
        "requires-python": effective_requires_python,
    }


def _validate_requires_python(value: str) -> str:
    try:
        requested = SpecifierSet(value)
    except InvalidSpecifier as error:
        raise SystemExit(f"PEP 723 requires-python is invalid: {value}") from error
    combined = requested & _SUPPORTED_PYTHON
    candidates = {
        Version("3.8"),
        Version("3.9"),
        Version("3.10"),
        Version("3.11"),
        Version("3.12"),
        Version("3.13"),
        Version("3.14"),
        Version("3.15"),
        Version("4.0"),
    }
    for specifier in requested:
        token = specifier.version.removesuffix(".*")
        try:
            version = Version(token)
        except InvalidVersion:
            continue
        candidates.add(version)
        release = version.release
        if len(release) == 1:
            candidates.add(Version(f"{release[0]}.0"))
            candidates.add(Version(f"{release[0] + 1}.0"))
        elif len(release) == 2:
            candidates.add(Version(f"{release[0]}.{release[1]}.1"))
            candidates.add(Version(f"{release[0]}.{release[1] + 1}"))
        else:
            candidates.add(Version(".".join(map(str, (*release[:-1], release[-1] + 1)))))
    if not any(combined.contains(candidate, prereleases=True) for candidate in candidates):
        raise SystemExit(
            f"notebook requires-python {value!r} does not intersect marimo-export >=3.10,<3.15"
        )
    floor = Version("3.10")
    ceiling = Version("3.15")
    if not any(
        candidate < floor and requested.contains(candidate, prereleases=True)
        for candidate in candidates
    ) and not any(
        candidate >= ceiling and requested.contains(candidate, prereleases=True)
        for candidate in candidates
    ):
        return value
    effective = []
    has_lower_bound = False
    has_upper_bound = False
    for specifier in requested:
        token = specifier.version.removesuffix(".*")
        try:
            version = Version(token)
        except InvalidVersion:
            version = None
        if version is not None and version < floor and specifier.operator in {">", ">="}:
            continue
        if version is not None and version >= ceiling and specifier.operator in {"<", "<="}:
            continue
        if version is not None and specifier.operator in {">", ">="}:
            has_lower_bound = True
        if version is not None and specifier.operator in {"<", "<="}:
            has_upper_bound = True
        effective.append(str(specifier))
    if not has_lower_bound:
        effective.append(">=3.10")
    if not has_upper_bound:
        effective.append("<3.15")
    return str(SpecifierSet(",".join(effective)))


def _export_root(candidate: Path, *, required: bool) -> Path | None:
    browser = candidate / "packages/browser/package.json"
    python = candidate / "packages/python/pyproject.toml"
    if browser.is_file() and python.is_file():
        return candidate
    if required:
        _require_file(browser, "browser package")
        _require_file(python, "Python package")
    return None


def _pack_browser(root: Path, destination: Path) -> Path:
    subprocess.run(
        [
            "pnpm",
            "--dir",
            str(root),
            "--filter",
            "@marimo-team/marimo-export",
            "pack",
            "--pack-destination",
            str(destination),
        ],
        check=True,
    )
    packages = sorted(destination.glob("marimo-team-marimo-export-*.tgz"))
    if len(packages) != 1:
        raise SystemExit(f"expected one packed browser package in {destination}")
    return packages[0]


def _build_python(root: Path, destination: Path) -> Path:
    subprocess.run(
        [
            "uv",
            "build",
            "--package",
            "marimo-export",
            "--wheel",
            "--no-sources",
            "--out-dir",
            str(destination),
        ],
        cwd=root,
        check=True,
    )
    packages = sorted(destination.glob("marimo_export-*.whl"))
    if len(packages) != 1:
        raise SystemExit(f"expected one Python wheel in {destination}")
    return packages[0]


def _write_pyproject(
    path: Path,
    *,
    slug: str,
    requires_python: object,
    dependencies: object,
    package_version: str,
    python_package: Path | None,
) -> None:
    if not isinstance(requires_python, str):
        raise SystemExit("PEP 723 requires-python must be a string")
    if not isinstance(dependencies, list):
        raise SystemExit("PEP 723 dependencies must be an array")
    requirements = [
        item
        for item in dependencies
        if isinstance(item, str) and _requirement_name(item) != "marimo-export"
    ]
    requirement = (
        "marimo-export[all]"
        if python_package is not None
        else f"marimo-export[all]=={package_version}"
    )
    requirements.append(requirement)
    rendered = [
        "[project]",
        f"name = {json.dumps(f'marimo-static-{slug}')}",
        'version = "0.0.0"',
        f"requires-python = {json.dumps(requires_python)}",
        "dependencies = [",
        *(f"  {json.dumps(item)}," for item in requirements),
        "]",
        "",
        "[tool.uv]",
        "package = false",
    ]
    if python_package is not None:
        rendered.extend(
            (
                "",
                "[tool.uv.sources]",
                f"marimo-export = {{ path = {json.dumps(python_package.as_posix())} }}",
            )
        )
    rendered.append("")
    path.write_text("\n".join(rendered), encoding="utf-8")


def _write_package_json(
    path: Path,
    *,
    slug: str,
    browser_package: Path | None,
    package_version: str,
    loaders: list[str],
) -> None:
    dependencies = {
        "@marimo-team/marimo-export": (
            f"file:{browser_package.as_posix()}" if browser_package is not None else package_version
        ),
    }
    for loader in loaders:
        dependencies.update(LOADER_DEPENDENCIES[loader])
    value = {
        "name": f"marimo-static-{slug}",
        "version": "0.0.0",
        "private": True,
        "type": "module",
        "scripts": {
            "build": "vp build",
            "check": "vp check",
            "dev": "vp dev --host 127.0.0.1",
            "preview": "vp preview --host 127.0.0.1",
            "typecheck": "tsc --noEmit",
        },
        "dependencies": dict(sorted(dependencies.items())),
        "devDependencies": {
            "typescript": "6.0.3",
            "vite-plus": "0.3.0",
        },
        "engines": {"node": ">=22.18.0"},
        "packageManager": "pnpm@11.25.0",
    }
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    return "" if match is None else match.group(1).lower().replace("_", "-")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise SystemExit("app name must contain a letter or digit")
    return slug


def _title(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-"))


def _replace(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    if before not in text:
        raise SystemExit(f"template marker {before!r} is missing from {path}")
    path.write_text(text.replace(before, after), encoding="utf-8")


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} is unavailable: {path}")


def _require_empty_destination(path: Path) -> None:
    if not path.exists():
        if not path.parent.is_dir():
            raise SystemExit(f"output parent is unavailable: {path.parent}")
        return
    if not path.is_dir():
        raise SystemExit(f"output path is not a directory: {path}")
    if any(path.iterdir()):
        raise SystemExit(f"output directory is not empty: {path}")


def _commit(staging: Path, destination: Path) -> None:
    if destination.exists():
        destination.rmdir()
    os.replace(staging, destination)


if __name__ == "__main__":
    main()
