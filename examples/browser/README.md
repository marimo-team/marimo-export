# Browser example

This Vite app reads a published `widgets.py` execution over HTTP. It decodes typed JSON, mounts Vega-Lite, and hydrates raw and composed AnyWidgets from static files.

## Publish the widget scenarios

Start the notebook on an existing marimo server:

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

Build the workspace and publish all three scenarios into Vite's static directory:

```bash
make build
node packages/client/dist/cli.mjs publish \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/widgets.py \
  --plan examples/_notebooks/widgets.plan.yaml \
  --out examples/browser/public/export
```

After `publish` returns, stop the marimo server. The browser reads `index.json` and its verified payloads from `public/export`.

## Run the static consumer

```bash
pnpm --dir examples/browser dev
```

Open `http://127.0.0.1:4113/`. Switch between `baseline`, `boosted`, and `violet` to release the current chart and widget model graphs before mounting the next scenario. The controls exercise local model changes, `initialize()` exports, binary state, nested models, styles, and cleanup callbacks.
