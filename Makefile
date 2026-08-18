SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: help bootstrap format lint typecheck test build docs-build docs-serve check

FORMAT_PATHS := \
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
	README.md \
	package.json \
	pnpm-workspace.yaml \
	pyproject.toml \
	tsconfig.base.json \
	vite.config.ts
LINT_PATHS := apps examples packages vite.config.ts
PYTHON_PATHS := packages/python scripts skills

help: ## List development targets.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

format: ## Format Python, TypeScript, and documentation source.
	pnpm exec vp fmt $(FORMAT_PATHS)
	uv run ruff format $(PYTHON_PATHS)

bootstrap: ## Install the locked Python and TypeScript workspaces.
	uv sync --all-packages --all-groups --all-extras --locked
	pnpm install --frozen-lockfile

lint: ## Check Python and TypeScript source.
	pnpm exec vp lint --deny-warnings $(LINT_PATHS)
	uv run ruff check $(PYTHON_PATHS)

typecheck: ## Type-check every Python and TypeScript package.
	pnpm exec vp run -r typecheck
	uv run --group test ty check packages/python

test: ## Run Python, browser core, loader, skill, and example tests.
	pnpm exec vp run -r test
	uv run --group test --all-extras pytest -q \
		packages/python/tests \
		skills/notebook-to-static-app/tests

build: ## Build Python, npm, docs, and example packages.
	pnpm exec vp run -r build
	test -s apps/docs/.vitepress/dist/llms.txt
	test -s apps/docs/.vitepress/dist/llms-full.txt
	uv build --package marimo-export --clear --no-sources

docs-build: ## Build the public documentation site.
	pnpm --filter @marimo-team/marimo-export-docs build
	test -s apps/docs/.vitepress/dist/llms.txt
	test -s apps/docs/.vitepress/dist/llms-full.txt

docs-serve: ## Serve public documentation at http://127.0.0.1:4173/.
	pnpm --filter @marimo-team/marimo-export-docs dev

check: ## Run the complete local quality gate.
	pnpm exec vp fmt --check $(FORMAT_PATHS)
	uv run ruff format --check $(PYTHON_PATHS)
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory typecheck
	@$(MAKE) --no-print-directory test
	@$(MAKE) --no-print-directory build
	pnpm --filter @marimo-team/marimo-export test:package
	@set -eu; \
		wheel=$$(printf '%s\n' ./dist/marimo_export-*.whl); \
		test -f "$$wheel"; \
		uv run --isolated --no-project --with "$$wheel" python scripts/smoke_python_package.py; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --help >/dev/null; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --version >/dev/null
