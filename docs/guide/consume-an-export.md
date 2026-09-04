---
title: Consume a notebook export
description: Read the same exported states and outputs from Python, a browser, an agent, or another client.
---

# Consume a notebook export

A notebook export gives every consumer the same default exported state, authored
state aliases, complete input vectors, and named outputs. The examples on this
page use `dist/report`, created by
[Build your first notebook export](getting-started).

| Job                                   | Interface                                         |
| ------------------------------------- | ------------------------------------------------- |
| Read and verify local files           | `open_export()` and `verify_export()`             |
| Load immutable results in a browser   | `openExport()`                                    |
| Drive a changing prepared publication | Browser `prepared` subpath                        |
| Ground an agent answer                | Python reader, CLI verification, or export format |
| Implement another reader              | [Export format](../reference/export-format)       |

## Open from Python

Select the `monthly` exported state, decode its JSON output, and read its
rendered report bytes:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
monthly = notebook_export.state("monthly")
summary = monthly.output("summary").json()
report = monthly.output("report")

print(dict(monthly.inputs))
print(dict(summary))
print(report.codec, len(report.asset_bytes()) > 0)
```

Expected output:

```text
{'days': 30}
{'days': 30, 'label': 'Last 30 days'}
marimo.output.v1 True
```

Opening validates canonical `index.json` and leaves output assets lazy. The
quickstart keeps `summary` inline, so `json()` decodes it directly from the
index. `asset_bytes()` reads and verifies the selected `report` asset. The
reader also exposes:

- `identity`, the SHA-256 of the exact `index.json` bytes
- `spec_sha256`, the identity of the authored `ExportSpec`
- `default_state`, the resolved default `ExportState`
- notebook and producer facts
- input names, control bindings, output names, aliases, and states

## Select an exported state

Readers support these selection forms:

- `state(alias)` selects an authored state alias such as `monthly`.
- Python `state_by_fingerprint(fingerprint)` selects an exact state identity.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` replaces each supplied root input in the current
  exported state, then selects the resulting complete vector. It does not
  deep-merge nested objects.

Resolution returns a state already present in the notebook export. Preparing a
new input vector requires another producer operation or a Python service.

## Read an asset-backed output from Python

After building the [market dashboard](market-dashboard), an output backed by a
`BlobAsset` exposes bytes verified during that access and its media metadata:

```python
from marimo_export import open_export

market_export = open_export("examples/vite-vanilla/public/export")
chart = market_export.default_state.output("performance_chart").blob_asset()
print(chart.media_type, chart.filename, len(chart.data))
```

Use `scalar()` for a native scalar and `json()` for portable JSON. Use
`blob_asset()` for text, HTML, images, Parquet, Vega-Lite, AnyWidget, and custom
media types stored through the BlobAsset envelope. NumPy, Arrow, rendered-output,
and complete-cell accessors return verified raw bytes for a compatible decoder.

The Python reader validates framing but does not interpret NumPy, Arrow, or
marimo snapshot semantics.

## Open from a browser

A browser reads the export over HTTP. Configure the static server so
`dist/report` is available at `/export/`, then open the same `monthly`
exported state and both outputs:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";

const notebookExport = await openExport("/export/");
const monthly = notebookExport.state("monthly");
const summary = await monthly.output("summary").load(jsonLoader());
const report = await monthly.output("report").load(marimoOutputLoader());

console.log(summary); // { days: 30, label: "Last 30 days" }
console.log(report.output?.mimetype); // text/markdown
```

Both quickstart loaders have no peer runtime. The rendered report loader returns
an inert snapshot record. Treat its `output.data` as text until the application
applies an explicit rendering and sanitization policy. Specialized loaders can
require a peer runtime.
[Output representations](../reference/representations) maps stored
representations to browser loaders and their peer dependencies.

## Follow a prepared publication

Applications can open a `marimo-export.prepared.v1` manifest with the browser
`prepared` subpath:

```ts
import {
  fetchPreparedExportManifest,
  openPreparedPublication,
} from "@marimo-team/marimo-export/prepared";

const manifestUrl = new URL("/prepared/current.json", location.href);
const manifest = await fetchPreparedExportManifest(manifestUrl);
const publication = await openPreparedPublication(manifest, manifestUrl);
```

The manifest binds one immutable export identity, export URL, complete input
vector, and state fingerprint. `PreparedStateController` owns semantic state
updates and cancellation. `PreparedPublicationRefresh` validates a newer
manifest before replacing the browser publication and preserves a compatible
current selection.

Use [Serve a prepared publication](prepared-publications) to create and serve a
concrete static manifest before adding refresh and route-grace behavior.

## Verify the complete export

Python:

```python
from marimo_export import verify_export

result = verify_export("dist/report")
```

CLI:

```bash
uv run marimo-export verify dist/report
```

Browser:

```ts
const result = await notebookExport.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

Verification reads every declared asset. API and JSON results return exported
state, state-output-pair, unique-asset, and verified-byte counts. Human CLI
output omits the state-output-pair count. `index.json` is the integrity root.

## Retain evidence for an agent

Bind data-driven claims to the selected state and output. Retain notebook,
producer, spec, state fingerprint, codec, media type, asset SHA-256, and
verification facts when the answer needs an auditable source.

[Use notebook exports with agents](agents-and-automation) develops this
workflow. [Build a browser application](browser-applications) covers complete
state transitions and mount disposal.
