---
title: What is marimo-export?
description: Follow selected marimo notebook results from an ExportSpec to a portable notebook export and its readers.
---

# What is marimo-export?

marimo-export runs selected states of a [marimo](https://marimo.io/) notebook
and writes their named results to a portable directory. Python, TypeScript,
agents, and custom clients can read that directory after the producer stops.

Suppose `report.py` derives a summary and chart from a `days` slider. This
`ExportSpec` publishes weekly and monthly results:

```yaml
schema: marimo-export.spec.v2
default_state: weekly
states:
  weekly: {}
  monthly:
    days: 30
outputs:
  summary:
    source: { kind: json, selector: summary }
  chart:
    source: { kind: output, selector: chart }
```

The `weekly` **state row** keeps the slider's initial value. The `monthly` row
sets it to `30`. Planning fills omitted inputs from the captured input baseline,
so every row becomes a **complete input vector**.

`summary` and `chart` are **outputs**. An output is a stable consumer-facing
name available in every exported state. Its source selects a notebook result.
Its **representation**, a codec and media type, tells readers how that result is
stored and decoded.

Together, the complete input vectors and named outputs form the export's finite
**state-output relation**:

| Exported state | Complete inputs | `summary`   | `chart`                  |
| -------------- | --------------- | ----------- | ------------------------ |
| `weekly`       | `{"days": 7}`   | JSON record | Rendered-output snapshot |
| `monthly`      | `{"days": 30}`  | JSON record | Rendered-output snapshot |

## From notebook to consumer

Each public operation owns one transition:

| Operation | What it does                                                                     | Result                    |
| --------- | -------------------------------------------------------------------------------- | ------------------------- |
| `plan`    | Inspects the producer, completes state rows, and finds reusable and missing work | `ExportPlan`              |
| `prepare` | Starts a saved notebook and prepares missing states                              | Leased `PreparedExport`   |
| `capture` | Borrows a named live session and prepares missing states                         | Leased `PreparedExport`   |
| `write`   | Copies one prepared export to a destination and verifies it                      | Notebook export directory |
| `build`   | Runs `prepare`, then `write`, for a saved notebook                               | Notebook export directory |
| `open`    | Validates canonical `index.json` and creates an immutable reader                 | `NotebookExport` reader   |
| `resolve` | Selects an exported state by alias or input values                               | `ExportState`             |
| `load`    | Verifies and decodes one output representation                                   | Loaded value              |
| `mount`   | Attaches a loaded interactive value to a document element                        | Disposable mounted view   |

The written notebook export has one entry point:

```text
dist/report/
  index.json
  assets/
    <content-addressed files>
```

`index.json` records notebook and producer provenance, state aliases, complete
input vectors, output descriptors, and asset references. An output descriptor
records the representation, provenance, and either an inline value or a
content-addressed asset reference.

## Preparation and reuse

marimo-export keeps reusable producer results in an **export repository**. A
**prepared state** belongs to one producer identity, output plan, and complete
input fingerprint. A **prepared export** is a lease-protected export generation
for one exact `ExportSpec`.

The export repository and marimo's computation cache solve different problems:

| Storage                  | Reuses                                          |
| ------------------------ | ----------------------------------------------- |
| marimo computation cache | Notebook cell results during producer execution |
| marimo-export repository | Prepared states and complete export generations |

An exact repository match can satisfy a request before a notebook starts. When
one state changes, preparation can reuse matching prepared states and execute
the missing state. [Preparation and reuse](concepts/preparation-and-reuse)
explains the identity and lease model.

## Reading and publishing

A reader opens one immutable notebook export. A **prepared manifest** is a small
JSON record that points to one export and selects one exported state. A browser
can follow a changing manifest through a **prepared publication**, preserving
the last committed view while a replacement loads.

Opening validates the index. Loading verifies one selected asset. Complete
verification checks the full export closure. Mounting a chart, widget, or custom
interactive result grants its code the browser page's authority.

## Application integration

[Marimo Studio](https://github.com/marimo-team/marimo-studio) uses marimo-export
to pair authored web views with prepared notebook states for zero-Python static
exports. Studio owns view source and presentation while marimo-export owns state
preparation, portable outputs, integrity, and browser loading.

## The finite boundary

A consumer can resolve input vectors already present in the state-output
relation. Another Python-derived input vector requires another preparation run
or a live Python service. This makes marimo-export a fit for reports,
dashboards, static applications, and agent inputs whose supported states can be
declared before consumption.

Continue with [Why export notebook states?](why) to evaluate that boundary, or
[build your first notebook export](guide/getting-started) to run the complete
two-state example.
