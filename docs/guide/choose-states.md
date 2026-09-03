---
title: Choose states and outputs
description: Inspect a notebook, declare sparse states, and publish the representations each consumer needs.
---

# Choose states and outputs

An `ExportSpec` selects a finite state-output relation from one notebook. Each
named state becomes a complete input assignment, and each output publishes one
selected notebook result in every state.

The deterministic quickstart declares two states and one JSON output:

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
```

The notebook defines a `days` slider with a value of `7` and derives `summary`
from `days.value`. Planning infers `days` as the input because it affects the
selected output. `default_state` names the alias that readers select when they
do not request another state.

## Inspect the notebook inputs

Inspect the notebook before writing state rows:

```bash
uv run marimo-export inspect examples/quickstart/report.py --json
```

File inspection runs the notebook's initial autorun with the current Python
environment and its file, credential, package, and network access.

The quickstart reports this definition. The excerpt omits unrelated fields:

```json
{
  "name": "days",
  "kind": "ui",
  "input_mode": "value",
  "value": 7,
  "value_available": true,
  "portable_input": true,
  "sensitive": false,
  "input_dependencies": [],
  "control_paths": { "Hbol-0": [] },
  "domain": { "debounce": false, "step": null }
}
```

Use the inspected definition name as the state key. For the slider, write
`days: 30`. The state value matches the frontend value shown by `inspect`, so it
is `30` rather than `days.value`, `{value: 30}`, or a runtime control ID.

The inspection fields answer different authoring questions:

| Field                | Authoring decision                                                                           |
| -------------------- | -------------------------------------------------------------------------------------------- |
| `name`               | Key to use in a state row                                                                    |
| `kind`               | `ui` for a marimo control or `ordinary` for a Python definition                              |
| `input_mode`         | `value` replaces the complete frontend value, while `patch` applies an AnyWidget trait patch |
| `value`              | Current frontend JSON shape when `value_available` is `true`                                 |
| `domain`             | Available control hints such as options, minimum, maximum, or step                           |
| `input_dependencies` | Other input roots that affect this definition                                                |
| `control_paths`      | Runtime IDs mapped to paths inside a composed control                                        |
| `portable_input`     | Whether the definition can enter a state vector                                              |
| `sensitive`          | Whether the control contains password input and is rejected from export state                |

For a composed control, copy the array or object shape reported in `value`.
`control_paths` describes browser event routing. Its IDs and path steps are not
state keys.

An AnyWidget reports `input_mode: "patch"`. Its state value is an object whose
keys are widget traits. The producer merges that sparse trait patch over the
complete serializer-owned model state captured at baseline, then verifies the
accepted complete value.

An ordinary definition can be input-capable even when `value_available` is
`false`. That field means inspection did not include a copy of its value.
`portable_input` reports whether planning can use it. Planning also rejects an
ordinary definition assigned by its cell's final named expression because the
authored assignment and state override would compete for ownership.

## Write sparse states

Each state row can omit inputs that should retain the captured baseline:

```yaml
states:
  weekly: {}
  monthly:
    days: 30
```

For a file build, the baseline comes from the initial autorun of the saved
notebook. For live capture, it comes from the selected session. In the
quickstart, `weekly` resolves to `{"days": 7}` and `monthly` resolves to
`{"days": 30}`.

The producer fills every omitted input before execution. It then hashes the
complete input object. Two rows that resolve to the same object share one state
fingerprint and later reuse one prepared-state artifact while retaining both
authored names as aliases:

```yaml
default_state: current
states:
  baseline: {}
  current: {}
```

Use explicit values when a consumer must receive the same state regardless of
the current live baseline. Keep an empty row when the captured baseline is
the intended product state.

State values can contain null, booleans, Unicode strings, finite numbers in the
JavaScript safe-integer range, arrays, and string-keyed objects. NaN and
infinity are invalid. The [ExportSpec reference](../reference/export-spec)
defines the complete wire contract.

## Treat observations as authoring evidence

An observation is a complete portable input vector recorded after a successful
normal notebook run. Planning returns a revision-consistent set of observations
projected to the inferred inputs:

```bash
uv run marimo-export observations list examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --json
```

Observations show states that have worked in the matching saved notebook. They
do not add states to the notebook export. Copy a chosen vector into `states` and
give it a stable name when consumers should be able to select it.

Use [Manage the export repository](manage-repository) to inspect, clear, and
repopulate observation history.

## Choose each output source

Start with JSON for records, arrays, summaries, and metrics:

```yaml
outputs:
  summary:
    source: { kind: json, selector: summary }
```

Each output has one source kind:

| Source         | Stored result                                                         | Typical consumer                               |
| -------------- | --------------------------------------------------------------------- | ---------------------------------------------- |
| `kind: json`   | Canonical portable JSON                                               | Python, browser, agent, or custom client       |
| `kind: native` | marimo scalar, JSON, NumPy, Arrow, or `BlobAsset` representation      | Typed Python or browser loader                 |
| `kind: export` | `BlobAsset` returned by one declared exporter                         | Chart, table, media, or domain-specific loader |
| `kind: output` | Formatted marimo output and replay resources                          | marimo-aware browser application               |
| `kind: cell`   | Cell identity, terminal output, console records, and replay resources | marimo-aware browser application or agent      |

JSON, native, export, and rendered-output selectors begin with a Python
definition name. They can continue through attributes, nonnegative array items,
or JSON-string mapping keys. Mapping keys take precedence over attributes.

Select a complete cell by its authored name or an inspected runtime ID:

```yaml
outputs:
  summary_cell:
    source: { kind: cell, by: name, value: summary_cell }
```

Every normalized state must produce every named output. One output name also
keeps the same codec and media type across all states.

## Install exporter dependencies in the producer

The base Python package supports JSON, native, rendered-output, complete-cell,
and `blob.*` export paths. Install the matching extra before using an exporter
that depends on another Python distribution:

| Exporter                          | Install command                     |
| --------------------------------- | ----------------------------------- |
| `altair.vegalite` or `altair.png` | `uv add "marimo-export[charts]"`    |
| `parquet.table`                   | `uv add "marimo-export[parquet]"`   |
| `anywidget.bundle`                | `uv add "marimo-export[anywidget]"` |
| Every bundled exporter dependency | `uv add "marimo-export[all]"`       |

The extra belongs in the environment that runs `build` or hosts the session
used by `capture`. A browser loader has its own npm peer dependencies.

Use expanded exporter form when options are required:

```yaml
outputs:
  prices:
    source: { kind: export, selector: selected_prices }
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: prices.parquet
      dependencies: []
```

A custom exporter names an importable `module:symbol` callable. Declare every
helper module whose source affects the returned bytes, including ordinary
imports. Custom exporter calls run for each state that needs preparation.
Restart a live session after changing an exporter or helper module that the
session already imported.

## Check the resolved relation

Run `plan` before a costly build or capture:

```bash
uv run marimo-export plan examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --json
```

Inspect `inputs`, the complete `states` mappings, `default_alias`, `outputs`,
`observations`, `reusable_states`, `missing_states`, and `exact_reuse`. Continue
with [Build or capture](build-and-capture) after the relation matches the
states and outputs your consumers need.
