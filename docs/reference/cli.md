---
title: CLI reference
description: Command syntax, repository selection, machine output, side effects, and exit codes.
---

# CLI reference

`marimo-export` plans, prepares, verifies, and manages notebook exports.

```text
marimo-export {
  plan,
  build,
  capture,
  inspect,
  verify,
  observations,
  repository,
  doctor
} ...
```

## `plan`

```text
marimo-export plan NOTEBOOK --spec FILE
                   [--repository DIR] [--timeout SECONDS] [--json]
```

Resolves inferred inputs, normalized states, the default, observations,
repository reuse, and missing work. An exact repository match avoids notebook
startup. Cold planning executes the initial autorun.

## `build`

```text
marimo-export build NOTEBOOK --spec FILE --output DIR
                    [--repository DIR] [--replace]
                    [--timeout SECONDS] [--json | --jsonl]
```

Prepares missing states, writes the verified export to `DIR`, and closes the
owned notebook process tree. `--replace` atomically replaces an existing export
directory.

## `capture`

```text
marimo-export capture SERVER --session ID --spec FILE --output DIR
                      [--repository DIR] [--replace]
                      [--timeout SECONDS] [--json | --jsonl]
```

Prepares one named live session through the export repository, writes the
verified export to `DIR`, and closes the preparation lease. The server and
session remain active. `--replace` atomically replaces an existing export
directory.

Live server authentication reads `MARIMO_EXPORT_ACCESS_TOKEN` and
`MARIMO_EXPORT_SERVER_TOKEN` from the environment.

## `inspect`

```text
marimo-export inspect NOTEBOOK_OR_SERVER
                      [--session ID]
                      [--timeout SECONDS] [--json]
```

With a notebook path, executes the initial autorun and reports definitions,
cells, input modes, control paths, and producer facts. With a server URL, lists
sessions. Add `--session ID` to inspect one live session.

Server inspection reads the live authentication environment variables.

## `verify`

```text
marimo-export verify EXPORT [--json]
```

Reads `index.json` and every declared asset. The result contains state, output,
asset, and verified-byte counts.

## `observations list`

```text
marimo-export observations list NOTEBOOK --spec FILE
                                [--repository DIR]
                                [--timeout SECONDS] [--json]
```

Resolves the notebook plan, then reports its producer identity, inferred inputs,
observation revision, and each projected input vector with its fingerprint and
revision. Planning can execute the notebook's initial autorun.

## `observations clear`

```text
marimo-export observations clear NOTEBOOK --spec FILE
                                 [--repository DIR]
                                 [--timeout SECONDS] [--json]
```

Resolves the notebook plan, clears its producer observations, and reports the
producer identity, prior observation revision, and number removed.

## `repository status`

```text
marimo-export repository status [--repository DIR] [--json]
```

Reports producer, observation, prepared-state, identity, generation, byte, and
active-lease counts.

## `repository prune`

```text
marimo-export repository prune [--repository DIR] [--dry-run] [--json]
```

Applies repository retention. `--dry-run` reports removable prepared states,
generations, and bytes while leaving artifacts unchanged. Active leases protect
their artifacts.

## `doctor`

```text
marimo-export doctor [--repository DIR] [--json]
```

Reports the effective repository, Python executable and version, marimo-export
version, and pinned Marimo adapter compatibility. A failed compatibility check
returns exit code `4`.

## Common options

| Option              | Contract                                                    |
| ------------------- | ----------------------------------------------------------- |
| `--spec FILE`       | Strict JSON or YAML ExportSpec                              |
| `--repository DIR`  | Export repository for one command                           |
| `--timeout SECONDS` | Positive finite startup or inactivity timeout, default `30` |
| `--json`            | One terminal JSON success or failure object                 |
| `--jsonl`           | Progress records followed by one terminal JSON Lines record |

Repository precedence is:

1. `--repository DIR`
2. `MARIMO_EXPORT_REPOSITORY`
3. the operating system cache directory

## Output contracts

Human results use standard output. Human progress, warnings, and errors use
standard error.

JSON success:

```json
{ "ok": true, "result": {} }
```

JSON failure:

```json
{ "error": { "code": "...", "message": "..." }, "ok": false }
```

JSON Lines progress:

```json
{
  "progress": {
    "cache": null,
    "completed": 0,
    "elapsed_seconds": null,
    "kind": "state_started",
    "message": null,
    "state": "weekly",
    "total": 1
  },
  "type": "progress"
}
```

JSON Lines success:

```json
{ "ok": true, "result": {}, "type": "result" }
```

JSON Lines failure:

```json
{ "error": { "code": "...", "message": "..." }, "ok": false, "type": "error" }
```

Progress objects include every `ProgressEvent` field. Unused fields are `null`.
`--json` suppresses progress. `--json` and `--jsonl` are mutually exclusive.
Machine modes write one compact sorted-key object per line to standard output.

## Exit codes

|  Exit | Meaning                                   |
| ----: | ----------------------------------------- |
|   `0` | Success                                   |
|   `2` | Arguments or local value shape            |
|   `3` | Environment, transport, or live session   |
|   `4` | Export planning or Marimo compatibility   |
|   `5` | State execution or output materialization |
|   `6` | Export or asset integrity                 |
|   `7` | Filesystem or export repository           |
| `130` | Interrupted                               |
| `141` | Closed output pipe                        |

[Build or capture](../guide/build-and-capture.md) provides the complete producer
workflow.
