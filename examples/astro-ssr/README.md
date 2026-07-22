# Astro AnyWidget example

This static Astro site consumes one `widgets` publication twice. The page frontmatter loads the
baseline `raw_counter` from a local directory and renders its inert `initialState`. The browser then
loads the publication from `/export/`, mounts the widget, and exposes local model control.

## Run the example

Install the workspace and build the producer and frontend packages:

```bash
corepack enable
pnpm install --frozen-lockfile
uv sync --all-extras
make build
```

Start a user-managed marimo server in the notebook environment. The publish command can attach to
this server whether it was just started or was already running:

```bash
uv run --package marimo-export --extra anywidget marimo edit \
  examples/_notebooks/widgets.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300 \
  --no-token \
  --no-skew-protection \
  --skip-update-check
```

In another terminal, publish every scenario into Astro's static directory:

```bash
rm -rf examples/astro-ssr/public/export
node packages/client/dist/cli.mjs publish \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/widgets.py \
  --plan examples/_notebooks/widgets.plan.yaml \
  --out examples/astro-ssr/public/export
```

Stop the marimo server. Build and serve the site while Python remains stopped:

```bash
MARIMO_EXPORT_DIR="$PWD/examples/astro-ssr/public/export" \
  pnpm --dir examples/astro-ssr build
pnpm --dir examples/astro-ssr preview
```

Open `http://127.0.0.1:4112/`. The left panel was rendered by Astro from
`LoadedAnyWidget.initialState`. The right panel fetches `/export/index.json` and its verified payload,
mounts the exported frontend module, updates local model state, and disposes the mount on `pagehide`.

The page exposes `data-widget-status`, `data-widget-count`, `data-ssr-state`, and
`data-widget-control` for browser checks.
