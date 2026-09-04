#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

usage() {
	cat <<'EOF'
Usage: ./scripts/release.sh [--dry-run]

Releases the coordinated Python and npm package version at origin/main. The
command requires a clean checkout at that exact commit and successful CI. It
creates and pushes the annotated vX.Y.Z tag that starts trusted publishing.
EOF
}

error() {
	printf 'ERROR: %s\n' "$1" >&2
}

require_command() {
	if ! command -v "$1" >/dev/null 2>&1; then
		error "Missing required command: $1"
		exit 1
	fi
}

registry_status() {
	local package="$1"
	local url="$2"
	local status
	if ! status="$(
		curl --location --silent --show-error \
			--output /dev/null \
			--write-out '%{http_code}' \
			"$url"
	)"; then
		error "Could not query the registry for $package"
		return 1
	fi
	printf '%s\n' "$status"
}

require_existing_npm_package() {
	local package="$1"
	local url="$2"
	local status
	status="$(registry_status "$package" "$url")"
	case "$status" in
	200) ;;
	404)
		error "npm package must exist before trusted publishing can release it: $package"
		exit 1
		;;
	*)
		error "Registry query for $package returned HTTP $status"
		exit 1
		;;
	esac
}

require_unpublished() {
	local package="$1"
	local url="$2"
	local status
	status="$(registry_status "$package" "$url")"
	case "$status" in
	404) ;;
	200)
		error "Registry version already exists: $package"
		exit 1
		;;
	*)
		error "Registry query for $package returned HTTP $status"
		exit 1
		;;
	esac
}

dry_run=0
case "${1:-}" in
"") ;;
--dry-run) dry_run=1 ;;
-h | --help)
	usage
	exit 0
	;;
*)
	error "Unknown argument: $1"
	usage >&2
	exit 1
	;;
esac
if [[ "$#" -gt 1 ]]; then
	error "Expected at most one argument"
	exit 1
fi

for command in curl gh git node uv; do
	require_command "$command"
done

if [[ -n "$(git status --porcelain)" ]]; then
	error "The working tree must be clean"
	git status --short >&2
	exit 1
fi

git fetch origin main --tags
commit="$(git rev-parse HEAD)"
remote_commit="$(git rev-parse origin/main)"
if [[ "$commit" != "$remote_commit" ]]; then
	error "HEAD must match origin/main"
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

version="$(uv version --package marimo-export --short)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	error "Package version must use final X.Y.Z form: $version"
	exit 1
fi
manifest="packages/browser/package.json"
npm_version="$(node -p "require('./$manifest').version")"
if [[ "$npm_version" != "$version" ]]; then
	error "Public package versions must match: Python $version, $manifest $npm_version"
	exit 1
fi

tag="v$version"
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
	error "Release tag already exists: $tag"
	exit 1
fi
require_existing_npm_package \
	"@marimo-team/marimo-export" \
	"https://registry.npmjs.org/@marimo-team%2Fmarimo-export"
require_unpublished \
	"@marimo-team/marimo-export@$version" \
	"https://registry.npmjs.org/@marimo-team%2Fmarimo-export/$version"
require_unpublished \
	"marimo-export==$version" \
	"https://pypi.org/pypi/marimo-export/$version/json"

ci_run="$(gh run list \
	--workflow ci.yml \
	--branch main \
	--commit "$commit" \
	--event push \
	--limit 1 \
	--json databaseId,status,conclusion,url \
	--jq 'if length == 0 then "" else (.[0] | [.databaseId, .status, .conclusion, .url] | .[]) end')"
if [[ -z "$ci_run" ]]; then
	error "No main CI run found for $commit"
	exit 1
fi
{
	IFS= read -r ci_run_id
	IFS= read -r ci_status
	IFS= read -r ci_conclusion
	IFS= read -r ci_url
} <<<"$ci_run"
ci_conclusion="${ci_conclusion:-pending}"
if [[ "$ci_status" != "completed" || "$ci_conclusion" != "success" ]]; then
	error "Main CI must pass before releasing: $ci_status/$ci_conclusion"
	printf 'CI run: %s\n' "$ci_url" >&2
	printf 'Run gh run watch %s --exit-status, then retry.\n' "$ci_run_id" >&2
	exit 1
fi

repository_url="$(gh repo view --json url --jq .url)"
printf 'Release: %s\nCommit:  %s\nCI:      %s\n' "$tag" "$commit" "$ci_url"
if [[ "$dry_run" == "1" ]]; then
	printf '\nDry run complete. Run ./scripts/release.sh to create and push %s.\n' "$tag"
	exit 0
fi

git tag -a "$tag" -m "release: $version"
if ! git push origin "$tag"; then
	git tag -d "$tag" >/dev/null
	error "Failed to push $tag. The local tag was deleted."
	exit 1
fi

printf '\nRelease %s started.\n' "$tag"
printf 'Publish workflow: %s/actions/workflows/publish.yml\n' "$repository_url"
