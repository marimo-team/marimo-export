# marimo-export for Python

`marimo-export` captures selected results from a running marimo notebook and reads the resulting static publication from Python or the command line.

From the source checkout, prepare the environment that runs the notebook and capture client:

```bash
uv sync --all-extras --locked
```

The Python dependency and lockfile temporarily pin `peter-gy/marimo` commit `0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`, which supplies the `BlobAsset` lazy-cache codec required by capture. Publishing the Python distribution requires a compatible marimo core release and a corresponding released dependency bound.

## Install after the core release

After marimo publishes the required core codec and marimo-export declares that released lower bound, install a released package with:

```bash
uv add "marimo-export[png]"
```

## Capture

Start the notebook through its prepared environment, run it, then capture the active session:

```bash
uv run --with altair==6.0.0 marimo edit examples/_notebooks/finance.py \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 3456

export MARIMO_EXPORT_TOKEN="<token>"
uv run marimo-export capture \
  http://localhost:3456/ \
  --spec examples/_notebooks/finance.export.yaml \
  --output dist/finance
```

`--no-sandbox` keeps the kernel in the prepared uv environment. Capture executes marimo-export inside the running kernel, so that environment must contain the same marimo-export version as the client and every selected exporter extra. The external specification selects live globals, trusted expressions, or rendered cell payloads. It can also define finite variants through existing marimo UI controls. The notebook source needs no marimo-export imports.

## Python API

Use `capture()` for one operation:

```python
import os

from marimo_export import capture

result = capture(
    "http://localhost:3456/",
    spec="examples/_notebooks/finance.export.yaml",
    into="dist/finance",
    access_token=os.environ["MARIMO_EXPORT_TOKEN"],
)

print(result.path)
print(result.cache)
```

Use `Client` when several operations share a connection:

```python
import os

from marimo_export import Client

with Client(
    "http://localhost:3456/",
    access_token=os.environ["MARIMO_EXPORT_TOKEN"],
) as client:
    session = client.session()
    description = session.inspect()
    result = session.capture(
        spec="examples/_notebooks/finance.export.yaml",
        into="dist/finance",
    )

print([global_.to_dict() for global_ in description.globals])
print(result.assets)
```

`client.session()` requires exactly one active session. Pass a primary session ID when the server hosts several sessions. Closing a client leaves the selected marimo session running.

The package root exports runtime-checkable, read-only protocols for `Session`, `NotebookProvenance`, `ProducerProvenance`, `Publication`, `PublishedVariant`, `PublishedOutput`, and `PublishedFormat`. Obtain these objects through `Client` and `open_publication()`.

### `capture(server_url, *, spec, into, session=None, replace=False, access_token=None, server_token=None, timeout=300.0, max_index_bytes=16777216, max_asset_bytes=67108864, max_publication_bytes=536870912)`

Captures selected values from one existing session and returns a `CaptureResult`. Omitting `session` requires exactly one active session. `max_index_bytes` limits the returned publication index. `max_asset_bytes` limits each outer `BlobAsset` cache envelope. `max_publication_bytes` limits the serialized index plus the declared sizes of the unique outer envelopes. Capture checks all three limits before downloading an asset.

### `Client(server_url, *, access_token=None, server_token=None, timeout=300.0, max_index_bytes=16777216, max_asset_bytes=67108864, max_publication_bytes=536870912)`

Connects to a user-managed marimo server.

- `server_url`: Absolute HTTP or HTTPS server URL. It may contain one `access_token` query value.
- `access_token`: Explicit marimo access token. It must match the URL token when both are present.
- `server_token`: Optional `Marimo-Server-Token` value.
- `timeout`: Request timeout in seconds.
- `max_index_bytes`: Maximum bytes accepted for the returned publication index.
- `max_asset_bytes`: Maximum bytes accepted for one outer `BlobAsset` cache envelope. The client checks each declared asset size before download.
- `max_publication_bytes`: Maximum serialized index bytes plus declared unique envelope bytes. The client checks the complete closure before download.

Credentials are redacted before public errors, receipts, and publication data are constructed.

### `Client.sessions()`

Returns active sessions as a tuple without inspecting notebook state. Each `Session` exposes `id`, `filename`, and `path`.

### `Client.session(session_id=None)`

Returns a borrowed `Session`. Omitting `session_id` requires exactly one active session. Missing or ambiguous selection errors include the active session details. The owning `Client` must remain open while the session is used.

### `Session.inspect()`

Returns a `SessionDescription` with the session ID, notebook filename and path, document SHA-256, marimo and marimo-export versions, selectable global descriptors, frozen cell-output descriptors, existing UI controls, and `builtin_exporters` resolved inside the attached kernel.

`SessionDescription.to_dict()` returns the same fields as detached JSON-compatible values.

| Member                  | Contract                                      |
| ----------------------- | --------------------------------------------- |
| `session_id`            | Borrowed session ID                           |
| `filename`, `path`      | Server-reported notebook location             |
| `document_sha256`       | Digest of the live notebook document          |
| `marimo_version`        | marimo version inside the attached kernel     |
| `marimo_export_version` | marimo-export version inside that kernel      |
| `globals`               | Sorted `GlobalDescription` values             |
| `cells`                 | Authored cell descriptors                     |
| `controls`              | Existing UI control descriptors               |
| `builtin_exporters`     | Built-in exporter availability in that kernel |

Each `GlobalDescription` exposes the global `name`, its qualified `python_type` descriptor, and `to_dict()`. Names are bounded to 1024 characters and Python type descriptors to 512 UTF-8 bytes. A longer type descriptor is shortened with a `#sha256:` digest suffix. Each `BuiltinExporterDescription` exposes the exporter `name`, stored `format_id`, whether it is `available`, its optional installation `extra`, and `to_dict()`. The tuple is sorted by exporter name. Each `CellDescription` exposes `id`, optional `name`, optional execution `status`, `has_output`, optional output `media_type`, and `to_dict()`.

Each `ControlDescription` exposes `name`, `type`, a detached JSON `value`, `sensitive`, a detached JSON `domain`, and `to_dict()`. When available and JSON-safe, `domain` can contain `options`, `start`, `stop`, `step`, `steps`, `max-selections`, `allow-select-none`, and `precision`. Sensitive controls publish `value: null` and an empty domain. Capture rejects a variant that targets a sensitive control before applying any UI value.

### `Session.capture(*, spec, into, replace=False)`

Captures one `ExportSpec` into a local publication and returns a `CaptureResult`.

- `spec`: An `ExportSpec`, mapping, JSON file, or YAML file.
- `into`: Destination publication directory.
- `replace`: Commit a verified replacement at the existing destination path.

Capture preflights named globals, cells, and exporters before UI mutation. It then applies each UI variant, projects selected values inside the live kernel, restores the starting controls and stale-cell set, downloads verified marimo cache objects, commits `index.json` last, and releases temporary server files. Trusted expressions evaluate after each variant settles.

`Session.capture()` inherits the index, asset, and complete-publication limits from its owning `Client`.

Capture validates the specification and destination before remote execution. For replacement, this preflight verifies the existing publication and every referenced asset. A new destination commits through an atomic no-replace directory rename. Replacement revalidates the destination before commit, hard-links verified new cache assets into the existing cache, retains old assets for readers that already loaded the previous index, and atomically replaces `index.json` as the commit point. A same-key asset with different bytes fails the replacement.

### `CaptureResult`

| Member              | Contract                                            |
| ------------------- | --------------------------------------------------- |
| `path`              | Committed local publication path                    |
| `session_id`        | Borrowed marimo session ID                          |
| `variants`          | Published variant names                             |
| `outputs`           | Output names across the publication                 |
| `assets`            | Number of unique transferred cache assets           |
| `bytes_transferred` | Total transferred envelope bytes                    |
| `cache`             | `CacheSummary` with `hits`, `misses`, and `skipped` |
| `to_dict()`         | Detached JSON-compatible capture summary            |

`CacheSummary.to_dict()` returns its three counts as a detached JSON-compatible object.

## Read a publication

```python
from marimo_export import open_publication

publication = open_publication("dist/finance")
description = publication.describe()
summary = (
    publication
    .variant("current")
    .output("summary")
    .format("json")
    .json()
)

publication.verify()
```

`open_publication()` validates the index. `publication.describe()` returns detached public metadata. A `PublishedFormat` read verifies the selected MessagePack `BlobAsset` against its size and SHA-256 before returning inner bytes, text, or JSON.

### `open_publication(path, *, max_index_bytes=16777216, max_asset_bytes=67108864, max_publication_bytes=536870912)`

Opens a local directory and returns a `Publication`. The reader bounds `index.json` by `max_index_bytes` and validates it immediately. It preflights every declared envelope size against `max_asset_bytes`, then checks the actual index bytes plus the declared sizes of every unique referenced asset against `max_publication_bytes`. It verifies each cache object when its format is read or when `publication.verify()` is called. Increase a limit explicitly for a trusted publication that exceeds its default.

On Windows, keep the publication directory tree unchanged until the reader completes its second file-identity check. The reader rejects reparse points and fails when those validation checks detect a changed path.

### Publication navigation

| Member                      | Contract                                                |
| --------------------------- | ------------------------------------------------------- |
| `publication.notebook`      | Notebook filename and live document SHA-256             |
| `publication.producer`      | marimo and marimo-export versions                       |
| `publication.variant_names` | Available variant names                                 |
| `publication.variant(name)` | Select one explicit variant                             |
| `variant.name`              | Selected variant name                                   |
| `variant.controls`          | Values for control names declared by the specification  |
| `variant.outputs`           | Output names in the selected variant                    |
| `variant.output(name)`      | Select one output                                       |
| `output.variant`            | Containing variant name                                 |
| `output.name`               | Selected output name                                    |
| `output.formats`            | Format names on the selected output                     |
| `output.format(name)`       | Select one representation                               |
| `format.variant`            | Containing variant name                                 |
| `format.output`             | Containing output name                                  |
| `format.name`               | Selected format label                                   |
| `publication.describe()`    | Detached public metadata without cache references       |
| `publication.verify()`      | Verify every unique asset and return the verified count |

A missing variant, output, or format raises `PublicationError` with code `not_found`. Its bounded details contain `kind`, `name`, `name_truncated`, `available`, `available_count`, and `available_truncated` for agent recovery.

### `PublishedFormat`

| Member                 | Contract                                                        |
| ---------------------- | --------------------------------------------------------------- |
| `format_id`            | Portable format identifier                                      |
| `media_type`           | Media type for the inner data                                   |
| `metadata`             | Detached JSON metadata                                          |
| `filename`             | Optional base filename from the verified envelope               |
| `bytes()`              | Return inner bytes after verification                           |
| `text()`               | Decode verified UTF-8 bytes when the charset is absent or UTF-8 |
| `json(max_values=...)` | Parse verified bytes with a positive JSON-unit limit            |
| `verify()`             | Verify the selected envelope without decoding its inner data    |

`text()` accepts a media type with no charset or an explicit UTF-8 charset. Read other encodings with `bytes()` and decode them in the application.

`json()` defaults `max_values` to `100000`. The count includes containers, scalar values, and object keys. Pass a larger positive safe integer when a trusted projected document requires it.

## Custom exporters

An exporter receives a selected Python value and returns a `Projection`. A cell source supplies its rendered payload data. Use a named global when the exporter needs the original Python object or a live AnyWidget model. Asynchronous exporters may return an awaitable that resolves to a `Projection`.

```python
import json

from marimo_export import Projection


def geojson(value: object) -> Projection:
    return Projection(
        json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        format_id="geojson.v1",
        media_type="application/geo+json",
        filename="regions.geojson",
        metadata={},
    )
```

### `Projection(data, *, format_id, media_type, filename=None, metadata=None)`

Describes one portable representation.

- `data`: Bytes consumed by Python or the browser loader.
- `format_id`: Stable loader identifier that starts with an alphanumeric character, continues with alphanumerics, `.`, `_`, `+`, or `-`, and uses at most 255 ASCII bytes.
- `media_type`: Printable ASCII `type/subtype` value with optional parameters using at most 1024 bytes.
- `filename`: Optional Windows-portable base filename using at most 255 UTF-8 bytes.
- `metadata`: JSON object with decoding or provenance facts, bounded to 100,000 units, 256 nesting levels, and 262,144 canonical UTF-8 JSON bytes.

Construction rejects non-byte data, invalid or oversized format IDs and media types, unsafe filenames, and metadata outside the shared JSON contract.

The marimo adapter converts the projection into a cached `BlobAsset`. Exporter code remains independent of marimo cache keys and publication layout.

Reference an installed exporter from the specification:

```yaml
formats:
  geojson:
    exporter:
      import: my_project.exports:geojson
      version: "1"
```

Import and notebook-global variable exporters require an explicit version. A notebook-global callable uses `variable` in place of `import`.

## Built-in exporters

| Name        | Format ID              | Python extra |
| ----------- | ---------------------- | ------------ |
| `json`      | `json.v1`              | Base         |
| `text`      | `text.v1`              | Base         |
| `bytes`     | `bytes.v1`             | Base         |
| `html`      | `html.v1`              | Base         |
| `vegalite`  | `vegalite.v1`          | Base         |
| `arrow`     | `dataframe.arrow.v1`   | `dataframe`  |
| `parquet`   | `dataframe.parquet.v1` | `dataframe`  |
| `png`       | `vegalite.png.v1`      | `png`        |
| `anywidget` | `anywidget.v1`         | `anywidget`  |

Three built-in exporters accept options:

| Exporter  | Option        | Contract                                                                     |
| --------- | ------------- | ---------------------------------------------------------------------------- |
| `json`    | `indent`      | `null` by default, or a non-negative JavaScript-safe integer                 |
| `json`    | `sort_keys`   | Boolean, default `true`                                                      |
| `parquet` | `compression` | Default `NONE`. Accepts `NONE`, `SNAPPY`, `GZIP`, `BROTLI`, `LZ4`, or `ZSTD` |
| `png`     | `scale`       | Finite positive number, default `1`                                          |

Parquet compression names are case-insensitive and normalize to uppercase. `null` compression normalizes to `NONE`. Integer PNG scales must stay within the JavaScript safe range. `text`, `bytes`, `html`, `arrow`, `vegalite`, and `anywidget` accept no option keys.

Alias a built-in exporter under another publication label with an explicit `exporter`:

```yaml
formats:
  thumbnail:
    exporter: png
    options:
      scale: 0.5
```

The source checkout installs all exporter extras with:

```bash
uv sync --all-extras --locked
```

For a released package, select the same extras in the running notebook environment. Exporters execute on the server side.

## Cache behavior

For a cacheable source, marimo hashes the source value, exporter, version, normalized options, and projection ABI. A warm call restores the complete `BlobAsset` and skips exporter execution.

If marimo cannot hash a source value, marimo-export runs the exporter live, encodes its `BlobAsset`, and persists those primitive bytes through a separate cached function. The capture succeeds and reports cache reuse as `skipped`.

A custom source package may register marimo's `CustomStub` for deterministic hashing and restoration. Stub registration remains a source-package concern.

## Errors

Service, capture, and publication failures derive from `MarimoExportError`. The package root exports `SpecError`, `TransportError`, `SessionError`, `CaptureError`, `PublicationError`, and `IntegrityError` as supported handling points. Invalid local arguments, object lifecycle errors, serializer errors, and filesystem operations may raise their native `TypeError`, `ValueError`, `RuntimeError`, or `OSError` boundaries.

Each `MarimoExportError` exposes a stable `code`, detached JSON `details`, and `wire()` for one machine-readable error object.

The CLI maps these boundaries to stable exit categories and emits one `{ "ok": ... }` object when `--json` is set.

The CLI exposes `session`, `capture`, `inspect`, `read`, and `verify`. `session SERVER` lists active session summaries. Add `--session ID` to inspect one session. `capture` prevalidates the specification and destination. `capture`, `inspect`, `read`, and `verify` accept `--max-index-bytes`, `--max-asset-bytes`, and `--max-publication-bytes`. `read` requires an explicit `--variant` and `--format`.

See the [full documentation](https://github.com/marimo-team/marimo-export/tree/main/docs) for specification, capture, browser, CLI, and trust contracts.
