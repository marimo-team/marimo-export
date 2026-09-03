---
title: How notebook exports work
description: How an ExportSpec becomes reusable prepared states and one verified notebook export.
---

# How notebook exports work

marimo-export resolves an `ExportSpec`, prepares its finite state relation through
Marimo, and writes one verified directory for Python and browser consumers.

```text
notebook + ExportSpec
  -> ExportPlan
  -> prepared states in the export repository
  -> PreparedExport
  -> index.json + content-addressed assets
```

## ExportSpec declares the product surface

```yaml
schema: marimo-export.spec.v2
default_state: baseline
states:
  baseline: {}
  weekly:
    interval: 1wk
outputs:
  summary:
    source: { kind: json, selector: report.summary }
  report:
    source: { kind: output, selector: report.view }
```

The spec contains exactly `schema`, `default_state`, `states`, and `outputs`.
marimo-export infers the input definitions from selected output dependencies and
keys present in state rows. It fills sparse rows from the notebook baseline and
deduplicates rows that resolve to the same complete input vector.

`default_state` names the state selected by Python readers, browser readers, and
prepared manifests when a caller supplies no state.

## ExportPlan makes the work inspectable

`plan()` returns an immutable `ExportPlan` with:

- notebook, producer, output-plan, and spec SHA-256 identities
- inferred input names and normalized states
- the authored default alias and its state fingerprint
- output names
- observed input vectors from the export repository
- reusable and missing state fingerprints
- `exact_reuse`, which reports that the plan came from a matching prepared export
  before notebook startup

The output-plan identity depends on `outputs`. Changing aliases, state rows, or
the default keeps prepared state artifacts reusable. Adding an output changes the
output-plan identity and prepares the requested output relation again.

## Marimo caches computation

Marimo owns notebook parsing, dependency execution, cache keys, invalidation,
restoration, serialization, signing, and cache stores. marimo-export adapts those
capabilities inside its contained Marimo compatibility modules.

marimo-export owns the finite state relation and portable prepared results. Its
export repository stores observations, prepared output artifacts, immutable
export generations, reservations, leases, and retention metadata. Repository
records do not replace Marimo's computation cache.

This split supports two recovery paths:

- A missing prepared export can be reconstructed while Marimo reuses valid cell
  cache entries.
- A missing Marimo cache can leave an existing verified prepared export reusable.

## prepare and capture return a leased artifact

`prepare()` owns notebook startup when missing work exists. `capture()` borrows a
named live session. Both return `PreparedExport`.

The handle keeps its repository generation leased while the caller opens files,
serves assets, builds a browser manifest, or writes a deployment directory. Close
the handle after the last consumer releases its assets.

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("report.export.yaml")
with prepare("report.py", spec=spec) as prepared:
    print(prepared.plan.missing_states)
    prepared.write("dist/report")
```

`build()` composes `prepare()` and `PreparedExport.write()`. It validates the
destination before notebook execution.

## index.json defines the portable relation

```text
dist/report/
  index.json
  assets/
    <sha256>.bin
    <sha256>.arrow
    <sha256>.npy
    <sha256>.output.json
    <sha256>.cell.json
```

The index records `spec_sha256`, the default state fingerprint, notebook and
producer facts, input and output names, control bindings, aliases, complete state
vectors, output descriptors, and asset declarations. Descriptor provenance
contains the originating Python type. Marimo cache paths remain producer-local.

Python and TypeScript readers validate canonical index bytes, exact state and
output sets, fingerprints, representation consistency, and asset declarations.
`verify_export()` or `NotebookExport.verify()` reads and verifies the complete
asset closure.

## Applications consume immutable or changing publications

Core readers open one immutable export:

```ts
import { openExport } from "@marimo-team/marimo-export";

const notebookExport = await openExport("/exports/report/");
const initial = notebookExport.defaultState;
```

The `@marimo-team/marimo-export/prepared` subpath adds manifest validation,
semantic input updates, control routing, cancellation, last-good restoration,
publication refresh, and disposal. Applications provide a `PreparedStatePort`
that loads required outputs and commits one complete view.

Use [Choose states and outputs](guide/choose-states.md), [Build or
capture](guide/build-and-capture.md), and the [export format
reference](reference/export-format.md) for the exact contracts.
