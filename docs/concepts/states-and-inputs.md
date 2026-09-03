---
title: States and inputs
description: Understand inferred inputs, captured baselines, sparse state rows, aliases, fingerprints, defaults, and observations.
---

# States and inputs

An export state is one complete assignment for every input inferred from an
`ExportSpec`. Authors can write sparse state rows because marimo-export fills
omitted values from the captured baseline.

A `StateSpace` validates reusable state rows and expands an optional Cartesian
matrix. A complete `ExportSpec` combines those rows with the named outputs to
prepare. Planning then infers the inputs that connect the two.

Consider a notebook whose current controls are `interval: 1d` and
`region: All`:

```yaml
schema: marimo-export.spec.v2
default_state: current
states:
  current: {}
  daily:
    interval: 1d
  weekly:
    interval: 1wk
  europe:
    region: Europe
outputs:
  summary:
    source: { kind: json, selector: report.summary }
```

Planning completes the rows as follows:

| State alias | Authored row     | Complete input vector            | Fingerprint |
| ----------- | ---------------- | -------------------------------- | ----------- |
| `current`   | `{}`             | `interval: 1d`, `region: All`    | A           |
| `daily`     | `interval: 1d`   | `interval: 1d`, `region: All`    | A           |
| `weekly`    | `interval: 1wk`  | `interval: 1wk`, `region: All`   | B           |
| `europe`    | `region: Europe` | `interval: 1d`, `region: Europe` | C           |

`current` and `daily` select the same state fingerprint because their
complete input vectors are equal. marimo-export executes that vector once.

## Inputs are inferred

The `ExportSpec` has no `inputs` field. Planning infers inputs from:

- canonical UI roots in the selected outputs' dependency closure
- definition names used as keys in state rows

A definition is a name created by a notebook cell. The dependency closure
contains each selected result and every definition required to compute it. That
closure contributes supported marimo UI elements and
[AnyWidget](https://anywidget.dev/) roots whose serializer accepts portable
model state. An ordinary Python definition becomes an input when a state row
names it explicitly.

Input names are definition names. Use the UI element name such as
`interval_selector`, not its `.value` property.

Run `uv run marimo-export inspect NOTEBOOK --json` before authoring a spec when the
available definitions or their input modes are unclear. An input mode reports
whether a state row replaces the complete value or applies a sparse patch. File
inspection runs the notebook's initial autorun, which is marimo's first
dependency execution after opening the notebook. It runs with the current file,
credential, network, and package access.

Planning rejects a selected input when its value is missing, contains a password
control, or cannot be represented as portable JSON. It also rejects an ordinary
definition assigned by the defining cell's final named expression because the
notebook and the state row would both own that value.

Selected output dependencies determine which definitions can vary as inputs.
State execution still runs every available authored cell before it completes.
An unrelated enabled cell that fails can therefore fail preparation even when
no published output depends on that cell.

## A matrix expands a state space

Use `matrix` when each value for one input can combine with every value for the
other inputs:

```yaml
schema: marimo-export.states.v1
default_state: matrix-000000
matrix:
  interval: [1d, 1wk]
  region: [All, Europe]
```

The matrix expands to four named states. Use explicit `states` for combinations
that need stable domain names or when some combinations are invalid. A
`StateSpace` can contain both forms.

## The captured baseline completes sparse rows

The captured baseline is the complete value of every inferred input before
marimo-export applies an authored state row. For a saved notebook, the baseline
comes from the initial autorun. For capture, it comes from the selected live
session.

An empty row means “use the captured baseline.” A key present in the row
replaces that input for the state.

`baseline` has no reserved meaning as a state alias. A spec can call an empty
row `current`, `default`, or any other valid state name. Use “captured baseline”
for the input vector and “state alias” for the authored name.

## Normalization produces state fingerprints

marimo-export converts each complete input vector to canonical portable JSON.
The SHA-256 digest of those canonical bytes is the state fingerprint.

Equivalent vectors share one fingerprint even when several state aliases refer
to them. Negative zero normalizes to zero for state identity.

Portable state values include:

- null
- booleans
- Unicode strings
- finite JavaScript-safe numbers
- arrays of portable values
- objects with string keys and portable values

NaN, positive infinity, and negative infinity are invalid state inputs.

## The default state starts reader selection

`default_state` names one authored state alias. Readers select its normalized
state when the caller supplies no state.

The `ExportPlan` retains the authored default alias. The written `index.json`
stores the corresponding state fingerprint. This lets readers start from the
same complete vector even when several aliases share it.

## Readers resolve existing states

A reader can select an exported state through these operations:

| Operation                       | Input                 | Behavior                                                           |
| ------------------------------- | --------------------- | ------------------------------------------------------------------ |
| `state(alias)`                  | Authored state alias  | Returns the state targeted by the alias                            |
| Python `state_by_fingerprint()` | State fingerprint     | Returns the state with that exact identity                         |
| `resolve(inputs)`               | Complete input vector | Returns the matching exported state                                |
| `state.resolve(patch)`          | Root-input patch      | Replaces each supplied root value, then returns the complete match |

`state.resolve(patch)` is a shallow root replacement. It does not deep-merge
nested objects or apply AnyWidget authoring-patch semantics.

Resolution returns a state already present in the notebook export. Preparing a
new input vector requires another producer run.

## Observations are authoring evidence

An observation is one successful complete input vector recorded during a normal
notebook run. The export repository retains observations by producer and assigns
them a revision.

Planning projects compatible observations onto the inferred input names and
reports them in the `ExportPlan`. An observation becomes an exported state after
an author adds a state row to the `ExportSpec`.

Use `marimo-export observations list NOTEBOOK --spec FILE` to inspect this
evidence before choosing state rows. Read the [ExportSpec
reference](../reference/export-spec) for exact names, value limits, selectors,
and AnyWidget patch behavior.
