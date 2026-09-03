from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import marimo_export
import marimo_export.delivery as delivery_module
import marimo_export.inspection as inspection_module
import marimo_export.integration as integration_module
import pytest
from marimo_export.inspection import CellDescription, DefinitionDescription, SessionDescription

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_MARIMO_NAME = r"(?<![\w-])marimo(?!-export)"
_MARIMO_VERSION = re.compile(
    rf"(?i){_MARIMO_NAME}(?:\[[^\]\r\n]+\])?"
    r"(?:\s*[<>=!~]{1,2}\s*v?|\s+v?)(?P<version>\d+\.\d+(?:\.\d+)?)"
)
_MARIMO_COMMIT = re.compile(rf"(?i){_MARIMO_NAME}[^\r\n]{{0,120}}?\b(?P<commit>[0-9a-f]{{40}})\b")


def test_root_package_exposes_the_public_api() -> None:
    expected = (
        "ExportPlan",
        "ExportRepository",
        "ExportResult",
        "ExportSpec",
        "NotebookExport",
        "OutputSpec",
        "PreparedExport",
        "ProgressEvent",
        "StateSpace",
        "VerificationResult",
        "build",
        "capture",
        "open_export",
        "plan",
        "prepare",
        "verify_export",
    )
    assert marimo_export.__all__ == expected
    for name in expected:
        assert getattr(marimo_export, name) is not None


def test_inspection_records_have_one_public_import_location() -> None:
    assert CellDescription.__module__ == "marimo_export.inspection"
    assert DefinitionDescription.__module__ == "marimo_export.inspection"
    assert SessionDescription.__module__ == "marimo_export.inspection"


def test_root_records_have_one_public_import_location() -> None:
    assert marimo_export.ExportPlan.__module__ == "marimo_export.planning"
    assert marimo_export.ExportRepository.__module__ == "marimo_export.repository"
    assert marimo_export.ExportResult.__module__ == "marimo_export.result"
    assert marimo_export.ExportSpec.__module__ == "marimo_export.spec"
    assert marimo_export.NotebookExport.__module__ == "marimo_export.reader"
    assert marimo_export.OutputSpec.__module__ == "marimo_export.spec"
    assert marimo_export.PreparedExport.__module__ == "marimo_export.prepared"
    assert marimo_export.ProgressEvent.__module__ == "marimo_export.progress"
    assert marimo_export.StateSpace.__module__ == "marimo_export.spec"
    assert marimo_export.VerificationResult.__module__ == "marimo_export.reader"


def test_root_functions_delegate_to_the_sdk_services() -> None:
    assert marimo_export.build.__module__ == "marimo_export._build"
    assert marimo_export.capture.__module__ == "marimo_export._services.capture_export"
    assert marimo_export.open_export.__module__ == "marimo_export.reader"
    assert marimo_export.plan.__module__ == "marimo_export._services.plan_export"
    assert marimo_export.prepare.__module__ == "marimo_export._services.prepare_export"
    assert marimo_export.verify_export.__module__ == "marimo_export.verification"


def test_internal_integration_and_preflight_helpers_stay_private() -> None:
    for name in ("ControlRootCandidate", "canonical_cell_id", "select_control_roots"):
        assert name not in vars(integration_module)
        assert name not in vars(inspection_module)
    assert "preflight_export_destination" not in vars(delivery_module)


def test_removed_root_names_raise_attribute_error() -> None:
    removed = {
        "BlobAsset",
        "CaptureLimitError",
        "CaptureLimits",
        "Client",
        "OwnedNotebook",
        "Session",
        "DeliveryResult",
        "StagedDelivery",
        "canonical_json_bytes",
        "canonical_json_sha256",
        "document_sha256",
        "inspect_notebook",
        "open_notebook",
        "parse_canonical_json",
        "portable_json",
        "state_fingerprint",
        "stage",
    }

    for name in removed:
        assert name not in dir(marimo_export)
        with pytest.raises(AttributeError):
            getattr(marimo_export, name)


def test_domain_modules_do_not_import_runtime_adapters() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for name in (
        "descriptors.py",
        "errors.py",
        "index.py",
        "inspection.py",
        "limits.py",
        "result.py",
        "spec.py",
        "wire.py",
    ):
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


def test_public_modules_do_not_import_private_repository_implementation() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    violations: list[str] = []
    for name in (
        "delivery.py",
        "planning.py",
        "prepared.py",
        "progress.py",
        "result.py",
        "spec.py",
        "verification.py",
    ):
        source = package / name
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if any(
            module.startswith("marimo_export._repository")
            for node in ast.walk(tree)
            for module in _imported_modules(node)
        ):
            violations.append(name)
    assert violations == []


def test_services_use_only_the_private_repository_preparation_port() -> None:
    services = Path(__file__).parents[1] / "src" / "marimo_export" / "_services"
    violations: list[str] = []
    for source in services.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if module.startswith("marimo_export._repository") and not module.startswith(
                    "marimo_export._repository.preparation"
                ):
                    violations.append(source.name)
    assert violations == []


def test_cli_imports_public_sdk_and_focused_modules() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    sources = (package / "cli.py", *(package / "_cli").glob("*.py"))
    forbidden = (
        "marimo._",
        "marimo_export._marimo",
        "marimo_export._repository",
    )
    violations: list[str] = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            violations.extend(
                f"{source.name}:{getattr(node, 'lineno', 0)}: {module}"
                for module in _imported_modules(node)
                if module == "sqlite3" or module.startswith(forbidden)
            )
    assert violations == []


def test_private_cache_imports_stay_in_the_cache_adapter() -> None:
    package = Path(__file__).parents[1] / "src" / "marimo_export"
    forbidden = (
        "marimo._save.cache",
        "marimo._save.loaders",
        "marimo._save.signing",
        "marimo._save.stores",
    )
    violations: list[str] = []
    for source in package.rglob("*.py"):
        relative = source.relative_to(package)
        if relative.parts[:3] == ("_marimo", "compat", "cache"):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            violations.extend(
                f"{relative}:{getattr(node, 'lineno', 0)}: {module}"
                for module in _imported_modules(node)
                if module.startswith(forbidden)
            )
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


def test_compatibility_adapter_module_graph_imports_cleanly() -> None:
    script = """
import importlib

for name in (
    "child_run",
    "execution",
    "file_closure",
    "output_data",
    "projections",
    "receipts",
    "replay",
):
    importlib.import_module(f"marimo_export._marimo.compat.{name}")
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_documentation_defers_marimo_versions_to_package_metadata() -> None:
    root = Path(__file__).parents[3]
    violations = [
        path.relative_to(root).as_posix()
        for path in (root / "docs").rglob("*.md")
        if _MARIMO_VERSION.search(path.read_text(encoding="utf-8"))
    ]

    assert violations == []


def test_development_marimo_release_references_match_the_adapter() -> None:
    root = Path(__file__).parents[3]
    release = json.loads(
        (root / "packages/python/src/marimo_export/_marimo/compat/release.json").read_text(
            encoding="utf-8"
        )
    )
    with (root / "packages/python/pyproject.toml").open("rb") as stream:
        package = tomllib.load(stream)
    expected_version = release["version"]
    expected_commit = release["commit"]

    assert f"marimo=={expected_version}" in package["project"]["dependencies"]
    assert re.fullmatch(r"[0-9a-f]{40}", expected_commit)

    version_drift: list[str] = []
    commit_drift: list[str] = []
    for path in (root / "development_docs").rglob("*.md"):
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            version_drift.extend(
                f"{relative}:{line_number}: {match.group('version')}"
                for match in _MARIMO_VERSION.finditer(line)
                if match.group("version") != expected_version
            )
            commit_drift.extend(
                f"{relative}:{line_number}: {match.group('commit')}"
                for match in _MARIMO_COMMIT.finditer(line)
                if match.group("commit") != expected_commit
            )

    assert version_drift == []
    assert commit_drift == []


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
    'sqlite': 'sqlite3' in sys.modules,
    'marimo_private': sorted(name for name in sys.modules if name.startswith('marimo._')),
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
        "sqlite": False,
        "marimo_private": [],
        "kernel_lifecycle": False,
        "cache_loaders": False,
    }


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
