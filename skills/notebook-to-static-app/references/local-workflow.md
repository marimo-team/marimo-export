# Local workflow

Use these paths while marimo-export remains unpublished:

```bash
MARIMO_EXPORT_ROOT=/Users/petergy/Projects/opensource/marimo-team/marimo-export
NOTEBOOK_PATH=/absolute/path/to/notebook.py
STATIC_APP_DIR=/absolute/path/to/static-app
```

## Contents

- [Create the workspace](#create-the-workspace)
- [Start and inspect the notebook](#start-and-inspect-the-notebook)
- [Build and capture](#build-and-capture)
- [Build and preview](#build-and-preview)
- [Existing live servers](#existing-live-servers)

## Create the workspace

Run the scaffold against a new or empty directory:

```bash
python3 \
  "$MARIMO_EXPORT_ROOT/skills/notebook-to-static-app/scripts/scaffold_app.py" \
  --notebook "$NOTEBOOK_PATH" \
  --output "$STATIC_APP_DIR" \
  --marimo-export-root "$MARIMO_EXPORT_ROOT" \
  --loader parquet \
  --loader vegalite
```

The scaffold packs the local browser package into `vendor/`. For repeated app
builds, pack once into a directory outside each app and pass the tarball with
`--browser-package`.

Available loader names are `anywidget`, `arrow`, `numpy`, `parquet`, and
`vegalite`. Scalar and image outputs need no additional loader package.

The scaffold reads the notebook's PEP 723 dependencies and creates:

```text
pyproject.toml
package.json
tsconfig.json
vite.config.ts
index.html
src/main.ts
src/style.css
```

Install both environments:

```bash
uv sync --project "$STATIC_APP_DIR"
pnpm --dir "$STATIC_APP_DIR" install
```

The generated `pnpm-workspace.yaml` keeps the app self-contained when its
directory sits inside another pnpm workspace.

If the notebook imports a local Python package that its PEP 723 metadata does
not declare, add that package to `pyproject.toml` with a uv path source. Keep
the notebook source unchanged.

Put custom exporter modules in the app directory. Use this path for the live
notebook and both producer commands:

```bash
STATIC_APP_PYTHONPATH="$STATIC_APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
```

## Start and inspect the notebook

Choose an unused port:

```bash
NOTEBOOK_PORT=2718
env PYTHONPATH="$STATIC_APP_PYTHONPATH" \
  uv run --project "$STATIC_APP_DIR" marimo edit "$NOTEBOOK_PATH" \
  --headless \
  --no-token \
  --no-sandbox \
  --port "$NOTEBOOK_PORT"
```

Keep that process running. Open `http://127.0.0.1:2718` with a browser and wait
for initial execution.

List sessions:

```bash
uv run --project "$STATIC_APP_DIR" marimo-export session \
  "http://127.0.0.1:$NOTEBOOK_PORT" \
  --timeout 180 \
  --json
```

Inspect the selected session:

```bash
uv run --project "$STATIC_APP_DIR" marimo-export session \
  "http://127.0.0.1:$NOTEBOOK_PORT" \
  --session SESSION_ID \
  --timeout 180 \
  --json
```

The inspection result is the source of truth for ExportSpec input and output
definition names.

## Build and capture

Build from the file into the directory Vite will deploy:

```bash
env PYTHONPATH="$STATIC_APP_PYTHONPATH" \
  uv run --project "$STATIC_APP_DIR" marimo-export build "$NOTEBOOK_PATH" \
    --spec "$STATIC_APP_DIR/app.export.yaml" \
    --output "$STATIC_APP_DIR/public/export" \
    --replace \
    --timeout 300 \
    --json
```

Verify:

```bash
uv run --project "$STATIC_APP_DIR" marimo-export verify \
  "$STATIC_APP_DIR/public/export" \
  --json
```

Capture the open session into a proof directory outside Vite's `public/`
tree:

```bash
env PYTHONPATH="$STATIC_APP_PYTHONPATH" \
  uv run --project "$STATIC_APP_DIR" marimo-export capture \
    "http://127.0.0.1:$NOTEBOOK_PORT" \
    --session SESSION_ID \
    --spec "$STATIC_APP_DIR/app.export.yaml" \
    --output "$STATIC_APP_DIR/.exports/capture" \
    --replace \
    --timeout 300 \
    --json
```

Verify:

```bash
uv run --project "$STATIC_APP_DIR" marimo-export verify \
  "$STATIC_APP_DIR/.exports/capture" \
  --json
```

`public/export` is now the browser source. Keep `.exports/capture` as local
producer evidence.

## Build and preview

```bash
pnpm --dir "$STATIC_APP_DIR" run typecheck
pnpm --dir "$STATIC_APP_DIR" run build
APP_PORT=4173
pnpm --dir "$STATIC_APP_DIR" exec vp preview \
  --host 127.0.0.1 \
  --port "$APP_PORT" \
  --strictPort
```

Open the Vite URL with `agent-browser`, exercise the app, and close the browser
session when finished.

## Existing live servers

Capture requires the notebook kernel to import the same marimo-export version
as the calling client. For local work, start the notebook through the generated
uv project. For an environment that is already running, install the current
wheel into that environment before capture:

```bash
uv build --package marimo-export --project "$MARIMO_EXPORT_ROOT"
```

Use the wheel under `$MARIMO_EXPORT_ROOT/dist`. Restart the notebook kernel
after installation, then inspect the session before capture.
