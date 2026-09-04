from __future__ import annotations

import pydoc
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import agent_plugins
import marimo._code_mode as code_mode
import marimo_export.agent as export_agent
import pytest

_ROOT = Path(__file__).parents[3]
_PLUGIN_FILES = {
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


def test_agent_module_exports_the_resource_surface() -> None:
    assert export_agent.__all__ == ["agent_plugin", "agent_skill"]


def test_marimo_code_mode_discovers_the_marimo_export_capability() -> None:
    assert code_mode.capabilities()["marimo-export"] == "marimo_export.agent"


def test_agent_capability_entry_point_loads_the_instruction_module() -> None:
    capabilities = [
        entry_point
        for entry_point in distribution("marimo-export").entry_points
        if entry_point.group == "marimo.agent.capability"
    ]

    assert [(entry.name, entry.value) for entry in capabilities] == [
        ("marimo-export", "marimo_export.agent")
    ]
    assert capabilities[0].load() is export_agent


def test_agent_capability_loads_and_renders_help_in_a_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import pydoc
import sys
from importlib.metadata import distribution

assert "marimo_export.agent" not in sys.modules
entry = next(
    entry
    for entry in distribution("marimo-export").entry_points
    if entry.group == "marimo.agent.capability"
)
module = entry.load()
assert module.__name__ == "marimo_export.agent"
assert "import marimo_export as mox" in pydoc.render_doc(module)
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_agent_plugin_exposes_the_packaged_static_app_skill() -> None:
    plugin = export_agent.agent_plugin()
    skill = export_agent.agent_skill()

    assert plugin.manifest.name == "marimo-export"
    assert skill in plugin.skills
    assert skill.path.name == "notebook-to-static-app"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "agents" / "openai.yaml").is_file()
    assert (skill / "references" / "contracts.md").is_file()
    assert (skill / "references" / "workflow.md").is_file()
    assert (skill / "scripts" / "scaffold_app.py").is_file()
    assert (skill / "assets" / "app" / "src" / "main.ts").is_file()
    assert skill.frontmatter.splitlines()[0] == "name: notebook-to-static-app"
    assert {path.relative_to(plugin.path).as_posix() for path in plugin.files} == _PLUGIN_FILES


def test_agent_plugin_build_plan_contains_only_authored_resources() -> None:
    plan = agent_plugins.build_plan(_ROOT / "packages/python")

    assert {mapping.target.as_posix() for mapping in plan.files} == _PLUGIN_FILES


def test_agent_module_help_points_to_public_operations_and_installed_resources() -> None:
    plugin = export_agent.agent_plugin()
    skill = export_agent.agent_skill()
    rendered = pydoc.render_doc(export_agent)

    assert str(plugin.path) in rendered
    assert str(skill / "SKILL.md") in rendered
    assert "plan = mox.plan(" in rendered
    assert "result = mox.build(" in rendered
    assert "verified = mox.verify_export(result.path)" in rendered
    assert "resources = export_agent.agent_plugin()" in rendered
    assert "https://marimo-team.github.io/marimo-export/llms.txt" in rendered


def test_agent_module_help_preserves_the_sdk_path_when_plugin_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> agent_plugins.Plugin:
        raise agent_plugins.AgentPluginError("marker unavailable")

    monkeypatch.setattr(export_agent, "agent_plugin", fail)
    rendered = pydoc.render_doc(export_agent)

    assert "import marimo_export as mox" in rendered
    assert "marker unavailable" in rendered
    assert "Reinstall marimo-export" in rendered
