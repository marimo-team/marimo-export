# Validation

Validate through the boundary a consumer depends on. Unit tests protect strict local contracts. The integration suite proves live capture, same-process marimo cache reuse, transfer, local commit, and detached Python and CLI reading against a real server.

## Required handoff gate

Run from the workspace root:

```bash
make format
make check
```

Review the files changed by `make format` before running the gate. `make check` covers formatting, lint, TypeScript and Python types, unit tests, real integration, package builds, and packed-package smoke tests.

CI also runs the publication reader suite on native Windows so the reparse-point and stable-path contract is exercised through that platform's filesystem APIs.

## Evidence by change surface

| Change surface                                      | Required evidence                                                                                                     |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ExportSpec` fields, defaults, or validation        | Pydantic wire model, generated schema, parser tests, ty, and Pyrefly                                                  |
| Projection fields or exporter registry              | Projection and exporter tests plus live capture integration                                                           |
| Named global, expression, or cell-payload selection | Bridge tests and real-session integration                                                                             |
| UI variants, quiescence, or restoration             | Success and failure bridge tests plus real-session integration                                                        |
| Cache identity or `CustomStub` support              | Cold, warm, changed-input, changed-version, changed-options, and stub tests                                           |
| Unhashable source fallback                          | Exporter success, cache disposition, and publication read tests                                                       |
| Exact `.bin` receipt                                | File-store cold and warm tests plus another configured store when available                                           |
| Transfer ticket or virtual-file cleanup             | Bounds, expiry, release, cleanup-failure, and integration tests                                                       |
| Local publication commit or replacement             | No-replace rename, cache merge, collision, retained-asset, and index-commit tests                                     |
| Publication schema                                  | Pydantic wire model, generated schema, and strict cross-language decoder tests                                        |
| Python publication reader                           | Index, asset, closure-limit, integrity, path, POSIX descriptor, Windows stable-path, envelope, read, and verify tests |
| Browser reader or source                            | Reader, source, cancellation, byte-limit, and public API tests                                                        |
| CLI args, JSON, stderr, redaction, or exits         | CLI subprocess tests through the installed Python entrypoint                                                          |
| Loader decoding or mounting                         | Owning loader tests and a real browser mount for interactive formats                                                  |
| Package exports or dependencies                     | Full build, wheel inspection, npm pack inspection, and import smoke                                                   |
| Documentation and navigation                        | VitePress build, local-link check, and copied-command review                                                          |
| marimo dependency or private adapter                | Full Python suite, integration, upstream seam review, and package inspection                                          |

## Focused Python checks

```bash
uv run ruff check packages/python
uv run ty check packages/python
uv run pyrefly check
uv run --all-extras --package marimo-export pytest -q packages/python/tests
```

Run focused files while iterating:

```bash
uv run --package marimo-export pytest -q packages/python/tests/test_spec.py
uv run --package marimo-export pytest -q packages/python/tests/test_projection.py
uv run --package marimo-export pytest -q packages/python/tests/test_marimo_cache.py
uv run --package marimo-export pytest -q packages/python/tests/test_client.py
uv run --package marimo-export pytest -q packages/python/tests/test_reader.py
uv run --package marimo-export pytest -q packages/python/tests/test_cli.py
```

Cache tests should prove:

1. Cold projection writes one `BlobAsset` `.bin` object.
2. Warm projection resolves the same exact object.
3. Source identity changes invalidate the projection.
4. Exporter version and normalized options change identity.
5. Unhashable sources export and report `skipped`.
6. Registered custom-stub bytes participate in identity.
7. A durable flush precedes receipt resolution.
8. The receipt digest matches the configured store bytes.

## Focused browser checks

```bash
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export build
pnpm --filter @marimo-team/marimo-export test:package
```

Run an owning loader package after changing its format contract:

```bash
pnpm --filter @marimo-team/marimo-export-loader-vegalite check
pnpm --filter @marimo-team/marimo-export-loader-vegalite test
pnpm --filter @marimo-team/marimo-export-loader-vegalite prepack
```

Replace the package name with the changed loader.

The AnyWidget runtime has a focused native Chromium gate:

```bash
pnpm --filter @marimo-team/marimo-export-loader-anywidget exec \
  playwright install --only-shell chromium
pnpm --filter @marimo-team/marimo-export-loader-anywidget test:browser
```

This gate imports embedded ESM through browser object URLs, mounts a composed model graph, applies model updates, injects styles, and verifies disposal. The package `test` script runs the native Chromium project as part of `make test`.

Browser reader tests should corrupt the envelope bytes, size, digest, MessagePack shape, media type, filename, format ID, and metadata independently. Every corruption must fail before loader code runs.

## Live integration proof

Run:

```bash
make integration
```

The integration starts a tokenless loopback marimo edit server from the workspace environment, activates a notebook session, then drives the public Python API and installed CLI entrypoint. Its notebook fixture has no PEP 723 dependencies, so the process uses the host workspace environment.

The current automated real-process proof covers:

1. Explicit session selection and inspection.
2. Named global, trusted expression, and rendered cell-payload selection.
3. The starting UI state and one changed finite state.
4. Starting-control restoration after successful capture.
5. Six cold projection misses followed by six warm hits in the same process.
6. Capture of an unsaved executed cell and stable live document digest during each capture.
7. Transfer and verification of six `.bin` cache assets through the public capture path.
8. Python publication reads while the server runs.
9. CLI capture and session inspection while the server runs.
10. Python plus CLI inspect, read, and verify after the server stops.

Focused unit and browser evidence covers the adjacent contracts:

- Bridge tests cover control restoration after exporter failure.
- Cache tests cover unhashable sources, `skipped`, custom stubs, and exact configured-store receipts.
- Transfer tests cover ticket bounds, release, expiry, and cleanup failures.
- Client tests cover new-directory commit and replacement with `index.json` as the commit point.
- Authentication and remote-client tests cover query parsing, headers, and token redaction.
- Browser tests cover publication verification and format loading. The browser workflow below supplies post-shutdown runtime evidence.

Keep live capture as a real-process integration. A mocked transport cannot prove code-mode state, marimo cache identity, background flushes, or virtual-file serving. The current integration does not exercise authentication, another host, a sandboxed notebook environment, cross-restart cache reuse, custom stores, or a browser runtime.

## Publication inspection

For a produced publication:

1. Parse `index.json` through Python and browser decoders.
2. Confirm every asset key resolves beneath `cache`.
3. Verify each unique envelope size and SHA-256.
4. Decode each `BlobAsset` and compare its fields with the index.
5. Confirm a new destination used the no-replace directory commit, or confirm replacement merged cache objects before atomically replacing `index.json`.
6. Stop the notebook server and read representative formats through the Python and browser readers.

Exercise missing files, path traversal, oversized indexes, assets, and declared closures, malformed UTF-8, malformed MessagePack, digest mismatches, duplicate asset references, aborted reads, and loader failures.

## Package inspection

Build both distributions:

```bash
make build
```

Inspect the Python wheel and source distribution. Confirm the package contains `marimo_export`, `py.typed`, CLI metadata, and the license. Confirm optional serializer dependencies stay in their named extras.

The current development wheel must declare the exact `peter-gy/marimo` commit that supplies `BlobAsset`. Python package publication remains gated on an official marimo release with that codec. The release gate replaces the direct commit dependency with the compatible released lower bound and validates installation through normal dependency resolution.

Inspect the npm tarball:

```bash
pnpm --filter @marimo-team/marimo-export test:package
pnpm --dir packages/browser pack --dry-run --json
```

Confirm the package exposes one browser entrypoint, imports no Node built-ins, and declares each runtime dependency it imports. Inspect every changed loader package the same way.

## Documentation and browser proof

```bash
BASE_PATH=/marimo-export pnpm --filter @marimo-team/marimo-export-docs build
pnpm --filter @marimo-team/marimo-export-docs typecheck
pnpm --filter @marimo-team/marimo-export-example-browser build
```

For runtime evidence, capture a publication, stop the marimo server, and serve the browser example. Inspect its JSON and Vega-Lite paths. Run the AnyWidget native browser gate for embedded module import, model interaction, style mounting, and disposal. Add a browser path for each other format changed by the work.

## Final review

1. Run `git diff --check`.
2. Read each changed file once for behavior and once for wording.
3. Search for old schema names, old package names, old commands, em dash characters, prose semicolons, and completion residue.
4. Confirm generated files came from the owning build command.
5. Confirm the diff updates every crossed contract.
