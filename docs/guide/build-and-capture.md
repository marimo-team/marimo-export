---
title: Build or capture
description: Create the same notebook export from a saved notebook file or an active marimo session.
---

# Build or capture

`build` supports automated publication. `capture` reuses an active notebook
without reproducing its completed work. Both commands write the same notebook
export format for Python, browser, agent, and custom clients.

| Source state                                              | Producer  |
| --------------------------------------------------------- | --------- |
| A saved notebook should start, run, and close for the job | `build`   |
| An active kernel already holds the required state         | `capture` |

## Build from a notebook file

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

`build` creates a temporary sibling copy, starts an authenticated loopback
marimo server, executes the notebook, prepares every state, writes the export,
then closes the session and process tree. It verifies that the original source
did not change during the run.

Pass `--replace` to atomically replace an existing real destination directory
on macOS or Linux.

## Capture an active session

List the server's sessions:

```bash
marimo-export session http://127.0.0.1:2718 --json
```

Inspect the selected session before writing the ExportSpec:

```bash
marimo-export session http://127.0.0.1:2718 \
  --session SESSION_ID \
  --json
```

Create the export:

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output dist/finance
```

Capture leaves the selected server and session active. Omit `--session` when
the server has exactly one session.

The notebook environment must contain the same marimo-export implementation
and the dependencies used by its exporters. The bridge compares both the
package version and installed source identity before state execution.

## Authenticate to the server

Set credentials through the environment:

```bash
export MARIMO_EXPORT_ACCESS_TOKEN="..."
export MARIMO_EXPORT_SERVER_TOKEN="..."
```

`--access-token` and `--server-token` provide the same values for one command.
Diagnostics and export data exclude these credentials.

## Verify the completed export

```bash
marimo-export inspect dist/finance
marimo-export verify dist/finance
```

`inspect` reports states, inputs, outputs, representations, and declared asset
size. `verify` reads every declared asset and checks its length, digest, native
framing, and descriptor agreement.

Use `--json` for one machine-readable success or failure record. The
[CLI reference](../reference/cli.md) defines command options, output, and exit
codes.
