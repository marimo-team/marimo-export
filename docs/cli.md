# Build or capture

`build` opens a notebook file. `capture` uses an open notebook session. Both
commands create the same notebook export.

| Task                  | Command   |
| --------------------- | --------- |
| Build from a file     | `build`   |
| Capture a session     | `capture` |
| Find notebook values  | `session` |
| Summarize an export   | `inspect` |
| Verify exported files | `verify`  |

## Build from a file

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

`build` prepares every state, writes the export, and closes its notebook
session. The notebook file stays unchanged.

Use `--replace` to atomically replace an existing destination on macOS or
Linux.

## Capture an open notebook

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output dist/finance
```

Capture leaves the selected session open. Omit `--session` when the server has
exactly one session.

The notebook environment must provide the same marimo-export version and the
dependencies used by its exporters.

Set server credentials through the environment:

```bash
export MARIMO_EXPORT_ACCESS_TOKEN="..."
export MARIMO_EXPORT_SERVER_TOKEN="..."
```

The equivalent flags are `--access-token` and `--server-token`.

## Find, inspect, and verify

```bash
marimo-export session http://127.0.0.1:2718
marimo-export session http://127.0.0.1:2718 --session SESSION_ID
marimo-export inspect dist/finance
marimo-export verify dist/finance
```

`session` lists open notebooks or one session's available values. `inspect`
summarizes a finished export. `verify` reads and checks every exported file.

## Common options

| Option              | Commands                      | Behavior                                          |
| ------------------- | ----------------------------- | ------------------------------------------------- |
| `--replace`         | `build`, `capture`            | Replace an existing export atomically             |
| `--timeout SECONDS` | `build`, `capture`, `session` | Set the inactivity timeout, which defaults to 30  |
| `--json`            | every command                 | Write one machine-readable result to standard out |

Progress resets the inactivity timeout.

JSON success and failure use stable top-level shapes:

```json
{ "ok": true, "result": {} }
```

```json
{ "error": { "code": "...", "message": "..." }, "ok": false }
```

## Exit codes

| Exit | Meaning                         |
| ---- | ------------------------------- |
| 0    | success                         |
| 2    | arguments, spec, or local input |
| 3    | server connection               |
| 4    | session or managed notebook     |
| 5    | notebook state or output        |
| 6    | export or integrity check       |
| 7    | filesystem write                |
| 141  | closed output pipe              |
