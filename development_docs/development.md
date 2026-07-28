# Development

The workspace uses pnpm and Vite+ for browser packages, loaders, examples, and the documentation site. uv owns the Python package, CLI, tests, and build. The root Makefile composes both toolchains.

Read [`architecture.md`](./architecture.md) before changing schemas, live capture, projection caching, transfer tickets, or package boundaries.

## Install

Use the repository-pinned runtimes:

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm --filter @marimo-team/marimo-export-loader-anywidget exec \
  playwright install --only-shell chromium
uv sync --all-extras --locked
```

The package-scoped Playwright version selects the Chromium headless shell used by the AnyWidget browser gate.

The Python package temporarily pins `marimo @ git+https://github.com/peter-gy/marimo.git@0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`. The lock resolves that exact revision, which supplies the `BlobAsset` lazy-cache codec.

For unpublished cross-repository work, overlay the inspected core checkout:

```bash
uv pip install \
  --python .venv/bin/python \
  --editable /Users/petergy/Projects/personal/marimo
UV_NO_SYNC=1 uv run --package marimo-export \
  pytest -q packages/python/tests/test_marimo_cache.py
```

Set `UV_NO_SYNC=1` on each command that should retain the overlay. Run `uv sync --all-extras --locked` to restore the pinned Git revision.

Python package publication is gated on an official marimo release that contains the codec. The release change replaces the exact Git requirement with the released lower bound and validates the wheel through normal dependency resolution.

## Workspace map

| Path                                        | Ownership                                                                                |
| ------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `packages/python`                           | Python API, CLI, specifications, exporters, capture, transfer, and static reading        |
| `packages/python/src/marimo_export/_marimo` | Private marimo compatibility and runtime adapters                                        |
| `packages/python/src/marimo_export/_remote` | HTTP authentication, session selection, scratchpad transport, and Server-Sent Events     |
| `packages/browser`                          | Browser publication source, schema, verification, envelope decoding, and loader dispatch |
| `packages/loader-vegalite`                  | Vega-Lite decoding and mounting                                                          |
| `packages/loader-anywidget`                 | Static AnyWidget graph decoding and mounting                                             |
| `schemas`                                   | Generated external specification and publication schemas                                 |
| `apps/docs`                                 | VitePress renderer for `docs`                                                            |
| `examples/_notebooks`                       | Ordinary marimo notebooks and adjacent export specifications                             |
| `examples/browser`                          | Static browser publication consumer                                                      |

## Commands

```bash
make format
make format-check
make lint
make typecheck
make test
make integration
make build
make package-smoke
make check
```

Run focused Python checks from the workspace root:

```bash
uv run ruff check packages/python
uv run ty check packages/python
uv run pyrefly check
uv run --all-extras --package marimo-export pytest -q packages/python/tests
uv build --package marimo-export --clear --no-sources
```

Run the browser package while iterating:

```bash
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export build
```

Run the native AnyWidget browser gate while changing its loader or runtime:

```bash
pnpm --filter @marimo-team/marimo-export-loader-anywidget test:browser
```

Run [`make check`](../Makefile) before handoff. [`validation.md`](./validation.md) maps each change surface to focused evidence.

## Change the owning boundary

- Domain models, Python reading, capture orchestration, and CLI behavior belong in `packages/python` outside adapter packages.
- Private marimo behavior belongs in `_marimo`.
- Remote HTTP and scratchpad behavior belongs in `_remote`.
- Static browser behavior belongs in `packages/browser`.
- Format dependencies belong in the matching Python extra or browser loader package.

A wire-shape change updates the schema, Python decoder, browser decoder, fixtures, tests, examples, and documentation in one change.

## Change an export specification

`ExportSpec` is strict and Python-owned. When changing it:

1. Update the private Pydantic v2 wire model in `marimo_export.spec`.
2. Update Python semantic validation and decoding where the behavior changes.
3. Preserve JSON-safe values and safe integers across Python and TypeScript boundaries.
4. Keep variant, output, and format labels outside projection cache identity.
5. Update an adjacent example specification.
6. Add unknown-field and malformed-input tests.
7. Run `make schemas`, commit `schemas/spec.v1.json`, and run `make schemas-check`.
8. Verify the live bridge against a running notebook.

Variants target existing marimo UI controls. Sources target live globals, trusted expressions, or rendered cell payloads.

`_SpecWire` generates the JSON Schema and owns structure and portable lexical constraints. Python `ExportSpec` decoding remains authoritative for Python identifiers and keywords, expression syntax, exporter import references, and built-in exporter option semantics.

Publication wire changes start in the private `_PublicationWire` model and its nested Pydantic models in `marimo_export.publication`. Run `make schemas` to regenerate `schemas/publication.v1.json`, then update the browser decoder, shared fixtures, and conformance tests. Do not edit either checked-in schema by hand.

Keep runtime semantic checks outside generated-schema extensions. The 262,144-byte canonical projection-metadata limit is enforced by `Projection` and on raw `BlobAsset.metadata_json` before decoding. It is not represented by a custom JSON Schema keyword.

## Add a format

Define a Python exporter:

```python
from marimo_export import Projection


def ndjson(rows) -> Projection:
    return Projection(
        encode_ndjson(rows),
        format_id="ndjson.v1",
        media_type="application/x-ndjson",
        filename="rows.ndjson",
    )
```

Add an exporter descriptor with its public name, import reference, version, normalized options, optional extra, and availability probe. Keep serialization in the format module.

Define a browser loader when consumers need typed decoding or mounting:

```ts
import type { FormatLoader } from "@marimo-team/marimo-export";

export function ndjsonLoader<T>(decode: (value: unknown) => T): FormatLoader<readonly T[]> {
  return {
    formatId: "ndjson.v1",
    async load(context) {
      const text = await context.text();
      if (text.length === 0) return [];
      return text.split("\n").map((line) => decode(JSON.parse(line)));
    },
  };
}
```

Test the exporter through a complete `Projection` and capture. Test the loader through `PublishedFormat.load()` so verification and `BlobAsset` decoding remain in the exercised path.

An interactive loader also defines mount prerequisites, cancellation, teardown, content security policy, and executable-code behavior.

## Change cache integration

Keep source identity and result persistence separate:

- `CustomStub` defines deterministic identity and restoration for a source type.
- A source package can register its stub directly or through marimo's lazy stub registration hook when the optional type is first encountered.
- A stub's deterministic `to_bytes()` must encode the concrete source type and codec or schema version. Values with different semantics or decoding contracts must produce different identity bytes.
- `BlobAsset` contains the complete portable projector result.
- The lazy cache owns lookup, `.bin` persistence, and restoration.
- `CacheAssetRef` identifies the exact cache object selected for publication.

Stub registration is process-global and remains owned by the source package. Capture reads the active registry through marimo's normal hashing path and does not mutate it per request.

Every cache change needs cold, warm, changed source, changed exporter version, changed options, unhashable fallback, and registered custom-stub tests.

Resolve the asset from the current callable's cache hash after a durable flush. Avoid session-wide touched-key sets. Read and verify the exact cache object through the configured `Store` for both cold and warm paths.

## Change live capture

Capture is a transaction over borrowed notebook state:

1. Inspect the current document and controls.
2. Apply one variant.
3. Resolve and project selected values.
4. Restore the starting controls and stale-cell set.
5. Register exact cache objects for transfer.
6. Download and verify them.
7. Commit a new publication directory or replace the existing index at its stable path.
8. Release temporary files.

Keep primary failures authoritative during restoration, transfer release, and client close. Add integration evidence for every lifecycle change.

The scratchpad protocol uses bounded Server-Sent Events and one correlation marker per request. It imports the marimo-export bridge in the running kernel, so the notebook environment must contain the matching package and requested exporter extras. Avoid automatic retries after dispatch. Preserve token redaction in URLs, headers, exceptions, receipts, and CLI output.

## Change marimo integration

Private imports stay in `packages/python/src/marimo_export/_marimo`. Before updating the marimo dependency:

1. Inspect every imported private seam in the target marimo checkout.
2. Update `_marimo/compat.py` and adapter code together.
3. Run code-mode, cache, `BlobAsset`, transfer, and document-digest tests.
4. Run the real integration against a marimo edit server.
5. Confirm cold and warm capture through the configured store.
6. Confirm UI restoration on success and failure.
7. Confirm Python and browser reads after the server stops.

`make integration` supplies the real-process success path, same-process cold and warm projection, transfer, and detached Python and CLI read evidence. Use focused bridge tests for failure restoration and the browser workflow in [`validation.md`](./validation.md) for post-shutdown browser evidence.

The first compatible marimo release becomes the package lower bound after the `.bin` `BlobAsset` codec passes the private-seam, live integration, and package-resolution gates.

## CLI behavior

The CLI is the Python package entrypoint. `--json` writes one success or error object to stdout. Human progress goes to stderr. Preserve exit codes `0`, `1`, `2`, `3`, `4`, `5`, `6`, `7`, `130`, and `141` for success, internal errors, input, connection, session selection, capture, publication integrity, filesystem failures, interruption, and a closed output pipe.

`session SERVER` lists active session summaries. `session SERVER --session ID` inspects one session. Binary `read` requires `--to FILE`. `capture`, `inspect`, `read`, and `verify` accept the index, per-envelope, and complete-publication byte limits. Capture validates its specification and destination before connecting.

Local Python reads use descriptor-relative no-follow opens on POSIX. The Windows fallback rejects reparse points and requires the publication tree to remain stable until its second file-identity check completes.

New publication destinations use an atomic no-replace directory rename. Replacement keeps the destination path stable, hard-links verified assets, retains assets for readers of the previous index, rejects same-key content collisions, and atomically replaces `index.json` as the commit point.

## Browser package

The npm package exposes one browser entrypoint. Keep it free of Node built-ins, session control, local filesystem APIs, and Python capture dependencies.

`openPublication()` owns index validation and loader registration. `PublishedFormat` owns verified envelope reads and loader dispatch. Loader packages receive inner bytes through `FormatLoaderContext` and never decode `return.bin` directly.

## Documentation

`docs` is the public source rendered by `apps/docs`. `development_docs` owns implementation and validation guidance. Package READMEs ship with their packages.

Build the docs after changing navigation, examples, commands, or API names:

```bash
BASE_PATH=/marimo-export pnpm --filter @marimo-team/marimo-export-docs build
pnpm --filter @marimo-team/marimo-export-docs typecheck
```
