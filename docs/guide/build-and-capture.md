---
title: Build or capture
description: Choose the producer that owns the notebook, prepare missing states, and write one verified export.
---

# Build or capture

Use `build` when a saved notebook file is the source of truth. Use `capture`
when a running marimo session already owns the environment or current baseline.

| Starting point       | Command                 | Producer ownership after the command                     |
| -------------------- | ----------------------- | -------------------------------------------------------- |
| Saved notebook       | `marimo-export build`   | The managed server, session, and process tree are closed |
| Running edit session | `marimo-export capture` | The borrowed server and session remain active            |

Both commands use the same `ExportSpec`, export repository, state preparation,
verification, and notebook export format.

## Build from a saved notebook

Build the deterministic quickstart from the repository root:

```bash
mkdir -p dist
uv run marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart
```

`build` validates the destination before notebook execution. It plans the
relation, prepares missing states, stages the notebook export, verifies
`index.json` and every declared asset, then commits the directory. Add
`--replace` when the destination already exists:

```bash
uv run marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart \
  --replace \
  --jsonl
```

JSON Lines output contains ordered `progress` records followed by one `result`
or `error` record. Use `--json` when automation needs one terminal object and no
progress stream.

### Grant access to the notebook directory

A file build creates a temporary copy beside the notebook. The notebook's
parent directory must be a real writable directory. The managed marimo server
runs the copy while marimo-export checks that the authored source stays stable.
Cleanup removes the copy and closes the owned server, session, and process tree.
The authored notebook remains byte-for-byte unchanged.

The notebook autorun and selected state runs use the current producer
environment. They can read files, import packages, access credentials, and make
network requests available to that process. Review notebook code with the same
care as any Python program before building it.

The destination parent must already exist and be writable. Staging occurs
beside the destination so the final directory can be installed atomically on a
supporting filesystem. `--replace` checks that an existing destination has not
changed since preflight before it commits the replacement.

## Inspect reuse before preparation

Run `plan` against the same repository used by `build`:

```bash
uv run marimo-export plan examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --json
```

The repository looks up one exact requested relation through three hashes:

```text
producer identity + output-plan identity + ExportSpec identity
```

The producer identity covers the notebook source and document, the installed
marimo and marimo-export implementations, Python and platform facts, installed
distribution versions, and relevant local source files. The output-plan
identity covers the output declarations. The ExportSpec identity covers the
complete requested relation.

This combined value is the plan identity used for repository lookup. A committed
notebook export has a separate export identity, which is the SHA-256 digest of
its canonical `index.json`.

When the repository contains that exact verified generation, `plan`, `prepare`,
and `build` reuse it before a notebook process starts. marimo-export still
rechecks the file producer identity and verifies the selected repository
artifact.

When the exact generation is absent, planning compares state fingerprints under
the same producer and output plan. It reuses matching prepared states and runs
the missing states. Common changes behave as follows:

| Change                                  | Preparation result                                    |
| --------------------------------------- | ----------------------------------------------------- |
| Exact repeat                            | Reuse the complete generation before notebook startup |
| Change the default alias                | Reuse matching states and assemble a new generation   |
| Add one state                           | Prepare the new state and reuse matching states       |
| Remove one state                        | Assemble a new generation from the remaining states   |
| Change an output declaration            | Create a new output-plan scope                        |
| Change notebook or producer environment | Create a new producer scope                           |

Observations do not make an exact export stale because they are authoring
evidence, not published states. Update the `ExportSpec` when an observed vector
should enter the exported relation.

Source, environment, implementation, or live-document drift during preparation
fails the operation before generation commit. The repository preserves its
previous current generation. A state that completed before a later failure can
remain reusable for the next attempt.

## Retain the prepared export from Python

Use `prepare()` when an application needs the leased repository generation
before deciding where to write it:

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("examples/quickstart/report.export.yaml")

with prepare("examples/quickstart/report.py", spec=spec) as prepared:
    notebook_export = prepared.open()
    print(notebook_export.default_state.output("summary").json())
    prepared.write("dist/quickstart", replace=True)
```

The `PreparedExport` owns a generation lease. Keep the handle open while reading
its files. `prepared.asset(relative)` creates an independent asset lease for a
file consumer or HTTP response that can outlive the parent handle. Close each
asset handle after its consumer finishes.

## Capture a running session

Start the quickstart notebook in one terminal:

```bash
uv run marimo edit examples/quickstart/report.py \
  --headless \
  --no-token \
  --port 2718
```

The server binds to `127.0.0.1` by default. `--no-token` is scoped to this local
loopback example. Keep authentication enabled for any server reachable by
another machine. Open the printed URL and wait for the notebook to finish its
initial run.

List the sessions from another terminal:

```bash
uv run marimo-export inspect http://127.0.0.1:2718 --json
```

Inspect the selected session when you need its current frontend input values,
definition names, or cell IDs:

```bash
uv run marimo-export inspect http://127.0.0.1:2718 \
  --session SESSION_ID \
  --json
```

Capture and write the quickstart relation:

```bash
uv run marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart \
  --replace \
  --jsonl
```

The server process and selected edit session belong to the caller. `capture`
borrows them, runs each missing state in a transient child, downloads and
verifies the result, and leaves the server and parent session active. State
overrides stay inside the child run, so the current parent controls and authored
notebook source remain unchanged.

The live session must be able to import the same marimo-export version and
implementation as the client. It must also contain every Python dependency used
by the notebook and its exporters. Restart the session after changing an
already imported custom exporter module.

Python `capture()` returns a `PreparedExport` instead of writing a destination:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("examples/quickstart/report.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/quickstart", replace=True)
```

The CLI composes the same call with `PreparedExport.write()` and closes the
preparation lease after writing.

## Configure the live connection

`SERVER` must be an absolute HTTP or HTTPS URL. Plain HTTP is accepted for
`localhost` and loopback IP addresses. Use HTTPS for a remote host. The URL can
include a base path, and marimo-export adds a trailing slash when needed. User
information, query strings, and fragments are rejected.

The two credentials have separate protocol roles:

| Environment variable         | Request header              |
| ---------------------------- | --------------------------- |
| `MARIMO_EXPORT_ACCESS_TOKEN` | `Authorization: Bearer ...` |
| `MARIMO_EXPORT_SERVER_TOKEN` | `Marimo-Server-Token: ...`  |

Set them in the capture environment:

```bash
export MARIMO_EXPORT_ACCESS_TOKEN="..."
export MARIMO_EXPORT_SERVER_TOKEN="..."
```

The Python `capture()` and `connect()` APIs also accept `access_token=` and
`server_token=`. An explicit Python argument takes precedence over its
environment variable. Keep credentials out of the server URL. Diagnostics and
structured bridge errors redact configured token values.

The HTTP transport rejects redirects and sends each request once. It does not
retry a failed request. Check the server and selected session, then rerun the
operation when recovery is safe. A timed-out scratchpad operation can continue
in the remote session, and a later retry can reuse any state that committed
successfully.

`--timeout` defaults to 30 seconds and must be a positive finite number. It
bounds connection setup, inactivity while reading a scratchpad execution
stream, each bounded asset download, and repository reservation acquisition.
Progress on the stream renews the inactivity deadline. Each operation receives
its own budget, so a progressing multi-state capture can run longer than the
configured number of seconds.

## Verify the written export

Verify the destination after either producer path:

```bash
uv run marimo-export verify dist/quickstart
```

`verify` reads the canonical index and every declared asset, then checks sizes,
SHA-256 digests, native framing, state fingerprints, and descriptor agreement.
Use [Manage the export repository](manage-repository.md) to inspect reusable
storage or [Consume a notebook export](consume-an-export.md) to read the result.
