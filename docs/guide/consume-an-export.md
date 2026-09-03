---
title: Consume a notebook export
description: Read the same exported states and outputs from Python, a browser, an agent, or another client.
---

# Consume a notebook export

A notebook export gives every consumer the same default state, authored state
aliases, complete input vectors, and named outputs. The examples on this page
use `dist/report`, created by
[Build your first notebook export](getting-started.md).

| Job                                   | Interface                                         |
| ------------------------------------- | ------------------------------------------------- |
| Read and verify local files           | `open_export()` and `verify_export()`             |
| Load immutable results in a browser   | `openExport()`                                    |
| Drive a changing prepared publication | Browser `prepared` subpath                        |
| Ground an agent answer                | Python reader, CLI verification, or export format |
| Implement another reader              | [Export format](../reference/export-format.md)    |

## Open from Python

Select the `monthly` state and decode its JSON output:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
monthly = notebook_export.state("monthly")
summary = monthly.output("summary").json()
print(summary)
```

Expected output:

```text
{'days': 30, 'label': 'Last 30 days'}
```

Opening validates canonical `index.json` and leaves output assets lazy. The
quickstart keeps `summary` inline, so `json()` decodes it directly from the
index. When an output references an asset, its reader verifies that asset before
decoding it. The reader also exposes:

- `identity`, the SHA-256 of the exact `index.json` bytes
- `spec_sha256`, the identity of the authored `ExportSpec`
- `default_state`, the resolved default `ExportState`
- notebook and producer facts
- input names, control bindings, output names, aliases, and states

## Select an exported state

Readers support three forms of selection:

- `state(alias)` selects an authored state alias such as `monthly`.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` completes a sparse transition from the current state.

Resolution returns a state already present in the notebook export. Preparing a
new input vector requires another producer operation or a Python service.

## Open from a browser

A browser reads the export over HTTP. Configure the static server so
`dist/report` is available at `/exports/report/`, then open the same
`monthly` state:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const monthly = notebookExport.state("monthly");
const summary = await monthly.output("summary").load(jsonLoader());

console.log(summary); // { days: 30, label: "Last 30 days" }
```

The JSON loader has no peer runtime. Specialized loaders can require one.
[Output representations](../reference/representations.md) maps stored
representations to browser loaders and their peer dependencies.

## Follow a prepared publication

Applications can open a `marimo-export.prepared.v1` manifest with the browser
`prepared` subpath:

```ts
import {
  fetchPreparedExportManifest,
  openPreparedPublication,
} from "@marimo-team/marimo-export/prepared";

const manifestUrl = new URL("/runtime/prepared.json", location.href);
const manifest = await fetchPreparedExportManifest(manifestUrl);
const publication = await openPreparedPublication(manifest, manifestUrl);
```

The manifest binds one immutable export identity, export URL, complete input
vector, and state fingerprint. `PreparedStateController` owns semantic state
updates and cancellation. `PreparedPublicationRefresh` swaps to a newer verified
manifest while preserving a compatible current selection.

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

Verification reads every declared asset and returns state, output, asset, and
byte counts. `index.json` is the integrity root.

## Retain evidence for an agent

Bind data-driven claims to the selected state and output. Retain notebook,
producer, spec, state fingerprint, codec, media type, asset SHA-256, and
verification facts when the answer needs an auditable source.

[Use notebook exports with agents](agents-and-automation.md) develops this
workflow. [Build a browser application](browser-applications.md) covers complete
state transitions and mount disposal.
