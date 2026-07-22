# Validation

Validate through the boundary a consumer depends on. Unit tests protect local contracts. The remote integration proves production, transfer, and consumption across real marimo, Python, HTTP, WebSocket, filesystem, and TypeScript boundaries.

## Required handoff gate

Run from the workspace root:

```bash
make format
make check
```

Review the files changed by `make format` before running the gate. `make check` runs:

1. Vite+ and Ruff format checks.
2. Vite+ and Ruff linting.
3. TypeScript, ty, and Pyrefly type checks.
4. TypeScript and Python unit suites.
5. Every TypeScript workspace build.
6. Python wheel and source-distribution builds through uv.
7. The real remote marimo integration.
8. A packed npm install that imports every public entrypoint and runs the installed CLI.

## Evidence by change surface

| Change surface                                                         | Required evidence                                                                 |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Plan decoding, defaults, inputs, options, or scenario uniqueness       | Python and TypeScript plan tests plus ty and Pyrefly                              |
| Authored scheduling, UI convergence, state guards, or runner lifecycle | `test_runner.py`, context tests, and remote integration                           |
| Process gate, notebook user arguments, nested apps, or Polars restore  | Runner subprocess tests, context tests, and remote integration                    |
| Producer mode and execution-type boundary                              | Context and actual-kernel remote tests plus a relaxed edit-mode integration       |
| Synthetic-cell identity or `Projection`                                | Projection and execution-boundary tests plus remote integration                   |
| HTML cache identity, virtual media repair, or static HTML portability  | HTML and runner tests, plus a fresh-process proof for restart changes             |
| Root `Store` keys, immutable writes, or mirror repair                  | Cache, index, worker tests, and remote integration                                |
| Index or reference schema                                              | Python index tests, TypeScript reference and reader tests, and remote integration |
| Universal reader or `ExportSource`                                     | Reader and source tests in browser-compatible modules                             |
| Remote protocol, attachment, ownership, close, or timeout              | Remote and session tests plus remote integration                                  |
| Stage TTL, release, or restart adoption                                | Python delivery tests plus remote integration                                     |
| Pull, atomic writes, local path safety, or verification                | Checkout and source tests plus remote integration                                 |
| CLI arguments, stdout, stderr, JSON, or exit codes                     | CLI tests through `runCli()` and the owning package build                         |
| Arrow, Parquet, Vega-Lite, or AnyWidget loader                         | Owning loader tests, package build, and a real decoder or browser mount           |
| Package exports, versions, dependencies, or build backend              | Public API tests, full build, packed install smoke, and content inspection        |
| Browser rendering or interaction                                       | Browser run against a pulled publication                                          |
| Next.js or Astro integration                                           | Framework build and server-rendered or generated-page read                        |
| Documentation navigation or examples                                   | VitePress build and copied-command verification                                   |
| marimo pin or private adapter                                          | Python suite, remote integration, package inspection, and upstream seam review    |

## Focused TypeScript checks

Run the core package suite:

```bash
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export build
pnpm --filter @marimo-team/marimo-export test:package
```

The automated CLI suite calls `runCli()` in-process. The package smoke installs the packed tarball in a temporary project, imports the root, `/remote`, and `/node` entrypoints, and runs the installed binary:

```bash
pnpm --filter @marimo-team/marimo-export build
pnpm --filter @marimo-team/marimo-export test:package
```

Run one test file while iterating:

```bash
pnpm --dir packages/client exec vp test tests/reader.test.ts --run
pnpm --dir packages/client exec vp test tests/session.test.ts --run
```

Build one loader and its workspace dependencies:

```bash
pnpm exec vp run -t @marimo-team/marimo-export-arrow#build
```

Replace the package name with the loader under test.

## Focused Python checks

Run static checks and the Python suite:

```bash
uv run ruff check packages/producer
uv run ty check packages/producer
uv run pyrefly check
uv run --package marimo-export pytest -q packages/producer/tests
```

Run cache-boundary files while iterating:

```bash
uv run --package marimo-export pytest -q packages/producer/tests/test_execution_boundary.py
uv run --package marimo-export pytest -q packages/producer/tests/test_runner.py
uv run --package marimo-export pytest -q packages/producer/tests/test_projection.py
uv run --package marimo-export pytest -q packages/producer/tests/test_delivery.py
```

`test_execution_boundary.py` proves the targeted-runner flush boundary, a complete `Projection` round trip through marimo's `LazyLoader`, and Polars Arrow restoration in a subprocess. `test_projection.py` protects the public `Projection` contract, exporter behavior, and synthetic projection identity. The terminal restoration cases in `test_runner.py` exercise the generated cell through the scenario boundary.

`test_runner.py` protects the full scenario boundary. Its focused cases cover UI defaults that would otherwise fail, recreated UI objects, state pair relinking, setter pre-state, getter-only hits, projection alias deduplication, terminal restoration over an unpicklable source, process-wide serialization, and root and nested notebook arguments.

`test_projection.py` protects the built-in HTML exporter and rejection of runtime-backed fragments. The HTML cases in `test_runner.py` protect virtual-media inlining, exact-length reads, primitive content-token invalidation, targeted producer repair, and warm projection hits.

`test_delivery.py` protects atomic stages, exact expiry fields, active timer cleanup, explicit release, and restart adoption from directory modification time.

## Remote integration proof

Run:

```bash
make integration
```

The test starts a dedicated marimo 0.23.14 server through the local Python distribution. It uses temporary notebook, cache, counter, stage, and checkout directories.

The proof covers:

1. Authenticated notebook-path attachment and an owned upstream-routed session.
2. Remote capability description and exact runtime versions.
3. A cold three-scenario build with definition and UI inputs.
4. A warm build that preserves authored and exporter counters.
5. Output and format label changes that preserve native projection hits.
6. Exporter-version invalidation that reruns projection work while preserving authored hits.
7. Payload-mirror deletion followed by repair from a cached complete `Projection`.
8. Verified staging and an incremental second pull that skips matching payloads.
9. Local verification against the original `ExportRef`.
10. Repeated CLI publication with structured output.
11. TypeScript scenario resolution and output reads after the marimo server stops.

Keep this as a real-process integration. A mocked transport cannot prove native cache identity, scratchpad dispatch, stage serving, payload repair, or post-server consumption.

Use focused unit tests for failure paths that the main integration should not destabilize, including edit-scope preflight, primary session lookup, resumed attachment, kiosk rejection, close retries, UI recreation cycles, stage timer failures, symlink rejection, and bounded reads.

## Publication inspection

For any changed fixture or produced checkout, verify the commit chain:

1. Verify the external `ExportRef` against `index.json`.
2. Parse the index through both Python and TypeScript decoders when its schema changed.
3. Verify every unique payload key, size, and digest.
4. Confirm `index.json` is written after payloads.
5. Stop the producer and read representative outputs from the durable directory.

For local filesystem changes, include regular files, missing files, symlink leaves, escaping paths, oversized files, aborts, existing matching payloads, and failed temporary writes. Keep source and destination trees outside concurrent untrusted writes while running these checks. The pathname-based implementation does not protect against every intermediate-directory replacement race.

## Package inspection

### TypeScript package

Build and inspect the npm file list:

```bash
pnpm --filter @marimo-team/marimo-export build
pnpm --filter @marimo-team/marimo-export test:package
pnpm --dir packages/client pack --dry-run --json
```

Confirm:

- The tarball contains the root, `/remote`, `/node`, and CLI outputs declared by `publishConfig.exports` and `bin`.
- The installed entrypoints expose the expected runtime values and `marimo-export --version` matches package metadata.
- The universal root imports no Node built-ins.
- The `/node` output contains filesystem transfer support.
- Package metadata declares version `0.0.0` and `Apache-2.0`.
- Runtime dependencies match imports from published entrypoints.

Inspect each changed loader package the same way. Its format dependency belongs in that package and its `formatId` matches the Python exporter.

Remove ignored loader `dist` directories before the workspace format, lint, and type gates. Workspace package resolution must use source exports. Rebuild and inspect `dist` only through the owning package build and pack commands.

### Python package

Build through uv:

```bash
uv build --package marimo-export
```

Inspect the resulting wheel and source distribution. Confirm:

- Base requirements contain the exact `marimo==0.23.14` pin.
- Dataframe and PNG dependencies remain in their named extras.
- Metadata declares version `0.0.0`, the Marimo Team author, and Apache-2.0.
- The wheel contains `marimo_export`, `py.typed`, and the license.
- `Projection` imports and pickles through `marimo_export.Projection`.
- Importing `marimo_export.remote` in a base environment leaves optional serializer packages unloaded.

`make build` must produce both TypeScript packages and these Python artifacts.

## Documentation and examples

Build the documentation with its deployment base path:

```bash
BASE_PATH=/marimo-export pnpm --filter @marimo-team/marimo-export-docs build
pnpm --filter @marimo-team/marimo-export-docs typecheck
```

Build each consumer example:

```bash
pnpm --filter @marimo-team/marimo-export-example-browser build
pnpm --filter @marimo-team/marimo-export-example-next build
pnpm --filter @marimo-team/marimo-export-example-astro build
```

For runtime validation, produce and pull a publication, stop the marimo server, then run the consumer. Inspect the rendered scenario and output through the browser or generated HTML boundary. A build proves module and type integration. Browser behavior, content security policy, mounting, and interaction require runtime evidence.

### AnyWidget codec proof

AnyWidget support requires evidence across the producer, cache, wire, and browser boundaries:

1. Export a raw AnyWidget and an `mo.ui.anywidget(...)` wrapper through the public plan path.
2. Verify deterministic model IDs, reachable-model closure, binary buffer restoration, CSS, and nested child references in `anywidget.v1`.
3. Run the same plan warm and prove the complete `Projection` restores from marimo's native cache.
4. Change synchronized state, ESM, and CSS separately. Verify each produces a new projection when the corresponding notebook state changes.
5. Pull the publication, stop the marimo server, and mount the widget in a real browser.
6. Mount through a Next.js or Astro client boundary after the server side reads the publication.
7. Exercise initialize, render, nested `host.getWidget()`, local model changes, abort, cleanup callbacks, and idempotent disposal.
8. Confirm that importing the loader and decoding the payload during SSR does not access browser globals.
9. Confirm malformed model references, buffer paths, base64 data, ESM specs, and format IDs fail before module execution.

## Final review

Before handoff:

1. Run `git diff --check`.
2. Read each changed file once for behavior and once for wording.
3. Search changed prose for stale schema names, package names, commands, em dash characters, and prose semicolons.
4. Confirm generated output came from the owning build command.
5. Confirm the diff stays within the intended plane or updates every crossed contract.
