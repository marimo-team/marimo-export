---
title: Build or capture
description: Prepare reusable notebook states from a file or live session and write a verified export.
---

# Build or capture

Use `build` for a saved notebook file. Use `capture` when a named live session
already owns the environment or completed computation.

| Source         | Preparation call         | Result                                 |
| -------------- | ------------------------ | -------------------------------------- |
| Saved notebook | `prepare()` or `build()` | Owned session closes after preparation |
| Live session   | `capture()`              | Borrowed session remains active        |

Both paths use the same `ExportSpec`, export repository, `PreparedExport`, and
portable export format.

## Inspect the plan

```bash
marimo-export plan finance.py \
  --spec finance.export.yaml
```

The result reports the producer identity, inferred inputs, normalized states,
default alias, outputs, observations, reusable states, and states to prepare.
Planning may execute the notebook's initial autorun when the repository has no
exact prepared export.

Use JSON for automation:

```bash
marimo-export plan finance.py \
  --spec finance.export.yaml \
  --json
```

## Build from a notebook file

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

`build` validates the destination, resolves the plan, prepares missing states,
writes a staged export, verifies it, and commits the directory atomically. Pass
`--replace` to replace an existing export directory.

Stream preparation progress as JSON Lines:

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance \
  --replace \
  --jsonl
```

Progress records have `type: "progress"`. The final record has `type: "result"`
or `type: "error"`. `--json` emits one terminal object and suppresses progress.

## Retain a PreparedExport in Python

Use `prepare()` when an application needs to serve assets, create a prepared
manifest, inspect the immutable export, or choose when to write it:

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("finance.export.yaml")

with prepare("finance.py", spec=spec) as prepared:
    export = prepared.open()
    print(export.default_state.fingerprint)

    manifest = prepared.manifest(
        "/runtime/export/",
        state="baseline",
        refresh_interval_ms=1_000,
    )
    prepared.write("dist/finance", replace=True)
```

The context owns the repository lease. `PreparedExport.asset(relative)` returns
an independently leased `PreparedAsset` for HTTP response lifetimes that extend
beyond the parent handle. Close each asset handle after its response completes.

## Capture an active session

List live sessions:

```bash
marimo-export inspect http://127.0.0.1:2718 --json
```

Inspect one session:

```bash
marimo-export inspect http://127.0.0.1:2718 \
  --session SESSION_ID \
  --json
```

The CLI capture command prepares the export through the configured repository,
writes it to a deployment directory, and closes its preparation lease:

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output dist/finance \
  --replace \
  --jsonl
```

Use Python when an application needs to retain the prepared handle before
writing:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("finance.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/finance", replace=True)
```

The notebook environment must import the same marimo-export implementation and
exporter dependencies as the client. Restart a live session after changing an
already imported custom exporter module.

## Configure the export repository

Pass one repository explicitly:

```python
from marimo_export import ExportRepository, ExportSpec, prepare

spec = ExportSpec.from_file("finance.export.yaml")

with ExportRepository.open(".exports") as repository:
    with prepare("finance.py", spec=spec, repository=repository) as prepared:
        print(prepared.reused_states)
```

CLI repository precedence is:

1. `--repository DIR`
2. `MARIMO_EXPORT_REPOSITORY`
3. the operating system cache directory

Inspect and prune repository storage:

```bash
marimo-export repository status
marimo-export repository prune --dry-run
marimo-export repository prune
```

Active leases protect prepared exports and state artifacts from pruning.

## Authenticate to a live server

Set credentials through the environment:

```bash
export MARIMO_EXPORT_ACCESS_TOKEN="..."
export MARIMO_EXPORT_SERVER_TOKEN="..."
```

To read credentials from files, load each value into its environment variable:

```bash
IFS= read -r MARIMO_EXPORT_ACCESS_TOKEN < access-token
IFS= read -r MARIMO_EXPORT_SERVER_TOKEN < server-token
export MARIMO_EXPORT_ACCESS_TOKEN MARIMO_EXPORT_SERVER_TOKEN
```

Diagnostics redact configured token values.

## Verify the deployment directory

```bash
marimo-export verify dist/finance
```

`verify` reads every declared asset and checks its size, SHA-256, native framing,
and descriptor agreement. Use `verify_export("dist/finance")` for the same Python
boundary.
