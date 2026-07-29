# Validation

Use the narrowest command during development and the root gate before handoff.

## Root gate

```bash
make check
```

The gate checks:

- Markdown, JSON, TypeScript, and Python formatting
- Vite+ and Ruff lint
- TypeScript and ty
- Python unit and integration contracts
- browser core and loader tests
- cross-language canonical fixtures
- all package and app builds
- packed Python and browser entry points

## Contract suites

Python tests cover:

- strict ExportSpec decoding and programmatic parity
- state normalization and sibling packets
- marimo capability probing
- managed autorun caching, process ownership, and SSE shutdown ordering
- child-local execution, upstream cache activity, and projection receipts
- run-local phase and fresh-child timing records
- projection cleanup and parent preservation
- transfer integrity and atomic writes
- local publication filesystem safety
- CLI results, errors, and exit categories

TypeScript tests cover:

- canonical publication parsing
- exact state resolution
- native codec framing and integrity
- loader matching and cancellation
- NumPy, Arrow, and Parquet decoding
- AnyWidget model behavior and disposal
- Vega-Lite mount finalization
