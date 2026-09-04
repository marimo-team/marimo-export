"""Use marimo-export from notebook agents."""

from __future__ import annotations

import sys
from textwrap import indent
from types import ModuleType

import agent_plugins

_DISTRIBUTION_NAME = "marimo-export"
_SKILL_NAME = "notebook-to-static-app"


def agent_plugin() -> agent_plugins.Plugin:
    """Return the Agent Plugin installed with this marimo-export version."""
    return agent_plugins.locate(_DISTRIBUTION_NAME)


def _agent_skill(plugin: agent_plugins.Plugin) -> agent_plugins.Skill:
    for skill in plugin.skills:
        if skill.path.name == _SKILL_NAME:
            return skill
    raise agent_plugins.AgentPluginError(
        "The marimo-export Agent Plugin has no notebook-to-static-app skill. "
        "Reinstall marimo-export."
    )


def agent_skill() -> agent_plugins.Skill:
    """Return the packaged notebook-to-static-app Agent Skill."""
    return _agent_skill(agent_plugin())


def _sdk_help(summary: str) -> str:
    return f"""{summary}

Start with the public Python API:

    import marimo_export as mox
    from marimo_export.inspection import inspect_notebook

    description = inspect_notebook("report.py")
    spec = mox.ExportSpec.from_file("report.export.yaml")
    plan = mox.plan("report.py", spec=spec)
    result = mox.build(
        "report.py",
        spec=spec,
        output="dist/report",
        replace=True,
    )
    verified = mox.verify_export(result.path)

The command-line interface calls these same public operations. Use
`marimo_export.sessions` for a live server and `mox.capture()` for a named live
session.

Browse the published documentation map at:

    https://marimo-team.github.io/marimo-export/llms.txt
"""


def _module_help(summary: str) -> str:
    sdk = _sdk_help(summary)
    try:
        plugin = agent_plugin()
        skill = _agent_skill(plugin)
        tree = indent(plugin.tree(max_depth=3, max_files=50), "    ")
    except agent_plugins.AgentPluginError as error:
        return f"""{sdk}

The installed Agent Plugin could not be resolved: {error}
Reinstall marimo-export to restore its version-matched skill resources.
"""

    return f"""{sdk}

The installed Agent Plugin carries the complete static-application workflow
and resources that match this package version:

{tree}

Read the notebook-to-static-app skill instructions at:

    {skill / "SKILL.md"}

Traverse the same resources programmatically:

    import marimo_export.agent as export_agent

    resources = export_agent.agent_plugin()
    skill = export_agent.agent_skill()
    print(resources)
    print(skill.body)
"""


__all__ = ["agent_plugin", "agent_skill"]


class _AgentModule(ModuleType):
    @property
    def __doc__(self) -> str | None:  # pyrefly: ignore [bad-override]
        summary = self.__dict__.get("__doc__")
        return _module_help(summary) if isinstance(summary, str) else None

    @__doc__.setter
    def __doc__(self, value: str | None) -> None:
        self.__dict__["__doc__"] = value


sys.modules[__name__].__class__ = _AgentModule
