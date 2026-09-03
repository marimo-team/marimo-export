SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: help bootstrap format lint typecheck test build docs-build docs-serve check package
.PHONY: _anti-slop-check

FORMAT_PATHS := \
	.pnpmfile.mjs \
	.github \
	apps \
	development_docs \
	docs \
	examples \
	packages \
	scripts \
	skills \
	AGENTS.md \
	CLAUDE.md \
	CONTRIBUTING.md \
	README.md \
	SECURITY.md \
	package.json \
	pnpm-workspace.yaml \
	pyproject.toml \
	tsconfig.base.json \
	vite.config.ts
LINT_PATHS := .pnpmfile.mjs apps examples packages vite.config.ts
PYTHON_PATHS := packages/python scripts skills
DIST_DIR := $(CURDIR)/dist
PYTHON_DIST_DIR := $(DIST_DIR)/python
NPM_DIST_DIR := $(DIST_DIR)/npm
PNPM_BIN := $(CURDIR)/node_modules/.bin
export PATH := $(PNPM_BIN):$(PATH)
# The local wrapper selects pnpm's managed Node runtime. A nested `pnpm exec`
# exposes a relative Node shim that child tools cannot use.
VP := $(PNPM_BIN)/vp

help: ## List development targets.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

format: ## Format Python, TypeScript, and documentation source.
	$(VP) fmt $(FORMAT_PATHS)
	uv run ruff format $(PYTHON_PATHS)

bootstrap: ## Install the locked Python and TypeScript workspaces.
	uv sync --all-packages --all-groups --all-extras --locked
	pnpm install --frozen-lockfile

_anti-slop-check:
	node --test --test-concurrency=1 tools/oxlint/anti-slop/test/*.test.ts tools/oxlint/anti-slop/test/compatibility/*.test.ts
	tsc -p tools/oxlint/anti-slop/tsconfig.json --noEmit

lint: _anti-slop-check ## Check Python and TypeScript source.
	$(VP) lint --deny-warnings $(LINT_PATHS)
	uv run ruff check $(PYTHON_PATHS)

typecheck: ## Type-check every Python and TypeScript package.
	$(VP) run -r typecheck
	uv run --group test ty check packages/python

test: ## Run Python, browser core, loader, skill, and example tests.
	$(VP) run -r test
	uv run --group test --all-extras pytest -q \
		packages/python/tests \
		skills/notebook-to-static-app/tests

build: ## Build Python, npm, docs, and example packages.
	$(VP) run -r build
	test -s apps/docs/.vitepress/dist/llms.txt
	test -s apps/docs/.vitepress/dist/llms-full.txt
	test -s apps/docs/.vitepress/dist/sitemap.xml
	uv build --package marimo-export --clear --no-sources

package: ## Build and verify Python and npm release artifacts.
	rm -rf "$(DIST_DIR)"
	mkdir -p "$(PYTHON_DIST_DIR)/from-sdist" "$(NPM_DIST_DIR)"
	pnpm --filter @marimo-team/portable-json build
	pnpm --filter @marimo-team/marimo-export build
	@set -eu; \
		version=$$(uv version --package marimo-export --short); \
		(cd packages/browser && pnpm --config.ignore-scripts=true pack \
			--out "$(NPM_DIST_DIR)/marimo-team-marimo-export-$$version.tgz")
	uv build --package marimo-export --out-dir "$(PYTHON_DIST_DIR)" --no-sources
	uvx twine check "$(PYTHON_DIST_DIR)"/*.whl "$(PYTHON_DIST_DIR)"/*.tar.gz
	uv build --wheel "$(PYTHON_DIST_DIR)"/*.tar.gz \
		--out-dir "$(PYTHON_DIST_DIR)/from-sdist"
	./scripts/verify-dist.sh

docs-build: ## Build the public documentation site.
	pnpm --filter @marimo-team/marimo-export-docs build
	test -s apps/docs/.vitepress/dist/llms.txt
	test -s apps/docs/.vitepress/dist/llms-full.txt
	test -s apps/docs/.vitepress/dist/sitemap.xml

docs-serve: ## Serve public documentation through Portless.
	BASE_PATH= $(VP) run --filter @marimo-team/marimo-export-docs dev

check: ## Run the complete local quality gate.
	$(VP) fmt --check $(FORMAT_PATHS)
	uv run ruff format --check $(PYTHON_PATHS)
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory typecheck
	@$(MAKE) --no-print-directory test
	@$(MAKE) --no-print-directory build
	pnpm --filter @marimo-team/portable-json test:package
	pnpm --filter @marimo-team/marimo-export test:package
	@set -eu; \
		wheel=$$(printf '%s\n' ./dist/marimo_export-*.whl); \
		test -f "$$wheel"; \
		uv run --isolated --no-project --with "$$wheel" python scripts/smoke_python_package.py; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --help >/dev/null; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --version >/dev/null
