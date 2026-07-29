.DEFAULT_GOAL := check

.PHONY: bootstrap format lint typecheck test build check

FORMAT_PATHS := \
	.github \
	apps \
	development_docs \
	docs \
	examples \
	packages \
	scripts \
	AGENTS.md \
	CLAUDE.md \
	README.md \
	package.json \
	pnpm-workspace.yaml \
	pyproject.toml \
	tsconfig.base.json \
	vite.config.ts
LINT_PATHS := apps examples packages vite.config.ts

format:
	pnpm exec vp fmt $(FORMAT_PATHS)
	uv run ruff format packages/python scripts

bootstrap:
	uv sync --all-packages --all-groups --all-extras
	pnpm install --frozen-lockfile

lint:
	pnpm exec vp lint --deny-warnings $(LINT_PATHS)
	uv run ruff check packages/python scripts

typecheck:
	pnpm exec vp run -r typecheck
	uv run ty check packages/python

test:
	pnpm exec vp run -r test
	uv run --group test --all-extras pytest -q packages/python/tests

build:
	pnpm exec vp run -r build
	uv build --package marimo-export --clear --no-sources

check:
	pnpm exec vp fmt --check $(FORMAT_PATHS)
	uv run ruff format --check packages/python scripts
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
