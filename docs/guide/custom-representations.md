---
title: Custom representations
description: Convert one notebook result to a versioned BlobAsset and validate it with a matching browser loader.
---

# Custom representations

A custom representation pairs a Python exporter with a consumer that recognizes
the same versioned media type. Use a built-in representation when its data shape
and lifecycle already fit the application.

## Return a BlobAsset from Python

Create `summary_exporter.py` beside the notebook:

```python
import json
from collections.abc import Mapping

from marimo_export.outputs import BlobAsset
from marimo_export.wire import portable_json


def encode_summary(value: Mapping[str, object]) -> BlobAsset:
    normalized = portable_json(value, "summary")
    data = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return BlobAsset(
        data=data,
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

Select the callable in the ExportSpec:

```yaml
outputs:
  summary_data:
    source: { kind: export, selector: summary }
    exporter:
      name: summary_exporter:encode_summary
      options: {}
      dependencies:
        - json
```

The callable receives the selected notebook value first and exporter options as
keyword arguments. Add every helper module whose source affects the returned
bytes to `dependencies`, including ordinary imported helpers. A live session
uses module objects already loaded in that kernel, so restart it after changing
an imported exporter or helper module.

Exporter source and declared dependencies contribute to the output leaf's
marimo computation-cache identity when a state executes. Exact prepared-export
reuse returns before importing the exporter. Give mutable external exporter
source an explicit producer freshness input when edits must invalidate an exact
generation.

## Validate the representation in TypeScript

Install the browser package:

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

interface Summary {
  readonly days: number;
  readonly label: string;
}

export const summaryLoader = defineBlobAssetLoader<Summary>({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    const value: unknown = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(payload.data),
    );
    if (
      value === null ||
      Array.isArray(value) ||
      typeof value !== "object" ||
      !("days" in value) ||
      !("label" in value) ||
      typeof value.days !== "number" ||
      !Number.isFinite(value.days) ||
      typeof value.label !== "string"
    ) {
      throw new TypeError("Summary requires finite numeric days and a string label");
    }
    signal?.throwIfAborted();
    return Object.freeze({ days: value.days, label: value.label });
  },
});
```

Inside a browser application where `state` is the selected `ExportState`, load
the output through the explicit loader:

```ts
const summary = await state.output("summary_data").load(summaryLoader);
```

Use a new media-type version when a consumer cannot read both the old and new
payload shape. The export verifies the BlobAsset envelope and bytes. The loader
owns representation-specific shape validation.

## Return a mountable value

A loader may return an object with `mount(element, options)`. The mount must
return an idempotent `dispose()` handle and release its nodes, listeners, object
URLs, renderer state, and other owned resources.

A custom loader runs application-supplied code during `load()`. A returned
mountable value runs more application-supplied code during `mount()`. Keep
parsing and validation before dynamic module execution, honor abort signals,
and document any network origins or Content Security Policy capabilities these
operations require.

Use [Output representations](../reference/representations) to compare
built-in choices and the [browser loader reference](../reference/browser/loaders)
for `defineOutputLoader()`, loader resolution, and mount contracts.
