"""Verify that PyPI serves the exact local release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FINAL_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_artifacts(dist: Path, version: str) -> dict[str, Path]:
    paths = [
        *dist.glob(f"marimo_export-{version}-*.whl"),
        dist / f"marimo_export-{version}.tar.gz",
    ]
    artifacts = {path.name: path for path in paths if path.is_file()}
    if len(artifacts) != 2:
        raise RuntimeError(
            f"expected one wheel and one source distribution for {version}, "
            f"found {sorted(artifacts)}"
        )
    return artifacts


def verify_release(dist: Path, version: str, metadata: dict[str, Any]) -> None:
    local = _local_artifacts(dist, version)
    published_version = metadata.get("info", {}).get("version")
    if published_version != version:
        raise RuntimeError(f"PyPI metadata reports {published_version!r}, expected {version!r}")

    urls = metadata.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("PyPI metadata has no release file list")
    remote = {
        item.get("filename"): item
        for item in urls
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    if set(remote) != set(local):
        raise RuntimeError(
            f"PyPI release files {sorted(remote)} do not match local artifacts {sorted(local)}"
        )

    for filename, path in local.items():
        digests = remote[filename].get("digests")
        published = digests.get("sha256") if isinstance(digests, dict) else None
        expected = _sha256(path)
        if published != expected:
            raise RuntimeError(
                f"PyPI artifact {filename} has SHA-256 {published!r}, expected {expected}"
            )


def _fetch_release(version: str) -> dict[str, Any]:
    url = f"https://pypi.org/pypi/marimo-export/{version}/json"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.load(response)
    except (json.JSONDecodeError, urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"could not read {url}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"PyPI returned an invalid release record from {url}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--dist", type=Path, default=Path("dist/python"))
    arguments = parser.parse_args()
    if FINAL_VERSION.fullmatch(arguments.version) is None:
        parser.error(f"version must use final X.Y.Z form: {arguments.version}")
    try:
        metadata = _fetch_release(arguments.version)
        verify_release(arguments.dist.resolve(), arguments.version, metadata)
    except RuntimeError as error:
        parser.exit(1, f"ERROR: {error}\n")
    print(f"Verified PyPI artifacts for marimo-export {arguments.version}.")


if __name__ == "__main__":
    main()
