# marimo-export

Precompute selected marimo notebook states for an interactive app that needs
no Python runtime after deployment.

```bash
uv add marimo-export
```

```python
from marimo_export import ExportSpec, build

result = build(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

`build` opens the notebook, prepares the selected states, writes the export,
and closes its session. The notebook file stays unchanged.

Use `capture` when the notebook is already open:

```python
from marimo_export import ExportSpec, capture

result = capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

Install optional exporter families as needed:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

See the
[Python API](https://github.com/marimo-team/marimo-export/blob/main/docs/python-api.md),
[ExportSpec guide](https://github.com/marimo-team/marimo-export/blob/main/docs/export-spec.md),
and [CLI reference](https://github.com/marimo-team/marimo-export/blob/main/docs/cli.md).
