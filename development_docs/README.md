# Contributor documentation

marimo-export is a uv and pnpm workspace for:

- one Python producer and local reader package
- one public browser package
- five private loader implementation packages
- one VitePress documentation app
- one vanilla Vite market dashboard

Start with:

- [Architecture](architecture.md) for execution, caching, wire format, and
  package boundaries
- [Development](development.md) for setup, focused commands, exporters, and
  loaders
- [Validation](validation.md) for contract suites and the root gate

The root workflow is:

```bash
make bootstrap
make format
make check
make build
```
