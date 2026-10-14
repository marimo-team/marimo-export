<p align="center">
  <a href="https://marimo-team.github.io/marimo-export/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://marimo-team.github.io/marimo-export/brand/marimo-export-lockup-stacked-dark.svg">
      <img alt="marimo-export" src="https://marimo-team.github.io/marimo-export/brand/marimo-export-lockup-stacked-light.svg" width="300">
    </picture>
  </a>
</p>

<p align="center">
  <strong>Prepare notebook results. Share them anywhere.</strong>
</p>

<p align="center">
  <a href="https://marimo-team.github.io/marimo-export/"><strong>Documentation</strong></a> ·
  <a href="https://marimo-team.github.io/marimo-export/guide/getting-started"><strong>Getting started</strong></a> ·
  <a href="https://marimo-team.github.io/marimo-export/reference/python-api"><strong>Python API</strong></a> ·
  <a href="https://marimo-team.github.io/marimo-export/reference/cli"><strong>CLI</strong></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/marimo-export/"><img alt="PyPI" src="https://img.shields.io/pypi/v/marimo-export.svg"></a>
  <a href="https://pypi.org/project/marimo-export/"><img alt="Supported Python versions" src="https://img.shields.io/pypi/pyversions/marimo-export.svg"></a>
  <a href="https://github.com/marimo-team/marimo-export/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/marimo-team/marimo-export/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/marimo-team/marimo-export/blob/main/LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache--2.0-6c6f78.svg"></a>
</p>

The `marimo-export` Python package precomputes selected input combinations from a
[marimo](https://marimo.io/) reactive Python notebook and publishes named outputs
as a **notebook export**. Applications and agents read its static files without
running Python or receiving the notebook source.

- **Choose the results.** Publish named data, tables, charts, rendered cells,
  and interactive widgets for a finite set of states.
- **Reuse the computation.** Reuse prepared states across exports and marimo's
  native cell cache when a new state needs to run.
- **Verify what you read.** Open and verify an export from Python, or read it in
  the browser with
  [`@marimo-team/marimo-export`](https://www.npmjs.com/package/@marimo-team/marimo-export).

## Install

Add the package to a Python project with [uv](https://docs.astral.sh/uv/):

```bash
uv add marimo-export
```

The package installs the marimo release it pins. Add the optional exporter
families selected by your `ExportSpec`:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

The base package supports JSON, native marimo values, and custom exporter
callables. `charts` adds Altair and PNG charts, `parquet` adds Parquet tables,
and `anywidget` adds AnyWidget bundles. `all` installs every family.

## Quickstart

Create `report.py`:

<!-- quickstart-source: report.py -->

```python
import marimo

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

### Build with the CLI

```bash
mkdir -p dist
uv run marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
uv run marimo-export verify dist/report
```

`build` prepares missing states, writes the export, verifies every declared
asset, and closes the notebook process it started.

### Build from Python

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

## Use from marimo code mode

marimo discovers the installed agent module through its capability registry:

```python
import marimo._code_mode as cm
import marimo_export.agent as export_agent

print(cm.capabilities()["marimo-export"])
help(export_agent)
```

`help(export_agent)` shows the public Python workflow and the installed path to
the version-matched [Agent Skill](https://agentskills.io/specification). Code
can inspect the same resources directly:

```python
skill = export_agent.agent_skill()
print(skill.body)
```

## Learn more

- [Getting started](https://marimo-team.github.io/marimo-export/guide/getting-started)
- [Choose states and outputs](https://marimo-team.github.io/marimo-export/guide/choose-states)
- [Python API](https://marimo-team.github.io/marimo-export/reference/python-api)
- [CLI reference](https://marimo-team.github.io/marimo-export/reference/cli)
- [Agents and automation](https://marimo-team.github.io/marimo-export/guide/agents-and-automation)

Preparing an export executes notebook code with the notebook environment's file,
credential, network, and package access.
