#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

LOADER_DEPENDENCIES = {
    "anywidget": {"@anywidget/types": "^0.4.0"},
    "arrow": {"@uwdata/flechette": "^2.5.0", "lz4js": "0.2.0"},
    "numpy": {},
    "parquet": {"hyparquet": "^1.26.2"},
    "vegalite": {"vega-embed": "^7.1.0"},
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create local uv and Vite plumbing for one marimo static app."
    )
    parser.add_argument("--notebook", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--marimo-export-root", required=True, type=Path)
    parser.add_argument("--browser-package", type=Path)
    parser.add_argument(
        "--loader", action="append", choices=sorted(LOADER_DEPENDENCIES), default=[]
    )
    parser.add_argument("--name")
    args = parser.parse_args()

    notebook = args.notebook.expanduser().resolve()
    output = args.output.expanduser().resolve()
    export_root = args.marimo_export_root.expanduser().resolve()
    _require_file(notebook, "notebook")
    _require_file(export_root / "packages/browser/package.json", "browser package")
    _require_file(export_root / "packages/python/pyproject.toml", "Python package")
    if output.exists():
        if not output.is_dir():
            raise SystemExit(f"output path is not a directory: {output}")
        if any(output.iterdir()):
            raise SystemExit(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    metadata = _script_metadata(notebook)
    slug = _slug(args.name or notebook.stem)
    browser_package = (
        args.browser_package.expanduser().resolve()
        if args.browser_package is not None
        else _pack_browser(export_root, output / "vendor")
    )
    _require_file(browser_package, "packed browser package")

    template = Path(__file__).resolve().parents[1] / "assets/app"
    shutil.copytree(template, output, dirs_exist_ok=True)
    (output / "public").mkdir()
    _replace(output / "index.html", "__APP_TITLE__", _title(slug))
    _write_pyproject(
        output / "pyproject.toml",
        slug=slug,
        requires_python=_effective_requires_python(metadata.get("requires-python", ">=3.11")),
        dependencies=metadata.get("dependencies", []),
        python_package=export_root / "packages/python",
    )
    _write_package_json(
        output / "package.json",
        slug=slug,
        browser_package=browser_package,
        loaders=args.loader,
    )
    provenance = {
        "notebook": str(notebook),
        "sha256": hashlib.sha256(notebook.read_bytes()).hexdigest(),
    }
    (output / ".notebook-source.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)


def _script_metadata(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == "# /// script"), None
    )
    if start is None:
        return {"dependencies": [], "requires-python": ">=3.11"}
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
    requires_python = parsed.get("requires-python", ">=3.11")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, str) for item in dependencies
    ):
        raise SystemExit("PEP 723 dependencies must be an array of strings")
    if not isinstance(requires_python, str):
        raise SystemExit("PEP 723 requires-python must be a string")
    return {"dependencies": dependencies, "requires-python": requires_python}


def _pack_browser(root: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
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
    return packages[0].resolve()


def _write_pyproject(
    path: Path,
    *,
    slug: str,
    requires_python: object,
    dependencies: object,
    python_package: Path,
) -> None:
    assert isinstance(requires_python, str)
    assert isinstance(dependencies, list)
    requirements = [item for item in dependencies if isinstance(item, str)]
    if not any(_requirement_name(item) == "marimo-export" for item in requirements):
        requirements.append("marimo-export[all]")
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
        "",
        "[tool.uv.sources]",
        (
            "marimo-export = { path = "
            f"{json.dumps(str(python_package.resolve()))}, editable = true }}"
        ),
        "",
    ]
    path.write_text("\n".join(rendered), encoding="utf-8")


def _write_package_json(
    path: Path,
    *,
    slug: str,
    browser_package: Path,
    loaders: list[str],
) -> None:
    dependencies = {
        "@marimo-team/marimo-export": browser_package.as_uri(),
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
            "typescript": "^6.0.3",
            "vite-plus": "0.2.4",
        },
        "engines": {"node": ">=22.18.0"},
        "packageManager": "pnpm@11.15.1",
    }
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    return "" if match is None else match.group(1).lower().replace("_", "-")


def _effective_requires_python(value: object) -> str:
    if not isinstance(value, str):
        raise SystemExit("PEP 723 requires-python must be a string")
    if ">=3.11" in {part.strip().replace(" ", "") for part in value.split(",")}:
        return value
    return f"{value},>=3.11"


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


if __name__ == "__main__":
    main()
