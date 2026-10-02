"""Validate the installed Python distribution and released marimo dependency."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_AGENT_PLUGIN_FILES = {
    "plugin.json",
    "skills/notebook-to-static-app/SKILL.md",
    "skills/notebook-to-static-app/agents/openai.yaml",
    "skills/notebook-to-static-app/assets/app/.gitignore",
    "skills/notebook-to-static-app/assets/app/index.html",
    "skills/notebook-to-static-app/assets/app/pnpm-workspace.yaml",
    "skills/notebook-to-static-app/assets/app/src/main.ts",
    "skills/notebook-to-static-app/assets/app/src/style.css",
    "skills/notebook-to-static-app/assets/app/src/vite-env.d.ts",
    "skills/notebook-to-static-app/assets/app/tsconfig.json",
    "skills/notebook-to-static-app/assets/app/vite.config.ts",
    "skills/notebook-to-static-app/references/contracts.md",
    "skills/notebook-to-static-app/references/workflow.md",
    "skills/notebook-to-static-app/scripts/scaffold_app.py",
}
_ROOT_API = {
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
}
_FOCUSED_NAMES = {
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


def main(expected_version: str | None = None) -> None:
    source_requires_python, project_requirements = _source_project()
    package_version = metadata.version("marimo-export")
    if expected_version is not None and package_version != expected_version:
        raise RuntimeError(
            f"Installed marimo-export {package_version} does not match expected {expected_version}"
        )
    requirements = metadata.requires("marimo-export") or []
    requires_python = metadata.metadata("marimo-export")["Requires-Python"]
    if SpecifierSet(requires_python) != SpecifierSet(source_requires_python):
        raise RuntimeError(
            "marimo-export wheel must preserve the Python range from packages/python/pyproject.toml"
        )
    for name in ("marimo", "agent-plugins"):
        requirement = project_requirements[name]
        if requirement not in requirements:
            raise RuntimeError(f"marimo-export wheel must require {requirement}")

    import marimo_export

    if set(marimo_export.__all__) != _ROOT_API:
        raise RuntimeError("installed marimo-export distribution has an unexpected root API")
    if (
        "agent_plugins" in sys.modules
        or "sqlite3" in sys.modules
        or any(name.startswith("marimo._") for name in sys.modules)
    ):
        raise RuntimeError("importing marimo-export root must not load runtime implementations")
    for name in _ROOT_API:
        getattr(marimo_export, name)
    for name in _FOCUSED_NAMES:
        try:
            getattr(marimo_export, name)
        except AttributeError:
            pass
        else:
            raise RuntimeError(f"installed marimo-export root must not expose {name}")

    if marimo_export.ExportPlan.__module__ != "marimo_export.planning":
        raise RuntimeError("installed ExportPlan must use its public planning module")
    if marimo_export.ExportRepository.__module__ != "marimo_export.repository":
        raise RuntimeError("installed ExportRepository must use its public repository module")
    if marimo_export.PreparedExport.__module__ != "marimo_export.prepared":
        raise RuntimeError("installed PreparedExport must use its public prepared module")
    if marimo_export.StateSpace.__module__ != "marimo_export.spec":
        raise RuntimeError("installed StateSpace must use its public spec module")

    from marimo_export.delivery import StagedDelivery, stage
    from marimo_export.observations import ObservedInputs
    from marimo_export.outputs import BlobAsset
    from marimo_export.sessions import Client, Session, connect
    from marimo_export.wire import canonical_json_bytes, state_fingerprint

    if BlobAsset.__module__ != "marimo_export.outputs":
        raise RuntimeError("installed BlobAsset must use its focused output module")
    if Client.__module__ != "marimo_export.client" or Session.__module__ != "marimo_export.client":
        raise RuntimeError("installed session records must use the session implementation")
    if not callable(connect):
        raise RuntimeError("installed sessions module must expose connect")
    if ObservedInputs.__module__ != "marimo_export.observations":
        raise RuntimeError("installed observations module must expose ObservedInputs")
    if StagedDelivery.__module__ != "marimo_export.delivery" or not callable(stage):
        raise RuntimeError("installed delivery module must expose staging")
    if len(state_fingerprint({"state": "ready"})) != 64:
        raise RuntimeError("installed wire module must expose state_fingerprint")
    if canonical_json_bytes({"state": "ready"}) != b'{"state":"ready"}':
        raise RuntimeError("installed wire module must expose canonical JSON")

    lifespans = {
        (entry.name, entry.value)
        for entry in metadata.entry_points(group="marimo.kernel.lifespan")
        if entry.dist is not None and entry.dist.name == "marimo-export"
    }
    if lifespans != {("marimo-export", "marimo_export._marimo.entrypoints:kernel_lifespan")}:
        raise RuntimeError("marimo-export wheel must register its managed kernel lifespan")

    import marimo_export.agent as export_agent

    capabilities = {
        (entry.name, entry.value)
        for entry in metadata.entry_points(group="marimo.agent.capability")
        if entry.dist is not None and entry.dist.name == "marimo-export"
    }
    if capabilities != {("marimo-export", "marimo_export.agent")}:
        raise RuntimeError("marimo-export wheel must register its code-mode capability")

    plugin = export_agent.agent_plugin()
    if plugin.manifest.name != "marimo-export" or plugin.manifest.issues:
        raise RuntimeError("marimo-export wheel contains an invalid Agent Plugin")
    installed_plugin_files = {path.relative_to(plugin.path).as_posix() for path in plugin.files}
    if installed_plugin_files != _AGENT_PLUGIN_FILES:
        raise RuntimeError("marimo-export wheel contains an unexpected Agent Plugin file inventory")
    skill = export_agent.agent_skill()
    _verify_installed_scaffold(skill.path, package_version, source_requires_python)
    _verify_installed_cli(package_version)


def _verify_installed_cli(package_version: str) -> None:
    command = Path(sys.executable).with_name(
        "marimo-export.exe" if sys.platform == "win32" else "marimo-export"
    )
    subprocess.run([str(command), "--help"], stdout=subprocess.DEVNULL, check=True, timeout=30)
    version = subprocess.run(
        [str(command), "--version"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
        timeout=30,
    )
    if version.stdout.strip() != f"marimo-export {package_version}":
        raise RuntimeError(f"Unexpected marimo-export version output: {version.stdout.strip()}")


def _source_project() -> tuple[str, dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "packages/python/pyproject.toml"
    with path.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    if not isinstance(project, dict):
        raise RuntimeError(f"invalid Python project metadata in {path}")
    dependencies = project.get("dependencies")
    requires_python = project.get("requires-python")
    if not isinstance(dependencies, list) or not all(
        isinstance(requirement, str) for requirement in dependencies
    ):
        raise RuntimeError(f"invalid Python dependencies in {path}")
    if not isinstance(requires_python, str):
        raise RuntimeError(f"invalid Python range in {path}")
    return (
        requires_python,
        {Requirement(requirement).name: requirement for requirement in dependencies},
    )


def _verify_installed_scaffold(
    skill: Path, package_version: str, source_requires_python: str
) -> None:
    script = skill / "scripts/scaffold_app.py"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        notebook = root / "report.py"
        notebook.write_text("summary = {'status': 'ready'}\n", encoding="utf-8")
        output = root / "app"
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--notebook",
                str(notebook),
                "--output",
                str(output),
                "--loader",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"installed Agent Skill scaffold failed: {completed.stderr.strip()}")
        browser = json.loads((output / "package.json").read_text(encoding="utf-8"))
        if browser["dependencies"]["@marimo-team/marimo-export"] != package_version:
            raise RuntimeError("installed scaffold must pin the matching browser package")
        with (output / "pyproject.toml").open("rb") as stream:
            python = tomllib.load(stream)
        if python["project"]["dependencies"] != [f"marimo-export[all]=={package_version}"]:
            raise RuntimeError("installed scaffold must pin the matching Python package")
        if python["project"]["requires-python"] != source_requires_python:
            raise RuntimeError("installed scaffold must preserve the supported Python range")
        if (output / "vendor").exists():
            raise RuntimeError("installed scaffold must use registry package versions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version")
    main(parser.parse_args().expected_version)
