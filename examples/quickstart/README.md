# First notebook export

This example prepares two states from a local marimo notebook and publishes one
portable JSON output. It uses no network data or optional exporter package.

From the repository root:

```bash
mkdir -p dist
uv run marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart
uv run marimo-export verify dist/quickstart
```

Read the monthly state:

```bash
uv run python -c 'from marimo_export import open_export; export = open_export("dist/quickstart"); print(dict(export.state("monthly").output("summary").json()))'
```

Expected output:

```text
{'days': 30, 'label': 'Last 30 days'}
```
