# First notebook export

This example prepares two states from a local marimo notebook. Each state
publishes an inline JSON summary and a rendered report asset.

From the repository root:

```bash
mkdir -p dist
uv run marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart
uv run marimo-export verify dist/quickstart
```

The verifier reports two states, four state-output pairs, and two assets:

```text
Verified 2 assets and 923 B for 2 states
```

The `weekly` row keeps the slider's initial value, so planning completes the
authored `{}` row as `{"days": 7}`. The `monthly` row becomes `{"days": 30}`.
The export contains one entry point and two content-addressed report snapshots:

```text
dist/quickstart/
  index.json
  assets/
    <sha256>.output.json
    <sha256>.output.json
```

Read the monthly state:

```bash
uv run python -c 'from marimo_export import open_export; export = open_export("dist/quickstart"); print(dict(export.state("monthly").output("summary").json()))'
```

Expected output:

```text
{'days': 30, 'label': 'Last 30 days'}
```

`summary` is stored inside `index.json`. `report` uses the `marimo.output.v1`
codec, so Python reads its verified bytes with `asset_bytes()` and browser
applications decode it with `marimoOutputLoader()`.

## Open the static application

Build the export into the application's static files, then start Vite:

```bash
cd examples/quickstart
pnpm run export
pnpm run dev
```

Open the loopback URL printed by Vite. The application verifies the export,
switches between `weekly` and `monthly`, and renders both selected outputs. Its
static bundle contains `index.json`, the report assets, HTML, CSS, and JavaScript.
