---
title: How notebook exports work
description: Follow one notebook result from an ExportSpec through preparation, a notebook export, and its consumers.
---

# How notebook exports work

Suppose `report.py` computes a summary and a rendered report from an `interval`
control. You want a browser application to switch between daily and weekly
results after the Python process has stopped.

An `ExportSpec` names those two input states and the outputs to publish:

```yaml
schema: marimo-export.spec.v2
default_state: daily
states:
  daily: {}
  weekly:
    interval: 1wk
outputs:
  summary:
    source: { kind: json, selector: report.summary }
  report:
    source: { kind: output, selector: report.view }
```

marimo-export runs the selected states through
[marimo](https://marimo.io/), stores the named outputs, and writes a notebook
export:

```text
report.py + ExportSpec
        |
        v
      plan       complete sparse rows and find reusable work
        |
        v
prepare/capture  run missing states and retain a PreparedExport
        |
        v
   write/build   create index.json and its declared assets
        |
        v
  open/resolve   select an exported state
        |
        v
   load/mount    decode data or attach an interactive value
```

The producer is the Python environment that runs the notebook. A consumer is
Python code, a browser application, an agent, or another implementation that
reads the completed notebook export.

## 1. The ExportSpec selects finite work

Each row under `states` describes one input assignment. A state is complete
after marimo-export fills omitted inputs from the captured baseline, which is
the notebook's input vector at the start of planning.

The `daily` row in the example keeps the captured `interval`. The `weekly` row
replaces it with `1wk`. marimo-export normalizes both rows before execution and
gives each distinct complete input vector a state fingerprint.

Each entry under `outputs` publishes one notebook result under a stable name.
The output source selects the result. Its representation determines how that
result is stored and which readers can decode it.

Read [States and inputs](concepts/states-and-inputs.md) and [Outputs and
representations](concepts/outputs-and-representations.md) for these two parts of
the model.

## 2. Planning exposes the work

`plan()` or `marimo-export plan` returns an `ExportPlan`. The plan records the
inferred inputs, normalized states, default state alias, output names, observed
input vectors, reusable states, and missing states.

An exact prepared export can satisfy planning before marimo-export starts the
notebook. Otherwise, planning runs the notebook's initial autorun to inspect its
definitions and capture the baseline.

The plan changes when the producer, outputs, or authored states change. This
lets an application inspect the work before preparation begins.

## 3. Preparation runs missing states

`prepare()` starts and owns a temporary session for a saved notebook file.
`capture()` borrows one named session from a running marimo server. Both prepare
missing states and return a `PreparedExport`.

A `PreparedExport` is a leased handle to one immutable repository generation.
The lease keeps its files available while an application opens the export,
serves an asset, creates a prepared manifest, or writes a destination directory.
Close the handle after the last operation that needs those files.

The export repository stores prepared states and completed export generations.
marimo's content-addressed computation cache remains responsible for notebook
cell results. The two stores can reuse work independently. [Preparation and
reuse](concepts/preparation-and-reuse.md) explains how this connects to marimo's
automatic cell caching and cached WebAssembly exports.

Read [Preparation and reuse](concepts/preparation-and-reuse.md) before choosing
between `prepare`, `capture`, and `build`.

## 4. Writing creates the portable boundary

`PreparedExport.write()` copies and verifies one prepared export before it
commits the destination directory. `build()` combines `prepare()` and `write()`
for a saved notebook.

The written notebook export has one entry point:

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

`index.json` records the available states, aliases, inputs, outputs,
representations, provenance, and asset declarations. It never points into the
producer's marimo cache.

## 5. Consumers select existing states

Opening a notebook export validates `index.json` and leaves output assets lazy.
A reader can select:

- the declared default state
- a state alias such as `weekly`
- one complete exported input vector
- a sparse patch from the current exported state

Resolution selects a state already present in the notebook export. A request for
a new Python result needs another producer run or a Python service.

After state selection, a Python reader returns a scalar, portable JSON value, or
verified asset. A browser reader uses an explicit loader to decode an output.
Interactive values add a `mount()` lifecycle that the application must dispose.

## 6. Verification protects integrity

The notebook export identity is the SHA-256 digest of the exact canonical
`index.json` bytes. Each asset declaration carries its own size and SHA-256
digest. `verify_export()` and `NotebookExport.verify()` read every declared
asset and check the complete declared closure.

Verification proves that the files agree with `index.json`. Establish the
publisher's identity through the delivery channel or another authentication
mechanism. Review interactive browser code before mounting it because mounted
code receives the page's authority.

Read [Integrity and trust](concepts/integrity-and-trust.md) for the boundary
between validation, integrity, provenance, authentication, and executable code.

## Choose the next page

- Read [Why marimo-export](why.md) to decide whether finite precomputation fits
  the application.
- Follow the [Concepts](concepts/) in learning order.
- [Choose states and outputs](guide/choose-states.md) for an authoring workflow.
- [Build or capture](guide/build-and-capture.md) for producer commands.
- [Consume an export](guide/consume-an-export.md) for Python and browser readers.
- Use the [Terminology](reference/terminology.md) page for exact project nouns.
