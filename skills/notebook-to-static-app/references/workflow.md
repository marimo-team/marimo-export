# Workspace workflow

Run the scaffold from a marimo-export checkout:

```bash
NOTEBOOK_PATH=/absolute/path/to/notebook.py
STATIC_APP_DIR=/absolute/path/to/static-app
```

## Contents

- [Create the workspace](#create-the-workspace)
- [Build from a notebook file](#build-from-a-notebook-file)
- [Capture an open notebook](#capture-an-open-notebook)
- [Build and preview](#build-and-preview)
- [Sideload an unpublished wheel](#sideload-an-unpublished-wheel)

## Create the workspace

Run the scaffold against a new or empty directory:

```bash
uv run --frozen --group dev python \
  skills/notebook-to-static-app/scripts/scaffold_app.py \
  --notebook "$NOTEBOOK_PATH" \
  --output "$STATIC_APP_DIR" \
  --loader parquet \
  --loader vegalite
```

The scaffold builds and vendors the local Python wheel and browser tarball.
Pass `--python-package` or `--browser-package` to reuse packages that were
already built and reviewed.

Available loader names are `anywidget`, `arrow`, `numpy`, `parquet`, and
`vegalite`. Scalar and image outputs need no additional loader package.

The scaffold reads the notebook's PEP 723 dependencies and creates:

```text
pyproject.toml
package.json
vendor/
tsconfig.json
vite.config.ts
index.html
src/main.ts
src/style.css
```

The generated `requires-python` intersects the notebook range with the
marimo-export Python 3.11 floor.

Install both environments:

```bash
uv sync --project "$STATIC_APP_DIR"
pnpm --dir "$STATIC_APP_DIR" install
```

The generated `pnpm-workspace.yaml` keeps the app self-contained when its
directory sits inside another pnpm workspace.

The generated package references are relative, so the complete app directory
can move to another checkout or build worker. If the notebook imports a local
Python package that its PEP 723 metadata does not declare, add a relative uv
source for that package.

Put custom exporter modules in the app directory. Add this path to the selected
producer command and to the live notebook environment used by capture:

```bash
STATIC_APP_PYTHONPATH="$STATIC_APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
```

## Build from a notebook file

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

`build` owns a temporary sibling copy, loopback server, marimo session, and
cleanup. It verifies the original notebook before committing the export.

## Capture an open notebook

Use `capture` when an existing edit session already holds the configured
environment or an expensive completed computation. List and inspect the live
session before writing the ExportSpec:

```bash
NOTEBOOK_PORT=2718
uv run --project "$STATIC_APP_DIR" marimo-export session \
  "http://127.0.0.1:$NOTEBOOK_PORT" \
  --timeout 180 \
  --json

SESSION_ID=s_...
uv run --project "$STATIC_APP_DIR" marimo-export session \
  "http://127.0.0.1:$NOTEBOOK_PORT" \
  --session "$SESSION_ID" \
  --timeout 180 \
  --json
```

Capture into the directory Vite will deploy:

```bash
env PYTHONPATH="$STATIC_APP_PYTHONPATH" \
  uv run --project "$STATIC_APP_DIR" marimo-export capture \
    "http://127.0.0.1:$NOTEBOOK_PORT" \
    --session "$SESSION_ID" \
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

The borrowed server and session remain active. Compare the notebook hash from
`.notebook-source.json` before handoff. A user may edit an attached notebook
while capture runs, so report a changed hash and leave the user’s source as-is.

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

## Sideload an unpublished wheel

Capture requires the client and notebook kernel to import the same
marimo-export implementation. The generated app vendors the current wheel
under `vendor/`. Serve that directory over loopback HTTP when the running
notebook environment cannot install a local path directly:

```bash
python -m http.server --bind 127.0.0.1 --directory "$STATIC_APP_DIR/vendor" 8765
```

Install the exact wheel URL into the notebook interpreter:

```bash
NOTEBOOK_PYTHON=/path/to/notebook/python
uv pip install \
  --python "$NOTEBOOK_PYTHON" \
  --reinstall \
  "http://127.0.0.1:8765/marimo_export-0.0.0-py3-none-any.whl"
uv pip check --python "$NOTEBOOK_PYTHON"
```

Restart the kernel, then inspect the session before capture. The bridge checks
the installed source identity in addition to the package version. Stop the
wheel server after the kernel imports the package.
