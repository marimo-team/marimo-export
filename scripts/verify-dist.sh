#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

version="$(uv version --package marimo-export --short)"
python_dist="$root/dist/python"
npm_dist="$root/dist/npm"

shopt -s nullglob
wheels=(
	"$python_dist"/marimo_export-"$version"-*.whl
	"$python_dist"/from-sdist/marimo_export-"$version"-*.whl
)
if [[ "${#wheels[@]}" -ne 2 ]]; then
	printf 'ERROR: Expected direct and source-rebuilt marimo-export wheels\n' >&2
	exit 1
fi

uv run python scripts/verify_release_artifacts.py

export UV_NO_CONFIG=1
for wheel in "${wheels[@]}"; do
	uv run --no-project --isolated --no-cache --with "$wheel" \
		python scripts/smoke_python_package.py
	uv run --no-project --isolated --no-cache --with "$wheel" \
		marimo-export --help >/dev/null
	actual="$(
		uv run --no-project --isolated --no-cache --with "$wheel" \
			marimo-export --version
	)"
	if [[ "$actual" != "marimo-export $version" ]]; then
		printf 'ERROR: Unexpected marimo-export version output: %s\n' "$actual" >&2
		exit 1
	fi
done

node scripts/smoke_npm_packages.mjs \
	"$npm_dist/marimo-team-portable-json-$version.tgz" \
	"$npm_dist/marimo-team-marimo-export-$version.tgz" \
	"$version"
