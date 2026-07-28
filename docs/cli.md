# CLI

The Python `marimo-export` CLI captures live notebook results and gives agents structured access to static publications.

```bash
uv sync --all-extras --locked
export MARIMO_EXPORT_TOKEN="<token>"
uv run marimo-export capture \
  http://localhost:3456/ \
  --spec finance.export.yaml \
  --output dist/finance
```

For the current source checkout, run `uv sync --all-extras --locked` and invoke the CLI through `uv run`. The same marimo-export checkout must be importable inside the running notebook environment because capture executes there.

## Commands

| Command                       | Contract                                                     |
| ----------------------------- | ------------------------------------------------------------ |
| `session SERVER`              | List active marimo session summaries                         |
| `session SERVER --session ID` | Inspect one session and its selectable values                |
| `capture SERVER`              | Project selected live results and commit a local publication |
| `inspect PUBLICATION`         | List variants, outputs, and formats                          |
| `read PUBLICATION OUTPUT`     | Read one verified format                                     |
| `verify PUBLICATION`          | Verify the index and every referenced cache asset            |

Long options require their complete spellings.

`marimo-export --version` prints the installed marimo-export version and exits.

## List and inspect sessions

```bash
uv run marimo-export session http://localhost:3456/ --json
```

The result lists each active session's ID, filename, and server-side path. Inspect one session explicitly:

```bash
uv run marimo-export session http://localhost:3456/ \
  --session SESSION_ID \
  --json
```

`session` and `capture` accept `--timeout SECONDS`. The default is 300 seconds.

The inspected result contains the session ID, notebook filename and path, live document SHA-256, `marimo_version`, `marimo_export_version`, global descriptors, frozen cell-output descriptors, UI controls, and the availability and format ID of each built-in exporter in the attached kernel. Each item in `globals` contains `name` and a qualified `python_type` descriptor. A control record contains `name`, `type`, `value`, `sensitive`, and `domain`. A domain can report `options`, `start`, `stop`, `step`, `steps`, `max-selections`, `allow-select-none`, and `precision`. Password values are redacted as `null`. Capture rejects a variant that targets a sensitive control before changing notebook state.

Set `MARIMO_EXPORT_TOKEN` for marimo access authentication and `MARIMO_EXPORT_SERVER_TOKEN` for the `Marimo-Server-Token` header. A local one-off command may carry one `access_token` query value in the server URL. Prefer the environment variables for shell history, scripts, and shared examples.

## Capture

```bash
uv run marimo-export capture \
  http://localhost:3456/ \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output dist/finance \
  --json
```

`--session` is optional when the server exposes one active session. Before connecting, capture loads and validates the specification and validates whether the destination can accept the requested operation. For `--replace`, this verifies the existing publication and every referenced asset.

A new destination commits through an atomic no-replace directory rename. `--replace` keeps the destination path stable. It revalidates the existing publication before commit, hard-links verified new cache assets into the existing cache, retains old assets for readers that already loaded the previous index, then atomically replaces `index.json` as the commit point. A cache key that already contains different bytes fails the replacement.

`--max-index-bytes BYTES` defaults to `16777216`. It limits the publication index returned by capture. `--max-asset-bytes BYTES` defaults to `67108864` and limits each outer `BlobAsset` cache envelope. Capture rejects an oversized declared result before download.

`--max-publication-bytes BYTES` defaults to `536870912`. It limits the serialized index plus the declared sizes of every unique outer envelope. Capture checks the complete closure before downloading an asset.

`capture` owns session selection, projection, verified transfer, local commit, and server cleanup. Its result reports the publication path, variants, outputs, asset count, transferred bytes, and projection cache dispositions.

## Inspect a publication

```bash
uv run marimo-export inspect dist/finance --json
```

The result exposes every variant's recorded controls, output names, format names, format IDs, media types, and metadata. Cache references stay inside the publication reader.

`--max-index-bytes BYTES` defaults to `16777216` and limits `index.json` before decoding.

`--max-asset-bytes BYTES` defaults to `67108864` and preflights every declared outer cache envelope. Inspection remains metadata-only and does not read the envelope bytes.

`--max-publication-bytes BYTES` defaults to `536870912` and preflights the actual index bytes plus the declared unique asset closure.

## Read one format

```bash
uv run marimo-export read dist/finance summary \
  --variant current \
  --format json \
  --json
```

`--variant` is required because a valid publication may omit `current`. `--format` selects one published representation.

With `--json`, the result records `variant`, `output`, the selected `format` alias, its stable `format_id`, `media_type`, and decoded `value`. A `--to` result records the same selection identity plus the absolute `path` and written `bytes`.

A missing variant, output, or format returns exit code `2` with error code `not_found`. Its bounded `details` record `kind`, `name`, `name_truncated`, `available`, `available_count`, and `available_truncated` so an agent can select from the reported surface.

`--max-index-bytes BYTES` defaults to `16777216` and limits `index.json`. `--max-asset-bytes BYTES` defaults to `67108864` and limits the outer cache envelope before decoding.

`--max-publication-bytes BYTES` defaults to `536870912` and rejects an oversized index-plus-unique-asset closure before the selected asset is read.

Text and JSON formats can be emitted to stdout. Binary formats require an output path:

```bash
uv run marimo-export read dist/finance chart \
  --variant aapl \
  --format png \
  --to chart.png
```

`--to` creates a new file and fails when that path already exists.

## Verify a publication

```bash
uv run marimo-export verify dist/finance --json
```

Verification reads every unique asset referenced by `index.json`, checks its recorded size and SHA-256, decodes the `BlobAsset` envelope, and checks the envelope fields against the index. Pass `--max-index-bytes BYTES`, `--max-asset-bytes BYTES`, or `--max-publication-bytes BYTES` to set positive read limits. The complete-publication limit defaults to `536870912` and is checked before an asset read. Malformed, missing, corrupt, or oversized publication data returns exit code `6`.

## Structured output

`--json` writes exactly one JSON object to stdout. Success uses:

```json
{
  "ok": true,
  "result": {}
}
```

Failure uses a nonzero exit status and:

```json
{
  "ok": false,
  "error": {
    "code": "selection_error",
    "message": "notebook global 'summary' is unavailable"
  }
}
```

Diagnostics go to stderr. Query-string credentials plus the configured access-token and server-token environment values are redacted before an error is formatted.

## Exit codes

| Code  | Meaning                                                             |
| ----- | ------------------------------------------------------------------- |
| `0`   | Command completed                                                   |
| `1`   | Unexpected internal error                                           |
| `2`   | Invalid arguments, specification, read selector, or request         |
| `3`   | Connection or authentication failed                                 |
| `4`   | Session selection failed                                            |
| `5`   | Capture source or control selection, projection, or transfer failed |
| `6`   | Publication or asset is malformed, missing, corrupt, or oversized   |
| `7`   | Filesystem operation failed                                         |
| `130` | Operation interrupted                                               |
| `141` | Output pipe closed before the command finished                      |

## Agent flow

An agent can discover a live source, capture it, and consume one bounded result:

```bash
export MARIMO_EXPORT_TOKEN="<token>"
export MARIMO_URL="http://localhost:3456/"

uv run marimo-export session "$MARIMO_URL" --json

uv run marimo-export session "$MARIMO_URL" \
  --session SESSION_ID \
  --json

uv run marimo-export capture "$MARIMO_URL" \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output dist/finance \
  --json

uv run marimo-export inspect dist/finance --json

uv run marimo-export read dist/finance summary \
  --variant current \
  --format json \
  --json
```

The selection names identify one published representation. The reader verifies its cache asset before returning a value.
