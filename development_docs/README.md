# Contributor documentation

marimo-export is a uv and pnpm workspace for one Python producer, one browser
core, five representation loaders, a documentation app, and a live finance
acceptance app.

Read:

- [Architecture](architecture.md) before changing execution, cache receipts,
  publication bytes, or package boundaries.
- [Development](development.md) for setup, focused commands, and extension
  workflows.
- [Validation](validation.md) for the release gates and live finance evidence.

The root commands are:

```bash
make bootstrap
make format
make check
make build
make acceptance-finance FINANCE_NOTEBOOK=/absolute/path/finance.py
```
