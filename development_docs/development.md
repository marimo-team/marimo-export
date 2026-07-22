# Development

The workspace uses pnpm and Vite+ for TypeScript packages, examples, and the documentation site. uv owns the Python producer environment, checks, and package build. The root Makefile composes both toolchains.

Read [`architecture.md`](./architecture.md) before changing cache behavior, scenario execution, schemas, remote attachment, staging, or package boundaries.

## Install

Use the repository-pinned versions:

```bash
corepack enable
pnpm install --frozen-lockfile
uv sync --all-extras
```

The root uv environment installs every Python projection extra for tests. The published base distribution depends on the exact supported marimo release.

## Workspace map

| Path                       | Ownership                                                                                          |
| -------------------------- | -------------------------------------------------------------------------------------------------- |
| `packages/producer`        | Plans, scenario execution, projections, indexes, remote dispatcher, and the private marimo adapter |
| `packages/client`          | Universal reader, remote client, Node transfer APIs, and CLI                                       |
| `packages/loader-arrow`    | Arrow IPC decoding through Flechette                                                               |
| `packages/loader-parquet`  | Parquet decoding through hyparquet                                                                 |
| `packages/loader-vegalite` | Vega-Lite decoding and browser mounting                                                            |
| `apps/docs`                | VitePress documentation renderer                                                                   |
| `examples/_notebooks`      | Self-contained PEP 723 notebooks and adjacent export plans                                         |
| `examples/browser`         | Static HTTP consumer                                                                               |
| `examples/next-ssr`        | Next.js server-side consumer                                                                       |
| `examples/astro-ssr`       | Astro server-side consumer                                                                         |

AnyWidget support belongs in `packages/loader-anywidget`, published as `@marimo-team/marimo-export-anywidget`. Keep its AFM runtime and browser dependency out of the root package.

Loader manifests expose `src/index.ts` to the workspace and publish `dist` through `publishConfig.exports`, following the core package pattern. Lint, type checks, and example builds from a clean checkout must not depend on ignored package build output.

## Commands

The Makefile is the workspace contract:

```bash
make format
make lint
make typecheck
make test
make integration
make build
make check
```

`make build` runs the recursive Vite+ workspace build, then uses `uv build --package marimo-export` to create the Python wheel and source distribution. `make integration` depends on that build. `make check` runs formatting, linting, types, unit tests, builds, the real remote integration, and a packed npm install smoke test.

Run one TypeScript package while iterating:

```bash
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export build
```

Run focused Python checks from the workspace root:

```bash
uv run ruff check packages/producer
uv run ty check packages/producer
uv run pyrefly check
uv run --package marimo-export pytest -q packages/producer/tests
uv build --package marimo-export
```

Use Vite+ task selection when a package and its workspace dependencies need to build together:

```bash
pnpm exec vp run -t @marimo-team/marimo-export#build
```

Run [`make check`](../Makefile) before handoff. [`validation.md`](./validation.md) maps each change surface to focused evidence.

## Change the owning plane

Keep behavior with its owner:

- Producer and Python codec changes belong in `packages/producer`.
- Remote attachment and universal control transport belong in `packages/client/src/remote`.
- Filesystem transfer and CLI behavior belong in `packages/client/src/node`.
- Index validation, integrity verification, and immutable read APIs belong in the universal root entrypoint.
- Format-specific frontend dependencies belong in one loader package.

A wire-shape change crosses planes. Update its Python encoder, TypeScript decoder, public types, fixtures, tests, and contributor documentation in one change.

## Change a plan

The TypeScript plan validator is fast structural preflight. The Python decoder remains authoritative for Python syntax, normalized built-in options, and plan hashing. Syntax validation does not resolve names against a notebook. The scenario runner resolves input targets against the authored graph, then synthetic-cell compilation and execution resolve projection sources, notebook exporters, imports, and optional serializer dependencies.

When changing the plan contract:

1. Define one normalized Python wire shape.
2. Keep TypeScript preflight aligned with the JSON-visible portion of that shape.
3. Include every option that changes bytes in synthetic-cell identity.
4. Keep scenario labels and output labels outside projection identity.
5. Update the annotated example at `examples/_notebooks/finance.plan.yaml`.
6. Add cross-language fixtures for safe numbers, defaults, and unknown fields.

Plan inputs target graph definitions or marimo UI elements. Projection sources target definitions or expressions. Expose each publishable result as a notebook definition when later cells need to reference it.

## Add a custom format

A custom format pairs one Python exporter with one TypeScript loader through a stable format ID.

Define the exporter in the notebook or an importable module:

```py
import json

from marimo_export import Projection


def ndjson_projection(rows) -> Projection:
    payload = b"\n".join(
        json.dumps(
            row,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        for row in rows
    )
    return Projection(
        payload,
        format_id="ndjson.v1",
        media_type="application/x-ndjson",
    )
```

Match the format in TypeScript:

```ts
import type { OutputLoader } from "@marimo-team/marimo-export";

export function ndjson<T>(decodeRow: (input: unknown) => T): OutputLoader<readonly T[]> {
  return {
    formatId: "ndjson.v1",
    async load(output) {
      const text = await output.text();
      if (text.length === 0) return [];
      return text.split("\n").map((line) => decodeRow(JSON.parse(line)));
    },
  };
}
```

The Python exporter owns option validation and serialization. The TypeScript loader owns decoding. A loader that returns a mountable runtime object also owns that object's frontend lifecycle. Keep `metadata` JSON-compatible. Metadata carries small decoding facts. Payload bytes carry data.

An importable exporter plan entry requires an explicit version. Change that version when behavior changes outside notebook lineage. A notebook exporter definition already participates in graph lineage and may add a version for an external resource or protocol revision.

Test the exporter through its complete `Projection`. Test the loader through `ExportOutput.load()` so format matching, verified payload reading, and decoding use the public consumer path.

## Add a built-in format family

A built-in format earns a Python module, a stable payload contract, and a consumer path. Add serialization under `packages/producer/src/marimo_export/projection/exporters/<contract>.py`. Add only its descriptor to the built-in registry. Keep shared helpers inside that format family unless two independent contracts have the same invariant.

Use the frontend protocol as the module boundary:

- Arrow and Parquet share dataframe normalization.
- Vega-Lite JSON and PNG share specification normalization.
- Altair uses Vega-Lite rather than an Altair-specific wire format.
- AnyWidget uses the static model graph and its own loader.
- A future Plotly JSON contract gets its own exporter and loader.

For an interactive format, implement the Python encoder and frontend loader in one change. Define the payload schema, format ID, media type, trust boundary, mount lifecycle, teardown behavior, SSR import behavior, and missing-backend behavior before adding convenience APIs.

When adding AnyWidget support:

1. Add `_marimo/anywidget.py` for marimo state synchronization, model-reference traversal, buffer extraction, ESM specs, and virtual-file reads.
2. Add `projection/exporters/anywidget.py` for canonical `anywidget.v1` encoding and `Projection` metadata.
3. Add the explicit built-in descriptor and an `anywidget` Python extra.
4. Add `packages/loader-anywidget` with a strict decoder and a static AFM runtime derived from marimo's current model, host, binding, style, and cleanup behavior.
5. Give every loader a workspace source export and packed `dist` export so clean validation does not require prebuilt artifacts.
6. Keep loader imports SSR-safe. Parse on load and execute module code only from `mount()`.
7. Test raw widgets, marimo wrappers, binary state, CSS, nested widgets, module failures, aborts, idempotent disposal, and one real browser mount.
8. Add a cold and warm producer proof. Change state and ESM independently and verify each invalidates when marimo identity requires it.

The AnyWidget codec uses one verified `Projection` payload containing marimo's static notification shape. Its public frontend surface is the `anywidget()` loader, the decoded snapshot, `mount()`, the AFM model, and `dispose()`.

## Change cache behavior

Keep the two cache roles distinct:

- Native marimo cache entries restore authored cells and complete synthetic-cell returns.
- Content-addressed payload and index objects form the portable publication closure.

marimo owns native identity. Generated projection source encodes the projection ABI, source, exporter specification, normalized options, and any declared exporter version. It must not encode scenario IDs, publication labels, or plan order.

Keep native cache identity in marimo's graph, generated cell source, and hashable dependency values. When marimo intentionally omits content for a value such as `Html`, add the smallest primitive dependency token that represents the portable content. Define portable results with `Projection` and a matching frontend loader. A marimo `CustomStub` is a Python cache codec. It is not a publication format.

Any change to state handling must preserve the guarded cases in `test_runner.py`: getter-only hits, getter and setter identity, transitive setter detection, relinking restored state pairs, and fresh scenario graph state. Avoid claims about arbitrary Python side effects.

Notebook arguments are process state outside marimo's native cache identity. A child runner with user arguments executes with native caching disabled. Preserve this boundary unless upstream cache identity incorporates the argument vector.

Any change to HTML handling must preserve these boundaries:

- A primitive token carries prepared HTML content into native synthetic-cell identity.
- A targeted live producer repair recreates virtual media bytes when an authored cache restore has lost their registry.
- `html.v1` produces a static fragment with supported virtual media inlined.

Keep the token derived from graph references, nested paths, concrete types, and prepared HTML text. The terminal synthetic cell must remain eligible for marimo's native cache after any targeted producer repair.

## Change marimo integration

Private marimo imports are confined to `packages/producer/src/marimo_export/_marimo`. An AST boundary test enforces that rule. `Projection` stays at the public pickle path `marimo_export.Projection`. `packages/producer/src/marimo_export/projection` is internal implementation.

The adapter pins marimo 0.23.14. Before changing that pin:

1. Inspect the new upstream implementation for every imported private operation.
2. Update `_marimo/compat.py`, the Python requirement, and adapter code together.
3. Run boundary, runner, HTML portability, delivery, projection, and execution tests.
4. Run the real remote integration.
5. Confirm warm projection restoration, state guards, runner teardown, payload repair, stage expiry, and post-server reads.

Current upstream dependencies are:

| Adapter concern               | Upstream area                                                                         |
| ----------------------------- | ------------------------------------------------------------------------------------- |
| Saved notebook loading        | `marimo._session.notebook.serializer` and `marimo._ast.load`                          |
| Managed child execution       | `marimo._runtime.app.kernel_runner` and runtime contexts                              |
| Producer context boundary     | edit-mode `KernelRuntimeContext.session_mode` and relaxed kernel execution type       |
| Graph execution and overrides | `marimo._runtime.dataflow` and runner hooks                                           |
| UI updates                    | `marimo._runtime.commands.UpdateUIElementCommand`                                     |
| State guards                  | `marimo._runtime.state` and graph transitive references                               |
| Native projection persistence | `marimo._save.loaders.lazy` and cache configuration                                   |
| Polars cache restoration      | `marimo._save.stubs.lazy_stub.BLOB_DESERIALIZERS`                                     |
| HTML preparation              | `marimo._convert.common.dom_traversal`, virtual-file reads, MIME types, and data URLs |
| Portable object storage       | `marimo._save.stores` through the root cache `Store`                                  |

`describe` reports adapter and runtime versions before a caller builds. Treat a changed server process topology, missing runner finalizer, changed cache lifecycle, changed HTML virtual-file behavior, changed Polars deserialization path, or changed child runtime configuration as an adapter compatibility failure.

## Preserve remote inversion of control

Remote commands attach to a running marimo server. Do not add server launch, environment installation, package synchronization, SSH process management, or GPU provisioning to the TypeScript API or CLI.

The supported control inputs are the server URL, authentication values, and one server-owned target:

- A notebook path lets the existing server create or resume a kernel in its prepared environment.
- A session ID borrows an existing kernel.

The remote package owns connection setup, scratchpad protocol requests, stage leases, and cleanup of a session created through that connection. It never owns the marimo server process. Examples may show a separate `marimo edit` command to make the workflow reproducible, but that command remains operator setup outside marimo-export.

## Cache integration fixture

`examples/_notebooks/cache_matrix.py` and its adjacent plan exercise the real producer path. The notebook records authored computation and custom projection calls with separate counters.

Run:

```bash
make integration
```

The proof covers a cold scenario matrix, a warm build, output-label reuse, exporter-version invalidation, payload-mirror repair, incremental pull, and frontend reads after the server stops. Keep this fixture deterministic. Add a focused unit test for the local contract and an integration assertion for any cache identity or lifecycle change.

## CLI and package builds

[`architecture.md`](./architecture.md#package-boundaries) defines the public entrypoints and dependency boundaries.

The CLI is built from the same package. Data goes to stdout and diagnostics go to stderr. Plan, build-record, and reference documents are capped at 16 MiB. `build` writes a raw `marimo-export.build.v1` record so it can feed `pull -`. Other structured commands use a `marimo-export.cli.v1` envelope with `--json`. Plain text `read` writes verified UTF-8 payload bytes exactly. JSON output is decoded, and binary output requires `--out`.

Workspace packages remain at version `0.0.0`. Package-local TypeScript `dist/` directories and root `dist/` Python archives are derived files. Inspect package contents from the owning build and pack commands.
