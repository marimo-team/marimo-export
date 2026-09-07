from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_pnpm_owns_the_workspace_node_runtime() -> None:
    manifest = cast(
        dict[str, Any],
        json.loads(ROOT.joinpath("package.json").read_text(encoding="utf-8")),
    )
    assert manifest["devEngines"]["runtime"] == {
        "name": "node",
        "version": "24.14.1",
        "onFail": "download",
    }

    lock = cast(
        dict[str, Any],
        yaml.safe_load(ROOT.joinpath("pnpm-lock.yaml").read_text(encoding="utf-8")),
    )
    assert lock["importers"]["."]["devDependencies"]["node"] == {
        "specifier": "runtime:24.14.1",
        "version": "runtime:24.14.1",
    }


def test_vite_setup_discovers_the_pnpm_runtime() -> None:
    action = cast(
        dict[str, Any],
        yaml.safe_load(ROOT.joinpath(".github/actions/setup-js/action.yml").read_text()),
    )
    setup = next(
        step
        for step in action["runs"]["steps"]
        if str(step.get("uses", "")).startswith("voidzero-dev/setup-vp@")
    )
    assert re.fullmatch(r"voidzero-dev/setup-vp@[0-9a-f]{40}", setup["uses"])
    inputs = setup["with"]
    runtime = json.loads(ROOT.joinpath(inputs["node-version-file"]).read_text())
    workspace = yaml.safe_load(ROOT.joinpath(inputs["version-file"]).read_text())
    lock = yaml.safe_load(ROOT.joinpath(inputs["cache-dependency-path"]).read_text())
    node_version = runtime["devEngines"]["runtime"]["version"]
    vite_version = workspace["catalog"]["vite-plus"]
    assert inputs.get("node-version", node_version) == node_version
    assert (
        inputs.get("version", vite_version) == lock["catalogs"]["default"]["vite-plus"]["version"]
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", vite_version)
    assert yaml.safe_load(inputs["run-install"]) == [{"args": ["--frozen-lockfile"]}]


def test_javascript_build_jobs_use_the_shared_workspace_setup() -> None:
    for name, consumers in {
        "ci.yml": ("quality", "test-frontend", "package"),
        "pages.yml": ("documentation", "build"),
        "publish.yml": ("build",),
    }.items():
        workflow = cast(
            dict[str, Any],
            yaml.load(
                ROOT.joinpath(".github", "workflows", name).read_text(encoding="utf-8"),
                Loader=yaml.BaseLoader,
            ),
        )
        jobs = cast(dict[str, Any], workflow["jobs"])
        for job_name in consumers:
            assert any(
                step.get("uses") == "./.github/actions/setup-js" for step in jobs[job_name]["steps"]
            ), (name, job_name)
