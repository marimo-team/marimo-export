---
title: Market dashboard
description: Build and open the advanced Yahoo Finance example from the repository checkout.
---

# Market dashboard

The market dashboard is an advanced producer-to-browser example. It prepares
five states from a Yahoo Finance notebook, verifies the notebook export, then
opens a vanilla [TypeScript](https://www.typescriptlang.org/) application that consumes five output
representations.

The Notebook tab opens a static HTML export of the original marimo source and
its captured outputs. The Exported app tab reads five exported states from a
notebook export whose declared assets were verified during the documentation
build. The application starts no Python runtime, WebAssembly runtime, or
application server.

<StaticApp />

## Prerequisites

Run this example from a repository checkout with:

| Requirement                                                  | Role                                                     |
| ------------------------------------------------------------ | -------------------------------------------------------- |
| [Git](https://git-scm.com/)                                  | Clones the repository                                    |
| [Python 3.14](https://www.python.org/)                       | Runs the notebook and Python package                     |
| [uv](https://docs.astral.sh/uv/)                             | Installs locked Python dependencies and runs the CLI     |
| [Node.js](https://nodejs.org/)                               | Runs the version pinned by the workspace                 |
| [pnpm](https://pnpm.io/)                                     | Installs dependencies and runs the example scripts       |
| A POSIX shell and [Make](https://www.gnu.org/software/make/) | Run the repository targets                               |
| HTTPS access to the Python and npm package registries        | Downloads dependencies that are absent from local caches |
| HTTPS access to [Yahoo Finance](https://finance.yahoo.com/)  | Retrieves historical prices during preparation           |
| A local browser and loopback network access                  | Opens the URL printed by the development server          |

::: warning Live market data
The preparation run requests historical prices from Yahoo Finance. Network
availability and the returned market data can change the run. Use the
[deterministic quickstart](getting-started) for the first local workflow.
:::

## Build and open the application

Clone the repository if needed, then run the example:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Open the loopback URL printed by [Vite](https://vite.dev/), the development
server included in the locked workspace. The dashboard switches among five
exported states and loads [Parquet](https://parquet.apache.org/) rows, a
domain-specific market summary, a [Vega-Lite](https://vega.github.io/vega-lite/)
chart specification, a [PNG](https://www.w3.org/TR/png/) image, and an
[AnyWidget](https://anywidget.dev/) interactive value. The browser runs no
Python process.

The example connects three authored sources:

- [`finance.py`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.py)
  owns the analysis and notebook controls.
- [`finance.export.yaml`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.export.yaml)
  selects five states and five outputs.
- [`src/main.ts`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/src/main.ts)
  opens the verified export and owns browser state transitions, loading,
  rendering, and mount disposal.

The same notebook export can be inspected by an agent, opened from Python, or
loaded by another browser application. The dashboard is one consumer of the
published outputs.

## Inspect reuse

Build the matching export a second time:

```bash
pnpm run export
```

The [export repository](manage-repository) stores reusable prepared states.
The matching `ExportSpec`, producer identity, and state fingerprints let the
second build reuse the prepared export before notebook startup.

This second build demonstrates reuse, not market-data freshness. Exact reuse can
return before the notebook imports Yahoo Finance or requests data. A different
output directory also retains the same repository identity. Use the
[freshness boundary](build-and-capture#inspect-reuse-before-preparation) when a
new market-data retrieval is required.

## Capture the open notebook

Start the notebook from `examples/vite-vanilla`:

```bash
pnpm run notebook
```

After the notebook finishes loading, list its live sessions from another
terminal:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export inspect http://127.0.0.1:2718 --json
```

Copy the reported session ID into the capture command:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output public/export \
  --replace \
  --jsonl
```

`capture` replaces the same local export and leaves the selected notebook
session active. The dashboard consumes the same `index.json` contract from
either producer path.

Related: [Choose states and outputs](choose-states) for your own notebook.
[Read an export](consume-an-export) covers Python, browser, and agent consumers.
