# Validation

Use the narrowest package command while developing and the root gate before
handoff.

## Root gate

```bash
make check
```

The gate checks:

- Markdown, JSON, TypeScript, and Python formatting
- Vite+ and Ruff lint
- TypeScript and Python types
- Python unit and integration contracts
- browser core and loader tests
- cross-language canonical fixtures
- every package and application build
- packed Python and npm entry points

## Python contracts

Python tests cover:

- strict ExportSpec JSON, YAML, and programmatic construction
- sparse-state normalization and sibling definitions
- ordinary and UI input application
- marimo capability probing
- managed autorun caching, process ownership, and SSE shutdown ordering
- state execution, notebook-cache activity, and output receipts
- phase and state-run timing records
- child teardown, source identity, and parent preservation
- transfer integrity and atomic export writes
- local export filesystem safety
- CLI human and JSON results, errors, and exit categories

## TypeScript contracts

TypeScript tests cover:

- canonical export parsing
- exact state resolution
- native codec framing and asset integrity
- loader matching and cancellation
- NumPy, Arrow, and Parquet decoding
- AnyWidget model behavior and disposal
- Vega-Lite mounting and finalization
- public package entry points

## Cross-language contracts

Python produces the canonical `ExportIndex` fixture consumed by the browser
tests. Both languages use the same canonical JSON cases, schema identifier,
state fingerprints, codec descriptors, media types, and error conditions.

## Runtime evidence

Use the live vanilla Vite example for the complete producer-to-browser path:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run verify:export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Browser inspection proves rendering, state transitions, cancellation, and
mount disposal. Unit tests do not substitute for that evidence.
