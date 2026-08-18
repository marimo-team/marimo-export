# marimo-export

Precompute selected notebook results from Python and read the resulting export.
Build from a file, capture an active session, or open an existing export.
Applications, agents, Python automation, and custom clients can consume the
same verified export.

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
[Python API](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/python-api.md),
[ExportSpec guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/choose-states.md),
[agent guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/agents-and-automation.md),
and [CLI reference](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/cli.md).
