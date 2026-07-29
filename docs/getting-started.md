# Getting started

Build a real Yahoo Finance notebook across six input states, then switch among
its static results from a vanilla Vite and TypeScript application. The browser
mounts an AnyWidget, an interactive Vega-Lite chart, a PNG, and decoded table
and array values after the notebook process exits.

The checked-in
[finance example](https://github.com/marimo-team/marimo-export/tree/main/examples/finance)
is both a uv workspace member and a pnpm workspace package. Its Python
dependencies include the local `marimo-export` package. Its browser
dependencies include the public npm package and the peers required by the
specialized loaders.

::: warning Live data
The build executes notebook-authored Python and requests market data from Yahoo
Finance. Review `examples/finance/finance.py` before running it. Yahoo Finance
availability and response data affect the build.
:::

## Install the workspaces

Install Git, uv, Node 22.18 or newer, and pnpm 11.15.1. Clone the repository and
install both workspaces:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
```

`make bootstrap` installs every uv workspace member and every pnpm workspace
package from the root lockfiles.

## Read the notebook contract

`examples/finance/finance.py` contains the finance notebook and ordinary
Exporter cells. The Exporter cells produce four browser representations:

```python
from marimo_export.exporters.altair import png, vegalite
from marimo_export.exporters.anywidget import bundle
from marimo_export.exporters.parquet import table

dashboard = bundle(widget)
chart_vegalite = vegalite(symbols_chart)
chart_png = png(symbols_chart, scale=2)
prices_parquet = table(df, compression="snappy", filename="prices.parquet")
```

The notebook also exposes `df`, `ohlc_matrix`, and `row_count` through marimo's
native Arrow, NumPy, and scalar cache codecs.

`examples/finance/finance.export.yaml` declares six sparse states and seven
outputs:

<<< ../examples/finance/finance.export.yaml

The input names address definitions in the marimo graph:

| Input              | Effect                                              |
| ------------------ | --------------------------------------------------- |
| `symbols`          | Yahoo Finance ticker universe                       |
| `interval`         | requested market interval                           |
| `start` and `end`  | requested date window                               |
| `chart_width`      | Altair chart width                                  |
| `symbols_selector` | frontend value of the marimo multiselect definition |

Every omitted value comes from the notebook baseline. marimo-export records the
complete normalized vector for each published state.

## Build and verify the publication

Run the package-level publication command from the repository root:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance run publish
```

The command executes:

```bash
uv run --locked --package marimo-export-finance-example \
  marimo-export build finance.py \
  --spec finance.export.yaml \
  --output public/publication \
  --replace
```

The command runs from `examples/finance`, so the paths resolve inside the
example package. A successful build reports:

```text
Published 6 states and 7 outputs to .../examples/finance/public/publication
```

Verify the static directory:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance \
  run verify:publication
```

`public/publication/index.json` contains complete input vectors, state
fingerprints, producer versions, cache codecs, media types, asset lengths, and
SHA-256 digests. The adjacent `assets` directory contains the unique payloads
referenced by the state and output relation.

## Run the Vite application

Start the browser app:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance dev
```

Open the local URL printed by Vite. The application:

1. opens `public/publication/index.json`
2. verifies every referenced asset
3. loads the `baseline` state
4. decodes the scalar, NumPy, Arrow, and Parquet outputs
5. mounts the AnyWidget, Vega-Lite, and PNG representations

Choose `focus`, `compact`, `narrow universe`, `short window`, or `weekly` to
load another complete state. Each transition aborts stale work, disposes the
current mounts, and mounts the newly selected outputs.

The app imports specialized loaders from the single public npm package:

```ts
import { imageLoader, openPublication, scalarLoader } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
```

Open the publication internals disclosure to inspect the complete input vector,
state fingerprint, codec, media type, and asset digest for every output.

Continue with [ExportSpec](export-spec.md) to define another state matrix or
[representations](representations.md) to add another exporter and browser
loader.
