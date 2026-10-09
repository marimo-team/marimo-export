#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 || ! "$2" =~ ^[0-9a-f]{40}$ || -z "${GH_REPO:-}" || -z "${PREVIEW_PREDICATE_TYPE:-}" ]]; then
	printf 'ERROR: Usage: GH_REPO=owner/repo PREVIEW_PREDICATE_TYPE=uri %s <dist-directory> <40-character-commit-sha>\n' "$0" >&2
	exit 2
fi

dist="$1"
commit="$2"
shopt -s nullglob
wheels=("$dist"/python/marimo_export-*.whl)
if [[ "${#wheels[@]}" -ne 1 || ! "$(basename "${wheels[0]}")" =~ ^marimo_export-([0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+)-py3-none-any\.whl$ ]]; then
	printf 'ERROR: Expected one marimo-export preview wheel\n' >&2
	exit 1
fi
version="${BASH_REMATCH[1]}"
npm_version="${version/.dev/-dev.}"
artifacts=(
	"${wheels[0]}"
	"$dist/python/marimo_export-$version.tar.gz"
	"$dist/npm/marimo-team-marimo-export-$npm_version.tgz"
	"$dist/marimo-export-$version-SHA256SUMS"
)
for artifact in "${artifacts[@]}"; do
	if [[ ! -f "$artifact" ]]; then
		printf 'ERROR: Missing preview artifact: %s\n' "$artifact" >&2
		exit 1
	fi
done

tag=preview
retained=30
server="${GITHUB_SERVER_URL:-https://github.com}"
release_url="$server/$GH_REPO/releases/tag/$tag"
downloads="$server/$GH_REPO/releases/download/$tag"
wheel_url="$downloads/$(basename "${wheels[0]}")"
npm_url="$downloads/marimo-team-marimo-export-$npm_version.tgz"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
mkdir "$temporary/checksums"
for artifact in "${artifacts[@]:0:3}"; do
	cp "$artifact" "$temporary/checksums/"
done
manifest="$(realpath "${artifacts[3]}")"
(cd "$temporary/checksums" && sha256sum --check "$manifest")

# The tag stays on the first preview commit; versioned asset URLs identify builds.
if ! gh release view "$tag" >/dev/null 2>&1; then
	gh release create "$tag" --prerelease --latest=false --target "$commit" \
		--title "Preview builds" --notes "Packages built from main."
fi
assets() {
	gh release view "$tag" --json assets --jq '.assets[].name'
}
uploaded_assets() {
	gh release view "$tag" --json assets --jq '.assets[] | select(.state == "uploaded" and .size > 0) | .name'
}
published="$(uploaded_assets)"
starters="$(gh release view "$tag" --json assets --jq '.assets[] | select(.state == "starter") | .name')"
upload() {
	local artifact="$1" name
	name="$(basename "$artifact")"
	# GitHub can leave an empty starter asset after a failed upload. It is not
	# published content and must be removed before retrying the same name.
	if grep -qxF "$name" <<<"$starters"; then
		gh release delete-asset "$tag" "$name" --yes
	fi
	if grep -qxF "$name" <<<"$published"; then
		gh release download "$tag" --pattern "$name" --dir "$temporary"
		if ! cmp -s "$artifact" "$temporary/$name"; then
			printf 'ERROR: Published preview bytes differ: %s\n' "$name" >&2
			exit 1
		fi
	else
		gh release upload "$tag" "$artifact"
	fi
}
for artifact in "${artifacts[@]:0:3}"; do
	upload "$artifact"
done

listing="$(assets)"
published="$(uploaded_assets)"
package_names() {
	printf '%s\n' "marimo_export-$1-py3-none-any.whl" "marimo_export-$1.tar.gz" \
		"marimo-team-marimo-export-${1/.dev/-dev.}.tgz"
}
# Discover interrupted builds too, so old orphan assets cannot accumulate.
versions="$(sed -nE \
	-e 's/^marimo_export-([0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+)-(py3-none-any\.whl)$/\1/p' \
	-e 's/^marimo_export-([0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+)\.tar\.gz$/\1/p' \
	-e 's/^marimo-export-([0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+)-SHA256SUMS$/\1/p' \
	-e 's/^marimo-team-marimo-export-([0-9]+\.[0-9]+\.[0-9]+)-dev\.([0-9]+)\.tgz$/\1.dev\2/p' \
	<<<"$listing" | sort -ruV)"
builds="$(while IFS= read -r candidate; do
	available=true
	while IFS= read -r name; do
		if ! grep -qxF "$name" <<<"$published"; then
			available=false
		fi
	done < <(package_names "$candidate")
	if [[ "$available" == true ]]; then
		printf '%s\n' "$candidate"
	fi
done <<<"$versions")"
# A complete set of packages is available even if its announcement failed.
# Include it when choosing the newest links and the retention window.
if [[ "$(head -n 1 <<<"$builds")" == "$version" ]]; then
	cat >"$temporary/notes.md" <<EOF
Packages built from \`main\` after CI and GitHub Pages pass for the same commit. The newest is marimo-export \`$version\` from [\`${commit:0:7}\`]($server/$GH_REPO/commit/$commit).

Install the Python package:

\`\`\`console
uv pip install "marimo-export @ $wheel_url"
\`\`\`

Install the CLI as a tool:

\`\`\`console
uv tool install --force "marimo-export @ $wheel_url"
\`\`\`

Install the matching browser package:

\`\`\`console
pnpm add "$npm_url"
\`\`\`

For a notebook with inline script metadata, declare \`marimo-export @ $wheel_url\` as a dependency. Downstream CI can pin these URLs to test this exact build.

This release keeps the newest $retained matching package builds. Install from PyPI and npm for released versions. The preview tag stays on its original commit; the versioned assets and provenance identify each build.

Download the wheel's attestation, then replace \`BUNDLE_FILE\` with the \`.jsonl\` filename printed by \`gh\`:

\`\`\`console
gh attestation download $(basename "${wheels[0]}") -R $GH_REPO
gh attestation verify $(basename "${wheels[0]}") -R $GH_REPO \\
  --bundle BUNDLE_FILE \\
  --predicate-type "$PREVIEW_PREDICATE_TYPE" \\
  --signer-workflow "$GH_REPO/.github/workflows/publish.yml"
\`\`\`

The signed preview predicate's \`buildDefinition.externalParameters.checkoutCommit\` identifies the packaged source commit. Its resolved dependencies also record the signing workflow commit, which can be newer.
EOF
	gh release edit "$tag" --notes-file "$temporary/notes.md"
fi

stale=""
if [[ "$(wc -l <<<"$builds")" -ge "$retained" ]]; then
	cutoff="$(head -n "$retained" <<<"$builds" | tail -n 1)"
	stale="$(awk -v cutoff="$cutoff" 'older { print } $0 == cutoff { older = 1 }' <<<"$versions")"
fi

# Old runs can finish after thirty newer builds. Do not announce pruned URLs.
if ! grep -qxF "$version" <<<"$stale"; then
	pull="$(gh api "repos/$GH_REPO/commits/$commit/pulls" \
		--jq '[.[] | select(.merged_at != null and .base.ref == "main")][0].number // empty')"
	if [[ -n "$pull" ]]; then
		comments="$(gh api --paginate "repos/$GH_REPO/issues/$pull/comments" --jq '.[].body')"
		if ! grep -qF "$wheel_url" <<<"$comments"; then
			cat >"$temporary/comment.md" <<EOF
marimo-export \`$version\` from this pull request is available as a [preview build]($release_url):

\`\`\`console
uv pip install "marimo-export @ $wheel_url"
pnpm add "$npm_url"
\`\`\`
EOF
			gh pr comment "$pull" --body-file "$temporary/comment.md"
		fi
	fi
fi

# Announce before pruning, so an announcement failure preserves older builds.
while IFS= read -r old_version; do
	if [[ -n "$old_version" ]]; then
		while IFS= read -r name; do
			if grep -qxF "$name" <<<"$listing"; then
				gh release delete-asset "$tag" "$name" --yes
			fi
		done < <(package_names "$old_version"; printf '%s\n' "marimo-export-$old_version-SHA256SUMS")
	fi
done <<<"$stale"

if grep -qxF "$version" <<<"$stale"; then
	printf 'Preview %s is outside the newest %s builds; its assets were removed.\n' "$version" "$retained"
else
	upload "${artifacts[3]}"
	printf 'Published %s and %s\n' "$wheel_url" "$npm_url"
fi
