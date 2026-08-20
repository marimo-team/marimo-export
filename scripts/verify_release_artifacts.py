"""Verify the exact Python and npm artifacts produced for one release."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - contributor Python 3.10
    import tomli as tomllib

REPOSITORY_URL = "git+https://github.com/marimo-team/marimo-export.git"
FORBIDDEN_DEPENDENCY_PREFIXES = ("catalog:", "file:", "link:", "workspace:")


def _single(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise RuntimeError(f"expected one {label}, found {len(paths)}")
    return paths[0]


def _public_versions(root: Path) -> dict[str, str]:
    with (root / "packages/python/pyproject.toml").open("rb") as stream:
        python = tomllib.load(stream)
    browser = json.loads((root / "packages/browser/package.json").read_text(encoding="utf-8"))
    portable = json.loads(
        (root / "packages/portable-json/package.json").read_text(encoding="utf-8")
    )
    return {
        "marimo-export": python["project"]["version"],
        "@marimo-team/marimo-export": browser["version"],
        "@marimo-team/portable-json": portable["version"],
    }


def _require_release_version(root: Path) -> str:
    versions = _public_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        raise RuntimeError(f"public package versions must match: {versions}")
    version = unique.pop()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise RuntimeError(f"public package version must use final X.Y.Z form: {version}")
    return version


def _wheel_payload(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name) for name in archive.namelist() if not name.endswith("/RECORD")
        }


def _verify_wheel(path: Path, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        metadata_name = _single(
            [Path(name) for name in archive.namelist() if name.endswith(".dist-info/METADATA")],
            f"METADATA record in {path.name}",
        ).as_posix()
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        if metadata["Name"] != "marimo-export" or metadata["Version"] != version:
            raise RuntimeError(f"{path.name} contains unexpected Python package metadata")
        entry_points = _single(
            [
                Path(name)
                for name in archive.namelist()
                if name.endswith(".dist-info/entry_points.txt")
            ],
            f"entry_points.txt in {path.name}",
        ).as_posix()
        entries = archive.read(entry_points).decode("utf-8")
        if "marimo-export = marimo_export.cli:main" not in entries:
            raise RuntimeError(f"{path.name} has no marimo-export console entry point")
        if "marimo-export = marimo_export._marimo.entrypoints:kernel_lifespan" not in entries:
            raise RuntimeError(f"{path.name} has no managed kernel lifespan entry point")


def _verify_sdist(path: Path, version: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        pyproject = _single(
            [member for member in archive.getmembers() if member.name.endswith("/pyproject.toml")],
            f"pyproject.toml in {path.name}",
        )
        stream = archive.extractfile(pyproject)
        if stream is None:
            raise RuntimeError(f"could not read {pyproject.name}")
        value = tomllib.loads(stream.read().decode("utf-8"))
        if value["project"]["version"] != version:
            raise RuntimeError(f"{path.name} contains the wrong Python package version")


def _manifest_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _manifest_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _manifest_strings(child)]
    return []


def _export_targets(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(target for child in value.values() for target in _export_targets(child))
    raise RuntimeError("npm package exports must contain strings or condition objects")


def _verify_npm_tarball(
    path: Path,
    *,
    name: str,
    version: str,
    directory: str,
    portable_dependency: bool,
) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        manifest_member = members.get("package/package.json")
        if manifest_member is None:
            raise RuntimeError(f"{path.name} has no package.json")
        stream = archive.extractfile(manifest_member)
        if stream is None:
            raise RuntimeError(f"could not read package.json from {path.name}")
        manifest = json.loads(stream.read())
        if manifest.get("name") != name or manifest.get("version") != version:
            raise RuntimeError(f"{path.name} contains unexpected npm package metadata")
        if manifest.get("publishConfig") != {"access": "public"}:
            raise RuntimeError(f"{path.name} must publish as a public package")
        if manifest.get("repository") != {
            "type": "git",
            "url": REPOSITORY_URL,
            "directory": directory,
        }:
            raise RuntimeError(f"{path.name} has unexpected repository metadata")
        dependencies = manifest.get("dependencies", {})
        if portable_dependency and dependencies.get("@marimo-team/portable-json") != version:
            raise RuntimeError(f"{path.name} must depend on @marimo-team/portable-json {version}")
        forbidden = [
            value
            for value in _manifest_strings(
                {
                    "dependencies": dependencies,
                    "optionalDependencies": manifest.get("optionalDependencies", {}),
                    "peerDependencies": manifest.get("peerDependencies", {}),
                }
            )
            if value.startswith(FORBIDDEN_DEPENDENCY_PREFIXES)
        ]
        if forbidden:
            raise RuntimeError(f"{path.name} contains unpublished dependency sources: {forbidden}")
        for target in _export_targets(manifest["exports"]):
            relative = target.removeprefix("./")
            if f"package/{relative}" not in members:
                raise RuntimeError(f"{path.name} does not contain export target {target}")


def verify(root: Path) -> None:
    version = _require_release_version(root)
    python_root = root / "dist/python"
    npm_root = root / "dist/npm"
    direct_wheel = _single(
        list(python_root.glob(f"marimo_export-{version}-*.whl")),
        "direct Python wheel",
    )
    rebuilt_wheel = _single(
        list((python_root / "from-sdist").glob(f"marimo_export-{version}-*.whl")),
        "wheel rebuilt from the source distribution",
    )
    sdist = _single(
        list(python_root.glob(f"marimo_export-{version}.tar.gz")),
        "Python source distribution",
    )
    portable = npm_root / f"marimo-team-portable-json-{version}.tgz"
    browser = npm_root / f"marimo-team-marimo-export-{version}.tgz"
    for path in (portable, browser):
        if not path.is_file():
            raise RuntimeError(f"release artifact is missing: {path}")

    _verify_wheel(direct_wheel, version)
    _verify_wheel(rebuilt_wheel, version)
    _verify_sdist(sdist, version)
    if _wheel_payload(direct_wheel) != _wheel_payload(rebuilt_wheel):
        raise RuntimeError("the direct and source-rebuilt wheels contain different payloads")
    _verify_npm_tarball(
        portable,
        name="@marimo-team/portable-json",
        version=version,
        directory="packages/portable-json",
        portable_dependency=False,
    )
    _verify_npm_tarball(
        browser,
        name="@marimo-team/marimo-export",
        version=version,
        directory="packages/browser",
        portable_dependency=True,
    )
    print(f"Verified coordinated marimo-export {version} release artifacts.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="marimo-export repository root",
    )
    arguments = parser.parse_args()
    verify(arguments.root.resolve())


if __name__ == "__main__":
    main()
