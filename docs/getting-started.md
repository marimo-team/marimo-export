# Getting started

This workflow runs the cache-matrix notebook on a local marimo server, publishes three scenarios, verifies the publication, and reads it after the Python producer stops.

Run every command from the repository root. The workflow requires Node 22.18 or newer, pnpm, and uv.

## Install and build

```bash
pnpm install --frozen-lockfile
pnpm --filter @marimo-team/marimo-export build
```

The Python producer is a uv workspace package. `uv run --package marimo-export` installs its exact marimo dependency into the command environment when needed.

## Start the producer

Keep this command running in one terminal:

```bash
uv run --package marimo-export marimo edit \
  examples/_notebooks/cache_matrix.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300 \
  --no-token \
  --no-skew-protection \
  --skip-update-check
```

`--no-sandbox` keeps the notebook kernel in the environment that contains `marimo-export`. The server binds to loopback. The two `--no-*` security flags make this local workflow copyable. Omit both when the server is reachable from another machine so marimo authentication and skew protection remain enabled.

The repository selects marimo's default `relaxed` execution type and enables cell caching in `pyproject.toml`. Both interactive notebook work and export builds therefore use the notebook's file-backed cache. If this notebook has previously run with `strict` execution, begin with a fresh `examples/_notebooks/__marimo__/cache` directory before publishing.

## Check producer capabilities

Run this command in a second terminal:

```bash
node packages/client/dist/cli.mjs describe \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/cache_matrix.py
```

`describe` reports the marimo and marimo-export versions, the active marimo adapter, and the available built-in projection exporters.

## Publish the scenario matrix

```bash
node packages/client/dist/cli.mjs publish \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/cache_matrix.py \
  --plan examples/_notebooks/cache_matrix.plan.json \
  --out /tmp/cache-matrix-export \
  --record /tmp/cache-matrix.build.json
```

`publish` performs three operations in order:

1. It builds the plan inside an attached marimo kernel.
2. It stages the immutable projection closure on the server and pulls it into `/tmp/cache-matrix-export`.
3. It verifies the local index and every referenced payload against the build's `ExportRef`.

The build record at `/tmp/cache-matrix.build.json` contains the server, notebook path, `ExportRef`, and build receipt. Keep it when another process needs to anchor `index.json` to the authenticated build response.

## Inspect and read

```bash
node packages/client/dist/cli.mjs inspect \
  /tmp/cache-matrix-export \
  --ref /tmp/cache-matrix.build.json

node packages/client/dist/cli.mjs read \
  /tmp/cache-matrix-export large calculation \
  --format json \
  --ref /tmp/cache-matrix.build.json
```

The `large` scenario resolves `scale` to `5` and the `multiplier` UI value to `3`. Its `calculation` output is:

```json
{
  "multiplier": 3,
  "result": 153,
  "scale": 5
}
```

Verify the complete publication explicitly:

```bash
node packages/client/dist/cli.mjs verify \
  /tmp/cache-matrix-export \
  --ref /tmp/cache-matrix.build.json
```

## Stop Python and read again

Stop the marimo server with `Ctrl-C`, then rerun the `read` command. The publication contains the complete JavaScript consumption boundary:

```text
/tmp/cache-matrix-export/
├── index.json
└── cache/
    └── marimo-export/
        └── payloads/
            └── sha256/
                └── <payload digest>
```

The checked-in Node example reads every scenario from the same directory:

```bash
node examples/read-checkout.mjs /tmp/cache-matrix-export
```

The browser, Next.js, and Astro commands are in [Read exports](./read-exports.md).

## Run a warm build

Start the producer again and rerun `publish`. marimo restores matching authored and projection cells from `__marimo__/cache/`. The pull verifies the local content-addressed payloads and skips matching files.

Interactive execution can warm authored cells whose marimo identity matches the build. Synthetic projection cells exist during export builds, so a matching earlier export warms those cells. See [Cache identity](./export-plans.md#cache-identity) for the exact identity rules.

## Next steps

- Edit [the cache-matrix plan](https://github.com/marimo-team/marimo-export/blob/main/examples/_notebooks/cache_matrix.plan.json) and learn the contract in [Export plans](./export-plans.md).
- Run the prepared environment on another machine through [Remote execution](./remote-execution.md).
- Publish an interactive widget through [AnyWidget](./anywidget.md).
- Use `openExport()` from [Read exports](./read-exports.md).
