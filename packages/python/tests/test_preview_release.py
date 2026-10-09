from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from base64 import b64encode
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "a" * 40
DOWNLOADS = "https://github.com/marimo-team/marimo-export/releases/download/preview"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Export",
            "GIT_AUTHOR_EMAIL": "export@example.test",
            "GIT_COMMITTER_NAME": "Export",
            "GIT_COMMITTER_EMAIL": "export@example.test",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(root: Path) -> str:
    _git(root, "commit", "--allow-empty", "-m", "change")
    return _git(root, "rev-parse", "HEAD")


def _version(root: Path, commit: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts/preview-version.sh"), commit],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_preview_versions_sort_between_releases_and_survive_tagging(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _commit(tmp_path)
    _git(tmp_path, "tag", "-a", "v0.1.4", "-m", "release")
    first = _commit(tmp_path)
    candidate = _commit(tmp_path)
    before_tag = _version(tmp_path, candidate).stdout.strip()
    _git(tmp_path, "tag", "-a", "v0.1.5", "-m", "release", candidate)
    next_commit = _commit(tmp_path)

    versions = [
        _version(tmp_path, first).stdout.strip(),
        before_tag,
        _version(tmp_path, candidate).stdout.strip(),
        _version(tmp_path, next_commit).stdout.strip(),
    ]

    assert versions == ["0.1.5.dev1", "0.1.5.dev2", "0.1.5.dev2", "0.1.6.dev1"]
    assert Version(versions[0]) < Version(versions[1]) < Version("0.1.5") < Version(versions[3])


def test_preview_version_ignores_suffixed_and_unreachable_tags(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    released = _commit(tmp_path)
    _git(tmp_path, "tag", "v0.1.4", released)
    _git(tmp_path, "checkout", "--detach", released)
    _git(tmp_path, "commit", "--allow-empty", "-m", "unreachable release")
    _git(tmp_path, "tag", "v9.0.0")
    _git(tmp_path, "checkout", "--detach", released)
    _commit(tmp_path)
    _git(tmp_path, "tag", "v0.1.5-rc1")
    _git(tmp_path, "tag", "v0.1.5.dev10")
    candidate = _commit(tmp_path)

    completed = _version(tmp_path, candidate)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "0.1.5.dev2"


def test_preview_version_requires_a_previous_final_release(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _commit(tmp_path)
    candidate = _commit(tmp_path)

    completed = _version(tmp_path, candidate)

    assert completed.returncode == 1
    assert "No vX.Y.Z release tag precedes" in completed.stderr


def _artifacts(version: str) -> dict[str, bytes]:
    npm_version = version.replace(".dev", "-dev.")
    artifacts = {
        f"marimo_export-{version}-py3-none-any.whl": f"wheel {version}".encode(),
        f"marimo_export-{version}.tar.gz": f"source {version}".encode(),
        f"marimo-team-marimo-export-{npm_version}.tgz": f"browser {npm_version}".encode(),
    }
    artifacts[f"marimo-export-{version}-SHA256SUMS"] = "".join(
        f"{sha256(contents).hexdigest()}  {name}\n" for name, contents in sorted(artifacts.items())
    ).encode()
    return artifacts


class _GitHub:
    def __init__(self, root: Path, *, versions: tuple[str, ...] = ()) -> None:
        self.root = root
        self.state_path = root / "github.json"
        self.commands = root / "commands"
        self.commands.mkdir()
        assets = {
            name: b64encode(contents).decode()
            for version in versions
            for name, contents in _artifacts(version).items()
        }
        self.write(
            {
                "exists": bool(assets),
                "assets": assets,
                "starters": [],
                "notes": "",
                "comments": [],
                "calls": [],
                "fail": "",
                "pull": "162",
                "runs": {
                    "ci.yml": "completed\nsuccess\nhttps://example.test/ci\n",
                    "pages.yml": "completed\nsuccess\nhttps://example.test/pages\n",
                },
            }
        )
        command = self.commands / "gh"
        command.write_text(
            f"#!{sys.executable}\n"
            + """import json, os, sys
from base64 import b64decode, b64encode
from pathlib import Path
state_path = Path(os.environ["FAKE_GITHUB"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
state_path.write_text(json.dumps(state))
operation = " ".join(args[:2])
if state["fail"] == operation:
    state["fail"] = ""
    state_path.write_text(json.dumps(state))
    sys.exit(1)
if operation == "run list":
    assert args[args.index("--branch") + 1] == "main"
    assert args[args.index("--event") + 1] == "push"
    assert args[args.index("--limit") + 1] == "1"
    print(state["runs"][args[args.index("--workflow") + 1]], end="")
elif operation == "release view":
    if not state["exists"]:
        sys.exit(1)
    if "--json" in args:
        query = args[args.index("--jq") + 1]
        if '"starter"' in query:
            names = state["starters"]
        elif '"uploaded"' in query:
            names = [name for name, data in state["assets"].items() if b64decode(data)]
        else:
            names = [*state["assets"], *state["starters"]]
        print("\\n".join(names))
elif operation == "release create":
    assert "--prerelease" in args and "--latest=false" in args
    state["exists"] = True
    state["target"] = args[args.index("--target") + 1]
elif operation == "release upload":
    path = Path(args[3])
    assert path.name not in state["assets"]
    assert path.name not in state["starters"]
    state["assets"][path.name] = b64encode(path.read_bytes()).decode()
elif operation == "release download":
    name = args[args.index("--pattern") + 1]
    (Path(args[args.index("--dir") + 1]) / name).write_bytes(b64decode(state["assets"][name]))
elif operation == "release edit":
    state["notes"] = Path(args[args.index("--notes-file") + 1]).read_text()
elif operation == "release delete-asset":
    if args[3] in state["starters"]:
        state["starters"].remove(args[3])
    else:
        del state["assets"][args[3]]
elif args[0] == "api":
    if args[1].endswith("/pulls"):
        print(state["pull"])
    else:
        print("\\n".join(state["comments"]))
elif operation == "pr comment":
    state["comments"].append(Path(args[args.index("--body-file") + 1]).read_text())
else:
    raise AssertionError(args)
state_path.write_text(json.dumps(state))
""",
            encoding="utf-8",
        )
        command.chmod(0o755)

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def write(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state))

    def env(self) -> dict[str, str]:
        return {
            **{
                key: value
                for key, value in os.environ.items()
                if key not in {"SSH_CLIENT", "SSH_CONNECTION"}
            },
            "FAKE_GITHUB": str(self.state_path),
            "GH_REPO": "marimo-team/marimo-export",
            "GH_TOKEN": "test-token",
            "GITHUB_SERVER_URL": "https://github.com",
            "PATH": f"{self.commands}{os.pathsep}{os.environ['PATH']}",
        }

    def publish(self, version: str, *, corrupt: bool = False) -> subprocess.CompletedProcess[str]:
        dist = self.root / "dist"
        for name, contents in _artifacts(version).items():
            directory = "python" if name.startswith("marimo_export-") else "npm"
            path = dist / directory / name if not name.endswith("SHA256SUMS") else dist / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        # Each invocation receives exactly one build, like download-artifact.
        for path in (dist / "python").glob("*.whl"):
            if path.name != f"marimo_export-{version}-py3-none-any.whl":
                path.unlink()
        if corrupt:
            (dist / "python" / f"marimo_export-{version}-py3-none-any.whl").write_bytes(b"damaged")
        return subprocess.run(
            ["bash", str(ROOT / "scripts/publish-preview.sh"), str(dist), COMMIT],
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )


@pytest.mark.parametrize("workflow", ["ci.yml", "pages.yml"])
@pytest.mark.parametrize(
    "run",
    [
        "",
        "in_progress\n\nhttps://example.test/run\n",
        "completed\nfailure\nhttps://example.test/run\n",
    ],
)
def test_preview_waits_for_both_exact_commit_workflows(
    tmp_path: Path, workflow: str, run: str
) -> None:
    github = _GitHub(tmp_path)
    state = github.state()
    state["runs"][workflow] = run
    github.write(state)

    completed = subprocess.run(
        ["bash", str(ROOT / "scripts/require-preview-checks.sh"), COMMIT],
        env=github.env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3
    assert workflow in completed.stderr
    for call in github.state()["calls"]:
        assert call[call.index("--commit") + 1] == COMMIT


@pytest.mark.parametrize("published", ["none", "partial", "starter", "complete"])
def test_preview_readiness_uses_the_validated_commit_and_completion_marker(
    tmp_path: Path, published: str
) -> None:
    github = _GitHub(tmp_path, versions=("0.1.5.dev1",) if published != "none" else ())
    if published in {"partial", "starter"}:
        state = github.state()
        del state["assets"]["marimo-export-0.1.5.dev1-SHA256SUMS"]
        if published == "starter":
            state["starters"].append("marimo-export-0.1.5.dev1-SHA256SUMS")
        github.write(state)
    _git(tmp_path, "init", "--quiet")
    _commit(tmp_path)
    _git(tmp_path, "tag", "v0.1.4")
    candidate = _commit(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("preview-version.sh", "require-preview-checks.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    workflow = yaml.load(
        (ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader
    )
    command = workflow["jobs"]["resolve"]["steps"][-1]["run"]
    output = tmp_path / "output"

    completed = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", command],
        cwd=tmp_path,
        env={
            **github.env(),
            "PUBLICATION_EVENT": "workflow_run",
            "PREVIEW_COMMIT": candidate,
            "GITHUB_SHA": "b" * 40,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"commit={candidate}\n" in output.read_text()
    assert "version=0.1.5.dev1\n" in output.read_text()
    assert f"publish={'false' if published == 'complete' else 'true'}\n" in output.read_text()


def test_readiness_api_failure_fails_publication_resolution(tmp_path: Path) -> None:
    github = _GitHub(tmp_path)
    state = github.state()
    state["fail"] = "run list"
    github.write(state)
    _git(tmp_path, "init", "--quiet")
    _commit(tmp_path)
    _git(tmp_path, "tag", "v0.1.4")
    candidate = _commit(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("preview-version.sh", "require-preview-checks.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    workflow = yaml.load(
        (ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader
    )
    output = tmp_path / "output"

    completed = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", workflow["jobs"]["resolve"]["steps"][-1]["run"]],
        cwd=tmp_path,
        env={
            **github.env(),
            "PUBLICATION_EVENT": "workflow_run",
            "PREVIEW_COMMIT": candidate,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "publish=false" not in output.read_text()
    assert all(call[:2] != ["release", "view"] for call in github.state()["calls"])


def test_preview_provenance_records_checkout_and_signing_workflow_separately(
    tmp_path: Path,
) -> None:
    output = tmp_path / "predicate.json"
    workflow_commit = "b" * 40
    subprocess.run(
        ["node", str(ROOT / "scripts/preview-provenance.mjs"), COMMIT, str(output)],
        env={
            **os.environ,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "marimo-team/marimo-export",
            "GITHUB_WORKFLOW_REF": (
                "marimo-team/marimo-export/.github/workflows/publish.yml@refs/heads/main"
            ),
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": workflow_commit,
            "GITHUB_EVENT_NAME": "workflow_run",
            "GITHUB_REPOSITORY_ID": "123",
            "GITHUB_REPOSITORY_OWNER_ID": "456",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_RUN_ID": "789",
            "GITHUB_RUN_ATTEMPT": "2",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    predicate = json.loads(output.read_text())

    definition = predicate["buildDefinition"]
    assert definition["buildType"] == (
        "https://github.com/marimo-team/marimo-export/blob/main/"
        "development_docs/releasing.md#preview-provenance-v1"
    )
    assert definition["externalParameters"]["checkoutCommit"] == COMMIT
    assert definition["resolvedDependencies"] == [
        {
            "uri": "git+https://github.com/marimo-team/marimo-export@refs/heads/main",
            "digest": {"gitCommit": workflow_commit},
        },
        {
            "uri": f"git+https://github.com/marimo-team/marimo-export@{COMMIT}",
            "digest": {"gitCommit": COMMIT},
        },
    ]
    assert predicate["runDetails"]["metadata"]["invocationId"].endswith("/runs/789/attempts/2")
    workflow = yaml.load(
        (ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader
    )
    steps = workflow["jobs"]["attest"]["steps"]
    provenance = next(step for step in steps if step["name"] == "Record preview source provenance")
    assert provenance["env"]["PREVIEW_COMMIT"] == "${{ needs.build.outputs.commit }}"
    assert "predicate-path" in steps[-1]["with"]


def test_preview_publishes_matching_packages_and_install_commands(tmp_path: Path) -> None:
    github = _GitHub(tmp_path)

    completed = github.publish("0.1.5.dev9")

    assert completed.returncode == 0, completed.stderr
    state = github.state()
    assert set(state["assets"]) == set(_artifacts("0.1.5.dev9"))
    assert state["target"] == COMMIT
    assert f"{DOWNLOADS}/marimo_export-0.1.5.dev9-py3-none-any.whl" in state["notes"]
    assert f"{DOWNLOADS}/marimo-team-marimo-export-0.1.5-dev.9.tgz" in state["notes"]
    assert "uv tool install --force" in state["notes"]
    assert "gh attestation verify" in state["notes"]
    assert len(state["comments"]) == 1
    assert "uv pip install" in state["comments"][0]
    assert "pnpm add" in state["comments"][0]


def test_older_preview_completion_keeps_the_newest_install_links(tmp_path: Path) -> None:
    github = _GitHub(tmp_path)
    assert github.publish("0.1.5.dev15").returncode == 0
    notes = github.state()["notes"]

    completed = github.publish("0.1.5.dev9")

    assert completed.returncode == 0, completed.stderr
    assert github.state()["notes"] == notes
    assert len(github.state()["assets"]) == 8


def test_preview_retention_removes_whole_builds(tmp_path: Path) -> None:
    github = _GitHub(tmp_path, versions=tuple(f"0.1.5.dev{number}" for number in range(1, 31)))

    completed = github.publish("0.1.6.dev1")

    assert completed.returncode == 0, completed.stderr
    assets = github.state()["assets"]
    assert len(assets) == 30 * 4
    assert set(_artifacts("0.1.5.dev1")).isdisjoint(assets)
    assert set(_artifacts("0.1.6.dev1")).issubset(assets)


def test_preview_retention_removes_old_interrupted_builds(tmp_path: Path) -> None:
    github = _GitHub(tmp_path, versions=tuple(f"0.1.5.dev{number}" for number in range(2, 32)))
    state = github.state()
    orphan = "marimo-team-marimo-export-0.1.5-dev.1.tgz"
    state["assets"][orphan] = b64encode(b"orphan").decode()
    state["starters"].append("marimo_export-0.1.5.dev1-py3-none-any.whl")
    github.write(state)

    completed = github.publish("0.1.5.dev32")

    assert completed.returncode == 0, completed.stderr
    assert orphan not in github.state()["assets"]
    assert github.state()["starters"] == []
    assert len(github.state()["assets"]) == 30 * 4


def test_preview_announcement_failure_preserves_older_builds(tmp_path: Path) -> None:
    github = _GitHub(tmp_path, versions=tuple(f"0.1.5.dev{number}" for number in range(1, 31)))
    state = github.state()
    state["fail"] = "pr comment"
    github.write(state)

    assert github.publish("0.1.5.dev31").returncode == 1
    assert set(_artifacts("0.1.5.dev1")).issubset(github.state()["assets"])

    completed = github.publish("0.1.5.dev31")

    assert completed.returncode == 0, completed.stderr
    assert len(github.state()["assets"]) == 30 * 4
    assert set(_artifacts("0.1.5.dev1")).isdisjoint(github.state()["assets"])


def test_older_build_cannot_replace_available_newer_links_after_announcement_failure(
    tmp_path: Path,
) -> None:
    github = _GitHub(tmp_path)
    state = github.state()
    state["fail"] = "pr comment"
    github.write(state)
    assert github.publish("0.1.5.dev15").returncode == 1
    notes = github.state()["notes"]

    completed = github.publish("0.1.5.dev9")

    assert completed.returncode == 0, completed.stderr
    assert github.state()["notes"] == notes


@pytest.mark.parametrize(
    "starter",
    ["marimo_export-0.1.5.dev1-py3-none-any.whl", "marimo-export-0.1.5.dev1-SHA256SUMS"],
)
def test_preview_retry_replaces_starter_assets(tmp_path: Path, starter: str) -> None:
    github = _GitHub(tmp_path, versions=("0.1.5.dev1",))
    state = github.state()
    del state["assets"][starter]
    state["starters"].append(starter)
    github.write(state)

    completed = github.publish("0.1.5.dev1")

    assert completed.returncode == 0, completed.stderr
    assert github.state()["starters"] == []
    assert set(github.state()["assets"]) == set(_artifacts("0.1.5.dev1"))


def test_late_preview_outside_retention_does_not_announce_removed_urls(tmp_path: Path) -> None:
    github = _GitHub(tmp_path, versions=tuple(f"0.1.5.dev{number}" for number in range(2, 32)))

    completed = github.publish("0.1.5.dev1")

    assert completed.returncode == 0, completed.stderr
    assert "its assets were removed" in completed.stdout
    assert len(github.state()["assets"]) == 30 * 4
    assert set(_artifacts("0.1.5.dev1")).isdisjoint(github.state()["assets"])
    assert github.state()["comments"] == []


def test_preview_rejects_transfer_damage_before_github_writes(tmp_path: Path) -> None:
    github = _GitHub(tmp_path)

    completed = github.publish("0.1.5.dev1", corrupt=True)

    assert completed.returncode == 1
    assert "FAILED" in completed.stdout
    assert github.state()["calls"] == []


@pytest.mark.parametrize("failure", ["release edit", "pr comment"])
def test_preview_retry_finishes_publication_without_overwriting_assets(
    tmp_path: Path, failure: str
) -> None:
    github = _GitHub(tmp_path)
    state = github.state()
    state["fail"] = failure
    github.write(state)
    assert github.publish("0.1.5.dev1").returncode == 1
    assert "marimo-export-0.1.5.dev1-SHA256SUMS" not in github.state()["assets"]

    completed = github.publish("0.1.5.dev1")
    repeated = github.publish("0.1.5.dev1")

    assert completed.returncode == repeated.returncode == 0, completed.stderr + repeated.stderr
    assert set(github.state()["assets"]) == set(_artifacts("0.1.5.dev1"))
    assert len(github.state()["comments"]) == 1
    uploads = [call for call in github.state()["calls"] if call[:2] == ["release", "upload"]]
    assert len(uploads) == 4


def test_preview_retry_rejects_different_published_bytes(tmp_path: Path) -> None:
    github = _GitHub(tmp_path, versions=("0.1.5.dev1",))
    state = github.state()
    state["assets"]["marimo_export-0.1.5.dev1-py3-none-any.whl"] = b64encode(b"different").decode()
    github.write(state)

    completed = github.publish("0.1.5.dev1")

    assert completed.returncode == 1
    assert "Published preview bytes differ" in completed.stderr
    assert github.state()["assets"] == state["assets"]


def test_preview_rejects_final_release_artifacts_before_github_writes(tmp_path: Path) -> None:
    github = _GitHub(tmp_path)

    completed = github.publish("0.1.5")

    assert completed.returncode == 1
    assert "Expected one marimo-export preview wheel" in completed.stderr
    assert github.state()["calls"] == []
