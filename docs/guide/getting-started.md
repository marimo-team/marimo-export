---
title: Build your first notebook export
description: Create, build, verify, and read a deterministic two-state notebook export with Python and uv.
---

# Build your first notebook export

Create a local marimo notebook, prepare two input states, verify the resulting
files, then read the monthly state from Python. This quickstart uses no external
data or optional exporter package.

## Install marimo-export

Install [Python 3.10 or newer](https://www.python.org/) and
[uv](https://docs.astral.sh/uv/). Create an empty project:

```bash
mkdir notebook-export-demo
cd notebook-export-demo
uv init --bare --no-workspace
uv add marimo-export
```

The installation downloads packages from the Python package registry when they
are absent from the local uv cache.

## Create the notebook

Create `report.py`:

<!-- quickstart-source: report.py -->

```python
import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    days = mo.ui.slider(1, 30, value=7, label="Days")
    return (days,)


@app.cell
def _(days):
    summary = {"days": days.value, "label": f"Last {days.value} days"}
    return (summary,)


if __name__ == "__main__":
    app.run()
```

The `days` slider is an input. The `summary` dictionary is the notebook result
this quickstart will publish.

## Select states and an output

Create `report.export.yaml`:

<!-- quickstart-source: report.export.yaml -->

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

The `weekly` state keeps the notebook's initial slider value. The `monthly`
state replaces it with `30`. Both states publish `summary` as portable JSON.

## Build and verify the export

Run:

```bash
mkdir -p dist
uv run marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
uv run marimo-export verify dist/report
```

`build` executes the selected states through marimo, stages the notebook export,
verifies it, and commits `dist/report`. The JSON values fit inside
`index.json`, so this example creates no separate asset files.

The verifier prints:

```text
Verified 0 assets and 0 B for 2 states
```

If `dist/report` already exists, pass `--replace` to use the guarded replacement
transaction.

## Read the monthly state

Create `read_report.py`:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
monthly = notebook_export.state("monthly")
summary = monthly.output("summary").json()
print(dict(summary))
```

Run it:

```bash
uv run python read_report.py
```

Expected output:

```text
{'days': 30, 'label': 'Last 30 days'}
```

Opening validates canonical `index.json`. The `monthly` alias selects one
complete exported state. `json()` returns the immutable portable value stored
for the `summary` output.

## Continue from the first export

- [How notebook exports work](../overview.md) names each object in the lifecycle.
- [Choose states and outputs](choose-states.md) develops input inspection,
  sparse rows, output sources, and exporter dependencies.
- [Consume a notebook export](consume-an-export.md) opens the same export from
  Python and a browser.
- [Run the market dashboard](market-dashboard.md) builds a multi-representation
  application from a repository checkout and live market data.

The repository keeps the same deterministic source under
`examples/quickstart/` and runs it through the Python integration suite.
