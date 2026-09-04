# marimo-export

The `marimo-export` Python package plans and prepares selected
[marimo](https://marimo.io/) notebook states, publishes named outputs as a
**notebook export**, and opens and verifies that export from Python. It can
prepare from a saved notebook or a named live session.

Select the states and outputs to publish from a marimo notebook. marimo-export
writes a portable, verified notebook export that browser applications and
agents read without a Python runtime or a copy of the notebook source.

[uv](https://docs.astral.sh/uv/) adds the package to a Python project:

```bash
uv add marimo-export
```

The package supports Python 3.10 through 3.14 and installs the marimo release
pinned by its package metadata.

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

## Run the CLI

```bash
mkdir -p dist
uv run marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
uv run marimo-export verify dist/report
```

`build` prepares missing states, writes the export, verifies every declared
asset, and closes the notebook process it started.

## Build from Python

```python
from pathlib import Path

from marimo_export import ExportSpec, build

Path("dist").mkdir(exist_ok=True)
spec = ExportSpec.from_file("report.export.yaml")
result = build("report.py", spec=spec, output="dist/python-report")

print(result.path)
```

A matching later call with a new destination can reuse the prepared export
before notebook startup. Pass `replace=True` to replace an existing complete
destination.

## Use from marimo code mode

Marimo discovers the installed agent module through its capability registry:

```python
import marimo._code_mode as cm
import marimo_export.agent as export_agent

print(cm.capabilities()["marimo-export"])
help(export_agent)
```

`help(export_agent)` shows the public Python workflow and the installed path to
the version-matched
[Agent Skill](https://agentskills.io/specification). Code can inspect the same
resources directly:

```python
skill = export_agent.agent_skill()
print(skill.body)
```

## Read the export

```python
from marimo_export import open_export, verify_export

export = open_export("dist/report")
summary = export.state("monthly").output("summary").json()
verified = verify_export("dist/report")

print(dict(summary))
print(verified.states, verified.outputs)
```

```text
{'days': 30, 'label': 'Last 30 days'}
2 4
```

Opening validates canonical `index.json` and leaves asset data lazy. Complete
verification reads every declared asset and returns exported-state,
state-output-pair, unique-asset, and verified-byte counts. The example has two
states and two output names, so `verified.outputs` is `4`.

Verification checks the notebook export against its loaded `index.json`. The
consumer still authenticates the publisher and delivery source.

## Capture a live session

After opening `report.py` in a running marimo server, `capture()` borrows the
named session and returns a leased `PreparedExport`:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("report.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/report", replace=True)
```

`replace=True` replaces the complete destination directory. Keep unrelated
application files outside `dist/report`.

Use `marimo-export inspect SERVER` to find session IDs. The selected session
remains active after capture. The live notebook environment and the client must
load the same marimo-export implementation and exporter dependencies.

## Install output exporters

Install the optional families selected by your `ExportSpec`:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

The base package supports JSON, native marimo values, and custom exporter
callables. The optional families add Altair and PNG charts, Parquet tables, and
AnyWidget bundles.

## Learn the complete API

- [Getting started](https://marimo-team.github.io/marimo-export/guide/getting-started)
- [Python API](https://marimo-team.github.io/marimo-export/reference/python-api)
- [Choose states and outputs](https://marimo-team.github.io/marimo-export/guide/choose-states)
- [CLI reference](https://marimo-team.github.io/marimo-export/reference/cli)
- [Agents and automation](https://marimo-team.github.io/marimo-export/guide/agents-and-automation)

Preparing an export executes notebook code with the notebook environment's file,
credential, network, and package access.
