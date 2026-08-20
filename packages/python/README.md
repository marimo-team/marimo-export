# marimo-export

Plan, prepare, write, and read verified exports of selected Marimo notebook
states.

```bash
uv add marimo-export
```

Build from a notebook file:

```python
from marimo_export import ExportSpec, build

result = build(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

`build` prepares missing states, writes the export, verifies its complete file
closure, and closes the owned notebook process tree. Matching later calls reuse
prepared repository artifacts.

Capture an active session:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("finance.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/finance", replace=True)
```

`capture` returns a leased `PreparedExport` and leaves the selected session
active. The handle can open the immutable export, lease individual assets, create
a prepared browser manifest, or write a deployment directory.

Install optional exporter families used by the spec:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

See the [Python API](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/python-api.md),
[ExportSpec guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/choose-states.md),
[agent guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/agents-and-automation.md),
and [CLI reference](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/cli.md).
