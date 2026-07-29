# Getting started

Install marimo-export in the Python environment that runs the notebook:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

Add ordinary cells that convert rich Python objects into native `BlobAsset`
values:

```python
from marimo_export.exporters.altair import vegalite
from marimo_export.exporters.parquet import table

chart_asset = vegalite(chart)
prices_asset = table(prices, filename="prices.parquet")
```

Create `notebook.export.yaml`:

```yaml
schema: marimo-export.spec.v1
inputs:
  - symbols
  - chart_width
states:
  baseline: {}
  compact:
    chart_width: 480
  focus:
    symbols: [AAPL, MSFT]
outputs:
  chart:
    source: chart_asset
  prices:
    source: prices_asset
  row_count:
    source: row_count
```

Build the publication:

```bash
marimo-export build notebook.py \
  --spec notebook.export.yaml \
  --output dist/notebook

marimo-export verify dist/notebook
```

Serve the directory through any static HTTP host. The URL passed to the browser
client is the directory that contains `index.json`:

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const publication = await openPublication("/notebook/");
const state = publication.state("baseline");

const rows = await state.output("prices").load(parquetRowsLoader());
const chart = await state.output("chart").load(vegaLiteLoader());
const mounted = await chart.mount(document.querySelector("#chart")!);
```

Call `mounted.dispose()` before replacing the chart or removing its host.
