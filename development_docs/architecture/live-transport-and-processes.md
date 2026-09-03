# Live transport and managed processes

marimo-export reaches a running Marimo kernel through one strict bridge. A file
producer creates and owns the kernel process. A live-session producer borrows a
kernel that an application already owns. Both paths use the same bridge request,
response, and asset-transfer contracts.

```text
file source -> OwnedNotebook -> ManagedServer -> Client -> Session -> bridge
live server -------------------------------> Client -> Session -> bridge
```

## Terms and ownership

| Term             | Contract                                                                                                    | Owner                                       |
| ---------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Owned notebook   | Single-use context over a stable source revision, sibling working copy, managed server, client, and session | `OwnedNotebook`                             |
| Managed server   | Authenticated loopback Marimo edit process and its descendant process tree                                  | `ManagedServer`                             |
| Client           | Local authenticated HTTP transport configuration                                                            | Caller                                      |
| Borrowed session | Handle for one session ID on an application-owned Marimo server                                             | Application                                 |
| Bridge request   | Correlated JSON request executed through Marimo's scratchpad route                                          | `_remote/client.py` and `_marimo/bridge.py` |
| Transfer ticket  | Bounded lease over temporary Marimo virtual files                                                           | Kernel transfer registry                    |

`Client.close()` invalidates the local client and every `Session` bound to it. It
sends no shutdown request and releases no application-owned server or session.

## Owned notebook lifecycle

`prepare()` starts an owned notebook only after exact prepared-export reuse has
missed and the preparation reservation is held. The owned path then follows this
order:

1. Read one revision-stable source byte snapshot.
2. Create a private sibling notebook copy with the same suffix.
3. Recheck the authored source revision and SHA-256.
4. Start `ManagedServer` with the sibling copy as its executable notebook and the
   authored path as its runtime filename.
5. Send the generated access token once through the child stdin pipe and close
   that pipe.
6. Wait for the loopback server and edit-session stream.
7. Instantiate the kernel with autorun disabled.
8. Confirm the managed cache activation handshake.
9. Submit every authored cell ID. Marimo executes the enabled graph according
   to its disabled-cell rules, then marimo-export validates the resulting
   baseline.
10. Plan and capture missing states through the bridge.
11. Commit each prepared state and the exact prepared-export generation while the
    managed server remains active.
12. Close the client, stop the managed server and descendants, then remove the
    sibling notebook copy.

The process working directory is the notebook directory. The child inherits the
caller's environment, including normal `sitecustomize` behavior. Managed
activation values are removed inside the kernel before notebook execution.

`OwnedNotebook` is single use. It checks the authored source before and after
inspection, planning, and capture. A changed hash or filesystem revision fails
with `notebook_changed`. The managed copy is the file Marimo executes, while the
authored path remains the logical runtime filename and source identity.

Repository publication precedes managed teardown. A teardown failure after the
generation commit closes the newly created `PreparedExport` handle and fails
the caller, while the committed generation remains available in the
repository. Changing this order requires an explicit repository rollback
design.

## Managed startup and process ownership

`ManagedServer` launches:

```text
python -m marimo_export._marimo.managed_server edit <working-copy>
  --headless
  --token-password-file -
  --no-skew-protection
  --host 127.0.0.1
  --port <allocated-port>
```

The access token is absent from command arguments and temporary files. The parent
writes it once to stdin. Server output goes to a private temporary log whose
diagnostic rendering redacts the token and retains at most the final 8 KiB.

The managed kernel lifespan must be available under the `marimo-export` entry
point. `MARIMO_KERNEL_LIFESPAN_DENYLIST` may not contain that name. When
`MARIMO_KERNEL_LIFESPAN_ALLOWLIST` is present, it must contain that name. The
parent checks this policy before starting the notebook process.

The kernel lifespan validates the exact supported Marimo cache seams, installs
the managed cache lease, installs parent-stop provenance, and writes a private
activation token. `ManagedServer.activate()` accepts the kernel only after
reading that exact token and completing baseline validation.

On POSIX, the server starts in a new process group. The owner records the server
group and descendant groups before and after notebook execution. It also passes
its process ID through `MARIMO_ANCESTOR_PID`, which lets Marimo's parent poller
terminate the server group after abrupt owner death. Kernel pollers own their
child groups.

On Windows, the server process starts suspended. `WindowsJob.create_for_process()`
creates a kill-on-close Job Object, assigns the process, then resumes it.
Descendants inherit the job. Normal shutdown terminates the job and waits for its
active-process count to reach zero before closing the handle. Abrupt owner death
closes the inherited job handle through the operating system.

## Managed edit-session activation

The managed path uses two server-sent event streams for different jobs:

- `_SessionStream` observes the owned edit session. It waits for `kernel-ready`,
  records cell source and status updates, and counts `completed-run` events.
- `HttpKernelTransport` reads one scratchpad execution stream for each bridge
  operation. It extracts the correlated bridge response from stdout and retains
  bounded stderr only when the scratchpad reports failure.

The owned edit session is instantiated with `autoRun: false`. After
instantiation completes, `ManagedServer` checks that cells are idle, verifies the
cache activation token, submits every authored cell, waits for another completed
run, and invokes `validate_baseline`. Baseline validation rejects failed or
cancelled authored cells before planning or capture.

## Borrowed session lifecycle

`sessions.connect()` constructs a `Client`. Network I/O begins when the caller
lists sessions or invokes an operation. `Client.sessions()` reads and validates
the complete Marimo session registry. `Client.session()` accepts an explicit
session ID, or selects the session only when the registry contains exactly one.

`Session.plan()` and `Session.capture()` keep the server and session active.
Live preparation resolves the producer and plan before reservation acquisition,
then resolves them again after acquiring the reservation. A changed producer
fails before state capture. The capture bridge records declared parent UI values
before the state loop, verifies them after the loop, and the client verifies the
parent document again after downloading assets.

| Session operation   | Result                                                                       |
| ------------------- | ---------------------------------------------------------------------------- |
| `inspect()`         | Immutable notebook, definition, cell, capability, and implementation records |
| `observe_inputs()`  | Portable live UI roots and typed control bindings                            |
| `plan(spec=...)`    | Repository-aware `ExportPlan` without state execution                        |
| `capture(spec=...)` | Leased `PreparedExport` prepared through the borrowed session                |

The two capture entry points divide timeout ownership differently:

| Entry point                                                    | HTTP transport timeout | Reservation and repository timeout |
| -------------------------------------------------------------- | ---------------------- | ---------------------------------- |
| `capture(server, timeout=T)`                                   | `T`                    | `T`                                |
| `connect(server, timeout=A)` then `session.capture(timeout=B)` | `A`                    | `B`                                |

`Session.plan()` uses the parent client's HTTP timeout and acquires no
preparation reservation.

## Server address and credential policy

`parse_server_address()` accepts an absolute HTTP or HTTPS URL. Plain HTTP is
restricted to `localhost` and loopback IP addresses. A remote host therefore
requires HTTPS.

The server address rejects:

- user information in the URL authority
- query strings and fragments
- whitespace and control characters
- ports outside 1 through 65535

Credentials use dedicated arguments and headers:

| Credential     | Header                          | Role                                                   |
| -------------- | ------------------------------- | ------------------------------------------------------ |
| `access_token` | `Authorization: Bearer <value>` | Authenticate to the Marimo server                      |
| `server_token` | `Marimo-Server-Token: <value>`  | Satisfy Marimo server-token and skew-protection checks |

`Client` reads `MARIMO_EXPORT_ACCESS_TOKEN` and
`MARIMO_EXPORT_SERVER_TOKEN` when the corresponding explicit argument is
`None`. An explicit value takes precedence. Credentials must contain
HTTP-header-compatible characters. They remain out of normalized server URLs,
object representations, operation URLs, and notebook exports.

The HTTP opener rejects redirects. A credentialed request therefore stays on
the configured origin instead of following a response to another host.

## HTTP and bridge protocol

The transport uses these Marimo edit-server surfaces:

| Surface                        | Use                                                            |
| ------------------------------ | -------------------------------------------------------------- |
| `GET api/sessions`             | Discover bounded session metadata                              |
| `POST api/kernel/execute`      | Run one correlated bridge request through the selected session |
| `GET @file/<member>`           | Download one temporary transfer asset                          |
| `GET /sse?session_id=...`      | Observe managed edit-session readiness and run completion      |
| `POST /api/kernel/instantiate` | Start the owned kernel without autorun                         |
| `POST /api/kernel/run`         | Execute the owned notebook's authored cells                    |
| `POST /api/kernel/shutdown`    | Request graceful owned-kernel shutdown                         |

Each scratchpad request uses schema `marimo-export.bridge.v1` and contains:

```text
schema
client_version
client_identity
request_id
operation
params
```

The bridge accepts `validate_baseline`, `inspect`, `observe_inputs`, `plan`,
`capture`, and `release`. It requires exact request fields, the installed package
version, the exact marimo-export implementation SHA-256, a nonempty request ID,
one known operation, and an object of operation-specific parameters. The client
uses a random response marker plus the request ID to reject missing, duplicated,
or mismatched responses.

The client submits each scratchpad operation once. It performs no automatic
retry after a transport failure. A timeout or broken response stream can leave
the remote scratchpad operation running, so caller retry is a new operation with
its own possible side effects.

The selected session executes the scratchpad bridge and notebook state work with
the kernel's file, package, credential, and network authority. Authentication
protects access to that authority. It does not reduce the authority of notebook
or exporter code.

## Server-sent event and response bounds

`SSEParser` incrementally parses strict UTF-8. It accepts carriage-return and
line-feed framing, multiline `data` fields, comments, and unknown fields. It
rejects NUL bytes, invalid UTF-8, empty or oversized event names, more than 4096
data lines, and events beyond their configured byte limit.

| Value                                 |             Bound |
| ------------------------------------- | ----------------: |
| Managed or scratchpad event           |            40 MiB |
| Bridge response extracted from stdout |             8 MiB |
| Session registry                      |             1 MiB |
| One session filename or path          | 32 KiB characters |
| Retained failed-scratchpad stderr     |  8 KiB characters |
| Bridge request JSON                   |             8 MiB |

The scratchpad reader accepts `stdout`, `stderr`, and `done`. Unknown event names
are ignored. Events after `done`, an absent `done`, invalid JSON, an invalid
success flag, multiple response markers, and a missing response marker fail the
operation. Structured bridge error messages and nested detail keys and values
pass through credential redaction before becoming `BridgeError`.

## Timeout semantics

The public timeout is positive and finite, but each owner applies it at its own
blocking boundary:

| Boundary                            | Deadline behavior                                 |
| ----------------------------------- | ------------------------------------------------- |
| Server readiness                    | Absolute deadline for loopback readiness          |
| Managed kernel or run wait          | Deadline resets after an accepted kernel event    |
| Scratchpad execution                | Deadline resets after each response chunk         |
| Session registry or asset download  | Absolute deadline for that bounded response       |
| Reservation acquisition             | Bounded by the preparation timeout                |
| Managed process stop                | Soft wait, forced wait, and final bounded reaping |
| Windows temporary-directory cleanup | Retries filesystem errors until the stop timeout  |

Progress on one state or operation can let a multi-state preparation exceed the
numeric timeout. Timeout remains an inactivity or per-boundary limit rather than
one wall-clock deadline for the complete preparation.

## Transfer tickets and asset verification

The bridge converts each unique non-scalar receipt into one temporary Marimo
virtual file. A transfer ticket records its ID, expiry time, and assets. Each
asset contains a codec, SHA-256, byte size, and relative `./@file/` URL.

Kernel transfer limits are:

- 64 MiB for one export asset
- 512 MiB for one ticket's unique asset bytes
- 4096 assets per ticket
- 64 active tickets per process
- 1 GiB retained across active and recovery tickets
- five minutes for the default lease and thirty minutes for the maximum lease

The caller's `CaptureLimits` may impose smaller asset and closure bounds. The
client extracts the ticket ID before validating the rest of the response, which
lets it release temporary files after malformed index, transfer, cache, or timing
data. It validates declared codecs, unique identities, exact asset membership,
the canonical index size, and total closure size before starting downloads.

Asset downloads must use the configured Marimo origin and one flat `@file`
member. User information, query strings, fragments, decoded path separators, dot
segments, and control characters are rejected. Each response is bounded while
reading. The client then checks exact length and SHA-256 before retaining bytes.

The client releases the ticket after every asset verifies. Failure and
cancellation also attempt release. The registry tries every virtual-file removal.
Files that remain registered enter a one-second recovery lease and count against
the same process limits until cleanup succeeds. Expired tickets are swept before
new admission and by a daemon timer. Releasing a ticket that has already expired
or completed returns `released: false` from the bridge and leaves no file owned by
that ticket.

## Teardown and failure precedence

`ManagedServer.stop()` attempts every cleanup phase in this order:

1. Mark the edit-session stream as closing.
2. Capture the latest known descendant process groups.
3. Request graceful kernel shutdown on POSIX.
4. Terminate and reap the server process tree.
5. Join and close the edit-session response stream.
6. Close the log and remove the temporary server directory.

POSIX process shutdown sends a soft signal, waits, sends a forced signal, waits
again, then kills recorded descendant groups. It excludes the caller's process
group and treats already-exited groups as complete. Windows terminates and waits
for the Job Object before closing its handle.

A cancellation exception takes precedence over ordinary cleanup failures.
Remaining failures attach as bounded cleanup diagnostics. With ordinary failures,
the first failure is the cause of `server_shutdown_failed` after every cleanup
phase has been attempted.

`OwnedNotebook` closes its client, managed server, and working copy in that
order. An active planning, capture, or source error remains primary. Cleanup
failure diagnostics record the operation and exception type without including
the cleanup exception message. When there is no active error, the first cleanup
failure is raised after the remaining resources have been attempted.

## Validation

Run the focused protocol and lifecycle suites:

```bash
uv run pytest -q \
  packages/python/tests/test_client.py \
  packages/python/tests/test_remote_auth.py \
  packages/python/tests/test_remote_client.py \
  packages/python/tests/test_remote_sse.py \
  packages/python/tests/test_producer.py \
  packages/python/tests/test_managed_server_process.py \
  packages/python/tests/test_managed_server_stream.py \
  packages/python/tests/test_managed_server_cleanup.py
```

Then run `packages/python/tests/test_managed_server_integration.py` and the live
build and capture paths. Process ownership claims require the Ubuntu and Windows
CI matrix.
