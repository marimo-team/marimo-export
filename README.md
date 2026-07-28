# marimo-export

marimo-export executes a finite input matrix through marimo and publishes the
resulting native cache returns as a verified static directory. A browser can
load the publication after the notebook server and every Python process stop.

```bash
marimo-export build notebook.py \
  --spec notebook.export.yaml \
  --output dist/notebook
```

An ExportSpec names notebook definitions directly:

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
    source: prices
  row_count:
    source: row_count
```

Sparse state rows are completed from the live baseline. Each output runs
through a temporary marimo leaf cell for every state. marimo owns dependency
analysis, execution, cache identity, persistence, and cache restoration.
marimo-export validates the resulting scalar, NumPy, Arrow, or `BlobAsset`
return and writes one canonical `index.json`.

Use authored Exporter functions when a Python object needs a browser
representation:

```python
from marimo_export.exporters.altair import png, vegalite
from marimo_export.exporters.parquet import table

chart_json = vegalite(chart)
chart_image = png(chart, scale=2)
prices_file = table(prices, filename="prices.parquet")
```

These calls belong in ordinary cached marimo cells. They return marimo's native
`BlobAsset`, so their execution and persistence stay visible to the notebook.

## Capture an existing session

`capture` attaches to a live kernel that already has its environment, secrets,
files, and expensive results:

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec notebook.export.yaml \
  --output dist/notebook
```

The kernel environment must import the same marimo-export version as the
client. `MARIMO_EXPORT_ACCESS_TOKEN` and `MARIMO_EXPORT_SERVER_TOKEN` provide
credentials without placing them in command history.

## Load the static publication

Python can inspect or verify a local publication:

```python
from marimo_export import open_publication

publication = open_publication("dist/notebook")
state = publication.state("baseline")
rows = state.output("row_count").scalar()
publication.verify()
```

TypeScript loads outputs through explicit codec-aware loaders:

```ts
import { openPublication, scalarLoader } from "@marimo-team/marimo-export";
import { arrowTableLoader } from "@marimo-team/marimo-export-loader-arrow";

const publication = await openPublication("/publications/notebook/");
const state = publication.state("baseline");
const rows = await state.output("row_count").load(scalarLoader());
const prices = await state.output("prices").load(arrowTableLoader());
```

The core client verifies the asset before the selected loader decodes it.
Specialized packages provide NumPy, Arrow, Parquet, AnyWidget, and Vega-Lite
loaders. Applications can define a `BlobAssetLoader` for any versioned media
type.

## Repository

- [Getting started](docs/getting-started.md)
- [ExportSpec](docs/export-spec.md)
- [Python API](docs/python-api.md)
- [Browser API](docs/browser-api.md)
- [Representations](docs/representations.md)
- [CLI](docs/cli.md)
- [Trust and integrity](docs/trust.md)
- [Contributor guide](development_docs/README.md)

Licensed under the [Apache License 2.0](LICENSE).
