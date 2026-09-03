# marimo-export

The `marimo-export` Python package plans, prepares, writes, opens, and verifies
notebook exports from saved [marimo](https://marimo.io/) notebooks or named live sessions.

[uv](https://docs.astral.sh/uv/) adds the package to a Python project:

```bash
uv add marimo-export
```

## Build from a notebook file

This example uses the repository's
[`report.py`](https://github.com/marimo-team/marimo-export/blob/main/examples/quickstart/report.py)
and
[`report.export.yaml`](https://github.com/marimo-team/marimo-export/blob/main/examples/quickstart/report.export.yaml).
Download both files into the current directory before running the code.

```python
from pathlib import Path

from marimo_export import ExportSpec, build

Path("dist").mkdir(exist_ok=True)
spec = ExportSpec.from_file("report.export.yaml")
result = build("report.py", spec=spec, output="dist/report")

print(result.path)
```

`build()` prepares missing states, writes the export, verifies every declared
asset, and closes the notebook process it started. A matching later call with a
new destination or `replace=True` can reuse the prepared export before notebook
startup.

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
2 2
```

Opening validates canonical `index.json` and leaves asset data lazy. Complete
verification reads every declared asset and returns state, output, asset, and
byte counts.

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
- [Use notebook exports with agents](https://marimo-team.github.io/marimo-export/guide/agents-and-automation)

The package supports Python 3.10 and newer. Preparing an export executes notebook
code with the notebook environment's file, credential, network, and package
access.
