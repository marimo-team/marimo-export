---
title: What is marimo-export?
description: Run selected marimo notebook states and publish their outputs for browsers, Python, and agents.
---

# What is marimo-export?

marimo-export runs selected states of a [marimo](https://marimo.io/) notebook and writes their named
outputs to a portable directory called a **notebook export**. Browser
applications, Python programs, agents, and custom clients can read it after the
Python producer stops.

The [quickstart](guide/getting-started) starts with one `days` slider and two
notebook results. An `ExportSpec` is the declaration that chooses which states
to run and which results to publish:

```yaml
default_state: weekly
states:
  weekly: {}
  monthly:
    days: 30
outputs:
  summary:
    source: { kind: json, selector: summary }
  report:
    source: { kind: output, selector: report }
```

## States are rows and outputs are columns

The notebook starts with `days: 7`. The empty `weekly` row keeps that value. The
`monthly` row replaces it with `30`.

| State     | Input          | `summary`     | `report`             |
| --------- | -------------- | ------------- | -------------------- |
| `weekly`  | `{"days": 7}`  | JSON in index | Rendered output file |
| `monthly` | `{"days": 30}` | JSON in index | Rendered output file |

This table is the **state-output relation**. Each row is an exported state with a
complete input vector. Each column is a named output available in every state.

The embedded quickstart application reads that generated export. Switching the
report window selects another row and renders both outputs from it.

<StaticApp example="quickstart" />

## Choose how each output is stored

`summary` and `report` begin as notebook results. The `ExportSpec` publishes each
under a stable output name.

An output representation tells a consumer how to read the stored value. The
summary uses portable JSON inside `index.json`. The report uses a
rendered-output snapshot in a content-addressed asset.

## Producers write and consumers read

The Python environment that runs the notebook is the **producer**. It inspects
the notebook, completes state rows, executes missing states, and writes the
notebook export.

A Python reader, browser application, agent, or custom client is a **consumer**.
It opens the written files, selects an exported state, and reads a named output.
Selecting another exported state runs no notebook code.

```text
dist/report/
  index.json
  assets/
    <sha256>.output.json
    <sha256>.output.json
```

`index.json` records the state-output relation and points to its assets. Opening
validates the index. Reading an asset-backed output verifies that asset. Complete
verification checks every declared asset.

## Reuse results between builds

marimo-export can reuse compatible results from earlier builds. When the
notebook must run, marimo can also restore compatible cell results. [Learn how
reuse works](concepts/caching).

Applications that change over time can point one stable URL at successive
exports. [Learn how to publish updates](concepts/exports-and-publications).

## Readers select states already in the export

A consumer can resolve `weekly`, `monthly`, or either complete input vector. An
input vector absent from the export needs another producer run or a live Python
service.

Related: [Choose notebook states](concepts/states-and-inputs) and [Store and load
outputs](concepts/outputs-and-representations).
