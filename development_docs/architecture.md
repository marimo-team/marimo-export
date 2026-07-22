# Architecture

marimo-export publishes selected notebook values as verified, portable projections. Python runs where the notebook environment and marimo cache already exist. Consumption has zero Python involvement. A browser, server-side renderer, Node application, or agent can use the publication after the Python process and remote server stop.

The central boundary is:

```text
notebook execution owns computation
marimo cache owns computation identity and reuse
marimo-export owns projection, publication, transfer, and consumption
```

## Responsibility map

| Plane    | Owner                                                       | Input                                     | Result                                                          |
| -------- | ----------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------- |
| Producer | `packages/producer`                                         | Saved notebook bytes plus an export plan  | Native cache entries, portable payloads, and an immutable index |
| Transfer | `packages/client/src/remote` and `packages/client/src/node` | A remote marimo target and an `ExportRef` | A temporary HTTP stage or durable local publication             |
| Consumer | Root `@marimo-team/marimo-export` entrypoint                | `index.json` plus referenced payloads     | Immutable notebook, scenario, and output objects                |
| Codec    | Python exporters and `packages/loader-*`                    | A notebook value or verified payload      | A `Projection` in Python or typed frontend value in TypeScript  |

The feature map follows those owners:

| Capability           | Contract                                                                                  |
| -------------------- | ----------------------------------------------------------------------------------------- |
| Parameter sweeps     | A plan records finite scenarios over definition and UI inputs                             |
| Notebook execution   | Each scenario runs from the same saved notebook snapshot in a fresh child runner          |
| Cache reuse          | marimo owns reuse for eligible authored cells and synthetic projection cells              |
| Projections          | A terminal synthetic cell converts a graph value to a complete portable `Projection`      |
| Publication          | A content-addressed index anchors every content-addressed payload                         |
| Remote production    | A TypeScript client controls an attached marimo kernel through a small versioned protocol |
| Transfer             | A verified temporary stage can be pulled into a durable directory                         |
| Frontend consumption | A universal TypeScript reader resolves scenarios and decodes verified outputs             |
| Agent consumption    | The Node CLI inspects scenarios, reads selected outputs, and verifies publications        |
| Format extension     | A Python exporter and TypeScript loader meet at one stable `formatId`                     |

The end-to-end flow is:

```text
saved notebook + plan
        |
        v
attached marimo kernel
        |
        +--> fresh scenario runners
        |       |
        |       +--> native authored-cell cache
        |       +--> native synthetic projection-cell cache
        |
        +--> portable payload mirror
        +--> immutable export index
                       |
                       v
              temporary HTTP stage
                       |
                       v
        index.json + cache/<payload-key>
                       |
                       v
          openExport() + output loaders
```

Frontend reactivity selects among the finite scenarios recorded in the index. `NotebookExport.resolve(inputs)` requires an exact recorded input vector. Publishing another vector requires another producer build. Consumers never recreate the notebook environment or execute notebook Python.

## Producer plane

### Plan contract

An export plan uses schema `marimo-export.plan.v1`:

```yaml
schema: marimo-export.plan.v1

inputs:
  scale:
    definition: scale
    default: 2
  multiplier:
    ui: multiplier
    default: 2

scenarios:
  - id: baseline
    inputs: {}
  - id: larger
    inputs:
      scale: 4
      multiplier: 3

outputs:
  summary:
    source: summary
    formats:
      json: {}
```

Each input selects exactly one notebook target:

- `definition` replaces a definition for the scenario and prunes its defining cells.
- `ui` updates a real marimo `UIElement` through `UpdateUIElementCommand`.
- `default` supplies a JSON value when a scenario omits that input.

A source string selects a notebook definition. An expression source uses `{ expression }`. A format selects one of:

- A built-in exporter by format name or `exporter` string.
- An importable `{ ref, version }` using `module:object` syntax.
- A notebook `{ definition, version? }` exporter.

Built-in options normalize before the plan digest and generated cell source are computed. JSON defaults to `indent: null` and `sort_keys: true`. Parquet defaults to `compression: "NONE"` and normalizes compression names to uppercase. PNG defaults to `scale: 1` and normalizes integral scales to integers. Built-ins with no options reject extra fields.

The TypeScript validator performs JSON and structural preflight before a remote request. The Python decoder is authoritative for Python identifiers, Python keywords, expression syntax, import-reference syntax, normalized options, and the plan digest. Expression parsing proves that an expression is valid Python syntax. It does not prove that its names exist in the notebook graph. The loaded runner resolves input targets against the graph. Synthetic-cell compilation and execution resolve projection sources and notebook exporter definitions. Importable exporters and optional serializer dependencies resolve at execution time.

Validators reject unknown fields, missing required inputs, unknown scenario inputs, duplicate input targets, duplicate scenario IDs, and duplicate resolved input vectors. When `scenarios` is omitted, the plan resolves to one scenario named `default`. Defaults make every recorded input vector complete. JSON integers must fit the JavaScript safe-integer range so Python and TypeScript compute the same identity.

The plan digest is the SHA-256 of the normalized wire form. It identifies the requested build. Individual projection hits still follow native synthetic-cell identity.

### Saved-notebook boundary

`marimo_export.worker.build()` runs inside an attached marimo kernel. The kernel supplies the notebook path, installed Python environment, runtime context, and configured root cache `Store`.

The producer requires `SessionMode.EDIT` and `execution_type == "relaxed"`. One adapter guard checks both conditions before the notebook snapshot is read, scenario work starts, or cache objects are written. The stock marimo server hosts each edit-mode kernel in a separate process. Standard and AppHost run paths host kernel roots in threads, while other run deployments can use subprocesses. The kernel context exposes the mode but not its hosting topology. Supporting run-mode production requires an upstream process-state isolation seam because root construction mutates process-wide `sys.argv` and `sys.path` before the adapter can acquire its scenario gate.

Relaxed execution is marimo's default. marimo 0.23.14 omits the execution type from native cell-cache identity, while strict execution adds declaration-cloning semantics. A local salt on synthetic projection cells would leave authored dependency keys ambiguous. The producer therefore rejects strict kernels. A notebook moving from strict to relaxed production must begin with a fresh `__marimo__/cache` directory so the relaxed producer cannot restore authored or synthetic entries created under strict semantics. The upstream seam is an execution-type-aware native cache identity or a cache policy that can reject entries from another execution type.

The producer reads the saved notebook once and records its exact bytes and SHA-256 digest. Every scenario deserializes those bytes into a fresh app. The producer rereads the saved file immediately before creating and writing the index. A change observed by that check aborts the build. The editor save and cache-store write do not share an atomic lock, so a save after the final reread can race the index write. The index still records the digest of the snapshot that produced its outputs.

Saved notebook bytes and the normalized plan form the build's recorded source identity. Results can also depend on the attached Python environment and external resources that notebook code reads. Save editor changes before starting a build and version external inputs through graph values or exporter versions when they affect published bytes.

### Scenario execution

For each resolved scenario, the producer performs this sequence:

1. Deserialize a fresh app from the saved snapshot.
2. Deep-copy definition and UI values for the scenario.
3. Validate input targets against the authored graph.
4. Compute the authored schedule and prune definition targets with marimo's `prune_cells_for_overrides()`.
5. Open a fresh `AppKernelRunner`, copy the attached kernel configuration through marimo's runtime updater, enable `runtime.cache_cells` when the root notebook has no user arguments, force `runtime.auto_reload` off, preserve the supported relaxed execution type and root notebook arguments, install a fresh nested-runner registry, and register definition overrides. Each nested app receives its own filename followed by the same user arguments once.
6. For UI inputs, process creator cells in graph frontiers. Expand the initialized creator closure with each frontier and run that union to quiescence.
7. Apply each frontier's UI values in graph order through `UpdateUIElementCommand` with lazy reactive execution and no frontend notification.
8. Reapply a requested value whenever later execution replaces its targeted `UIElement`. Each input receives at most `max(32, 4 * authored_cell_count)` applications, where `authored_cell_count` is the post-pruning authored schedule size.
9. After every UI creator is initialized, run remaining and stale authored cells to quiescence while continuing to reconcile replaced UI elements.
10. Append one terminal synthetic cell for each distinct projection identity. Append a paired preparation cell for each `anywidget.v1` projection.
11. Run AnyWidget preparation cells with native cell caching disabled. Each cell evaluates its notebook source and returns canonical payload bytes.
12. Prepare primitive HTML content tokens and run the terminal projection cells as targeted leaves, flushing native writes before and after the run.
13. Mirror every restored or computed payload.
14. Release the runner through the explicit teardown path.

Each quiescence loop allows at most `max(32, 4 * allowed_cell_count)` rounds. During UI setup, the allowed set is the union of creator-closure cells initialized so far. The final authored phase initially schedules every remaining valid cell in the post-pruning authored set plus any stale cells that marimo reports. It repeats graph frontiers until that allowed set has no pending or stale cells. Without UI inputs, the full post-pruning authored set is the initial schedule. The scenario fails if a phase does not settle.

Synthetic cells are appended after authored state settles. This keeps projections as terminal conversions over the resulting graph. The producer applies the same `State` and `SetFunctor` cache policy to authored and synthetic cells. AnyWidget preparation cells are the one deliberate uncached conversion boundary. Keep intended mutations and state transitions in authored cells so their graph dependencies remain explicit.

Fresh deserialization gives every scenario a new app, runner, globals dictionary, UI registry, and state registry. Child contexts share the root-scoped active lazy-loader registry and native cache key space. Equivalent native identities can therefore reuse work across the matrix. Publication objects use the attached root context's configured `Store`.

This is graph-state isolation inside one relaxed edit-mode kernel process. Scenarios still share imported modules, environment variables, files, random generators, native-library singletons, and background tasks. The producer serializes scenario runs across that process because marimo contexts, `sys.argv`, `sys.path`, and cache codec registries share process state. Notebook code that needs scenario-level process isolation belongs in separate producer processes.

Graph initialization must succeed before scheduling. It rejects notebook parse errors, cycles, and multiply defined names. The valid authored set then comes from marimo's compiled cell manager. Every valid authored cell remaining after definition pruning is scheduled. A native cache hit restores the cell definitions and return value without executing the cell body. Cell-body execution count is not a scenario contract. Model values needed by projections as graph definitions or state with explicit dependencies.

### Stateful cells

marimo includes the current `State` value when hashing a state getter. It also restores state setter effects from native cache records. A setter consumer can still be unsafe when its prior state is hidden from that cell's dependency closure.

The adapter therefore adds two narrow guards:

1. It resolves direct and transitive `State` and `SetFunctor` references by runtime identity. A state or setter nested inside a dictionary, list, tuple, or `SimpleNamespace` forces live execution. A setter reached transitively also forces live execution. A direct setter remains cache-eligible only when its exact paired `State` is also a direct reference. A direct getter-only cell remains cache-eligible.
2. After native restoration, it detects a `State` and `SetFunctor` defined by the same cell whose object links were restored separately. It reruns that producer cell with caching disabled to relink the pair.

The same state-cache policy covers authored and synthetic projection cells. These guards preserve native hits for getter-only consumers and direct getter-setter pairs while executing wrapper-hidden setters live. They do not redefine general Python state or arbitrary side-effect semantics.

The clean upstream replacement is an atomic cache restore for related `State` and `SetFunctor` definitions plus setter cache identity that includes the prior state it mutates.

### Native cache integration

marimo owns authored-cell and synthetic-cell code hashing, graph lineage, dependency hashing, module pinning, cache lookup, restoration, invalidation, and native persistence. marimo-export copies the attached kernel configuration to each child, enables `runtime.cache_cells` when the root notebook has no user arguments, forces `runtime.auto_reload` off, preserves relaxed execution and notebook arguments, and submits work through `AppKernelRunner`.

User arguments are ambient process state and do not participate in marimo's native cache identity. When the attached kernel has arguments beyond the notebook filename, the child executes authored and synthetic cells with native caching disabled. This prevents a cache entry produced under one argument vector from restoring under another.

marimo 0.23.14 restores cached Polars Arrow objects through PyArrow's C data bridge on a worker thread. That path can crash the producer process. While the process-wide scenario gate is held, the adapter scopes marimo's `.arrow` deserializer so Polars frame and series hints use `polars.read_ipc()`. Other Arrow values use the upstream deserializer, and the adapter restores the registry before releasing the gate. Cache keys and stored Arrow bytes remain under marimo's ownership.

The default `FileStore` resolves beneath the notebook's `__marimo__/cache` directory. Interactive execution and programmatic builds can use the same store. An interactive run can warm authored dependencies when marimo computes the same identity. A build can warm later interactive execution for the same reason.

An authored cell that performs equivalent serialization has its own native identity. It can warm shared authored dependencies. Synthetic projection reuse requires the same generated cell source and marimo dependency identity. Cells guarded for state writes and cells selected for targeted value repair execute with caching disabled for that run.

marimo-export does not add an execution-type salt to projection identity. Such a salt would separate terminal projection entries while leaving their authored dependencies in marimo's shared key space. The producer boundary and fresh-cache precondition keep one canonical upstream cache identity instead.

### Projection contract

The stable Python API is:

```py
Projection(
    payload: bytes,
    *,
    format_id: str,
    media_type: str = "application/octet-stream",
    metadata: Mapping[str, object] = {},
)
```

`Projection` lives at the public pickle path `marimo_export.Projection`. Runtime validation copies `metadata` into a JSON object and rejects incompatible keys or values.

The generated cell returns the complete `Projection` as its bare expression value. marimo's normal lazy-cache loader persists and restores that return. A hit restores the portable bytes, format identifier, media type, and metadata together. The exporter does not run on that path, even when an upstream value restored as an `UnhashableStub`.

When a synthetic projection cell misses and a direct source restored as an `UnhashableStub`, the defining cell and projection cell run in one `AppKernelRunner.run()` request. marimo's `CachedLifecycle` can then invalidate the defining cell entry, rerun it, and resume the projection cell. Nested stubs are not visible to that upstream preflight. The adapter detects them and runs their defining closure live before it schedules the projection cell.

`PROJECTION_CELL_ABI` participates in generated-cell identity. Increment it when generated runtime semantics change in a way that existing synthetic cache entries cannot satisfy.

Projection identity includes:

- The projection cell ABI.
- The normalized source wire value.
- The exporter reference or notebook exporter definition.
- The exporter version when present.
- Normalized options.
- The graph lineage and dependency values that marimo hashes.

Projection identity excludes scenario IDs, output names, format aliases, and plan order. Renaming a publication label changes the index while preserving the underlying projection hit.

A notebook exporter is a graph reference, so its defining cell lineage participates in identity. An importable exporter needs an explicit version because marimo cannot inspect code outside the notebook graph. Change that version whenever its serialized contract changes.

### HTML cache identity

marimo's dependency hasher does not add content bytes for marimo-owned `Html` values. Each synthetic cell therefore references a primitive byte token derived from its direct and function-level transitive graph references and `Html` values nested in dictionaries, lists, and tuples. The token includes each reference, nested path, concrete type, and HTML text after supported virtual media has been converted to data URLs. marimo hashes that byte token through its normal primitive path.

A restored `Html` value can retain a virtual-file URL after the child registry that owned the bytes has ended. Before computing the token, the adapter detects that unresolved URL and runs the defining producer closure live with caching disabled. The terminal projection remains cache-eligible. The repaired value supplies both the token content and the bytes that `html.v1` inlines.

The token is a dependency of the generated cell, so HTML content changes invalidate the native projection entry. `Projection` remains the portable artifact representation. No process-global codec registration participates in projection execution.

### Portable HTML

The `html.v1` exporter calls `marimo.as_html(value)` and converts marimo virtual files used by `img`, `audio`, and `video` `src` attributes to data URLs through marimo's own DOM conversion helper. It rejects any remaining `@file` reference and any parsed `<marimo-...>` runtime element before returning a `Projection`.

Static `Html` values and `mo.md` can therefore become server-independent fragments. Interactive tables, widgets, downloads, and other runtime-backed values need a dedicated portable exporter and frontend loader. External static image URLs remain unchanged.

A cached complete `Projection` survives a producer restart with its inlined media bytes. An authored `Html` entry can restore a virtual-file URL after the registry holding those bytes has ended. The targeted producer repair recreates those bytes before token computation. A native HTML cache codec that hashes static content and persists virtual media would remove this adapter repair.

### Publication storage

After execution or restoration, the producer writes a content-addressed payload object through the root configured `Store` at:

```text
marimo-export/payloads/sha256/<payload-sha256>
```

It writes the canonical index at:

```text
marimo-export/indexes/<index-sha256>.json
```

These direct objects serve transfer and consumption. They sit beside native cache objects under the default store but remain outside `LazyStore` manifests and touched-key tracking. A restored complete `Projection` can repair a missing payload mirror without rerunning its exporter.

Content-addressed writes compare existing bytes and repair missing or mismatched objects. Every write is read back and checked for exact equality. The default file store path uses a temporary file, flush, `fsync`, and atomic replace.

The marimo file store treats a zero-byte value as absent. The producer stores one internal sentinel byte for an empty remote payload. Staging materializes the actual zero-byte payload.

## Publication contract

### Directory layout

A staged or pulled publication has one layout:

```text
index.json
cache/<payload-key>
```

### Integrity records

`ExportRef` anchors the index:

```json
{
  "key": "marimo-export/indexes/<digest>.json",
  "sha256": "<digest>",
  "size": 1234
}
```

The key must match the digest. Index size is a positive safe integer.

`ExportIndex` uses schema `marimo-export.index.v1` and records:

- Notebook name and saved-source SHA-256.
- Normalized plan SHA-256.
- marimo and marimo-export producer versions.
- Scenario IDs and complete JSON input vectors.
- Output and format labels.
- Projection format ID, media type, metadata, and payload reference.

Each payload reference contains an exact key, SHA-256 digest, and non-negative byte size. Its key has the form `marimo-export/payloads/sha256/<digest>`. Both implementations enforce strict JSON, scenario uniqueness, portable paths, digests, and safe-number rules. TypeScript rejects conflicting repeated payload references while decoding. Python rejects them when `ExportIndex.payloads()` derives the closure before producer verification or staging.

### Commit ordering

The producer verifies every referenced payload, rereads the saved notebook, and writes the index last. A returned `ExportRef` names a complete cache closure for the snapshot digest recorded in that index. The final notebook reread and index write have the save race described in the saved-notebook boundary.

Staging writes payloads and index into a temporary directory before one atomic directory rename. Local pull writes verified payloads first and `index.json` last. The index is the commit record at every visible boundary.

### Integrity and authenticity

Integrity forms this chain:

```text
trusted ExportRef
    -> index bytes
        -> payload references
            -> payload bytes
```

SHA-256 verification detects corruption or substitution relative to the supplied reference. The transport, deployment channel, or calling application must establish the authenticity of the initial `ExportRef`.

## Transfer plane

### Remote attachment

marimo-export uses inversion of control for production. The caller supplies a running marimo server, its URL, credentials, and either a notebook path or session ID. marimo-export does not install the notebook environment, launch the marimo server, choose its machine, or own its process. This boundary lets a publisher use an environment that already has its Python packages, data access, credentials, accelerators, and marimo cache.

A notebook target can ask the existing server to create or resume one of its kernels. A session target borrows an existing kernel. Kernel routing is server behavior and does not transfer ownership of the server process to marimo-export. The remote client owns attachment, scratchpad requests, temporary stages, and cleanup of sessions that the server reports as newly created for that connection.

`connectRemote()` accepts exactly one target:

```ts
type RemoteTarget = { notebook: string } | { sessionId: string };
```

`{ notebook }` first sends an authenticated `POST /api/home/running_notebooks` request as a non-mutating edit-scope and skew-credential preflight. It then opens marimo's WebSocket route with a generated session ID and the exact notebook path. The upstream handshake may create a kernel or resume one. The client sends an explicit `autoRun: false` instantiation request when the handshake reports a new kernel that was not auto-instantiated. It retains the WebSocket for the connection lifetime.

Ownership comes from the handshake. A newly routed kernel is owned. A resumed kernel is borrowed. A kiosk handshake rejects the managed notebook target and directs the caller to a primary session ID. `{ sessionId }` reads `GET /api/sessions`, requires the ID to be a top-level key, opens no WebSocket, and is always borrowed. The primary-key check rejects consumer IDs and stale locators before scratchpad dispatch. Secondary consumer IDs cannot establish the exclusive control and ownership contract required by remote export operations.

Both target preflights require marimo's edit scope. When edit access is denied but `GET /api/version` remains readable, the client reports `unsupported_mode` with `marimo edit` guidance. The Python producer uses the same error code when the attached edit kernel uses strict execution. That rejection happens before snapshot or store access.

If notebook attachment is cancelled before the kernel-ready handshake, the client cannot determine whether the handshake created a kernel or attached the connection to kiosk mode. It closes the WebSocket and does not issue a blind shutdown against the generated ID. Configure a finite marimo session TTL as the cleanup backstop. Edit-mode servers require an explicit `--session-ttl` for TTL cleanup after WebSocket disconnect.

Fresh scenario graph state is independent from session ownership. Every scenario still runs in a new managed child runner inside the selected kernel.

The client sends a three-line Python bootstrap through marimo's scratchpad endpoint. Versioned `marimo-export.remote.v1` envelopes support `describe`, `build`, `stage`, and `release`. Each complete or incomplete SSE event is capped at 1,048,576 characters. The JSON line prefixed by `__MARIMO_EXPORT_RESPONSE__:` is capped at 1 MiB after UTF-8 encoding. Requests are queued per connection because scratchpad work mutates kernel-local runtime state. Cancellation can stop a queued request before dispatch. A request timeout stops the client from waiting, while already dispatched Python work may continue remotely.

One connection cannot isolate another connection to the same marimo session. Upstream disconnect cancellation interrupts that whole session. marimo-export therefore requires exclusive use of the attached kernel for the duration of each remote request.

The remote environment needs the `marimo-export` Python distribution plus extras selected by the plan. Its base dependency pins the compatible marimo release. `authToken` supplies bearer authorization and the WebSocket `access_token`. `serverToken` supplies `Marimo-Server-Token`.

`Remote.build()` returns an immutable, in-memory result containing an `ExportRef` plus elapsed milliseconds, scenario count, and projection count. Server and target remain connection metadata.

The CLI `build` command accepts a notebook target and emits a durable `marimo-export.build.v1` record containing the normalized server URL, notebook path, reference, and receipt. `pull` uses that record to open a fresh managed session after the producer session expires. One-shot `publish` can use a borrowed session because build and pull share one connection. A borrowed session cannot produce a reopenable build record.

### Connection close

`Remote.close()` rejects new work, waits for active requests and stage openings, and attempts every lease release. It shuts down an owned session once no active request, opening, or unreleased lease remains. After the shutdown request, it polls the authenticated running-notebooks endpoint until that exact session disappears under the same close deadline. This barrier lets a later managed connection reuse the notebook after shutdown completes. Cleanup preserves the first error, and the retained WebSocket closes at most once in the final path.

A failed remote cleanup keeps `close()` retryable. A later call retries remaining releases and then shuts down an owned session when the connection is clear. Borrowed sessions remain running.

The `pull` and `publish` CLI paths attempt stage release after transfer, then retry retained cleanup while closing the connection. If both attempts fail, the unreleased lease prevents owned-session shutdown. The command reports the cleanup failure, and the configured server session TTL remains the final cleanup backstop.

### Stage lifecycle

`Remote.open(ref)` creates a verified HTTP stage beside the notebook:

```text
public/.marimo-export/<stage-id>/
  index.json
  cache/marimo-export/payloads/sha256/<digest>
```

The returned lease exposes an `ExportSource`, `expiresAt` as epoch milliseconds, and `close()`. The wire field is `expires_at_ms`. The client accepts a stage URL from the marimo server origin with no credentials, query, or fragment. It rejects HTTP redirects and sends the returned notebook key as `X-Notebook-Id` when present.

Stages have a 30-minute lease. An in-process timer removes a stage at its deadline without waiting for another operation and retries cleanup failures after five seconds. After a producer restart, the next stage or release operation adopts existing directories, derives their remaining lease from directory modification time, and schedules cleanup. Process-local and file locks serialize mutations to each stage root. Explicit lease close remains the normal release path.

### Durable pull

`pullRemote()` composes a temporary lease with `pullExport()`. The Node transfer path:

1. Preflights the destination before opening a stage.
2. Verifies the staged index against the expected `ExportRef`.
3. Deduplicates payload references.
4. Checks existing regular files with bounded, abortable reads.
5. Skips files whose size and digest already match.
6. Downloads missing or invalid payloads with concurrency from 1 through 64, defaulting to 8.
7. Aborts peer downloads after the first transfer failure.
8. Verifies each payload before writing through a temporary file and atomic rename.
9. Commits `index.json` last.
10. Attempts stage release with a separate 10-second cleanup deadline.

The caller retains ownership of the `Remote` connection. A successful directory is independent of the remote server and Python environment. If a later pull fails, its previously committed index remains visible.

The Node path validates portable paths, rejects symlink leaves, checks resolved roots, uses `O_NOFOLLOW` for file reads, and rechecks anchored directories around writes. Node pathname APIs cannot eliminate every race caused by an untrusted process renaming an intermediate directory concurrently. Publication source and destination trees must remain outside concurrent untrusted local writes. Full protection requires directory-file-descriptor operations such as `openat`.

Before `publish` connects, the CLI resolves the publication root and `--record` parent to canonical filesystem paths. It rejects any record inside the publication, including a collision reached through an ancestor symlink.

## Consumer plane

The root package entrypoint uses web platform APIs and works in browsers, Node, Next.js, Astro, and other server-side rendering runtimes. Node built-ins remain behind `/node`.

`openExport(source, options)` reads `index.json`, verifies an optional external reference, validates the schema, and derives a canonical `ref` from the loaded bytes. The derived reference identifies those bytes. Authenticity still requires an independently trusted input reference or delivery channel. The public model is immutable:

```text
NotebookExport
  ref
  notebook
  planSha256
  producer
  scenarios() -> ExportScenario[]
  scenario(id) -> ExportScenario
  resolve(inputs) -> ExportScenario

ExportScenario
  id
  inputs
  outputs() -> ExportOutput[]
  output(name, formatName?) -> ExportOutput

ExportOutput
  name
  formatName
  formatId
  mediaType
  metadata
  ref
  bytes()
  text()
  json()
  blob()
  load(loader)
```

`httpSource()` and `memorySource()` are universal. An HTTP source accepts an absolute root, a browser-relative root, or a relative root with an explicit SSR base. `directorySource()` lives in `/node`. Applications can supply any `ExportSource` that implements `read(path, options)`.

The reader applies a 16 MiB default limit to an unanchored index when `maxBytes` is omitted. For an unanchored index, a caller-supplied `maxBytes` replaces that default in either direction. A supplied `ExportRef` bounds the index by its declared size, and `maxBytes` can add a stricter ceiling. Sources enforce portable paths, abort signals, and bounded reads. HTTP reads reject redirects and enforce limits against both declared and streamed byte counts.

Every payload read verifies the declared size and SHA-256 digest before decoding. Concurrent unsignaled reads for the same payload share one in-flight verified request. That entry is evicted when the request settles, so later reads fetch again. Signaled reads remain independent. Every caller receives a fresh `Uint8Array` copy.

`formatName` is the plan-defined publication label used by `scenario.output(name, formatName)`. Omitting it selects the projection when the output has one format and raises `ambiguous_format` when several labels exist. `formatId` comes from the Python `Projection` and selects the compatible codec. `output.load(loader)` compares the loader's exact `formatId` before reading bytes and raises `unsupported_format` on a mismatch.

Loader calls receive a context bound to the output and the caller's read options. The context exposes the caller's abort signal. Its byte, text, and JSON reads preserve the supplied signal and byte limit.

## Codec plane

Custom projection support has two contracts:

1. A Python exporter returns `Projection(payload, *, format_id, media_type, metadata)`.
2. A TypeScript `OutputLoader<T>` declares the same `formatId` and decodes the verified payload.

The format ID is the compatibility handshake. Output and format names remain publication labels.

Format dependencies stay at their owning boundary:

| Projection                              | Python requirement         | Frontend consumption                   |
| --------------------------------------- | -------------------------- | -------------------------------------- |
| JSON, text, HTML, bytes, Vega-Lite JSON | Base package               | Core reader methods or a loader        |
| Arrow and Parquet                       | `marimo-export[dataframe]` | Arrow or Parquet loader package        |
| PNG                                     | `marimo-export[png]`       | `blob()` or `bytes()`                  |
| AnyWidget                               | `marimo-export[anywidget]` | `@marimo-team/marimo-export-anywidget` |

Each frontend loader package owns its parsing or rendering dependency. The universal reader remains independent of Arrow, Parquet, and Vega runtimes.

HTML payloads can contain active frontend content. A consumer must apply the trust policy and content security policy required by its application.

### Built-in exporter layout

Built-in serialization code is organized by portable contract:

```text
packages/producer/src/marimo_export/projection/exporters/
  __init__.py
  _optional.py
  json.py
  text.py
  bytes.py
  html.py
  dataframe.py
  vegalite.py
  anywidget.py
  _anywidget_payload.py
```

`dataframe.py` owns both Arrow and Parquet because they share table normalization. `vegalite.py` owns Vega-Lite JSON and PNG because both begin with the same normalized specification. Altair values use the Vega-Lite contract. A chart library gets a separate module and loader when it has a distinct frontend protocol, such as Plotly JSON. Python library names alone do not justify another codec.

The built-in registry contains declarative descriptors for plan names, import references, cache versions, option normalizers, extras, and availability probes. Each descriptor points directly to its owning module. Moving the current references from `marimo_export.projection.exporters:<name>` to module-specific references changes synthetic-cell identity and causes one intentional cold projection build. Authored marimo cache entries remain under marimo's normal identity.

Optional dependencies remain lazy. JSON, text, bytes, HTML, and Vega-Lite JSON use the base producer. Dataframe, rendered PNG, and AnyWidget dependencies live in their owning extras. Importing the remote dispatcher does not import those packages.

### AnyWidget target contract

AnyWidget support must be an interactive portable codec, not an HTML fallback. The producer accepts an `anywidget.AnyWidget` instance or its `mo.ui.anywidget(...)` wrapper and returns one complete `Projection`:

```text
formatId: anywidget.v1
media type: application/vnd.marimo-export.anywidget+json
payload:
  schema
  rootModelId
  files
  modelNotifications
```

`modelNotifications` uses marimo's static `model-lifecycle` open-message shape. It carries model state, `_css`, buffer paths, base64 buffers, and ESM specs. `files` embeds marimo virtual ESM files as data URLs. `rootModelId` selects the projected view from the closed set of model notifications.

Base64 buffers are deliberate in `anywidget.v1`. They preserve marimo's existing static JSON shape, keep the payload inspectable, and require no container dependency in Python or TypeScript. Measure real publications before defining a framed binary successor. A binary successor gets a new format ID.

The producer builds the model closure from the selected root. It follows `anywidget:<model_id>` and `IPY_MODEL_<model_id>` references, includes each reachable child once, rejects unresolved references, and excludes unrelated live widgets. Runtime UUIDs become deterministic local model IDs. Model references use those local IDs, and virtual ESM URLs resolve through the embedded `files` map before canonical JSON encoding. Embedded modules accept literal `data:`, HTTP, and HTTPS dependencies. Package names, path-relative imports, computed imports, and computed `new URL(..., import.meta.url)` operands fail during projection preparation. Marimo virtual files referenced by `_css` become data URLs. Other relative CSS assets fail preparation. This keeps cold payload bytes stable and lets marimo cache the complete `Projection` through its normal lazy-cache path.

The marimo adapter owns widget-specific private imports. It wraps the child runner stream, feeds `ModelLifecycleNotification` messages into `SessionView`, and reads the consolidated model-open records after authored execution. The wrapper never stops the borrowed parent transport. It stays attached through widget lifecycle disposal so model-close notifications reach that transport, then one shared state detaches the root wrapper and every thread clone. Retained comms cannot forward after scenario release. The adapter then selects the root closure, canonicalizes model references, preserves buffer paths and bytes, validates embedded module dependencies, and embeds virtual ESM and CSS assets. The exporter module validates the stable `anywidget.v1` payload and owns projection metadata. This split contains upstream drift under `_marimo` while keeping the publication contract independent from marimo internals.

The required frontend package is `packages/loader-anywidget`, published as `@marimo-team/marimo-export-anywidget`. It depends on the core reader contract and the AFM type package, with no application framework or marimo frontend dependency. Its root export is an `anywidget()` loader that matches `anywidget.v1`. Payload parsing has no DOM side effects and can run during Next.js or Astro server rendering. `mount(element)` is the explicit browser boundary that imports and executes the exported module.

```ts
const output = published.scenario("baseline").output("map", "widget");
const widget = await output.load(anywidget<MapState>());
const mounted = await widget.mount(element);

try {
  mounted.model.set("zoom", 8);
  mounted.model.save_changes();
} finally {
  await mounted.dispose();
}
```

Each mount creates an isolated static model registry from the snapshot. It implements the AnyWidget Frontend Module model, `host.getModel()`, `host.getWidget()`, `initialize`, `render`, cleanup callbacks, abort signals, CSS mounting, binary restoration, and composed child views. It exposes the root model and `initialize` exports through the mounted handle. Disposal is idempotent and releases views, listeners, styles, child bindings, and object URLs. Dynamic import cannot be cancelled after browser module evaluation begins, so the public mount races its interest against the lifecycle signal. Disposal settles without waiting for a pending module and blocks late initialization or rendering.

The loader mirrors marimo's static AnyWidget behavior and the AFM types. It does not reproduce kernel transport, edit-mode hot reload, comm delivery, Python command handlers, or a second application state-store API. `model.set()` and widget-local interactions update the in-browser model. `model.send()` is a no-op. Python trait observers and notebook recomputation have no backend after publication.

AnyWidget modules are executable notebook content. The reader verifies their enclosing payload before the loader sees it. `output.load(anywidget())` parses and validates the snapshot. `mount()` crosses the code-execution boundary. Applications must trust the anchored publication and allow the required module and style policy in their content security policy.

### AnyWidget cache identity

Each AnyWidget projection has two synthetic cells:

1. An uncached preparation cell evaluates the raw AnyWidget or `mo.ui.anywidget(...)` source. It reads marimo's consolidated lifecycle capture and returns the complete canonical `anywidget.v1` payload as primitive bytes.
2. A cacheable terminal cell references those bytes as its sole runtime value dependency. It validates the built-in exporter descriptor and options, then returns a complete `Projection` containing the same bytes.

marimo owns the terminal cell's code hashing, graph lineage, byte hashing, lookup, persistence, and restoration through its native lazy cache. A cache hit restores the payload, format ID, media type, and metadata together. The preparation cell still runs to supply the current canonical bytes, while the terminal conversion does not run on a hit. Scenario IDs, public output labels, plan ordering, runtime model UUIDs, and virtual-file URLs stay outside the canonical payload.

Primitive payload bytes and the complete cached `Projection` are the entire AnyWidget cache boundary. `CustomStub`, `BlobAsset`, process-global cache codecs, side manifests, and parallel caches do not participate. Publication storage mirrors the restored or computed projection after native cache resolution.

## Package boundaries

The Python base distribution depends on the exact supported marimo version. Serializer dependencies live in extras. All private `marimo._...` imports remain under `packages/producer/src/marimo_export/_marimo`.

The TypeScript package exposes:

- `@marimo-team/marimo-export` for universal reading and loader contracts.
- `@marimo-team/marimo-export/remote` for fetch, WebSocket attachment, and remote control.
- `@marimo-team/marimo-export/node` for filesystem sources, pull, and verification.
- `marimo-export` as the Node CLI.

Dedicated codec packages expose one loader each. `@marimo-team/marimo-export-anywidget` stays import-safe in SSR runtimes and crosses into browser APIs only from `mount()`.

The workspace versions every package as `0.0.0`. The Python distribution builds with `uv_build`. The root `make build` runs recursive Vite+ builds for the JavaScript workspace and invokes uv directly for `packages/producer`.

## Private marimo boundary

The adapter targets marimo 0.23.14 and upstream commit `9b79b740d283d626058494d263e1c7e8c7ca1ebf`. The exact package pin and runtime check make this compatibility boundary explicit.

The private seam covers:

- Attached-kernel and saved-notebook discovery.
- Saved-app deserialization and child-runner creation.
- Definition pruning, UI commands, quiescent authored execution, and state guards.
- Native cache configuration, restoration, flushing, root `Store` access, and HTML content-token preparation.
- Synthetic cell registration.
- Temporary public staging beside the notebook.

| Adapter module         | Responsibility                                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `_marimo/runner.py`    | Scenario serialization, child lifecycle, graph mutation, definition inputs, and UI convergence       |
| `_marimo/execution.py` | Native targeted runs, cache eligibility, stub repair, HTML identity, quiescence, and state relinking |
| `_marimo/cache.py`     | Root store access, native flushes, immutable publication objects, and payload verification           |
| `_marimo/delivery.py`  | Verified projection-only stages, leases, and cleanup                                                 |
| `_marimo/context.py`   | Attached context discovery and immutable saved-notebook snapshots                                    |

### Runner teardown

Runner release flushes caches and releases every nested `App.embed()` runner deepest first. For each runner it disposes registered cell lifecycle items, resets hooks, clears outputs and globals, stops autoreload, removes the runner from its registry, and locates the exact upstream `weakref.finalize` callback that removes its child runtime context from the parent. It invokes that callback synchronously. Cleanup attempts every step, preserves the first failure as the cause, and treats a missing callback as a hard compatibility failure.

This cleanup is independent from publication commit. The producer verifies payloads and notebook bytes before writing the index later in the build.

### Upstreamable seams

The adapter can shrink through narrow upstream APIs while marimo-export keeps publication policy:

1. A supported programmatic notebook runner with saved-byte input, definition overrides, ordered UI updates, quiescent authored execution, terminal targets, and explicit close.
2. Atomic cache restore for related state objects and correct cache identity for setter consumers.
3. A native HTML cache codec that hashes prepared static content and preserves virtual-file bytes.
4. A supported cache context that exposes the configured store and durable flush.
5. A portable artifact result or cache-hit artifact callback that can expose content-addressed bytes without a second publication mirror.
6. A supported attached-kernel extension protocol that can replace scratchpad source transport.
7. A supported static AnyWidget snapshot and standalone hydration runtime that can replace the isolated adapter and loader implementation.

marimo-export continues to own scenario matrices, projection selection, public format IDs, index schemas, remote leases, transfer, integrity verification, and frontend loaders.
