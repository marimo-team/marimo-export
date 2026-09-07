"""Verify one published marimo-export release from source commit to registry bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, TypeGuard

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


def _workflow_run(
    workflow: str,
    commit: str,
    *,
    branch: str | None = None,
) -> dict[str, Any]:
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


def _positive_integer(value: object) -> TypeGuard[int]:
    return type(value) is int and value > 0


def _run_details(run_id: int) -> dict[str, Any]:
    value = _json_command("gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}")
    if not isinstance(value, dict) or value.get("id") != run_id:
        raise RuntimeError(f"release workflow returned an invalid run: {run_id}")
    return value


def _require_run_identity(run: dict[str, Any], *, event: str, branch: str, commit: str) -> None:
    if (
        run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or run.get("path") != ".github/workflows/publish.yml"
        or run.get("event") != event
        or run.get("head_branch") != branch
        or run.get("head_sha") != commit
        or run.get("status") != "completed"
        or not _positive_integer(run.get("run_attempt"))
    ):
        raise RuntimeError("release run does not match its repository, event, ref, and commit")


def _run_jobs(run_id: int) -> list[dict[str, Any]]:
    pages = _json_command(
        "gh",
        "api",
        "--paginate",
        "--slurp",
        f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?filter=all&per_page=100",
    )
    if not isinstance(pages, list) or any(
        not isinstance(page, dict) or not isinstance(page.get("jobs"), list) for page in pages
    ):
        raise RuntimeError(f"release run {run_id} returned invalid jobs")
    records = [record for page in pages for record in page["jobs"]]
    if any(not isinstance(record, dict) for record in records):
        raise RuntimeError(f"release run {run_id} returned invalid jobs")
    return records


def _latest_jobs(run_id: int) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in _run_jobs(run_id):
        name = job.get("name")
        if not isinstance(name, str) or not _positive_integer(job.get("run_attempt")):
            raise RuntimeError(f"release run {run_id} returned invalid job identity")
        grouped.setdefault(name, []).append(job)
    selected: dict[str, dict[str, Any]] = {}
    for name, jobs in grouped.items():
        attempt = max(job["run_attempt"] for job in jobs)
        latest = [job for job in jobs if job["run_attempt"] == attempt]
        if len(latest) != 1:
            raise RuntimeError(f"release run {run_id} has ambiguous latest job: {name}")
        selected[name] = {**latest[0], "url": latest[0].get("html_url")}
    return selected


def _require_jobs(jobs: dict[str, dict[str, Any]], names: tuple[str, ...]) -> None:
    failures = [
        name
        for name in names
        if jobs.get(name, {}).get("status") != "completed"
        or jobs.get(name, {}).get("conclusion") != "success"
    ]
    if failures:
        raise RuntimeError(f"release jobs did not pass: {', '.join(failures)}")


def _release_jobs(run_id: int, *, recovery: bool = False) -> dict[str, dict[str, Any]]:
    jobs = _latest_jobs(run_id)
    required = (*RELEASE_JOBS, "Release gate") if "Release gate" in jobs else RELEASE_JOBS
    if recovery:
        required = tuple(name for name in required if name != "Attest build provenance")
        if "Release gate" not in required:
            required = (*required, "Release gate")
        attest = jobs.get("Attest build provenance", {})
        if attest.get("status") != "completed" or attest.get("conclusion") != "skipped":
            raise RuntimeError("recovery must retain the original attestation")
    _require_jobs(jobs, required)
    return jobs


def _read_recovery_receipt(path: Path) -> dict[str, Any]:
    state = path.lstat()
    if not stat.S_ISREG(state.st_mode) or state.st_size > 65_536:
        raise RuntimeError("recovery receipt is not a bounded regular file")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("recovery receipt contains invalid JSON") from error
    if not isinstance(receipt, dict):
        raise RuntimeError("recovery receipt must contain one JSON object")
    return receipt


def _require_recovery_chain(
    receipt: dict[str, Any],
    recovery: dict[str, Any],
    source: dict[str, Any],
    version: str,
    commit: str,
) -> None:
    expected = {
        "schema": 1,
        "version": version,
        "tag": f"v{version}",
        "commit": commit,
        "repository": REPOSITORY,
        "workflow": ".github/workflows/publish.yml",
        "source_run": source["id"],
        "recovery_run": recovery["id"],
        "recovery_commit": recovery["head_sha"],
        "recovery_ref": "refs/heads/main",
    }
    if set(receipt) != {
        *expected,
        "source_attempt",
        "recovery_attempt",
        "artifact_id",
        "attestation_attempt",
        "checksums_sha256",
    } or any(
        type(receipt.get(key)) is not type(value) or receipt[key] != value
        for key, value in expected.items()
    ):
        raise RuntimeError("recovery receipt does not identify the exact release source chain")
    if (
        not _positive_integer(receipt.get("source_attempt"))
        or receipt["source_attempt"] > source["run_attempt"]
        or not _positive_integer(receipt.get("recovery_attempt"))
        or receipt["recovery_attempt"] > recovery["run_attempt"]
        or not _positive_integer(receipt.get("artifact_id"))
        or not _positive_integer(receipt.get("attestation_attempt"))
        or receipt["attestation_attempt"] > receipt["source_attempt"]
        or not isinstance(receipt.get("checksums_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["checksums_sha256"]) is None
    ):
        raise RuntimeError("recovery receipt has invalid artifact or attestation identity")


def _release_evidence(
    version: str,
    commit: str,
    recovery_run: int | None = None,
    *,
    receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any] | None]:
    if receipt is None:
        if recovery_run is not None:
            raise RuntimeError("the release has no recovery receipt for the selected run")
        original = _workflow_run("publish.yml", commit, branch=f"v{version}")
        source_id = original.get("databaseId")
        if not _positive_integer(source_id):
            raise RuntimeError("release workflow returned an invalid run ID")
        source = _run_details(source_id)
        _require_run_identity(source, event="push", branch=f"v{version}", commit=commit)
        if source.get("conclusion") != "success":
            raise RuntimeError("the tagged release workflow must complete successfully")
        return original, _release_jobs(source_id), None
    receipt_run = receipt.get("recovery_run")
    if not _positive_integer(receipt_run) or (
        recovery_run is not None and recovery_run != receipt_run
    ):
        raise RuntimeError("recovery receipt does not match the selected recovery run")
    recovery_run = receipt_run
    recovery = _run_details(recovery_run)
    harness_commit = recovery.get("head_sha")
    if not isinstance(harness_commit, str) or re.fullmatch(r"[0-9a-f]{40}", harness_commit) is None:
        raise RuntimeError("recovery run has an invalid harness commit")
    _require_run_identity(recovery, event="workflow_dispatch", branch="main", commit=harness_commit)
    if recovery.get("conclusion") != "success":
        raise RuntimeError("the matching recovery workflow must complete successfully")
    jobs = _release_jobs(recovery_run, recovery=True)
    harness_ci = _workflow_run("ci.yml", harness_commit, branch="main")
    source_id = receipt.get("source_run")
    if not _positive_integer(source_id):
        raise RuntimeError("recovery receipt has an invalid source run")
    source = _run_details(source_id)
    _require_run_identity(source, event="push", branch=f"v{version}", commit=commit)
    if recovery.get("display_title") != f"Recover v{version} from run {source_id}":
        raise RuntimeError("recovery run title does not match its release source chain")
    _require_jobs(_latest_jobs(source_id), ("Build and verify", "Attest build provenance"))
    _require_recovery_chain(receipt, recovery, source, version, commit)
    return (
        {"databaseId": recovery_run, "url": recovery["html_url"]},
        jobs,
        {**receipt, "source_url": source["html_url"], "harness_ci_url": harness_ci["url"]},
    )


def _expected_artifacts(
    version: str,
    assets: list[dict[str, Any]],
    *,
    recovery: bool = False,
) -> tuple[str, ...]:
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
        *({"release-recovery.json"} if recovery else set()),
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


def _verify_attestations(
    directory: Path,
    names: tuple[str, ...],
    tag: str,
    commit: str,
    *,
    source_run: int | None = None,
    attestation_attempt: int | None = None,
) -> None:
    for name in names:
        output = _run(
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

        if source_run is not None:
            expected = (
                f"https://github.com/{REPOSITORY}/actions/runs/{source_run}"
                f"/attempts/{attestation_attempt}"
            )
            try:
                verified = json.loads(output)
            except json.JSONDecodeError as error:
                raise RuntimeError("attestation verifier returned invalid JSON") from error
            if not isinstance(verified, list) or not any(
                isinstance(item, dict)
                and item.get("verificationResult", {})
                .get("signature", {})
                .get("certificate", {})
                .get("runInvocationURI")
                == expected
                for item in verified
            ):
                raise RuntimeError(f"release asset {name} has no verified original-run attestation")


def verify_public_release(version: str, *, recovery_run: int | None = None) -> dict[str, Any]:
    for command in ("gh", "node", "openssl", "pnpm", "tar"):
        if shutil.which(command) is None:
            raise RuntimeError(f"missing required command: {command}")

    tag = f"v{version}"
    commit = _tag_commit(tag)
    ci = _workflow_run("ci.yml", commit, branch="main")
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
    recovered = any(asset.get("name") == "release-recovery.json" for asset in release["assets"])
    downloaded_names = _expected_artifacts(version, release["assets"], recovery=recovered)
    names = tuple(name for name in downloaded_names if name != "release-recovery.json")

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
        if set(path.name for path in directory.iterdir()) != set(downloaded_names):
            raise RuntimeError("downloaded release assets do not match GitHub Release metadata")
        receipt = _read_recovery_receipt(directory / "release-recovery.json") if recovered else None
        release_run, jobs, recovery = _release_evidence(
            version,
            commit,
            recovery_run,
            receipt=receipt,
        )
        if recovery is not None:
            recovery["receipt_sha256"] = _sha256(directory / "release-recovery.json")
        checksums = _verify_checksums(directory, names)
        checksums["SHA256SUMS"] = _sha256(directory / "SHA256SUMS")
        if recovery is not None and checksums["SHA256SUMS"] != recovery["checksums_sha256"]:
            raise RuntimeError(
                "release checksum manifest differs from the recovered original bytes"
            )
        _verify_attestations(
            directory,
            names,
            tag,
            commit,
            source_run=recovery["source_run"] if recovery is not None else None,
            attestation_attempt=recovery["attestation_attempt"] if recovery is not None else None,
        )

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
        **({"recovery": recovery} if recovery is not None else {}),
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
    parser.add_argument("--recovery-run", type=int, help="select a recovery workflow run to verify")
    parser.add_argument("--json", action="store_true", help="write one verification object")
    arguments = parser.parse_args()
    if VERSION.fullmatch(arguments.version) is None:
        parser.error(f"version must use final X.Y.Z form: {arguments.version}")
    if arguments.recovery_run is not None and not _positive_integer(arguments.recovery_run):
        parser.error("recovery run must be a positive run ID")
    try:
        result = verify_public_release(arguments.version, recovery_run=arguments.recovery_run)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"ERROR: {error}\n")
    if arguments.json:
        json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        print(_render_human(result))


if __name__ == "__main__":
    main()
