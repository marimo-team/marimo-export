---
title: Get started
description: Create two deterministic notebook states, publish JSON and rendered output, verify the files, and read one state.
---

# Get started

Create a local [marimo](https://marimo.io/) reactive Python notebook, run it for
`weekly` and `monthly`, publish a JSON summary and rendered report for each
state, then verify and read the written notebook export.

If you are still choosing where Python should run, read [When to use
marimo-export](../why) first.

## Install marimo-export

Install [Python 3.10 through 3.14](https://www.python.org/downloads/) and
[uv](https://docs.astral.sh/uv/). Continuous integration tests each supported
version. The package metadata pins its supported marimo release.

Create an empty project:

```bash
mkdir notebook-export-demo
cd notebook-export-demo
uv init --bare --no-workspace
uv add marimo-export
```

The installation downloads packages from the Python package registry when they
are absent from the local uv cache.

Check the installation:

```bash
uv run marimo-export doctor
```

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

    return (mo,)


@app.cell
def _(mo):
    days = mo.ui.slider(1, 30, value=7, label="Days")
    days
    return (days,)


@app.cell
def _(days):
    summary = {"days": days.value, "label": f"Last {days.value} days"}
    summary
    return (summary,)


@app.cell
def _(days, mo, summary):
    report = mo.md(f"## {summary['label']}\n\nSelected window: **{days.value} days**")
    report
    return (report,)


if __name__ == "__main__":
    app.run()
```

The slider starts at `7` days. Each following cell shows one value: `days` is
the input, `summary` is structured data, and `report` is rendered notebook
output.

## Declare two states and two outputs

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
  report:
    source: { kind: output, selector: report }
```

The empty `weekly` row keeps the slider's starting value. The `monthly` row uses
`30`. Both states publish the same output names:

| State     | `days` | Outputs             |
| --------- | ------ | ------------------- |
| `weekly`  | `7`    | `summary`, `report` |
| `monthly` | `30`   | `summary`, `report` |

## Build and verify the export

Run:

```bash
mkdir -p dist
uv run marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
uv run marimo-export verify dist/report
```

`build` runs the notebook as a Python program with your file, credential,
package, and network access. Review the notebook before running it.

`build` runs both states, writes the notebook export, and verifies `index.json`
and both report assets.

You should see:

```text
Verified 2 assets and 923 B for 2 states
```

The byte count can change when the supported marimo snapshot encoding changes.
The stable result is two states with two named outputs each.

The static application below reads the same generated export. Switch between
the two states to see the JSON summary and rendered report change together.

<StaticApp example="quickstart" />

## Inspect the files

The written directory has one entry point and two asset files named by their
content hashes:

```text
dist/report/
  index.json
  assets/
    <sha256>.output.json
    <sha256>.output.json
```

`summary` stays inline in `index.json`. Each state produces a distinct `report`
snapshot, so each report has its own asset.

## Read the monthly state

Create `read_report.py`:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
monthly = notebook_export.state("monthly")
summary = monthly.output("summary")
report = monthly.output("report")

print(dict(monthly.inputs))
print(dict(summary.json()))
print(len(report.asset_bytes()) > 0)
```

Run it:

```bash
uv run python read_report.py
```

Expected output:

```text
{'days': 30}
{'days': 30, 'label': 'Last 30 days'}
True
```

`open_export()` validates canonical `index.json`. `json()` reads the inline
summary. `asset_bytes()` reads and verifies the selected report asset.

Related: [Overview](../overview) explains the concepts. [Browser
applications](browser-applications) covers the TypeScript reader. [Build and
capture](build-and-capture) covers planning, live sessions, progress,
cancellation, and replacement behavior.
