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

export UV_NO_CONFIG=1

verified=0
for ((attempt = 1; attempt <= 18; attempt++)); do
	if uv run --no-cache --no-project --isolated \
		python scripts/verify_pypi_artifacts.py "$version"; then
		verified=1
		break
	fi

	printf 'PyPI verification attempt %s of 18 did not find the exact marimo-export %s artifacts\n' \
		"$attempt" "$version"
	if [[ "$attempt" -lt 18 ]]; then
		sleep 10
	fi
done

if [[ "$verified" != "1" ]]; then
	printf 'ERROR: PyPI did not serve the exact marimo-export %s artifacts within three minutes\n' \
		"$version" >&2
	exit 1
fi

python_run=(
	uv run
	--no-cache
	--no-project
	--isolated
	--refresh-package marimo-export
	--default-index https://pypi.org/simple
	--with "marimo-export==$version"
)

RELEASE_VERSION="$version" "${python_run[@]}" \
	python -c "import os; from importlib.metadata import version; assert version('marimo-export') == os.environ['RELEASE_VERSION']"
"${python_run[@]}" python scripts/smoke_python_package.py
actual="$("${python_run[@]}" marimo-export --version)"
if [[ "$actual" != "marimo-export $version" ]]; then
	printf 'ERROR: Unexpected marimo-export version output: %s\n' "$actual" >&2
	exit 1
fi
