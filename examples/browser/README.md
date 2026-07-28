# Browser example

This Vite app reads the finance publication, switches between captured variants, decodes the summary JSON, and mounts the Vega-Lite chart.

Start `finance.py` in the environment where it works:

```bash
uv sync --all-extras --locked
uv run --package marimo-export --extra png --with altair==6.0.0 \
  marimo edit examples/_notebooks/finance.py \
  --no-sandbox \
  --headless \
  --no-token \
  --host 127.0.0.1 \
  --port 3456
```

Open the notebook once so its kernel is active. Capture the selected outputs into the app's public directory:

```bash
uv run marimo-export capture http://127.0.0.1:3456/ \
  --spec examples/_notebooks/finance.export.yaml \
  --output examples/browser/public/publication
```

`--no-sandbox` keeps the finance kernel in the uv environment that contains this marimo-export checkout, its PNG extra, Altair, and the pinned marimo commit. `--no-token` is suitable for this loopback-only demo. For an authenticated server, remove that flag, set `MARIMO_EXPORT_TOKEN`, and keep the capture URL free of credentials. A local one-off command may carry one `access_token` query value in the server URL.

The publication is static after capture. Stop the marimo server, then run the browser app:

```bash
pnpm --dir examples/browser dev
```

Open `http://127.0.0.1:4113/`. Use `?publication=/another/root/` to read a publication from a different HTTP path.
