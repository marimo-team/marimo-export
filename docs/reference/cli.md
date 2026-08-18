---
title: CLI reference
description: Command syntax, options, machine output, side effects, and exit codes.
---

# CLI reference

`marimo-export` creates, inspects, and verifies notebook exports.

```text
marimo-export {build,capture,session,inspect,verify} ...
```

## `build`

```text
marimo-export build NOTEBOOK --spec FILE --output DIR
                    [--replace] [--timeout SECONDS] [--json]
```

Starts and executes `NOTEBOOK`, prepares the ExportSpec states, and writes the
notebook export to `DIR`. The command owns its temporary marimo server, session,
and process tree.

- `--spec FILE`: JSON or YAML ExportSpec.
- `--output DIR`: New notebook export directory.
- `--replace`: Atomically replace an existing real directory on macOS or Linux.
- `--timeout SECONDS`: Server readiness and inactivity timeout. Default: `30`.

## `capture`

```text
marimo-export capture SERVER --session ID --spec FILE --output DIR
                      [--replace] [--access-token TOKEN]
                      [--server-token TOKEN] [--timeout SECONDS] [--json]
```

Prepares the ExportSpec states through an active edit session and writes the
notebook export to `DIR`. The command leaves the server and session active.

- `SERVER`: Absolute marimo server URL.
- `--session ID`: Selected session. Omit when the server has exactly one.
- `--access-token TOKEN`: Browser access token.
- `--server-token TOKEN`: Server authentication token.
- `--timeout SECONDS`: Connection and inactivity timeout. Default: `30`.

`MARIMO_EXPORT_ACCESS_TOKEN` and `MARIMO_EXPORT_SERVER_TOKEN` provide the two
credentials without placing them in the command line.

## `session`

```text
marimo-export session NOTEBOOK_OR_SERVER [--session ID]
                      [--access-token TOKEN] [--server-token TOKEN]
                      [--timeout SECONDS] [--json]
```

With a notebook path, starts and executes the notebook in a temporary session,
then reports definitions available to an ExportSpec. With a server URL, lists
sessions or inspects the selected session.

::: warning File inspection executes notebook code
`session NOTEBOOK` has the current file, credential, network, and package
access.
:::

## `inspect`

```text
marimo-export inspect EXPORT [--json]
```

Reads `index.json` and reports notebook identity, inputs, outputs, states,
representations, and declared asset size. It does not read every asset.

## `verify`

```text
marimo-export verify EXPORT [--json]
```

Reads every declared asset and verifies the complete export closure.

## Common output

Human success output goes to standard output. Human errors use standard error.
`--json` writes either the success or failure object to standard output. Every
expected failure returns a stable exit code.

Success:

```json
{ "ok": true, "result": {} }
```

Failure:

```json
{ "error": { "code": "...", "message": "..." }, "ok": false }
```

Progress resets the inactivity timeout. Credentials, server internals, and
session operation paths stay out of export data and public diagnostics.

## Exit codes

|  Exit | Meaning                         |
| ----: | ------------------------------- |
|   `0` | Success                         |
|   `2` | Arguments, spec, or local input |
|   `3` | Server transport                |
|   `4` | Session or managed notebook     |
|   `5` | Notebook state or output        |
|   `6` | Export or integrity check       |
|   `7` | Filesystem write                |
| `130` | Interrupted                     |
| `141` | Closed output pipe              |

[Build or capture](../guide/build-and-capture.md) provides the complete producer
workflow.
