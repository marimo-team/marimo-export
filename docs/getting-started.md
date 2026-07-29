# Getting started

Build a Yahoo Finance notebook across five saved market views, then open its
results in a vanilla Vite dashboard. The dashboard changes its chart, table,
quote explorer, and shareable snapshot from published files.

The checked-in
[vanilla Vite example](https://github.com/marimo-team/marimo-export/tree/main/examples/vite-vanilla)
is a uv workspace member and a pnpm workspace package. Its Python environment
includes the local `marimo-export` package. Its browser package uses the public
npm entry points and the peer dependencies required by each loader.

::: warning Live data
The build executes notebook-authored Python and requests historical prices from
Yahoo Finance. Review `examples/vite-vanilla/finance.py` before running it.
Yahoo Finance availability and response data affect the build.
:::

## Install the workspaces

Install Git, uv, Node 22.18 or newer, and pnpm 11.15.1. Clone the repository
and install both workspaces:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
```

`make bootstrap` installs every uv workspace member and pnpm workspace package
from the root lockfiles.

## Read the notebook contract

`examples/vite-vanilla/finance.py` fetches the price history, filters the
selected watchlist, and creates four browser representations:

```python
market_explorer = bundle(quote_detail)
performance_chart = vegalite(performance)
performance_snapshot = png(performance, scale=2)
price_history = table(
    selected_prices,
    compression="snappy",
    filename="price-history.parquet",
)
```

`examples/vite-vanilla/finance.export.yaml` declares five sparse states and
four outputs:

<<< ../examples/vite-vanilla/finance.export.yaml

The input names address definitions in the marimo graph:

| Input              | Effect                                      |
| ------------------ | ------------------------------------------- |
| `interval`         | daily or weekly market interval             |
| `symbols_selector` | watchlist value from the marimo multiselect |

Every omitted value comes from the notebook baseline. marimo-export records the
complete normalized vector for each published state.

## Build and verify the publication

Run the package-level command from the repository root:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run publish
```

The script executes:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export build finance.py \
  --spec finance.export.yaml \
  --output public/publication \
  --replace
```

The script runs from `examples/vite-vanilla`, so the relative paths resolve
inside the example package. A successful build reports:

```text
Published 5 states and 4 outputs to .../examples/vite-vanilla/public/publication
```

Verify the generated directory:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla \
  run verify:publication
```

`public/publication/index.json` contains the complete input vectors, state
fingerprints, producer versions, cache codecs, media types, asset lengths, and
SHA-256 digests. Its adjacent `assets` directory contains the payloads
referenced by the state and output relation.

## Run the market dashboard

Start the Vite application:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Open the local URL printed by Vite. The application opens and verifies the
publication, loads the `baseline` state, and renders the **Leaders** view.

Choose **Cloud**, **AI buildout**, **All names**, or **Weekly** to load another
complete state. Each transition aborts stale work, disposes the current chart
and widget, mounts the selected outputs, and derives the headline metrics and
latest-close table from the Parquet rows.

The application imports each specialized loader from the public npm package:

```ts
import { imageLoader, openPublication } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
```

Continue with [ExportSpec](export-spec.md) to define another state matrix or
[representations](representations.md) to add an exporter and browser loader.
