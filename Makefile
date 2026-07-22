.DEFAULT_GOAL := check

.PHONY: format format-check lint typecheck test integration build package-smoke check

FORMAT_PATHS := \
	.github \
	apps \
	development_docs \
	docs \
	examples \
	packages \
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
	uv run ruff format packages/producer

format-check:
	pnpm exec vp fmt --check $(FORMAT_PATHS)
	uv run ruff format --check packages/producer

lint:
	pnpm exec vp lint --deny-warnings $(LINT_PATHS)
	uv run ruff check packages/producer

typecheck:
	pnpm exec vp run -r typecheck
	uv run ty check packages/producer
	uv run pyrefly check

test:
	pnpm exec vp run -r test
	uv run --package marimo-export pytest -q packages/producer/tests

integration: build
	MARIMO_EXPORT_REMOTE_INTEGRATION=1 pnpm --dir packages/client exec vp test tests/remote-server.integration.test.ts --run

build:
	pnpm exec vp run -r build
	uv build --package marimo-export

package-smoke: build
	pnpm --filter @marimo-team/marimo-export test:package

check: format-check lint typecheck test integration package-smoke
