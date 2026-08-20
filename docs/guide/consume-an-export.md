---
title: Consume an export
description: Read the same prepared states and outputs from Python, a browser, an agent, or another client.
---

# Consume a notebook export

A notebook export exposes one default state, authored aliases, complete input
vectors, and the same named outputs to each consumer.

| Job                                   | Interface                                         |
| ------------------------------------- | ------------------------------------------------- |
| Read and verify local files           | `open_export()` and `verify_export()`             |
| Load immutable results in a browser   | `openExport()`                                    |
| Drive a changing prepared publication | Browser `prepared` subpath                        |
| Ground an agent answer                | Python reader, CLI verification, or export format |
| Implement another reader              | [Export format](../reference/export-format.md)    |

## Open from Python

```python
from marimo_export import open_export, verify_export

export = open_export("dist/report")
state = export.default_state
title = state.output("title").json()

verified = verify_export("dist/report")
```

Opening validates canonical `index.json` and leaves assets lazy. The reader
exposes:

- `identity`, the SHA-256 of exact `index.json` bytes
- `spec_sha256`, the identity of the authored ExportSpec
- `default_state`, the resolved `ExportState`
- notebook and producer facts
- input names, control bindings, output names, aliases, and states

## Open from a browser

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const title = await notebookExport.defaultState.output("title").load(jsonLoader());
```

Install the optional peer runtime used by each imported loader. [Output
representations](../reference/representations.md) maps stored forms to browser
loaders and peer dependencies.

## Select a prepared state

Readers support three forms of selection:

- `state(alias)` selects an authored alias.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` completes a sparse transition from the current state.

Resolution returns a state already present in the export. A new Python result
requires another preparation run or a Python service.

## Follow a prepared publication

Applications can open a `marimo-export.prepared.v1` manifest with the browser
prepared subpath:

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
result = verify_export("dist/report")
```

Browser:

```ts
const result = await notebookExport.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

Verification checks every declared asset and returns state, output, asset, and
byte counts. `index.json` is the integrity root.

## Retain evidence for an agent

Bind data-driven claims to the selected state and output. Retain notebook,
producer, spec, state fingerprint, codec, media type, asset SHA-256, and
verification facts when the answer needs an auditable source.

[Use notebook exports with agents](agents-and-automation.md) develops this
workflow. [Build a browser application](browser-applications.md) covers complete
state transitions and mount disposal.
