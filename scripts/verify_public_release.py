"""Verify one published marimo-export release from source commit to registry bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

REPOSITORY = "marimo-team/marimo-export"
SIGNER_WORKFLOW = f"{REPOSITORY}/.github/workflows/publish.yml"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RELEASE_JOBS = (
    "Build and verify",
    "Attest build provenance",
    "Publish npm packages",
    "Verify npm packages",
    "Publish Python package",
    "Verify Python package",
    "Create GitHub release",
)


def _pypi_verifier() -> Any:
    path = Path(__file__).with_name("verify_pypi_artifacts.py")
    spec = spec_from_file_location("verify_pypi_artifacts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load PyPI verifier: {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*arguments: str, timeout: float = 120) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(f"{' '.join(arguments)}: {detail}")
    return result.stdout


def _json_command(*arguments: str) -> Any:
    output = _run(*arguments)
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{' '.join(arguments)} returned invalid JSON") from error


def _tag_commit(tag: str) -> str:
    reference = _json_command("gh", "api", f"repos/{REPOSITORY}/git/ref/tags/{tag}")
    target = reference.get("object", {})
    if target.get("type") != "tag" or not isinstance(target.get("sha"), str):
        raise RuntimeError(f"release tag must be annotated: {tag}")
    annotation = _json_command(
        "gh",
        "api",
        f"repos/{REPOSITORY}/git/tags/{target['sha']}",
    )
    commit = annotation.get("object", {})
    if annotation.get("tag") != tag or commit.get("type") != "commit":
        raise RuntimeError(f"annotated tag does not resolve to one commit: {tag}")
    sha = commit.get("sha")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise RuntimeError(f"annotated tag contains an invalid commit: {tag}")
    return sha


def _workflow_run(workflow: str, commit: str, *, branch: str | None = None) -> dict[str, Any]:
    arguments = [
        "gh",
        "run",
        "list",
        "--repo",
        REPOSITORY,
        "--workflow",
        workflow,
        "--commit",
        commit,
        "--event",
        "push",
        "--limit",
        "1",
        "--json",
        "databaseId,status,conclusion,url,headSha",
    ]
    if branch is not None:
        commit_index = arguments.index("--commit")
        arguments[commit_index:commit_index] = ["--branch", branch]
    runs = _json_command(*arguments)
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise RuntimeError(f"no {workflow} push run found for {commit}")
    run = runs[0]
    if (
        run.get("headSha") != commit
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise RuntimeError(
            f"{workflow} must pass for {commit}: {run.get('status')}/{run.get('conclusion')}"
        )
    return run


def _release_jobs(run_id: int) -> dict[str, dict[str, Any]]:
    value = _json_command(
        "gh",
        "run",
        "view",
        str(run_id),
        "--repo",
        REPOSITORY,
        "--json",
        "jobs",
    )
    jobs = value.get("jobs") if isinstance(value, dict) else None
    if not isinstance(jobs, list):
        raise RuntimeError(f"release run {run_id} returned no jobs")
    by_name = {
        job["name"]: job
        for job in jobs
        if isinstance(job, dict) and isinstance(job.get("name"), str)
    }
    failures = [
        name
        for name in RELEASE_JOBS
        if by_name.get(name, {}).get("status") != "completed"
        or by_name.get(name, {}).get("conclusion") != "success"
    ]
    if failures:
        raise RuntimeError(f"release jobs did not pass: {', '.join(failures)}")
    return by_name


def _expected_artifacts(version: str, assets: list[dict[str, Any]]) -> tuple[str, ...]:
    names = tuple(sorted(asset["name"] for asset in assets if isinstance(asset.get("name"), str)))
    wheels = [
        name
        for name in names
        if name.startswith(f"marimo_export-{version}-") and name.endswith(".whl")
    ]
    expected = {
        "SHA256SUMS",
        f"marimo-team-marimo-export-{version}.tgz",
        f"marimo_export-{version}.tar.gz",
        *wheels,
    }
    if len(wheels) != 1 or set(names) != expected:
        raise RuntimeError(f"release assets do not match the package set: {list(names)}")
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksums(directory: Path, artifact_names: tuple[str, ...]) -> dict[str, str]:
    manifest = directory / "SHA256SUMS"
    entries: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        fields = line.split("  ", 1)
        if len(fields) != 2 or re.fullmatch(r"[0-9a-f]{64}", fields[0]) is None:
            raise RuntimeError("SHA256SUMS contains an invalid entry")
        digest, name = fields
        if name in entries:
            raise RuntimeError(f"SHA256SUMS contains a duplicate entry: {name}")
        entries[name] = digest
    expected = set(artifact_names) - {manifest.name}
    if set(entries) != expected:
        raise RuntimeError("SHA256SUMS does not address the complete package set")
    for name, expected_digest in entries.items():
        actual = _sha256(directory / name)
        if actual != expected_digest:
            raise RuntimeError(
                f"release asset {name} has SHA-256 {actual}, expected {expected_digest}"
            )
    return entries


def _verify_attestations(directory: Path, names: tuple[str, ...], tag: str, commit: str) -> None:
    for name in names:
        _run(
            "gh",
            "attestation",
            "verify",
            str(directory / name),
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            SIGNER_WORKFLOW,
            "--source-digest",
            commit,
            "--source-ref",
            f"refs/tags/{tag}",
            "--format",
            "json",
            timeout=180,
        )


def verify_public_release(version: str) -> dict[str, Any]:
    for command in ("gh", "node", "openssl", "pnpm", "tar"):
        if shutil.which(command) is None:
            raise RuntimeError(f"missing required command: {command}")

    tag = f"v{version}"
    commit = _tag_commit(tag)
    ci = _workflow_run("ci.yml", commit, branch="main")
    release_run = _workflow_run("publish.yml", commit)
    run_id = release_run.get("databaseId")
    if not isinstance(run_id, int):
        raise RuntimeError("release workflow returned an invalid run ID")
    jobs = _release_jobs(run_id)

    release = _json_command(
        "gh",
        "release",
        "view",
        tag,
        "--repo",
        REPOSITORY,
        "--json",
        "tagName,isDraft,isPrerelease,url,assets",
    )
    if (
        not isinstance(release, dict)
        or release.get("tagName") != tag
        or release.get("isDraft") is not False
        or release.get("isPrerelease") is not False
        or not isinstance(release.get("assets"), list)
    ):
        raise RuntimeError(f"GitHub Release metadata is incomplete for {tag}")
    names = _expected_artifacts(version, release["assets"])

    with tempfile.TemporaryDirectory(prefix="marimo-export-release-") as temporary:
        directory = Path(temporary)
        _run(
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            REPOSITORY,
            "--dir",
            str(directory),
            timeout=180,
        )
        if set(path.name for path in directory.iterdir()) != set(names):
            raise RuntimeError("downloaded release assets do not match GitHub Release metadata")
        checksums = _verify_checksums(directory, names)
        checksums["SHA256SUMS"] = _sha256(directory / "SHA256SUMS")
        _verify_attestations(directory, names, tag, commit)

        npm_name = f"marimo-team-marimo-export-{version}.tgz"
        _run("./scripts/publish-npm.sh", "--verify-only", str(directory / npm_name))
        python_directory = directory / "python"
        python_directory.mkdir()
        for name in names:
            if name.endswith((".whl", ".tar.gz")):
                shutil.copy2(directory / name, python_directory / name)
        pypi = _pypi_verifier()
        pypi.verify_release(python_directory, version, pypi._fetch_release(version))

    release_url = release.get("url")
    if not isinstance(release_url, str):
        raise RuntimeError(f"GitHub Release has no public URL: {tag}")
    return {
        "schema": "marimo-export.release-verification.v1",
        "version": version,
        "tag": tag,
        "commit": commit,
        "release_url": release_url,
        "workflows": {
            "ci": {"conclusion": "success", "url": ci["url"]},
            "release": {"conclusion": "success", "url": release_run["url"]},
        },
        "artifacts": [
            {
                "name": name,
                "sha256": checksums[name],
                "attestation": "verified",
            }
            for name in names
        ],
        "registries": {
            "npm": {"integrity": "verified", "version": version},
            "pypi": {"sha256": "verified", "version": version},
        },
        "fresh_installs": {
            "pnpm": {"conclusion": "success", "url": jobs["Verify npm packages"]["url"]},
            "python": {
                "conclusion": "success",
                "url": jobs["Verify Python package"]["url"],
            },
        },
    }


def _render_human(result: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"Verified marimo-export {result['tag']} at {result['commit']}.",
            f"Release: {result['release_url']}",
            f"CI: {result['workflows']['ci']['url']}",
            f"Publish: {result['workflows']['release']['url']}",
            f"Artifacts: {len(result['artifacts'])} checksummed and attested",
            "Registries: npm integrity and PyPI SHA-256 verified",
            "Fresh installs: pnpm and Python release jobs passed",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="./scripts/verify-release.sh", description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--json", action="store_true", help="write one verification object")
    arguments = parser.parse_args()
    if VERSION.fullmatch(arguments.version) is None:
        parser.error(f"version must use final X.Y.Z form: {arguments.version}")
    try:
        result = verify_public_release(arguments.version)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"ERROR: {error}\n")
    if arguments.json:
        json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        print(_render_human(result))


if __name__ == "__main__":
    main()
