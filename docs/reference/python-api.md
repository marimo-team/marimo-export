---
title: Python API
description: Choose the Python API for producing, reading, serving, or integrating notebook exports.
---

# Python API

The Python package prepares selected marimo notebook states, writes verified
notebook exports, and reads the same files that browser applications consume.
Python 3.10 or newer is required.

Install the base package to produce and read portable JSON, scalar, NumPy,
rendered-output, complete-cell, and blob outputs:

```bash
uv add marimo-export
```

Install a producer extra when an `ExportSpec` uses its exporter:

| Exporter                | Install                             |
| ----------------------- | ----------------------------------- |
| Altair Vega-Lite or PNG | `uv add "marimo-export[charts]"`    |
| AnyWidget               | `uv add "marimo-export[anywidget]"` |
| Parquet                 | `uv add "marimo-export[parquet]"`   |
| Every built-in exporter | `uv add "marimo-export[all]"`       |

## Choose a Python path

| Job                                                                     | Reference                                                            |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Define states and outputs, plan work, prepare, or build                 | [Produce an export](python/produce.md)                               |
| Open states and decode outputs                                          | [Read and verify exports](python/reader.md)                          |
| Inspect a notebook or capture a live session                            | [Sessions and inspection](python/sessions-and-inspection.md)         |
| Configure retention or record observed inputs                           | [Repository and observations](python/repository-and-observations.md) |
| Commit an application directory or retain a changing publication        | [Delivery and publications](python/delivery-and-publications.md)     |
| Embed marimo-export behavior in a marimo host                           | [Host integration](python/host-integration.md)                       |
| Implement against canonical JSON, indexes, descriptors, or typed errors | [Format records and errors](python/format-records-and-errors.md)     |

## The common workflow

```python
from pathlib import Path

from marimo_export import ExportSpec, OutputSpec, build, open_export

spec = ExportSpec(
    default_state="baseline",
    states={
        "baseline": {},
        "weekly": {"interval": "1wk"},
    },
    outputs={"summary": OutputSpec.json("report.summary")},
)

Path("dist").mkdir(exist_ok=True)
result = build("report.py", spec=spec, output="dist/report")
summary = open_export(result.path).default_state.output("summary").json()
```

An `ExportSpec` declares named state rows and named outputs. Planning fills each
sparse row from the notebook baseline and creates one complete input vector per
state. Preparation stores reusable results in the export repository. Writing
creates a notebook export whose `index.json` and assets can be verified without
running notebook code.

The package root exposes the common workflow:

```python
from marimo_export import (
    ExportPlan,
    ExportRepository,
    ExportResult,
    ExportSpec,
    NotebookExport,
    OutputSpec,
    PreparedExport,
    ProgressEvent,
    VerificationResult,
    build,
    capture,
    open_export,
    plan,
    prepare,
    verify_export,
)
```

Focused modules own application delivery, live sessions, observations, host
integration, output values, and the low-level format records. Their reference
pages identify the canonical import path for each API.
