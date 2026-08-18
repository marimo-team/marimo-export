---
title: Use with agents
description: Ground agent answers in exported notebook data or ask an agent to publish a notebook and build a bespoke frontend.
---

# Use notebook exports with agents

A notebook export gives an agent a bounded data source with named states,
explicit output representations, notebook provenance, content identity, and
verification evidence.

Agents can use an existing export to answer questions from prepared data. They
can also inspect a notebook, choose a focused publication surface, create the
export, and build a purpose-specific frontend outside the Python ecosystem.

## Ground an answer in an existing export

1. Verify the export.
2. Inspect its prepared states and outputs.
3. Select the state that matches the question.
4. Read an inspectable representation.
5. Bind claims to the state, output, and provenance used.

```bash
marimo-export inspect dist/report --json
marimo-export verify dist/report --json
```

The inspection record tells the agent which state names and output
representations exist. The agent must not infer a state that is absent from the
export or treat a visual representation as structured data it cannot decode.

## Choose agent-readable outputs

| Representation       | Agent use                                                          |
| -------------------- | ------------------------------------------------------------------ |
| Scalar               | Metrics, labels, statuses, thresholds, and identifiers             |
| NumPy                | Numeric arrays when the agent tooling can decode NPY               |
| Arrow                | Typed columnar data when Arrow tooling is available                |
| Parquet              | Tables, filtering, aggregation, comparisons, and data questions    |
| Versioned JSON asset | Domain records with an explicit schema                             |
| Vega-Lite            | Inspectable chart specification and visual companion               |
| PNG                  | Visual companion with limited machine-readable semantics           |
| AnyWidget            | Saved state and browser behavior, primarily for interactive review |

For agent-oriented publication, combine:

- one concise scalar or versioned JSON summary
- one inspectable table or array
- one human-facing chart, image, or widget when visual review helps

[Output representations](../reference/representations.md) defines the built-in
families and custom media-type seam.

## Retain evidence identity

Keep these fields with a data-driven answer or generated application:

- notebook filename and document SHA-256
- marimo and marimo-export producer versions
- state name and fingerprint
- output name, codec, and media type
- asset SHA-256 when the output has an asset
- verification result

These identifiers distinguish the source notebook, prepared scenario, stored
representation, and exact bytes.

## Ask an agent to publish a notebook

An agent should:

1. inspect notebook definitions before choosing inputs or outputs
2. identify the audience question or downstream data task
3. author a small set of meaningful prepared states
4. choose outputs that each consumer can decode
5. select `build` or `capture` from notebook ownership
6. verify the completed export
7. return evidence bound to notebook and export identity

```bash
marimo-export session report.py --json
marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report \
  --json
marimo-export verify dist/report --json
```

`session NOTEBOOK` executes notebook code with the producer environment's file,
credential, network, and package access.

## Ask an agent to create a bespoke frontend

The repository includes a [notebook-to-static-app
workflow](https://github.com/marimo-team/marimo-export/blob/main/skills/notebook-to-static-app/SKILL.md)
for coding agents. It guides the agent through notebook inspection, ExportSpec
design, package vendoring, export creation, frontend implementation, and
browser validation.

The generated frontend should use exported values as its data source. It should
exercise every state, preserve the last complete view during rapid changes,
report recoverable errors, and make no request to a Python server for notebook
results.

Use [Build a browser application](browser-applications.md) for the public
consumer contract and [Browser API](../reference/browser-api.md) for exact
methods.
