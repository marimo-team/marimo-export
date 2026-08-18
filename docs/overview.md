---
title: How notebook exports work
description: How selected notebook states become one verified export for applications, agents, Python automation, and custom clients.
---

# How notebook exports work

marimo-export runs selected notebook states through marimo and packages their
results as one verified export. Applications, agents, Python automation, and
custom clients can consume the same export.

The marimo notebook remains the source of truth for Python computation,
reactive dependencies, controls, and data access. An ExportSpec selects the
states and outputs to include. The resulting notebook export can be inspected
by agents, opened from Python, or loaded by a browser application.

## Four objects carry the workflow

| Object          | Owns                                                                          |
| --------------- | ----------------------------------------------------------------------------- |
| Notebook        | Python definitions, reactive dependencies, controls, and computation          |
| ExportSpec      | Prepared input choices, named states, outputs, and representation choices     |
| Notebook export | Complete states, provenance, output descriptors, and content-addressed assets |
| Consumer        | Human-facing application, agent, Python automation, or custom client          |

The notebook remains ordinary marimo source. State overrides and output
projection cells exist in an in-memory copy used for each export run.

## Prepared choices become complete states

An ExportSpec may omit values that should stay at the captured notebook
baseline:

```yaml
inputs: [interval, symbols_selector]
states:
  leaders: {}
  cloud:
    symbols_selector: [MSFT, GOOGL, AMZN]
  weekly:
    interval: 1wk
outputs:
  chart:
    source: performance
    exporter: altair.vegalite
  prices:
    source: selected_prices
    exporter: parquet.table
```

The producer fills each row into a complete input vector and records its
fingerprint. Every consumer sees the same state names and complete vectors.

[Choose states and results](guide/choose-states.md) defines the authoring
workflow. The [ExportSpec reference](reference/export-spec.md) defines the
accepted wire shape.

## marimo executes states and representations

Every state runs through marimo's reactive graph. marimo owns dependency
pruning, cell hashing, cache lookup, restoration, serialization, and
persistence. marimo-export adds one transient leaf per output so an exporter
such as `altair.vegalite` or `parquet.table` participates in the same execution
and cache lifecycle.

Use `build` when the producer owns notebook startup. Use `capture` when an open
session already owns the environment or completed computation. Both paths
produce the same export format.

## One index describes the complete export

```text
dist/finance/
  index.json
  assets/
    <sha256>.bin
    <sha256>.arrow
    <sha256>.npy
```

`index.json` records notebook and producer identity, input and output names,
complete states, fingerprints, representation descriptors, and asset sizes.
Asset filenames come from their SHA-256 digest.

The writer stages and verifies the complete directory before commit. Python
and browser readers verify asset length, digest, native framing, and descriptor
agreement before decoding a result.

The [export format reference](reference/export-format.md) defines the durable
consumer contract.

## Consumers open the same export

Python automation can open a local export, resolve a state, read its outputs,
and verify the complete directory. Agents can use the same reader or the CLI's
JSON output to enumerate states, inspect representations, and bind claims to
notebook, state, producer, and asset identity.

A browser application opens the export from static hosting:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const notebookExport = await openExport("./finance/");
const state = notebookExport.state("cloud");
const rows = await state.output("prices").load(parquetRowsLoader());
```

Opening fetches `index.json`. Output assets load on demand. Interactive values
such as Vega-Lite charts and AnyWidgets return disposable mount handles. A
frontend can present the results with HTML, CSS, TypeScript, or a frontend
framework without reproducing notebook computation in JavaScript.

Applications replacing several interactive outputs should stage and commit the
replacement as one complete view. Mounted modules have the browser page's
authority.

Use [Consume an export](guide/consume-an-export.md) to choose a reader, [Use
with agents](guide/agents-and-automation.md) for grounded analysis and
agent-built frontends, and [Build a browser
application](guide/browser-applications.md) for mount lifecycle and state
replacement. The [Browser API](reference/browser-api.md) defines exact methods.

## Prepared results define the static boundary

Consumers can resolve states present in the notebook export. A request that
needs a new Python result requires another export or a Python service. Browser
interactions that use saved data, chart state, or widget-local models can remain
inside the static consumer.
