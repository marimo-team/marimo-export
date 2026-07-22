from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import get_type_hints

import marimo_export
from marimo_export import Projection
from marimo_export._marimo import cache


def test_root_package_exposes_the_public_api() -> None:
    assert marimo_export.__all__ == ["Projection", "__version__"]
    assert Projection.__module__ == "marimo_export"


def test_projection_public_type_hints_resolve() -> None:
    assert get_type_hints(Projection)["metadata"] == Mapping[str, object]


def test_private_marimo_imports_stay_in_adapter_package() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for source in package.rglob("*.py"):
        if "_marimo" in source.relative_to(package).parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.startswith("marimo._") for name in names):
                violations.append(str(source.relative_to(package)))
    assert violations == []


def test_cache_keys_are_relative_posix_paths() -> None:
    assert cache.validate_key("marimo-export/payloads/sha256/value") == (
        "marimo-export/payloads/sha256/value"
    )
    for key in ("../secret", "/absolute", "nested/../../secret", "windows\\path"):
        try:
            cache.validate_key(key)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe key {key!r}")


def test_remote_import_does_not_load_optional_projection_packages() -> None:
    script = """
import json
import sys
import marimo_export.remote

names = {name.split('.', 1)[0] for name in sys.modules}
print(json.dumps(sorted(names & {'narwhals', 'pyarrow', 'vl_convert'})))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
