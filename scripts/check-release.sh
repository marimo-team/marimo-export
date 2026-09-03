#!/usr/bin/env bash
set -euo pipefail

error() {
	printf 'ERROR: %s\n' "$1" >&2
}

require_env() {
	if [[ -z "${!1:-}" ]]; then
		error "Missing required environment variable: $1"
		exit 1
	fi
}

require_env GITHUB_REF_NAME
require_env GITHUB_REF
require_env GITHUB_SHA
require_env GH_TOKEN

if [[ ! "$GITHUB_REF_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	error "Release tag must use final-version form vX.Y.Z: $GITHUB_REF_NAME"
	exit 1
fi

version="$(uv version --package marimo-export --short)"
npm_versions="$(
	# shellcheck disable=SC2016
	node -e '
const { readFileSync } = require("node:fs");
for (const path of [
  "packages/browser/package.json",
  "packages/portable-json/package.json",
]) {
  process.stdout.write(`${JSON.parse(readFileSync(path, "utf8")).version}\n`);
}
'
)"
while IFS= read -r npm_version; do
	if [[ "$npm_version" != "$version" ]]; then
		error "Public package versions must match: Python $version, npm $npm_version"
		exit 1
	fi
done <<<"$npm_versions"

if [[ "v$version" != "$GITHUB_REF_NAME" ]]; then
	error "Package version $version does not match tag $GITHUB_REF_NAME"
	exit 1
fi

repository_visibility="$(gh repo view --json visibility --jq .visibility)"
if [[ "$repository_visibility" != "PUBLIC" ]]; then
	error "Releases require a public GitHub repository. Current visibility: $repository_visibility"
	exit 1
fi
for environment in npm pypi; do
	if ! gh api "repos/{owner}/{repo}/environments/$environment" --silent >/dev/null; then
		error "Missing required GitHub environment: $environment"
		exit 1
	fi
done

if [[ "$(git cat-file -t "$GITHUB_REF")" != tag ]]; then
	error "Release tag $GITHUB_REF_NAME must be annotated"
	exit 1
fi

tag_commit="$(git rev-parse --verify "$GITHUB_REF^{commit}")"
if [[ "$tag_commit" != "$GITHUB_SHA" ]]; then
	error "Release tag $GITHUB_REF_NAME must resolve to workflow commit $GITHUB_SHA"
	exit 1
fi

if ! git rev-parse --verify "refs/remotes/origin/main^{commit}" >/dev/null; then
	error "Release validation requires a freshly fetched origin/main"
	exit 1
fi
if ! git merge-base --is-ancestor "$tag_commit" refs/remotes/origin/main; then
	error "Release commit $tag_commit is not on origin/main"
	exit 1
fi

ci_run="$(gh run list \
	--workflow ci.yml \
	--branch main \
	--commit "$tag_commit" \
	--event push \
	--limit 1 \
	--json status,conclusion,url \
	--jq 'if length == 0 then "" else (.[0] | [.status, .conclusion, .url] | .[]) end')"

if [[ -z "$ci_run" ]]; then
	error "No main CI run found for release commit $tag_commit"
	exit 1
fi
{
	IFS= read -r ci_status
	IFS= read -r ci_conclusion
	IFS= read -r ci_url
} <<<"$ci_run"
ci_conclusion="${ci_conclusion:-pending}"
if [[ "$ci_status" != completed || "$ci_conclusion" != success ]]; then
	error "Main CI must pass for release commit $tag_commit: $ci_status/$ci_conclusion"
	printf 'CI run: %s\n' "$ci_url" >&2
	exit 1
fi
