# Remote execution

marimo-export runs inside an attached marimo kernel. Put the Python producer in the environment that already contains the notebook's packages, credentials, data mounts, and accelerator libraries. The TypeScript client controls that environment through the marimo server and transfers portable projection files to the consumer.

Start the producer with `marimo edit` and marimo's default `relaxed` execution type. Stock edit servers isolate attached kernels in separate processes and expose marimo's edit-scoped scratchpad control endpoint. Run mode does not guarantee process isolation or grant access to that endpoint.

If the project overrides the execution type, configure the producer environment with:

```toml
[tool.marimo.experimental]
execution_type = "relaxed"
```

When switching a notebook from `strict` to `relaxed`, begin with a fresh notebook `__marimo__/cache` directory. marimo 0.23.14 gives relaxed and strict execution the same native cell-cache identity, so a relaxed producer could otherwise restore an entry created with strict semantics.

## Start a prepared producer

From this source checkout, start the cache-matrix producer with:

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

This source-checkout server binds to loopback. Omit `--no-token` and `--no-skew-protection` when another machine can reach the marimo server.

In the prepared notebook project, add the producer to the existing environment and run marimo through that project:

```bash
uv add marimo-export
uv run marimo edit notebook.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300 \
  --no-token \
  --no-skew-protection
```

The base `marimo-export` distribution depends on its exact supported marimo version. Add serializer extras for the formats declared by the plan:

```bash
uv add "marimo-export[anywidget,dataframe,png]"
uv run marimo edit notebook.py \
  --headless --no-sandbox --host 127.0.0.1 --port 2718 \
  --session-ttl 300 \
  --no-token --no-skew-protection
```

`--session-ttl 300` asks marimo to close a session five minutes after its WebSocket disconnects. Keep a finite TTL on every producer. If a notebook connection ends before `kernel-ready`, marimo-export cannot determine whether its generated session ID owns a new session or identifies a kiosk attachment, so it cannot safely request blind shutdown. The TTL also bounds the lifetime of a managed session that remains disconnected after cleanup fails. Transfer stages retain their separate 30-minute expiration.

| Extra       | Producer formats                |
| ----------- | ------------------------------- |
| `anywidget` | Portable AnyWidget model graphs |
| `dataframe` | Arrow IPC and Parquet           |
| `png`       | Vega-Lite PNG                   |

JSON, text, HTML, bytes, and Vega-Lite JSON use the base producer package. Running through the notebook project preserves its packages, credentials, and native libraries. `--no-sandbox` keeps marimo's kernel in that environment so the scratchpad can import `marimo_export` and project notebook objects directly.

The AnyWidget extra captures a selected widget's static model graph for the Pythonless loader. See [Publish AnyWidget outputs](./anywidget.md) for a complete notebook, plan, publication, and mount workflow.

The prepared-project command starts the loopback producer used by the [SSH tunnel](#reach-a-remote-machine-through-ssh). Keep that server bound to `127.0.0.1`. For a shared network endpoint, omit both `--no-*` flags and configure the client credentials described under [Authentication](#authentication).

## Reach a remote machine through SSH

Run the producer on the remote machine bound to its loopback interface. From the consumer machine, forward the port:

```bash
ssh -N -L 2718:127.0.0.1:2718 USER@HOST
```

Install the JavaScript client on the consumer machine:

```bash
pnpm add @marimo-team/marimo-export
```

The client then uses the forwarded endpoint:

```bash
pnpm exec marimo-export describe \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/remote/notebook.py
```

`--notebook` is the path visible to the remote marimo server. Pass `--session` with a primary session ID returned by `GET /api/sessions`.

## Session targets

`connectRemote()` accepts exactly one target:

```ts
{
  notebook: "/absolute/path/on/server/notebook.py";
}
```

or:

```ts
{
  sessionId: "s_1234";
}
```

A notebook target routes the file through marimo's WebSocket session connector. Marimo may create a managed session or resume an orphaned session. When the connector requires client-side instantiation, marimo-export sends `autoRun: false`. The client retains the connection for the remote handle's lifetime and records the connector's ownership result in `remote.session.owned`.

When the notebook is already active in kiosk mode, a notebook target fails with `session_open_failed`. Read `GET /api/sessions` and connect with one of its top-level keys as `target: { sessionId: "s_1234" }`.

Before opening the notebook WebSocket, marimo-export sends a `POST` to marimo's protected `api/home/running_notebooks` endpoint with the configured authentication and skew-protection headers. Notebook targets therefore require Fetch and WebSocket support, and the endpoint must accept the configured headers before the connector opens.

A session ID target first reads `GET /api/sessions` and requires an exact top-level key. This rejects expired IDs, typos, and kiosk consumer IDs before scratchpad control runs. A valid session target attaches as a borrowed session. A resumed orphan is also borrowed. In both modes, export scenarios run in fresh child runners created from the saved notebook snapshot. `remote.close()` releases active transfer leases. For a managed session, it requests shutdown and waits until marimo stops reporting that session. For a borrowed session, it closes the client connection and leaves the session running.

Run at most one remote request at a time against an attached kernel, including across clients, and keep interactive work idle until that request settles. Marimo's scratchpad disconnect watcher interrupts the attached session as a whole, so a timeout, abort, or transport disconnect can interrupt other work on that kernel. Each scenario gets fresh notebook graph state in a child runner. Imported modules, environment variables, files, random generators, native-library globals, and background tasks remain process-wide.

When marimo starts the notebook with user arguments, root cells and nested apps receive those arguments during export. The producer disables native cell caching for that build because the argument vector is process state outside marimo's cache identity.

## Publish through the CLI

`publish` combines build, transfer, and local verification:

```bash
pnpm exec marimo-export publish \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan export.plan.yaml \
  --out ./public/notebook-export \
  --record ./notebook-export.build.json
```

The producer keeps marimo's execution cache on the remote machine. A transfer stage contains `index.json` and the portable projection payloads referenced by that index. The stage is a temporary lease under the notebook's served `public/.marimo-export/` area. Closing the lease requests its release. A live producer expires the stage after 30 minutes, including while a transfer is active, so one pull must finish within that lease. After a producer restart, the next stage operation restores the remaining lease duration or removes an expired orphan.

The local publication persists after the stage, remote session, and Python server close.

## Use the TypeScript remote API

The `/remote` entrypoint owns session control. The `/node` entrypoint owns local filesystem transfer:

```ts
import { directorySource, pullRemote, verifyExport } from "@marimo-team/marimo-export/node";
import { connectRemote, validateExportPlan } from "@marimo-team/marimo-export/remote";

const plan = validateExportPlan({
  schema: "marimo-export.plan.v1",
  outputs: {
    summary: {
      source: "summary",
      formats: { json: {} },
    },
  },
});

const remote = await connectRemote({
  server: "http://127.0.0.1:2718/",
  target: { notebook: "/absolute/path/on/server/notebook.py" },
  timeoutMs: 300_000,
});

try {
  const description = await remote.describe();
  const build = await remote.build(plan);
  const pull = await pullRemote(remote, build.ref, {
    into: "./public/notebook-export",
    concurrency: 8,
  });
  const verification = await verifyExport({
    source: directorySource("./public/notebook-export"),
    ref: build.ref,
  });
  console.log({ description, build, pull, verification });
} finally {
  await remote.close();
}
```

[`examples/remote-client.mjs`](https://github.com/marimo-team/marimo-export/blob/main/examples/remote-client.mjs) runs the same workflow against the cache-matrix notebook. It reads `MARIMO_TOKEN` and `MARIMO_SERVER_TOKEN` from the environment when the marimo server requires them.

`validateExportPlan(value)` accepts `unknown`, performs structural wire preflight, and returns a frozen `ExportPlan`. `remote.build()` performs the same preflight before sending a request. Python remains authoritative for Python identifier and keyword validity, notebook definitions, import resolution, serializer availability, and exporter results.

### `connectRemote(options)`

Creates a remote session handle.

- `server`: Absolute HTTP or HTTPS base URL with no embedded credentials, query, or fragment.
- `target`: One notebook path or primary session ID from `GET /api/sessions`.
- `authToken`: marimo authentication token used for the bearer header and WebSocket `access_token`.
- `serverToken`: marimo skew-protection token sent as `Marimo-Server-Token`.
- `headers`: Additional request headers.
- `fetch`: Custom Fetch implementation.
- `WebSocket`: Custom WebSocket constructor used when opening a notebook.
- `connectTimeoutMs`: Notebook-session startup timeout in milliseconds. Default `30000`.
- `timeoutMs`: Remote request timeout in milliseconds. Default `300000`.
- `signal`: Abort signal for connection work.

The top-level `signal` bounds connection and notebook-session startup work. `remote.describe()`, `remote.build()`, `remote.open()`, lease `close()`, and `remote.close()` each accept `{signal, timeoutMs}` for that request. Describe, build, stage, and direct lease-release requests default to `300000` milliseconds. The complete `remote.close()` sequence defaults to `30000` milliseconds when neither the connection nor the close call supplies `timeoutMs`. That close deadline covers active requests, stage openings and releases, and managed-session shutdown. The retained local socket is always closed during cleanup.

A timeout means the client stopped waiting. Remote startup or execution may still complete on the producer, or marimo may interrupt the attached session when the scratchpad disconnects.

### `remote.describe(options?)`

Returns protocol, marimo-export version, marimo version, adapter name, and producer format capabilities. Each capability reports `available` and the required producer extra.

### `remote.build(plan, options?)`

Runs the complete scenario matrix and returns the publication reference and execution receipt:

```ts
{
  ref: { key, sha256, size },
  receipt: { elapsedMs, scenarioCount, projectionCount },
}
```

`projectionCount` counts scenario output-format entries. The receipt summarizes execution, and `ref` anchors the immutable publication. The `build` CLI command adds the server and notebook target when it writes a `marimo-export.build.v1` record for a later `pull` command.

The producer reads the saved notebook file once for the build and creates a fresh app from that snapshot for each scenario. After scenario and payload verification, it rereads the saved file once before writing the index and requires its bytes to match the captured snapshot.

### `remote.open(ref, options?)`

Creates a temporary server-side stage and returns `{source, expiresAt, close}`. `expiresAt` is the stage's Unix epoch expiration in milliseconds. Pass the source to `openExport()` or `pullExport()`, and call `close()` when transfer finishes. Expiration is a cleanup backstop, so consumers should release the lease explicitly.

### `remote.close(options?)`

Waits for active work and stage openings, attempts every open-stage release, and requests managed-session shutdown after no active work or leases remain. Shutdown completes after marimo's running-notebook endpoint stops reporting the session. `remote.close()` always closes the retained local socket and reports the first cleanup error. A failed stage release remains registered for retry. When a release fails during `remote.close()`, that close attempt skips managed-session shutdown, closes the local socket, and rejects. Marimo may later expire the disconnected session. Retry the release with the lease's `close()` or a later `remote.close()`. After every release succeeds, a later `remote.close()` requests shutdown for a managed session. A completed close is idempotent.

## Authentication

The CLI and `examples/remote-client.mjs` read credentials from environment variables:

| Variable              | Request behavior                                              |
| --------------------- | ------------------------------------------------------------- |
| `MARIMO_TOKEN`        | Supplies marimo authentication for HTTP and WebSocket access. |
| `MARIMO_SERVER_TOKEN` | Supplies marimo's skew-protection request header.             |

Use HTTPS when connecting across a network. An SSH port forward keeps the public client URL on loopback while the tunnel protects transport to the remote machine.

The build record contains the server URL, notebook path, `ExportRef`, and receipt. It contains no authentication token. Create records through a notebook target so a later pull can open a fresh session. Use an explicit session target for a same-connection `publish` or direct `Remote` workflow.

## Failure behavior

Remote operations throw `MarimoExportError` with a stable `code`. A readable run server or strict producer kernel reports `unsupported_mode`. Session lookup and attachment failures report `session_open_failed`. Other common codes include `session_timeout`, `session_unavailable`, `invalid_plan`, `scenario_failed`, `remote_timeout`, `stage_failed`, and `integrity_failed`.

A scenario failure can leave completed native cache entries and content-addressed payloads on the producer. The index is committed after every referenced payload passes verification and the saved-file comparison succeeds. A returned `ExportRef` therefore identifies one complete build.
