from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _pypi_verifier() -> Callable[[Path, str, dict[str, Any]], None]:
    spec = spec_from_file_location(
        "verify_pypi_artifacts",
        ROOT / "scripts/verify_pypi_artifacts.py",
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Callable[[Path, str, dict[str, Any]], None], module.verify_release)


def _checksum_writer() -> Callable[[Path], Path]:
    spec = spec_from_file_location(
        "verify_release_artifacts",
        ROOT / "scripts/verify_release_artifacts.py",
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Callable[[Path], Path], module.write_checksum_manifest)


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


def _bash() -> str:
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to locate Bash on Windows")
    bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
    if not bash.is_file():
        raise RuntimeError("Git Bash is unavailable")
    return str(bash)


def _run_release_check(
    root: Path,
    workflow_commit: str,
    *,
    ci_run: str,
    environments_exist: bool = True,
    repository_visibility: str = "PUBLIC",
) -> subprocess.CompletedProcess[str]:
    commands = root / "commands"
    commands.mkdir()
    _write_command(commands / "uv", "#!/bin/sh\nprintf '0.1.0\\n'\n")
    _write_command(commands / "node", "#!/bin/sh\nprintf '0.1.0\\n'\n")
    _write_command(
        commands / "gh",
        """#!/bin/sh
if [ "$1 $2" = "repo view" ]; then
    printf '%s\\n' "$FAKE_REPOSITORY_VISIBILITY"
elif [ "$1" = "api" ]; then
    [ "$FAKE_ENVIRONMENTS_EXIST" = "1" ]
else
    printf '%s' "$FAKE_CI_RUN"
fi
""",
    )
    environment = {
        **os.environ,
        "FAKE_CI_RUN": ci_run,
        "FAKE_ENVIRONMENTS_EXIST": "1" if environments_exist else "0",
        "FAKE_REPOSITORY_VISIBILITY": repository_visibility,
        "GH_TOKEN": "test-token",
        "GITHUB_REF": "refs/tags/v0.1.0",
        "GITHUB_REF_NAME": "v0.1.0",
        "GITHUB_SHA": workflow_commit,
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
    }
    return subprocess.run(
        [_bash(), str(ROOT / "scripts/check-release.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_publish_preflight_accepts_tagged_commit_after_main_advances(
    tmp_path: Path,
) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=True)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 0, result.stderr


def test_publish_preflight_rejects_commit_outside_main(tmp_path: Path) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)
    tree = _git(tmp_path, "rev-parse", f"{tag_commit}^{{tree}}")
    unrelated_main = _git(tmp_path, "commit-tree", tree, "-m", "unrelated main")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", unrelated_main)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 1
    assert f"Release commit {tag_commit} is not on origin/main" in result.stderr


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


def test_publish_preflight_requires_public_repository(tmp_path: Path) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
        repository_visibility="INTERNAL",
    )

    assert result.returncode == 1
    assert "Releases require a public GitHub repository" in result.stderr


def test_publish_preflight_requires_github_environments(tmp_path: Path) -> None:
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
        environments_exist=False,
    )

    assert result.returncode == 1
    assert "Missing required GitHub environment: npm" in result.stderr


def test_publish_preflight_accepts_exact_sha_with_successful_ci(tmp_path: Path) -> None:
    tag_commit, main_commit = _release_repository(tmp_path, advance_main=False)
    assert tag_commit == main_commit

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 0, result.stderr


def _workflow() -> tuple[str, dict[str, Any]]:
    source = ROOT.joinpath(".github/workflows/publish.yml").read_text(encoding="utf-8")
    workflow = cast(dict[str, Any], yaml.load(source, Loader=yaml.BaseLoader))
    return source, workflow


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return next(step for step in steps if step.get("name") == name)


def test_publish_workflow_coordinates_python_and_browser_distributions() -> None:
    _source, workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)

    build = jobs["build"]
    upload = _step(build, "Upload release artifacts")
    assert upload["with"]["path"].splitlines() == [
        "dist/SHA256SUMS",
        "dist/npm/*.tgz",
        "dist/python/*.whl",
        "dist/python/*.tar.gz",
    ]
    assert jobs["attest"]["needs"] == "build"
    assert jobs["publish-npm"]["needs"] == "attest"
    publish = _step(jobs["publish-npm"], "Publish npm packages")
    assert publish["run"] == (
        './scripts/publish-npm.sh "dist/npm/marimo-team-marimo-export-${GITHUB_REF_NAME#v}.tgz"'
    )
    assert jobs["publish-pypi"]["needs"] == "verify-npm"
    assert jobs["release-notes"]["needs"] == ["verify-npm", "verify-pypi"]


def test_publish_workflow_installs_the_browser_used_by_release_tests() -> None:
    _source, workflow = _workflow()
    build = workflow["jobs"]["build"]
    browser = _step(build, "Install AnyWidget test browser")
    release_gate = _step(build, "Run release gate")

    assert browser["run"] == (
        "pnpm --filter @marimo-export/internal-loader-anywidget exec playwright install "
        "--with-deps --only-shell chromium"
    )
    assert build["steps"].index(browser) < build["steps"].index(release_gate)


def test_publish_workflow_scopes_oidc_to_attestation_and_registry_jobs() -> None:
    source, workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)

    assert jobs["publish-npm"]["environment"]["name"] == "npm"
    assert jobs["publish-npm"]["permissions"]["id-token"] == "write"
    npm_setup = _step(jobs["publish-npm"], "Set up Node.js")
    assert npm_setup["with"]["node-version"] == "24"
    assert "registry-url" not in npm_setup["with"]
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"
    assert jobs["publish-pypi"]["permissions"]["id-token"] == "write"
    assert "secrets." not in source
    for name, job in jobs.items():
        if name not in {"attest", "publish-npm", "publish-pypi"}:
            assert job.get("permissions", {}).get("id-token") != "write"


def test_publish_workflow_creates_generated_release_with_verified_assets() -> None:
    _source, workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    release = jobs["release-notes"]
    command = _step(release, "Create GitHub release")["run"]

    assert release["permissions"]["contents"] == "write"
    assert "--generate-notes" in command
    assert "--verify-tag" in command
    assert "dist/SHA256SUMS" in command
    assert "dist/npm/*.tgz" in command
    assert "dist/python/*.whl" in command
    assert "dist/python/*.tar.gz" in command


def test_pypi_verification_matches_the_exact_local_artifacts(tmp_path: Path) -> None:
    version = "0.1.0"
    wheel = tmp_path / f"marimo_export-{version}-py3-none-any.whl"
    sdist = tmp_path / f"marimo_export-{version}.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"source")
    metadata = {
        "info": {"version": version},
        "urls": [
            {"filename": wheel.name, "digests": {"sha256": sha256(b"wheel").hexdigest()}},
            {"filename": sdist.name, "digests": {"sha256": sha256(b"source").hexdigest()}},
        ],
    }

    verify_release = _pypi_verifier()
    verify_release(tmp_path, version, metadata)

    metadata["urls"][0]["digests"]["sha256"] = sha256(b"different").hexdigest()
    with pytest.raises(RuntimeError, match="has SHA-256"):
        verify_release(tmp_path, version, metadata)


def test_checksum_manifest_addresses_flat_github_release_assets(tmp_path: Path) -> None:
    version = "0.1.0"
    manifests = {
        "packages/python/pyproject.toml": f'[project]\nversion = "{version}"\n',
        "packages/browser/package.json": f'{{"version":"{version}"}}\n',
    }
    for relative, contents in manifests.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    artifacts = {
        f"python/marimo_export-{version}-py3-none-any.whl": b"wheel",
        f"python/marimo_export-{version}.tar.gz": b"source",
        f"npm/marimo-team-marimo-export-{version}.tgz": b"browser",
    }
    for relative, contents in artifacts.items():
        path = tmp_path / "dist" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    manifest = _checksum_writer()(tmp_path)
    entries = [line.split("  ", 1) for line in manifest.read_text().splitlines()]
    assert {name: digest for digest, name in entries} == {
        Path(relative).name: sha256(contents).hexdigest()
        for relative, contents in artifacts.items()
    }


def test_npm_publisher_runs_registry_commands_from_the_artifact_directory(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = artifacts / "marimo-export.tgz"
    manifest = json.dumps({"name": "@marimo-team/marimo-export", "version": "0.0.1"}).encode()
    with tarfile.open(tarball, "w:gz") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))

    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(
        commands / "npm",
        """#!/bin/sh
directory="$PWD"
while [ "$directory" != "/" ]; do
    if [ -f "$directory/package.json" ]; then
        exit 70
    fi
    parent="$(dirname "$directory")"
    if [ "$parent" = "$directory" ]; then
        break
    fi
    directory="$parent"
done
if [ "$1" = "view" ]; then
    exit 1
fi
""",
    )
    result = subprocess.run(
        [_bash(), str(ROOT / "scripts/publish-npm.sh"), str(tarball)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
