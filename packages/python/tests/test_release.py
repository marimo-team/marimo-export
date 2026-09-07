from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from base64 import b64encode
from collections.abc import Callable
from hashlib import sha256, sha512
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PUBLICATION_JOBS = (
    "Build and verify",
    "Attest build provenance",
    "Publish npm packages",
    "Verify npm packages",
    "Publish Python package",
    "Verify Python package",
    "Create GitHub release",
)


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


def _public_release_verifier() -> Any:
    spec = spec_from_file_location(
        "verify_public_release",
        ROOT / "scripts/verify_public_release.py",
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    _release_repository(tmp_path, advance_main=False)
    other_commit = f"{'0' * 39}1"

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
    tag_commit, _main_commit = _release_repository(tmp_path, advance_main=False)

    result = _run_release_check(
        tmp_path,
        tag_commit,
        ci_run="completed\nsuccess\nhttps://example.test/ci\n",
    )

    assert result.returncode == 0, result.stderr


def test_release_preflight_accepts_detached_checkout_at_origin_main(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    commands = tmp_path / "commands"
    remote.mkdir()
    checkout.mkdir()
    commands.mkdir()
    _git(remote, "init", "--bare")
    _git(checkout, "init", "--initial-branch=main")
    _git(checkout, "config", "user.email", "release@example.com")
    _git(checkout, "config", "user.name", "marimo-export Release")
    scripts = checkout / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/release.sh", scripts / "release.sh")
    _git(checkout, "add", "scripts/release.sh")
    _git(checkout, "commit", "-m", "release")
    _git(checkout, "remote", "add", "origin", str(remote))
    _git(checkout, "push", "--set-upstream", "origin", "main")
    _git(checkout, "checkout", "--detach")
    assert _git(checkout, "branch", "--show-current") == ""

    _write_command(commands / "uv", "#!/bin/sh\nprintf '0.1.0\\n'\n")
    _write_command(commands / "node", "#!/bin/sh\nprintf '0.1.0\\n'\n")
    _write_command(
        commands / "curl",
        """#!/bin/sh
case "$*" in
  *0.1.0*) printf '404\n' ;;
  *) printf '200\n' ;;
esac
""",
    )
    _write_command(
        commands / "gh",
        """#!/bin/sh
if [ "$1 $2" = "repo view" ]; then
    case "$*" in
      *visibility*) printf 'PUBLIC\n' ;;
      *) printf 'https://example.test/marimo-export\n' ;;
    esac
elif [ "$1" = "api" ]; then
    printf '1\n'
else
    printf '123\ncompleted\nsuccess\nhttps://example.test/ci\n'
fi
""",
    )

    result = subprocess.run(
        [_bash(), "scripts/release.sh", "--dry-run"],
        cwd=checkout,
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run complete" in result.stdout


def _verify_registry(root: Path, registry: str, scenario: str) -> subprocess.CompletedProcess[str]:
    commands = root / "commands"
    scripts = root / "scripts"
    commands.mkdir()
    scripts.mkdir()
    # Stand in for registry visibility and installation at the child-command boundary.
    probe = """
printf '%s\\n' "$stage" >> "$PROBE_ROOT/events"
case "$SCENARIO:$stage" in
    unavailable:artifacts|broken:consumer) exit 1 ;;
    delayed:*)
        if [ ! -f "$PROBE_ROOT/$stage" ]; then
            touch "$PROBE_ROOT/$stage"
            exit 1
        fi
        ;;
esac
if [ "$stage" = consumer ]; then
    printf 'consumer passed\\n'
fi
"""
    _write_command(
        commands / "uv",
        '#!/bin/sh\ncase "$*" in\n*verify_pypi_artifacts.py*) stage=artifacts ;;\n'
        "*) stage=consumer ;;\nesac\n" + probe,
    )
    _write_command(commands / "node", "#!/bin/sh\nstage=consumer\n" + probe)
    _write_command(scripts / "publish-npm.sh", "#!/bin/sh\nstage=artifacts\n" + probe)
    # Git Bash can put its commands ahead of PATH stubs. A function intercepts every wait.
    return subprocess.run(
        [
            _bash(),
            "-c",
            'sleep() { :; }; source "$1"',
            "verify-registry",
            (ROOT / f"scripts/verify-{registry}.sh").as_posix(),
        ],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "PROBE_ROOT": root.as_posix(),
            "RELEASE_VERSION": "0.1.0",
            "SCENARIO": scenario,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("registry", ["pypi", "npm"])
def test_registry_verification_waits_for_files_and_a_working_install(
    tmp_path: Path, registry: str
) -> None:
    result = _verify_registry(tmp_path, registry, "delayed")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "consumer passed" in result.stdout


@pytest.mark.parametrize("registry", ["pypi", "npm"])
@pytest.mark.parametrize("scenario", ["unavailable", "broken"])
def test_registry_verification_exhausts_its_retry_budget(
    tmp_path: Path, registry: str, scenario: str
) -> None:
    result = _verify_registry(tmp_path, registry, scenario)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "0.1.0" in result.stderr
    attempts = (tmp_path / "events").read_text().splitlines()
    assert attempts.count("artifacts") == 18
    assert attempts.count("consumer") == (18 if scenario == "broken" else 0)


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
    assert set(jobs["publish-npm"]["needs"]) == {"build", "attest"}
    publish = _step(jobs["publish-npm"], "Publish npm packages")
    assert publish["run"] == (
        './scripts/publish-npm.sh "dist/npm/marimo-team-marimo-export-$RELEASE_VERSION.tgz"'
    )
    assert set(jobs["publish-pypi"]["needs"]) == {"build", "attest"}
    for registry in ("npm", "pypi"):
        assert set(jobs[f"verify-{registry}"]["needs"]) == {"build", f"publish-{registry}"}
    assert jobs["publish-pypi"]["if"] == jobs["publish-npm"]["if"]
    assert "if" not in publish
    assert set(jobs["release-notes"]["needs"]) == {"verify-npm", "verify-pypi", "build"}


@pytest.mark.parametrize(
    ("event", "failed_registry"),
    [("push", None), ("workflow_dispatch", None), ("push", "npm"), ("push", "pypi")],
)
def test_release_gate_requires_both_verified_registries(
    failed_registry: str | None, event: str
) -> None:
    _, workflow = _workflow()
    gate = workflow["jobs"]["complete"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "build",
        "attest",
        "publish-npm",
        "verify-npm",
        "publish-pypi",
        "verify-pypi",
        "release-notes",
    }
    results = {name: "success" for name in gate["needs"]}
    results["attest"] = "success" if event == "push" else "skipped"
    if failed_registry is not None:
        results[f"verify-{failed_registry}"] = "failure"
    step = _step(gate, "Require completed publication and verification")
    environment = {"EXPECTED_ATTEST": "success" if event == "push" else "skipped"}
    for name, expression in step["env"].items():
        if name == "EXPECTED_ATTEST":
            continue
        reference = re.fullmatch(r"\$\{\{\s*needs\.([a-z-]+)\.result\s*\}\}", expression)
        assert reference is not None, expression
        environment[name] = results[reference[1]]
    result = subprocess.run(
        [_bash(), "-eu", "-o", "pipefail", "-c", step["run"]],
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (0 if failed_registry is None else 1), result.stderr


def test_publish_workflow_scopes_oidc_to_attestation_and_registry_jobs() -> None:
    source, workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)

    assert jobs["publish-npm"]["environment"]["name"] == "npm"
    assert jobs["publish-npm"]["permissions"]["id-token"] == "write"
    npm_setup = _step(jobs["publish-npm"], "Set up Node.js")
    assert npm_setup["with"]["node-version"] == "24"
    assert "registry-url" not in npm_setup["with"]
    assert _step(jobs["publish-npm"], "Enable pnpm")["run"] == "corepack enable pnpm"
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


def test_public_release_verifier_checks_the_complete_downloaded_package_set(
    tmp_path: Path,
) -> None:
    version = "0.1.0"
    artifacts = {
        f"marimo-team-marimo-export-{version}.tgz": b"browser",
        f"marimo_export-{version}-py3-none-any.whl": b"wheel",
        f"marimo_export-{version}.tar.gz": b"source",
    }
    for name, contents in artifacts.items():
        (tmp_path / name).write_bytes(contents)
    (tmp_path / "SHA256SUMS").write_text(
        "".join(
            f"{sha256(contents).hexdigest()}  {name}\n"
            for name, contents in sorted(artifacts.items())
        ),
        encoding="utf-8",
    )
    verifier = _public_release_verifier()
    names = verifier._expected_artifacts(
        version,
        [{"name": name} for name in (*artifacts, "SHA256SUMS")],
    )

    checksums = verifier._verify_checksums(tmp_path, names)

    assert checksums == {name: sha256(contents).hexdigest() for name, contents in artifacts.items()}


def test_public_release_verifier_rejects_an_extra_release_asset() -> None:
    verifier = _public_release_verifier()

    with pytest.raises(RuntimeError, match="package set"):
        verifier._expected_artifacts(
            "0.1.0",
            [
                {"name": "SHA256SUMS"},
                {"name": "marimo-team-marimo-export-0.1.0.tgz"},
                {"name": "marimo_export-0.1.0-py3-none-any.whl"},
                {"name": "marimo_export-0.1.0.tar.gz"},
                {"name": "unexpected.zip"},
            ],
        )


@pytest.mark.parametrize("published", ["absent", "matching", "different"])
def test_npm_publisher_resumes_from_registry_state(
    tmp_path: Path,
    published: str,
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
    registry_command = """#!/bin/sh
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
"""
    integrity = "sha512-" + b64encode(sha512(tarball.read_bytes()).digest()).decode()
    if published == "different":
        integrity = "sha512-" + b64encode(sha512(b"different archive").digest()).decode()
    lookup = "exit 1\n" if published == "absent" else 'printf "%s\\n" "$PUBLISHED_INTEGRITY"\n'
    _write_command(commands / "pnpm", registry_command + lookup)
    _write_command(commands / "npm", registry_command + 'touch "$PUBLISH_MARKER"\n')
    result = subprocess.run(
        [_bash(), str(ROOT / "scripts/publish-npm.sh"), str(tarball)],
        cwd=ROOT,
        env={
            **os.environ,
            "PUBLISHED_INTEGRITY": integrity,
            "PUBLISH_MARKER": (tmp_path / "published").as_posix(),
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == (1 if published == "different" else 0), (
        result.stdout + result.stderr
    )
    assert (tmp_path / "published").exists() == (published == "absent")


@pytest.fixture
def recovery_verifier(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    verifier = _public_release_verifier()
    version = "0.1.0"
    commit, harness = "a" * 40, "b" * 40
    source_id, recovery_id = 101, 202
    packages = {
        "marimo-team-marimo-export-0.1.0.tgz": b"browser",
        "marimo_export-0.1.0-py3-none-any.whl": b"wheel",
        "marimo_export-0.1.0.tar.gz": b"source",
    }
    manifest = "".join(
        f"{sha256(payload).hexdigest()}  {name}\n" for name, payload in sorted(packages.items())
    ).encode()
    files = {**packages, "SHA256SUMS": manifest}

    def run_record(run_id: int, event: str, branch: str, sha: str) -> dict[str, Any]:
        return {
            "id": run_id,
            "event": event,
            "head_branch": branch,
            "head_sha": sha,
            "repository": {"full_name": "marimo-team/marimo-export"},
            "head_repository": {"full_name": "marimo-team/marimo-export"},
            "path": ".github/workflows/publish.yml",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "html_url": f"https://github.com/marimo-team/marimo-export/actions/runs/{run_id}",
            "display_title": "Recover v0.1.0 from run 101",
        }

    source = {
        **run_record(source_id, "push", "v0.1.0", commit),
        "run_attempt": 2,
        "conclusion": "failure",
    }
    recovery = run_record(recovery_id, "workflow_dispatch", "main", harness)

    def job(name: str, attempt: int = 1) -> dict[str, Any]:
        return {
            "name": name,
            "run_attempt": attempt,
            "status": "completed",
            "conclusion": "success",
            "html_url": f"https://example.test/jobs/{name}",
        }

    jobs = {
        source_id: [job("Build and verify", 2), job("Attest build provenance", 2)],
        recovery_id: [job(name) for name in (*PUBLICATION_JOBS, "Release gate")],
    }
    jobs[recovery_id][1]["conclusion"] = "skipped"
    receipt = {
        "schema": 1,
        "version": version,
        "tag": "v0.1.0",
        "commit": commit,
        "repository": "marimo-team/marimo-export",
        "workflow": ".github/workflows/publish.yml",
        "source_run": source_id,
        "source_attempt": 2,
        "artifact_id": 303,
        "attestation_attempt": 1,
        "checksums_sha256": sha256(manifest).hexdigest(),
        "recovery_run": recovery_id,
        "recovery_attempt": 1,
        "recovery_commit": harness,
        "recovery_ref": "refs/heads/main",
    }
    evidence: dict[str, Any] = {
        "source": source,
        "has_recovery": True,
        "tag_run": {**run_record(909, "push", "v0.1.0", commit), "conclusion": "cancelled"},
        "recovery": recovery,
        "receipt": receipt,
        "jobs": jobs,
        "harness_ci": "success",
        "invocation": "https://github.com/marimo-team/marimo-export/actions/runs/101/attempts/1",
        "verified_files": [],
        "registry_checks": [],
    }

    def json_command(*arguments: str) -> Any:
        if arguments[:3] == ("gh", "run", "list"):
            workflow = arguments[arguments.index("--workflow") + 1]
            selected = arguments[arguments.index("--commit") + 1]
            return [
                {
                    "databaseId": evidence["tag_run"]["id"] if workflow == "publish.yml" else 404,
                    "headSha": selected,
                    "status": "completed",
                    "conclusion": evidence["tag_run"]["conclusion"]
                    if workflow == "publish.yml"
                    else (evidence["harness_ci"] if selected == harness else "success"),
                    "url": "https://example.test/workflow",
                }
            ]
        if arguments[:3] == ("gh", "release", "view"):
            return {
                "tagName": "v0.1.0",
                "isDraft": False,
                "isPrerelease": False,
                "url": "https://example.test/release",
                "assets": [{"name": n} for n in files]
                + ([{"name": "release-recovery.json"}] if evidence["has_recovery"] else []),
            }
        endpoint = arguments[-1]
        if "/git/ref/tags/" in endpoint:
            return {"object": {"type": "tag", "sha": "c" * 40}}
        if "/git/tags/" in endpoint:
            return {"tag": "v0.1.0", "object": {"type": "commit", "sha": commit}}
        if endpoint.endswith("/jobs?filter=all&per_page=100"):
            run_id = int(endpoint.split("/runs/")[1].split("/")[0])
            return [{"jobs": jobs[run_id]}]
        if endpoint.endswith("/runs/909"):
            return evidence["tag_run"]
        if endpoint.endswith(f"/runs/{source_id}"):
            return source
        if endpoint.endswith(f"/runs/{recovery_id}"):
            return recovery
        raise RuntimeError(f"unavailable GitHub evidence: {arguments}")

    def command(*arguments: str, **_kwargs: Any) -> str:
        if arguments[:3] in {("gh", "run", "download"), ("gh", "release", "download")}:
            directory = Path(arguments[arguments.index("--dir") + 1])
            if arguments[1] == "run":
                directory.joinpath("release-recovery.json").write_text(json.dumps(receipt))
            else:
                for name, content in files.items():
                    directory.joinpath(name).write_bytes(content)
                if evidence["has_recovery"]:
                    directory.joinpath("release-recovery.json").write_text(json.dumps(receipt))
            return ""
        if arguments[:3] == ("gh", "attestation", "verify"):
            evidence["verified_files"].append(Path(arguments[3]).name)
            assert arguments[arguments.index("--source-ref") + 1] == "refs/tags/v0.1.0"
            assert arguments[arguments.index("--source-digest") + 1] == commit
            return json.dumps(
                [
                    {
                        "verificationResult": {
                            "signature": {
                                "certificate": {"runInvocationURI": evidence["invocation"]}
                            }
                        }
                    }
                ]
            )
        if arguments[0] == "./scripts/publish-npm.sh":
            evidence["registry_checks"].append("npm")
            return ""
        raise AssertionError(arguments)

    class PyPI:
        @staticmethod
        def _fetch_release(_version: str) -> dict[str, Any]:
            return {}

        @staticmethod
        def verify_release(directory: Path, selected: str, _metadata: Any) -> None:
            assert selected == version
            assert {path.name for path in directory.iterdir()} == {
                "marimo_export-0.1.0-py3-none-any.whl",
                "marimo_export-0.1.0.tar.gz",
            }
            evidence["registry_checks"].append("pypi")

    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(verifier, "_json_command", json_command)
    monkeypatch.setattr(verifier, "_run", command)
    monkeypatch.setattr(verifier, "_pypi_verifier", lambda: PyPI)
    return verifier, evidence


@pytest.mark.parametrize("selected_run", [None, 202])
def test_public_release_verifier_accepts_the_exact_recovery_chain(
    recovery_verifier: Any,
    selected_run: int | None,
) -> None:
    verifier, evidence = recovery_verifier
    result = verifier.verify_public_release("0.1.0", recovery_run=selected_run)
    assert result["commit"] == "a" * 40
    assert result["recovery"]["recovery_commit"] == "b" * 40
    assert result["recovery"]["source_attempt"] == 2
    assert result["recovery"]["attestation_attempt"] == 1
    expected_files = {
        "SHA256SUMS",
        "marimo-team-marimo-export-0.1.0.tgz",
        "marimo_export-0.1.0-py3-none-any.whl",
        "marimo_export-0.1.0.tar.gz",
    }
    assert set(evidence["verified_files"]) == expected_files
    assert {item["name"] for item in result["artifacts"]} == expected_files
    assert evidence["registry_checks"] == ["npm", "pypi"]
    assert result["fresh_installs"]["pnpm"]["conclusion"] == "success"
    assert result["fresh_installs"]["python"]["conclusion"] == "success"


@pytest.mark.parametrize(
    ("owner", "field", "value"),
    [
        ("source", "event", "workflow_dispatch"),
        ("source", "head_branch", "main"),
        ("source", "head_sha", "d" * 40),
        ("recovery", "head_branch", "topic"),
        ("recovery", "event", "push"),
        ("recovery", "conclusion", "failure"),
        ("receipt", "source_run", 999),
        ("receipt", "source_attempt", 3),
        ("receipt", "recovery_commit", "d" * 40),
        ("receipt", "recovery_attempt", 2),
        ("receipt", "attestation_attempt", 3),
        ("receipt", "checksums_sha256", "0" * 64),
    ],
)
def test_public_release_verifier_rejects_mismatched_recovery_identity(
    recovery_verifier: Any,
    owner: str,
    field: str,
    value: Any,
) -> None:
    verifier, evidence = recovery_verifier
    evidence[owner][field] = value
    with pytest.raises(RuntimeError):
        verifier.verify_public_release("0.1.0", recovery_run=202)
    assert evidence["registry_checks"] == []


@pytest.mark.parametrize(
    "failure",
    [
        "original-attest",
        "gate",
        "fresh-attest",
        "harness-ci",
        "unrelated-dispatch",
        "wrong-signing-run",
        "wrong-signing-attempt",
    ],
)
def test_public_release_verifier_requires_recovery_prerequisites(
    recovery_verifier: Any,
    failure: str,
) -> None:
    verifier, evidence = recovery_verifier
    if failure == "original-attest":
        evidence["jobs"][101][1]["conclusion"] = "failure"
    elif failure == "gate":
        evidence["jobs"][202][-1]["conclusion"] = "skipped"
    elif failure == "fresh-attest":
        evidence["jobs"][202][1]["conclusion"] = "success"
    elif failure == "harness-ci":
        evidence["harness_ci"] = "failure"
    elif failure == "unrelated-dispatch":
        evidence["recovery"]["display_title"] = "Some other successful dispatch"
    elif failure == "wrong-signing-run":
        evidence["invocation"] = evidence["invocation"].replace("/101/", "/999/")
    else:
        evidence["invocation"] = evidence["invocation"].replace("/attempts/1", "/attempts/2")
    with pytest.raises(RuntimeError):
        verifier.verify_public_release("0.1.0")
    assert evidence["registry_checks"] == []


def test_public_release_verifier_retains_receipt_identity_across_downstream_retries(
    recovery_verifier: Any,
) -> None:
    verifier, evidence = recovery_verifier
    evidence["recovery"]["run_attempt"] = 2
    evidence["source"]["run_attempt"] = 3
    result = verifier.verify_public_release("0.1.0")
    assert result["recovery"]["recovery_attempt"] == 1
    assert result["recovery"]["source_attempt"] == 2
    assert result["recovery"]["attestation_attempt"] == 1


def test_public_release_verifier_accepts_a_complete_tagged_publication(
    recovery_verifier: Any,
) -> None:
    verifier, evidence = recovery_verifier
    evidence["has_recovery"] = False
    evidence["source"]["conclusion"] = "success"
    evidence["tag_run"] = evidence["source"]
    evidence["jobs"][101] = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 2,
            "html_url": f"https://example.test/source/{name}",
        }
        for name in PUBLICATION_JOBS
    ]
    result = verifier.verify_public_release("0.1.0")
    assert result["commit"] == "a" * 40
    assert evidence["registry_checks"] == ["npm", "pypi"]
