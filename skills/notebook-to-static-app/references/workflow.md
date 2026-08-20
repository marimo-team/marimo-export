# Workspace workflow

Run the scaffold from a marimo-export checkout:

```bash
NOTEBOOK_PATH=/absolute/path/to/notebook.py
STATIC_APP_DIR=/absolute/path/to/static-app

uv run --frozen --group dev python \
  skills/notebook-to-static-app/scripts/scaffold_app.py \
  --notebook "$NOTEBOOK_PATH" \
  --output "$STATIC_APP_DIR" \
  --loader parquet \
  --loader vegalite
```

The scaffold vendors the current Python wheel and browser package, reads PEP 723
dependencies, and creates one uv and Vite workspace. Loader choices include
`anywidget`, `arrow`, `html`, `json`, `marimo-cell`, `marimo-output`, `numpy`,
`parquet`, `text`, and `vegalite`.

Install the generated environments:

```bash
uv sync --project "$STATIC_APP_DIR"
pnpm --dir "$STATIC_APP_DIR" install
```

## Inspect the notebook

```bash
uv run --project "$STATIC_APP_DIR" marimo-export inspect \
  "$NOTEBOOK_PATH" \
  --json
```

Inspection executes the initial autorun. Use the returned definitions, cells,
input modes, dependencies, portability, and sensitivity to author
`$STATIC_APP_DIR/app.export.yaml`.

```yaml
schema: marimo-export.spec.v1
default_state: baseline
states:
  baseline: {}
outputs:
  summary:
    source: { kind: value, selector: summary }
```

## Plan and build

```bash
uv run --project "$STATIC_APP_DIR" marimo-export plan \
  "$NOTEBOOK_PATH" \
  --spec "$STATIC_APP_DIR/app.export.yaml" \
  --timeout 300

uv run --project "$STATIC_APP_DIR" marimo-export build \
  "$NOTEBOOK_PATH" \
  --spec "$STATIC_APP_DIR/app.export.yaml" \
  --output "$STATIC_APP_DIR/public/export" \
  --replace \
  --timeout 300 \
  --jsonl

uv run --project "$STATIC_APP_DIR" marimo-export verify \
  "$STATIC_APP_DIR/public/export" \
  --json
```

`plan` reports inferred inputs, the default, normalized states, observations,
reusable fingerprints, and missing fingerprints. `build` validates the
destination before preparing missing work.

Put custom exporter modules in the app directory and add the directory to
`PYTHONPATH` for planning and preparation:

```bash
STATIC_APP_PYTHONPATH="$STATIC_APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
```

## Capture an open notebook

Start the notebook with the environment and custom exporter modules available.
List its sessions:

```bash
NOTEBOOK_PORT=2718

uv run --project "$STATIC_APP_DIR" marimo-export inspect \
  "http://127.0.0.1:$NOTEBOOK_PORT" \
  --timeout 180 \
  --json
```

Capture one selected session into the app's deployment directory:

```bash
SESSION_ID=s_...

env PYTHONPATH="$STATIC_APP_PYTHONPATH" \
  uv run --project "$STATIC_APP_DIR" marimo-export capture \
    "http://127.0.0.1:$NOTEBOOK_PORT" \
    --session "$SESSION_ID" \
    --spec "$STATIC_APP_DIR/app.export.yaml" \
    --output "$STATIC_APP_DIR/public/export" \
    --replace \
    --timeout 300 \
    --jsonl
```

The session remains active. Use the Python `capture()` context when the app needs
to inspect, serve, or retain the prepared generation before writing:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("app.export.yaml")
with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("public/export", replace=True)
```

The client and live kernel must import the same marimo-export implementation.
Restart the session after installing another wheel or changing an already loaded
custom exporter module.

## Build and preview the app

```bash
pnpm --dir "$STATIC_APP_DIR" run typecheck
pnpm --dir "$STATIC_APP_DIR" run build

APP_PORT=4173
pnpm --dir "$STATIC_APP_DIR" exec vp preview \
  --host 127.0.0.1 \
  --port "$APP_PORT" \
  --strictPort
```

Exercise every saved state, rapid successive changes, failure restoration,
mount disposal, desktop layout, and narrow layout. Confirm that notebook result
requests resolve from static export assets.

## Verify source identity before handoff

The scaffold records the notebook filename and SHA-256 in
`.notebook-source.json`. Compare that digest with the authored notebook before
handoff. Report a changed source and leave the notebook untouched.
