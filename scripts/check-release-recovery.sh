#!/usr/bin/env bash
set -euo pipefail

error() {
	printf 'ERROR: %s\n' "$1" >&2
}

require_command() {
	if ! command -v "$1" >/dev/null 2>&1; then
		error "Missing required command: $1"
		exit 1
	fi
}

if [[ "$#" -ne 2 ]]; then
	error "Usage: ./scripts/check-release-recovery.sh vX.Y.Z RUN_ID"
	exit 2
fi

tag="$1"
run_id="$2"
if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	error "Recovery tag must use final-version form vX.Y.Z: $tag"
	exit 1
fi
if [[ ! "$run_id" =~ ^[0-9]+$ ]]; then
	error "Recovery run ID must contain decimal digits: $run_id"
	exit 1
fi
if [[ "${GITHUB_REF_NAME:-}" != "main" ]]; then
	error "Release recovery must run from main"
	exit 1
fi
if [[ -z "${GITHUB_SHA:-}" || -z "${GH_TOKEN:-}" ]]; then
	error "Release recovery requires GITHUB_SHA and GH_TOKEN"
	exit 1
fi
for command in gh git jq node uv; do
	require_command "$command"
done

main_commit="$(git rev-parse refs/remotes/origin/main)"
if [[ "$GITHUB_SHA" != "$main_commit" ]]; then
	error "Release recovery commit must match origin/main"
	exit 1
fi
if [[ "$(git cat-file -t "refs/tags/$tag")" != tag ]]; then
	error "Release recovery requires an annotated tag: $tag"
	exit 1
fi
tag_commit="$(git rev-parse "refs/tags/$tag^{commit}")"
if ! git merge-base --is-ancestor "$tag_commit" refs/remotes/origin/main; then
	error "Release tag commit is not on origin/main: $tag_commit"
	exit 1
fi

version="${tag#v}"
python_version="$(uv version --package marimo-export --short)"
browser_version="$(node -p "require('./packages/browser/package.json').version")"
if [[ "$python_version" != "$version" || "$browser_version" != "$version" ]]; then
	error "Recovery tag $tag does not match current public package versions"
	exit 1
fi

run_json="$(gh run view "$run_id" --json name,event,headSha,status,conclusion,jobs,url)"
run_name="$(jq -r .name <<<"$run_json")"
run_event="$(jq -r .event <<<"$run_json")"
run_commit="$(jq -r .headSha <<<"$run_json")"
run_status="$(jq -r .status <<<"$run_json")"
run_url="$(jq -r .url <<<"$run_json")"
if [[ "$run_name" != "Release" || "$run_event" != "push" ]]; then
	error "Recovery artifacts must come from a tag-triggered Release workflow"
	exit 1
fi
if [[ "$run_commit" != "$tag_commit" ]]; then
	error "Recovery run commit $run_commit does not match $tag at $tag_commit"
	exit 1
fi
if [[ "$run_status" != "completed" ]]; then
	error "Recovery run must be complete: $run_url"
	exit 1
fi
for required_job in "Build and verify" "Attest build provenance"; do
	job_conclusion="$(
		jq -r --arg name "$required_job" \
			'[.jobs[] | select(.name == $name) | .conclusion] | if length == 1 then .[0] else "missing" end' \
			<<<"$run_json"
	)"
	if [[ "$job_conclusion" != "success" ]]; then
		error "Recovery source job did not pass: $required_job ($job_conclusion)"
		exit 1
	fi
done

printf 'Recovery: %s\nCommit:   %s\nRun:      %s\n' "$tag" "$tag_commit" "$run_url"
