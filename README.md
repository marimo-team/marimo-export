# marimo-export

marimo-export captures selected results from a running marimo notebook and publishes them as verified static outputs for Python agents and browsers.

The running notebook is the source of truth. The exporter runs inside its active kernel, so it uses the packages, files, credentials, and live values that already make the notebook work. The notebook remains ordinary marimo code. An external export specification selects results and portable representations.

## Capture a running notebook

Prepare this checkout, then start the finance notebook in the same environment:

```bash
uv sync --all-extras --locked
uv run --with altair==6.0.0 marimo edit examples/_notebooks/finance.py \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 3456
```

`--no-sandbox` keeps the running kernel in that prepared uv environment. The kernel must be able to import the same `marimo-export` version that the capture client uses, including every requested exporter extra.

The Python dependency and lockfile temporarily pin `peter-gy/marimo` commit `0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`, which supplies the `BlobAsset` lazy-cache codec required by capture. Publishing the Python distribution is gated on a compatible marimo core release and a matching released dependency bound.

After the notebook has run, inspect the adjacent `examples/_notebooks/finance.export.yaml` specification:

```yaml
schema: marimo-export.spec.v1

variants:
  current: {}
  aapl:
    symbol_picker: [AAPL]

outputs:
  summary:
    source: summary
    formats:
      json: {}

  chart:
    source:
      expression: price_chart.properties(width=800)
    formats:
      vegalite: {}
      png:
        options:
          scale: 2
```

Capture the active session into a local publication:

```bash
export MARIMO_EXPORT_TOKEN="<token>"
uv run marimo-export capture \
  http://localhost:3456/ \
  --spec examples/_notebooks/finance.export.yaml \
  --output dist/finance
```

The command applies each finite UI variant, projects the selected values inside the running kernel, restores the starting controls and stale-cell set, verifies every transferred cache asset, and commits `dist/finance/index.json` last.

## Read from Python or the CLI

```python
from marimo_export import open_publication

publication = open_publication("dist/finance")
summary = (
    publication
    .variant("current")
    .output("summary")
    .format("json")
    .json()
)
```

```bash
uv run marimo-export inspect dist/finance --json
uv run marimo-export read dist/finance summary --variant current --format json --json
uv run marimo-export verify dist/finance --json
```

The publication remains readable after the marimo server and Python kernel stop.

## Install after the core release

Publishing the Python package requires the first official marimo release that includes the `BlobAsset` lazy-cache codec. Once marimo-export declares that released lower bound, install it in a project with:

```bash
uv add "marimo-export[png]"
```

## Read from a browser

The Python package owns live attachment, capture, local publication reads, and CLI automation. The TypeScript package owns HTTP publication reads and browser loaders.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openPublication } from "@marimo-team/marimo-export";

const publication = await openPublication("/exports/finance/");
const summary = await publication.variant("current").output("summary").format("json").json();
```

Install a dedicated loader when a trusted publication needs its own decoder or renderer. The generic reader verifies the indexed bytes before handing them to the loader. Vega-Lite and AnyWidget dependencies stay in their loader packages. Browser readers expose Arrow and Parquet projections as verified bytes or blobs. An application can supply a custom `FormatLoader` when it owns a bounded decoder for either format.

## Documentation

- [Getting started](docs/getting-started.md)
- [Export specifications](docs/export-specification.md)
- [Live capture](docs/live-capture.md)
- [Read publications](docs/read-publications.md)
- [Publish AnyWidget outputs](docs/anywidget.md)
- [CLI](docs/cli.md)
- [Trust and integrity](docs/trust.md)
- [Contributor documentation](development_docs/README.md)

Licensed under the [Apache License 2.0](LICENSE).
