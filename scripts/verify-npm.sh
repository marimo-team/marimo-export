#!/usr/bin/env bash
set -euo pipefail

version="${RELEASE_VERSION:-}"
if [[ -z "$version" ]]; then
	ref_name="${GITHUB_REF_NAME:-}"
	version="${ref_name#v}"
fi

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	printf 'ERROR: Release version must use final-version form X.Y.Z: %s\n' "$version" >&2
	exit 1
fi

portable="@marimo-team/portable-json@$version"
browser="@marimo-team/marimo-export@$version"
npm_dist="${NPM_DIST_DIR:-dist/npm}"
portable_tarball="$npm_dist/marimo-team-portable-json-$version.tgz"
browser_tarball="$npm_dist/marimo-team-marimo-export-$version.tgz"

for ((attempt = 1; attempt <= 18; attempt++)); do
	if [[ "$(npm view "$portable" version 2>/dev/null || true)" == "$version" ]] && \
		[[ "$(npm view "$browser" version 2>/dev/null || true)" == "$version" ]]; then
		./scripts/publish-npm.sh --verify-only "$portable_tarball"
		./scripts/publish-npm.sh --verify-only "$browser_tarball"
		node scripts/smoke_npm_packages.mjs "$portable" "$browser" "$version"
		exit 0
	fi

	printf 'npm verification attempt %s of 18 did not find both %s packages\n' \
		"$attempt" "$version"
	if [[ "$attempt" -lt 18 ]]; then
		sleep 10
	fi
done

printf 'ERROR: npm did not verify marimo-export %s within three minutes\n' "$version" >&2
exit 1
