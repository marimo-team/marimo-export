---
title: Use with agents
description: Ground agent work in verified prepared notebook states and structured output representations.
---

# Use notebook exports with agents

A notebook export gives an agent a finite data source with named states, explicit
output representations, content identity, and verification facts.

## Ground an answer in an existing export

1. Verify the export.
2. Open its default or requested state.
3. Select an output the available tools can decode.
4. Bind claims to the source and state identities.

```bash
marimo-export verify dist/report --json
```

```python
from marimo_export import open_export

export = open_export("dist/report")
state = export.default_state
summary = state.output("summary").json()

evidence = {
    "export_sha256": export.identity,
    "spec_sha256": export.spec_sha256,
    "state_sha256": state.fingerprint,
    "output": "summary",
    "python_type": state.output("summary").descriptor.provenance.python_type,
}
```

The selected representation constrains the claims an agent can support. Pair a
visual output with JSON, a table, or an array when the answer depends on exact
values.

## Choose agent-readable outputs

| Representation         | Agent task                                           |
| ---------------------- | ---------------------------------------------------- |
| Portable JSON          | Summaries, records, arrays, metrics, and identifiers |
| Scalar                 | Labels, statuses, thresholds, and metrics            |
| Parquet or Arrow       | Filtering, aggregation, comparison, and typed tables |
| NumPy                  | Numeric arrays when NPY tooling is available         |
| Complete Marimo cell   | Output, console records, and cell identity           |
| Rendered Marimo output | Formatted output and replay resources                |
| Vega-Lite              | Inspectable chart specification and visual companion |
| PNG                    | Visual review paired with structured evidence        |
| AnyWidget              | Saved browser model state and interactive review     |
| Versioned BlobAsset    | Domain records with a named media-type contract      |

[Output representations](../reference/representations.md) maps these forms to
Python access and browser loaders.

## Retain evidence identity

Keep these facts with a data-driven answer or generated application:

- notebook filename and document SHA-256
- ExportSpec SHA-256
- notebook export identity from canonical `index.json`
- Marimo and marimo-export producer versions
- producer implementation SHA-256
- state aliases and fingerprint
- output name, codec, media type, and originating Python type
- asset SHA-256 when the output has an asset
- verification result

These records distinguish authored intent, producer implementation, selected
state, representation, and exact bytes.

## Ask an agent to prepare an export

An agent can inspect the notebook, author a finite spec, plan the work, build the
export, and verify it:

```bash
marimo-export inspect report.py --json
marimo-export plan report.py \
  --spec report.export.yaml \
  --json
marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report \
  --jsonl
marimo-export verify dist/report --json
```

Notebook inspection and preparation execute notebook code with the producer
environment's file, credential, network, and package access. The agent should
report external data dependencies and preserve the authored notebook source.

`plan` reports reusable state fingerprints. An unchanged second build can reuse
the exact prepared export before notebook startup. Adding one state prepares its
missing fingerprint while retaining matching state artifacts.

## Ask an agent to build a frontend

The repository includes a [notebook-to-static-app
workflow](https://github.com/marimo-team/marimo-export/blob/main/skills/notebook-to-static-app/SKILL.md).
It routes the agent through notebook inspection, ExportSpec design, preparation,
frontend implementation, and browser validation.

The frontend should exercise every saved state, preserve the last committed view
during rapid changes, dispose stale mounts, report recoverable errors, and load
notebook results from the verified export.

Use [Build a browser application](browser-applications.md) for prepared state
transitions and [Browser API](../reference/browser-api.md) for exact methods.
