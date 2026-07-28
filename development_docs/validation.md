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
- child-local execution and native cache receipts
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

## Live finance acceptance

```bash
make acceptance-finance \
  FINANCE_NOTEBOOK=/Users/petergy/Downloads/finance.py
```

The live gate uses Yahoo Finance and the source notebook's declared
dependencies. It proves:

- the source notebook remains byte-identical
- a running kernel accepts a hash-pinned wheel over loopback HTTP
- code mode authors visible Exporter cells into the acceptance copy
- borrowed capture completes cold and warm
- managed build completes cold and warm
- each warm run restores native marimo cache entries
- six states produce seven outputs
- Python and browser verify every representation
- a static server supports relocation, interactions, cancellation, and disposal
- browser network traffic reaches static application and publication files
- owned Python processes and sockets exit

The gate writes one bounded `acceptance.json` into its private run workspace.
Provider failure fails the run.
