#!/usr/bin/env bash
set -euo pipefail

verify_only=0
if [[ "${1:-}" == "--verify-only" ]]; then
	verify_only=1
	shift
fi

if [[ "$#" -ne 1 ]]; then
	printf 'Usage: ./scripts/publish-npm.sh [--verify-only] PACKAGE.tgz\n' >&2
	exit 2
fi

tarball="$1"
if [[ ! -f "$tarball" ]]; then
	printf 'ERROR: npm package tarball is missing: %s\n' "$tarball" >&2
	exit 1
fi
tarball="$(cd "$(dirname "$tarball")" && pwd)/$(basename "$tarball")"
npm_workdir="$(mktemp -d)"
cleanup() {
	rmdir "$npm_workdir" 2>/dev/null || true
}
trap cleanup EXIT
cd "$npm_workdir"

package_identity="$(
	# shellcheck disable=SC2016
	tar -xOf "$tarball" package/package.json | node -e '
let source = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { source += chunk; });
process.stdin.on("end", () => {
  const manifest = JSON.parse(source);
  process.stdout.write(`${manifest.name}\n${manifest.version}\n`);
});
'
)"
{
	IFS= read -r name
	IFS= read -r version
} <<<"$package_identity"
if [[ -z "$name" || -z "$version" ]]; then
	printf 'ERROR: Could not read npm package identity from %s\n' "$tarball" >&2
	exit 1
fi

local_integrity="sha512-$(openssl dgst -sha512 -binary "$tarball" | openssl base64 -A)"
if published_integrity="$(pnpm view "$name@$version" dist.integrity 2>/dev/null)"; then
	if [[ "$published_integrity" != "$local_integrity" ]]; then
		printf 'ERROR: npm already contains different bytes for %s@%s\n' "$name" "$version" >&2
		exit 1
	fi
	printf 'npm already contains the verified %s@%s artifact.\n' "$name" "$version"
	exit 0
fi

if [[ "$verify_only" == "1" ]]; then
	printf 'ERROR: npm package is not published: %s@%s\n' "$name" "$version" >&2
	exit 1
fi

# npm trusted publishing performs its OIDC exchange inside the npm publish command.
npm publish "$tarball" --access public
