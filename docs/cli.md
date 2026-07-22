# CLI

The `marimo-export` CLI builds notebook projections on a marimo server, publishes them as ordinary files, and gives agents bounded access to the result. It requires Node 22 or newer.

From the repository checkout, build and run the executable with:

```bash
pnpm install --frozen-lockfile
pnpm --filter @marimo-team/marimo-export build
node packages/client/dist/cli.mjs --help
```

Installed-package examples use `marimo-export`. Replace it with `node packages/client/dist/cli.mjs` when working from the checkout.

## Publish in one command

```bash
marimo-export publish \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan export.plan.yaml \
  --out ./public/notebook-export \
  --record ./notebook-export.build.json
```

`publish` validates the plan locally, builds every scenario in the attached kernel, pulls the exact payload closure, and verifies the local publication against the returned `ExportRef`. With `--notebook`, `--record` saves the durable `marimo-export.build.v1` record. Place the record outside `--out`. `publish` writes it after the remote build and before transfer, so a successfully written record remains available to `pull` if transfer or local verification fails.

Target a running session directly when its ID is already known:

```bash
marimo-export publish \
  --server http://127.0.0.1:2718/ \
  --session s_1234 \
  --plan export.plan.yaml \
  --out ./public/notebook-export
```

Pass exactly one of `--notebook` or `--session`. Supply `--session` with a top-level key from the server's `GET /api/sessions` response. A session target stays attached for the one `publish` command. `build` and `publish --record` require `--notebook` so a later `pull` can open a fresh session.

## Command map

| Command    | Contract                                                   |
| ---------- | ---------------------------------------------------------- |
| `publish`  | Build, pull, and verify one publication.                   |
| `build`    | Execute the plan remotely and emit a durable build record. |
| `pull`     | Pull the publication named by a build record.              |
| `describe` | Report producer versions and available exporters.          |
| `inspect`  | Page through scenarios or one scenario's output contracts. |
| `read`     | Read one declared output under a byte limit.               |
| `verify`   | Verify the index and every unique payload.                 |
| `version`  | Print the CLI package version. `--version` is equivalent.  |

## Build and pull separately

Use separate commands when execution and transfer happen in different jobs:

```bash
marimo-export build \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan export.plan.yaml \
  --record notebook-export.build.json

marimo-export pull notebook-export.build.json \
  --out ./public/notebook-export
```

`build` always writes the raw `marimo-export.build.v1` record to stdout, so it composes directly with stdin:

```bash
marimo-export build \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan export.plan.yaml \
  | marimo-export pull - --out ./public/notebook-export
```

`--plan -` reads a JSON or YAML plan from stdin. `pull -` reads a JSON build record from stdin. A build record contains the server URL, notebook path, `ExportRef`, and build receipt. It contains no credentials.

## Inspect a publication

`SOURCE` is a local publication directory or an absolute HTTP or HTTPS URL.

List scenarios:

```bash
marimo-export inspect ./public/notebook-export --json
```

Inspect one scenario's output formats:

```bash
marimo-export inspect ./public/notebook-export \
  --scenario baseline \
  --offset 0 \
  --limit 50 \
  --json
```

Without `--scenario`, each page contains scenario IDs, resolved inputs, and output counts. With `--scenario`, each page contains output names, public format names, format IDs, media types, metadata, and payload references.

`--offset` defaults to `0`. `--limit` defaults to `50` and accepts values through `500`.

## Read one output

`read` takes the source, scenario ID, and output name as positional arguments:

```bash
marimo-export read ./public/notebook-export baseline summary \
  --format json \
  --json
```

The format may be omitted when the output has exactly one format. JSON media types are decoded. For a text media type, plain `read` validates UTF-8 and writes the verified payload bytes to stdout exactly, preserving an existing byte-order mark or missing trailing newline. `read --json` emits the decoded text in its structured envelope. Binary output requires a file:

```bash
marimo-export read ./public/notebook-export baseline chart \
  --format png \
  --out ./chart.png
```

`--max-bytes` defaults to `1000000`. The command rejects a larger declared payload before fetching it. Increase the limit deliberately for a known output:

```bash
marimo-export read ./public/notebook-export baseline table \
  --format parquet \
  --max-bytes 20000000 \
  --out ./table.parquet
```

When `--out` is present, stdout reports the resolved path and the output provenance. `--out -` is invalid because binary bytes never share stdout with structured command output.

## Verify a publication

```bash
marimo-export verify ./public/notebook-export \
  --ref notebook-export.build.json \
  --concurrency 8 \
  --json
```

`verify` checks the index against `--ref` when supplied, then reads every unique payload and verifies its declared size and SHA-256. It returns `{ok, files, bytes, failures}` and exits with status `1` when `ok` is false.

`--ref` accepts a raw `ExportRef`, a `marimo-export.build.v1` record, or `-` for JSON read from stdin. Pass the same option to `inspect` and `read` when the build record is the trust anchor for the publication.

## Describe the producer

```bash
marimo-export describe \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --json
```

The result identifies the attached session, marimo and marimo-export versions, adapter, and built-in exporter availability. Run it before a build when a plan depends on AnyWidget, Arrow, Parquet, or PNG producer extras.

## Shared options

| Option              | Contract                                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `--json`            | Wraps success data in a `marimo-export.cli.v1` envelope. `build` remains a raw build record for pipeline composition. |
| `--timeout-ms MS`   | Sets the active command deadline. Default `300000`.                                                                   |
| `--concurrency N`   | Bounds concurrent payload work for `publish`, `pull`, and `verify`. Default `8`, maximum `64`.                        |
| `--ref FILE` or `-` | Anchors a static publication to an `ExportRef` or build record.                                                       |

The timeout bounds how long the client waits for active command work. Remote cleanup can continue afterward with a separate 10-second request timeout. A remote notebook operation can continue after the client times out.

## Authentication and build-record trust

The CLI reads credentials from its environment:

| Variable              | Request behavior                                              |
| --------------------- | ------------------------------------------------------------- |
| `MARIMO_TOKEN`        | Supplies marimo authentication for HTTP and WebSocket access. |
| `MARIMO_SERVER_TOKEN` | Supplies marimo's skew-protection request header.             |

`pull` normally reconnects to the server recorded by the build. When either authentication variable is set, pass `--server` as an explicit trust anchor:

```bash
MARIMO_TOKEN="$MARIMO_TOKEN" \
  marimo-export pull notebook-export.build.json \
  --server http://127.0.0.1:2718/ \
  --out ./public/notebook-export
```

The normalized `--server` URL must match the build record before credentials are sent.

Static HTTP `inspect`, `read`, and `verify` commands do not read these authentication variables. Serve protected publications through an authenticated application path or use the TypeScript `httpSource()` API with request headers.

## Stdout, stderr, and exits

Success data goes to stdout. Progress and errors go to stderr. This separation keeps build and read pipelines machine-safe.

For commands other than `build`, `--json` emits:

```json
{
  "schema": "marimo-export.cli.v1",
  "command": "inspect",
  "data": {}
}
```

JSON errors use the same schema on stderr and include structured `details` when the underlying error supplies them:

```json
{
  "schema": "marimo-export.cli.v1",
  "command": "read",
  "error": {
    "code": "missing_output",
    "message": "Output \"summary\" is missing."
  }
}
```

| Exit  | Meaning                                                             |
| ----- | ------------------------------------------------------------------- |
| `0`   | Command completed successfully.                                     |
| `1`   | Runtime, remote, integrity, timeout, or failed-verification result. |
| `2`   | Invalid command usage.                                              |
| `130` | Cancelled with `SIGINT`, `SIGTERM`, or an abort signal.             |

## Agent workflow

An agent can discover and read a notebook result without Python access:

```bash
marimo-export inspect ./public/notebook-export \
  --limit 20 \
  --json

marimo-export inspect ./public/notebook-export \
  --scenario baseline \
  --limit 50 \
  --json

marimo-export read ./public/notebook-export baseline summary \
  --format json \
  --max-bytes 1000000 \
  --json
```

The responses carry the notebook digest, plan digest, resolved inputs, format ID, media type, payload digest, and byte size needed to ground a result in one published notebook state.
