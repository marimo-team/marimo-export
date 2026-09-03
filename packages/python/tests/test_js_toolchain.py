from __future__ import annotations

import json
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
    for name in ("ci.yml", "pages.yml", "publish.yml"):
        workflow = cast(
            dict[str, Any],
            yaml.load(
                ROOT.joinpath(".github", "workflows", name).read_text(encoding="utf-8"),
                Loader=yaml.BaseLoader,
            ),
        )
        jobs = cast(dict[str, Any], workflow["jobs"])
        setup_steps = [
            step
            for job in jobs.values()
            for step in cast(list[dict[str, Any]], job["steps"])
            if str(step.get("uses", "")).startswith("voidzero-dev/setup-vp@")
        ]
        assert setup_steps
        for step in setup_steps:
            inputs = cast(dict[str, Any], step.get("with", {}))
            assert set(inputs) == {"version", "sfw", "cache", "run-install"}
