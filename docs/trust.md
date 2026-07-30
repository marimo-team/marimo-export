# Deploy an export safely

`build` and `capture` execute notebook code with access to its files,
credentials, network, and installed packages. Review the notebook, custom
exporters, and open capture session before creating an export.

Pin the notebook environment and data inputs when you need reproducible builds.

## Verify before deployment

```bash
marimo-export verify dist/finance
```

The Python and browser readers also verify assets before returning them.

## Review browser code

AnyWidget, Vega-Lite, and custom loaders may execute JavaScript or load external
resources with the same authority as the page.

- review widget code and custom loaders
- configure Content Security Policy and cross-origin access
- cap large output loads
- cancel stale state transitions
- dispose replaced charts and widgets

## Serve the export

Serve the directory over HTTPS or localhost. Pass `openExport()` the directory
URL that contains `index.json`.

Assets use content-based filenames and can receive immutable cache headers.
Choose an `index.json` cache policy that matches your deployment cadence.
