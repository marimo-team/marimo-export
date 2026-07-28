.DEFAULT_GOAL := check

.PHONY: format format-check schemas schemas-check lint typecheck test integration build package-smoke check

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

format-check:
	pnpm exec vp fmt --check $(FORMAT_PATHS)
	uv run ruff format --check packages/python scripts

schemas:
	uv run --package marimo-export python scripts/generate_schemas.py

schemas-check:
	uv run --package marimo-export python scripts/generate_schemas.py --check

lint:
	pnpm exec vp lint --deny-warnings $(LINT_PATHS)
	uv run ruff check packages/python scripts

typecheck:
	pnpm exec vp run -r typecheck
	uv run ty check packages/python
	uv run pyrefly check

test:
	pnpm exec vp run -r test
	uv run --all-extras --package marimo-export pytest -q packages/python/tests

integration: build
	MARIMO_EXPORT_REMOTE_INTEGRATION=1 uv run --package marimo-export pytest -q packages/python/tests/test_integration.py

build:
	pnpm exec vp run -r build
	uv build --package marimo-export --clear --no-sources

package-smoke: build
	pnpm --filter @marimo-team/marimo-export test:package
	@wheel=$$(printf '%s\n' ./dist/marimo_export-*.whl); \
		test -f "$$wheel"; \
		uv run --isolated --no-project --with "$$wheel" python scripts/smoke_python_package.py; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --help >/dev/null; \
		uv run --isolated --no-project --with "$$wheel" marimo-export --version >/dev/null

check: format-check schemas-check lint typecheck test integration package-smoke
