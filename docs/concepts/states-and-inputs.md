---
title: States
description: See how omitted inputs get values, identical states share work, and readers select a state.
---

# States

The quickstart notebook begins with one input:

```text
days = 7
```

Its `ExportSpec` declares two sparse state rows:

```yaml
default_state: weekly
states:
  weekly: {}
  monthly:
    days: 30
```

Planning fills omitted values from the captured baseline:

| State alias | State row      | Complete input vector |
| ----------- | -------------- | --------------------- |
| `weekly`    | `{}`           | `{"days": 7}`         |
| `monthly`   | `{"days": 30}` | `{"days": 30}`        |

A **state row** is the sparse object an author writes. An **exported state** has
a complete input vector and the outputs prepared for that vector.

Switch the quickstart app between `weekly` and `monthly`. Each choice selects the
matching complete input vector from the same notebook export.

<StaticApp example="quickstart" />

## How marimo-export finds inputs

An input is a notebook definition whose value can vary between exported states.
Planning infers input names from:

- supported [marimo](https://marimo.io/) controls in the notebook cells needed
  for the selected outputs
- ordinary definitions named by a state row

The quickstart's published `summary` and `report` depend on the `days` slider, so
`days` becomes an input.

Run `marimo-export inspect NOTEBOOK --json` before authoring a spec when a
definition name, current value, or input shape is unclear. File inspection runs
the notebook's initial autorun with the producer's file, credential, package, and
network access. The initial autorun is marimo's first automatic notebook run.

Planning rejects a missing, sensitive, unavailable, or nonportable input. The
[ExportSpec reference](../reference/export-spec) defines supported controls,
AnyWidget patch inputs, ordinary definitions, and exact value limits.

## Complete input values identify a state

marimo-export converts each complete input vector to [Portable
JSON](../reference/portable-json) with one canonical byte form. Its SHA-256
digest is the **state fingerprint**.

Two state rows that complete to the same vector share one fingerprint and run
once. They keep both authored names as **state aliases**.

```yaml
states:
  weekly: {}
  current: {}
```

If the captured baseline is `{"days": 7}`, both aliases select the same exported
state.

## The default alias chooses the starting state

`ExportSpec.default_state` names an authored default alias. The export index
stores the fingerprint selected by that alias. A reader returns the corresponding
default exported state when the caller supplies no selection.

## Readers select states already in the export

| Operation              | Selection                                        |
| ---------------------- | ------------------------------------------------ |
| `state(alias)`         | Authored state alias                             |
| `resolve(inputs)`      | Exact complete input vector                      |
| `state.resolve(patch)` | Shallow root replacement over the current vector |

`state.resolve(patch)` replaces each supplied root value. It does not deep-merge
nested objects. Every form returns a state already present in the notebook
export. Another vector requires another producer run.

## Use observations to choose states

An observation records input values from one successful notebook run. Planning
keeps the values that match its current input names so an author can choose
which ones to add as state rows.

Observations remain authoring evidence until a chosen vector becomes an explicit
state row. Run `marimo-export observations list NOTEBOOK --spec FILE` to inspect
them.

Related: [Outputs](outputs-and-representations) follows `summary`
and `report` from notebook results to consumer values.
