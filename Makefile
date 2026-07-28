.DEFAULT_GOAL := check

.PHONY: bootstrap format format-check lint typecheck test build package-smoke acceptance-finance check

FORMAT_PATHS := \
	.github \
	apps \
	development_docs \
	docs \
	packages \
	scripts \
	tests \
	AGENTS.md \
	CLAUDE.md \
	README.md \
	package.json \
	pnpm-workspace.yaml \
	pyproject.toml \
	tsconfig.base.json \
	vite.config.ts
LINT_PATHS := apps packages vite.config.ts

format:
	pnpm exec vp fmt $(FORMAT_PATHS)
	uv run ruff format packages/python scripts tests

format-check:
	pnpm exec vp fmt --check $(FORMAT_PATHS)
	uv run ruff format --check packages/python scripts tests

bootstrap:
	uv sync --all-groups --all-extras
	pnpm install --frozen-lockfile

lint:
	pnpm exec vp lint --deny-warnings $(LINT_PATHS)
	uv run ruff check packages/python scripts tests

typecheck:
	pnpm exec vp run -r typecheck
	uv run ty check packages/python

test:
	pnpm exec vp run -r test
	uv run --group test --all-extras pytest -q packages/python/tests

build:
	pnpm exec vp run -r build
	uv build --package marimo-export --clear --no-sources

package-smoke: build
	pnpm --filter @marimo-team/marimo-export test:package
	@set -eu; \
		wheel=$$(printf '%s\n' ./dist/marimo_export-*.whl); \
		test -f "$$wheel"; \
		uv run --isolated --no-project --with "$$wheel" python scripts/smoke_python_package.py; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --help >/dev/null; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --version >/dev/null

acceptance-finance:
	@test -n "$(FINANCE_NOTEBOOK)" || (echo "FINANCE_NOTEBOOK must be an absolute notebook path" >&2; exit 2)
	uv run --group acceptance python tests/acceptance/finance/run.py "$(FINANCE_NOTEBOOK)" \
		--workdir "$(if $(FINANCE_WORKDIR),$(FINANCE_WORKDIR),$(CURDIR)/.acceptance/finance)" \
		--replace
	pnpm --filter @marimo-team/marimo-export-finance-demo acceptance -- \
		--workdir "$(if $(FINANCE_WORKDIR),$(FINANCE_WORKDIR),$(CURDIR)/.acceptance/finance)"

check: format-check lint typecheck test package-smoke
