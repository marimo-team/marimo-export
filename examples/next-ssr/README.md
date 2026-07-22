# Next.js AnyWidget example

This app decodes a published AnyWidget during server rendering and mounts the same model graph in a Client Component. The marimo server can stop as soon as `publish` has copied the verified publication to the local filesystem.

## Publish the widgets notebook

Start the existing marimo environment with the notebook and its pinned dependencies:

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

Run the producer from another terminal:

```bash
make build
rm -rf /tmp/widgets-export
node packages/client/dist/cli.mjs publish \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/widgets.py \
  --plan examples/_notebooks/widgets.plan.yaml \
  --out /tmp/widgets-export
test -f /tmp/widgets-export/index.json
```

The publish command pulls `index.json` and its verified payload closure into `/tmp/widgets-export`. Stop the marimo server after the command completes.

## Run Next.js

```bash
MARIMO_EXPORT_DIR=/tmp/widgets-export pnpm --dir examples/next-ssr dev
```

Open [http://127.0.0.1:4111/](http://127.0.0.1:4111/). The Server Component reads `/tmp/widgets-export` through `directorySource`. The `/export/*` route exposes that same directory to `httpSource`, so both runtimes verify and consume the same bytes. The initial HTML contains the inert `baseline` model state. The Client Component mounts `wrapped_dashboard`, exposes its `rename()` initialize export, and releases the mounted model graph through `dispose()`.

`mount()` executes JavaScript authored by the notebook. Serve publications from trusted producers. A deployed content security policy must allow `blob:` in `script-src` for embedded widget modules.
