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
owned notebook process tree. `--replace` uses native directory exchange where
the filesystem supports it and guarded rollback replacement elsewhere. It
replaces the complete destination, including files that exist only in the old
directory.

## `capture`

```text
marimo-export capture SERVER --session ID --spec FILE --output DIR
                      [--repository DIR] [--replace]
                      [--timeout SECONDS] [--json | --jsonl]
```

Prepares one named live session through the export repository, writes the
verified export to `DIR`, and closes the preparation lease. The server and
session remain active. `--replace` uses the same guarded replacement contract as
`build`.

Live server authentication reads `MARIMO_EXPORT_ACCESS_TOKEN` and
`MARIMO_EXPORT_SERVER_TOKEN` from the environment.

Plain HTTP is accepted for loopback hosts. Remote servers require HTTPS. Server
URLs cannot contain user information, a query string, or a fragment, and the
client rejects redirects. `MARIMO_EXPORT_ACCESS_TOKEN` becomes an
`Authorization: Bearer` credential. `MARIMO_EXPORT_SERVER_TOKEN` becomes the
`Marimo-Server-Token` header. Explicit Python credentials take precedence over
these environment values.

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

Reads `index.json` and every declared asset. Human output reports exported
states, unique assets, and verified bytes. `--json` also reports the
state-output-pair count as `outputs`.

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

::: danger Producer-wide deletion
This command permanently removes the retained canonical vectors and event rows
for the plan's producer, including observations created through other specs for
that producer. It returns the number of canonical vectors removed. The monotonic
producer revision and prepared exports remain. Later successful notebook runs
can record new observations through an installed observation ledger.
:::

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
generations, and bytes. A live prune can also remove producer records and their
observation history, which the dry-run result does not report. Active leases
protect their artifacts.

Opening the repository attempts maintenance recovery before either mode. When
another process holds the maintenance transaction lock, opening continues without that
pass. Recovery can create the repository, adjust permissions, quarantine a
corrupt catalog, or retire an invalid artifact. The CLI uses the default
`RepositoryLimits` policy.

## `doctor`

```text
marimo-export doctor [--repository DIR] [--json]
```

Reports the effective repository, Python executable and version, marimo-export
version, and pinned marimo adapter compatibility. A failed compatibility check
returns exit code `4`.

## Common options

| Option              | Contract                                                                              |
| ------------------- | ------------------------------------------------------------------------------------- |
| `--spec FILE`       | Strict JSON or YAML ExportSpec                                                        |
| `--repository DIR`  | Export repository for one command                                                     |
| `--timeout SECONDS` | Positive finite startup, inactivity, or preparation-reservation timeout, default `30` |
| `--json`            | One terminal JSON success or failure object                                           |
| `--jsonl`           | Progress records followed by one terminal JSON Lines record                           |

Repository precedence is:

1. `--repository DIR`
2. `MARIMO_EXPORT_REPOSITORY`
3. the operating system cache directory

`marimo-export --version` prints the installed command version and exits.

## Output contracts

Human results use standard output. Human progress, warnings, and errors use
standard error.

JSON success wraps the command-specific result:

```json
{ "ok": true, "result": { "states": 2, "outputs": 2, "assets": 0, "bytes_verified": 0 } }
```

Most JSON failures use an error envelope:

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

`doctor --json` always returns its diagnostic record under `result`. A failed
compatibility check sets `ok` to `false`, returns exit code `4`, and keeps the
individual check result available for automation.

## Command result records

| Command              | Stable `result` content                                        |
| -------------------- | -------------------------------------------------------------- |
| `plan`               | Complete `ExportPlan` record                                   |
| `build`, `capture`   | Complete `ExportResult` record                                 |
| `inspect NOTEBOOK`   | `SessionDescription` record                                    |
| `inspect SERVER`     | `sessions` array with ID, filename, and path                   |
| `verify`             | State, output, asset, and verified-byte counts                 |
| `observations list`  | Producer, inputs, revision, and observed vectors               |
| `observations clear` | Producer, prior revision, and removed-vector count             |
| `repository status`  | Repository counts, accounted bytes, and active artifact leases |
| `repository prune`   | Retired state, generation, and byte counts                     |
| `doctor`             | Repository, Python, package, and marimo compatibility facts    |

Use the [Python records and errors](python/format-records-and-errors) reference
for planning, result, warning, and error field contracts.

## Exit codes

|  Exit | Meaning                                   |
| ----: | ----------------------------------------- |
|   `0` | Success                                   |
|   `1` | Unexpected internal failure               |
|   `2` | Arguments or local value shape            |
|   `3` | Environment, transport, or live session   |
|   `4` | Export planning or marimo compatibility   |
|   `5` | State execution or output materialization |
|   `6` | Export or asset integrity                 |
|   `7` | Filesystem or export repository           |
| `130` | Interrupted                               |
| `141` | Closed output pipe                        |

[Build or capture](../guide/build-and-capture) provides the complete producer
workflow.
