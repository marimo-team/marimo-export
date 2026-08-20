from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _release_repository(root: Path, *, advance_main: bool) -> tuple[str, str]:
    _git(root, "init")
    _git(root, "config", "user.email", "release@example.com")
    _git(root, "config", "user.name", "marimo-export Release")
    root.joinpath("release.txt").write_text("release\n", encoding="utf-8")
    _git(root, "add", "release.txt")
    _git(root, "commit", "-m", "release")
    tag_commit = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "-a", "v0.1.0", "-m", "release: 0.1.0")
    if advance_main:
        root.joinpath("release.txt").write_text("new main\n", encoding="utf-8")
        _git(root, "add", "release.txt")
        _git(root, "commit", "-m", "advance main")
    main_commit = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/main", main_commit)
    return tag_commit, main_commit


def _write_command(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run_release_check(
    root: Path,
    workflow_commit: str,
    *,
    ci_run: str,
) -> subprocess.CompletedProcess[str]:
    commands = root / "commands"
    commands.mkdir()
    _write_command(commands / "uv", "#!/bin/sh\nprintf '0.1.0\\n'\n")
    _write_command(commands / "node", "#!/bin/sh\nprintf '0.1.0\\n0.1.0\\n'\n")
    _write_command(commands / "gh", '#!/bin/sh\nprintf "%s" "$FAKE_CI_RUN"\n')
    environment = {
        **os.environ,
        "FAKE_CI_RUN": ci_run,
        "GH_TOKEN": "test-token",
        "GITHUB_REF": "refs/tags/v0.1.0",
        "GITHUB_REF_NAME": "v0.1.0",
        "GITHUB_SHA": workflow_commit,
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
    }
    return subprocess.run(
        [str(ROOT / "scripts/check-release.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_publish_preflight_rejects_an_older_ancestor_tag(tmp_path: Path) -> None:
    tag_commit, main_commit = _release_repository(tmp_path, advance_main=True)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 1
    assert f"must point to current origin/main {main_commit}" in result.stderr


def test_publish_preflight_rejects_nonmatching_workflow_sha(tmp_path: Path) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)
    other_commit = f"{'0' * 39}1"
    assert tag_commit != other_commit

    result = _run_release_check(
        tmp_path,
        other_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 1
    assert "must resolve to workflow commit" in result.stderr


def test_publish_preflight_requires_successful_exact_commit_ci(tmp_path: Path) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nfailure\nhttps://example.test/ci\n",
    )

    assert result.returncode == 1
    assert "Main CI must pass for release commit" in result.stderr


def test_publish_preflight_accepts_exact_sha_with_successful_ci(tmp_path: Path) -> None:
    tag_commit, main_commit = _release_repository(tmp_path, advance_main=False)
    assert tag_commit == main_commit

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 0, result.stderr


def test_publish_workflow_supplies_current_main_and_actions_token() -> None:
    workflow = ROOT.joinpath(".github/workflows/publish.yml").read_text(encoding="utf-8")
    build_index = workflow.index("  build:")
    permission_index = workflow.index("actions: read")
    fetch_index = workflow.index("refs/remotes/origin/main")
    step_index = workflow.index("- name: Verify release source")
    token_index = workflow.index("GH_TOKEN: ${{ github.token }}")
    check_index = workflow.index("./scripts/check-release.sh")
    setup_index = workflow.index("- name: Set up Vite+")
    sync_index = workflow.index("uv sync --all-packages --all-extras --locked")

    assert workflow.count("actions: read") == 1
    assert build_index < permission_index < fetch_index
    assert fetch_index < step_index < token_index < check_index < setup_index < sync_index
