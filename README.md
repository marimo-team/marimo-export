# marimo-export

Turn a marimo notebook into a deterministic, interactive web app served
from any CDN.

Precompute the states your audience can explore. Each state loads its fixed
result instantly from static files, with no Python server, kernel startup, or
Python WebAssembly runtime.

## Try it

This development preview runs from the repository:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run dev
```

Open the URL printed by Vite and switch between the five market views. The
export uses live Yahoo Finance data, so network availability can affect the
run.

## Use your notebook

[Choose the states and results](docs/export-spec.md), then build the export:

```bash
marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
```

Use [`capture`](docs/cli.md#capture-an-open-notebook) for a notebook that is
already running. Both commands leave the notebook source unchanged.

## Documentation

- [Run the market dashboard](docs/getting-started.md)
- [Choose states and results](docs/export-spec.md)
- [Build or capture](docs/cli.md)
- [Use the browser API](docs/browser-api.md) or [automate from
  Python](docs/python-api.md)
- [Choose output formats](docs/representations.md) and [deploy
  safely](docs/trust.md)

Licensed under Apache-2.0.
