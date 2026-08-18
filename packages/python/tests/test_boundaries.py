from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import marimo_export

_MARIMO_VERSION = re.compile(
    r"(?i)(?<![\w-])marimo(?:\[[^\]\r\n]+\])?"
    r"(?:\s*[<>=!~]{1,2}\s*v?|\s+v?)\d+\.\d+(?:\.\d+)?"
)


def test_root_package_exposes_the_public_api() -> None:
    expected = {
        "BlobAsset",
        "Client",
        "ExportSpec",
        "OutputSpec",
        "NotebookExport",
        "ExportResult",
        "Session",
        "build",
        "capture",
        "open_export",
    }
    assert set(marimo_export.__all__) == expected
    for name in expected:
        assert getattr(marimo_export, name) is not None


def test_domain_modules_do_not_import_runtime_adapters() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for name in ("errors.py", "export.py", "result.py", "spec.py"):
        source = package / name
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules = _imported_modules(node)
            if any(
                module.startswith(("marimo_export._marimo", "marimo_export._remote"))
                for module in modules
            ):
                violations.append(name)
    assert violations == []


def test_private_marimo_imports_stay_in_compatibility_adapters() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for source in package.rglob("*.py"):
        relative = source.relative_to(package)
        if relative.parts[:2] == ("_marimo", "compat"):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if any(
            module.startswith("marimo._")
            for node in ast.walk(tree)
            for module in _imported_modules(node)
        ):
            violations.append(relative.as_posix())
    assert violations == []


def test_private_adapter_imports_use_composition_roots() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    allowed = {
        "_marimo/anywidget.py",
        "_marimo/blob.py",
        "_marimo/composition.py",
        "_marimo/entrypoints.py",
        "_marimo/managed_server.py",
    }
    violations: list[str] = []
    for source in package.rglob("*.py"):
        relative = source.relative_to(package).as_posix()
        if relative.startswith("_marimo/compat/") or relative in allowed:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if any(
            module.startswith("marimo_export._marimo.compat")
            for node in ast.walk(tree)
            for module in _imported_modules(node)
        ):
            violations.append(relative)
    assert violations == []


def test_user_documentation_defers_marimo_versions_to_package_metadata() -> None:
    root = Path(__file__).parents[3]
    violations = [
        path.relative_to(root).as_posix()
        for directory in (root / "docs", root / "development_docs")
        for path in directory.rglob("*.md")
        if _MARIMO_VERSION.search(path.read_text(encoding="utf-8"))
    ]

    assert violations == []


def _imported_modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        base = f"{'.' * node.level}{node.module or ''}"
        resolved = [base]
        for alias in node.names:
            separator = "" if not base or base.endswith(".") else "."
            resolved.append(f"{base}{separator}{alias.name}")
        return tuple(resolved)
    return ()


def test_import_resolver_expands_from_import_members() -> None:
    tree = ast.parse(
        "from marimo import _future_private\nfrom marimo_export._marimo import compat\n"
    )
    modules = {module for node in ast.walk(tree) for module in _imported_modules(node)}

    assert "marimo._future_private" in modules
    assert "marimo_export._marimo.compat" in modules


def test_public_import_defers_runtime_and_optional_dependencies() -> None:
    script = """
import json
import sys
import marimo_export

names = {name.split('.', 1)[0] for name in sys.modules}
print(json.dumps({
    'optional': sorted(names & {'anywidget', 'pyarrow', 'vl_convert'}),
    'kernel_lifecycle': 'marimo._runtime.kernel_lifecycle' in sys.modules,
    'cache_loaders': 'marimo._save.loaders' in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "optional": [],
        "kernel_lifecycle": False,
        "cache_loaders": False,
    }


def test_blob_asset_binding_does_not_load_the_execution_graph() -> None:
    script = """
import json
import sys
import marimo_export

assert marimo_export.BlobAsset is not None
print(json.dumps({
    'pydantic': any(name == 'pydantic' or name.startswith('pydantic.') for name in sys.modules),
    'plan': 'marimo_export._execution.plan' in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {"pydantic": False, "plan": False}


def test_anywidget_capture_binding_does_not_load_the_execution_graph() -> None:
    script = """
import json
import sys
from marimo_export._marimo.anywidget import create_anywidget_capture

assert create_anywidget_capture() is not None
print(json.dumps({
    'pydantic': any(name == 'pydantic' or name.startswith('pydantic.') for name in sys.modules),
    'plan': 'marimo_export._execution.plan' in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {"pydantic": False, "plan": False}
