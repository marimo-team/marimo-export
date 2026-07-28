from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import marimo_export


def test_root_package_exposes_the_public_api() -> None:
    expected = {
        "BuiltinExporterDescription",
        "CacheSummary",
        "CaptureError",
        "CaptureResult",
        "CellDescription",
        "Client",
        "ControlDescription",
        "TransportError",
        "ExportSpec",
        "GlobalDescription",
        "IntegrityError",
        "JsonObject",
        "JsonValue",
        "MarimoExportError",
        "NotebookProvenance",
        "Projection",
        "ProducerProvenance",
        "Publication",
        "PublicationError",
        "PublishedFormat",
        "PublishedOutput",
        "PublishedVariant",
        "Session",
        "SessionDescription",
        "SessionError",
        "SpecError",
        "capture",
        "open_publication",
    }
    assert set(marimo_export.__all__) == expected
    for name in expected:
        assert getattr(marimo_export, name) is not None


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


def test_domain_modules_do_not_import_runtime_adapters() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for name in ("errors.py", "projection.py", "publication.py", "spec.py"):
        source = package / name
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (node.module or "").startswith(("marimo_export._marimo", "marimo_export._remote")):
                violations.append(name)
    assert violations == []


def test_public_import_does_not_load_optional_exporter_dependencies() -> None:
    script = """
import json
import sys
import marimo_export

names = {name.split('.', 1)[0] for name in sys.modules}
print(json.dumps(sorted(names & {'anywidget', 'marimo', 'narwhals', 'pyarrow', 'vl_convert'})))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
