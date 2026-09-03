---
title: Manage the export repository
description: Inspect reusable artifacts, apply retention, and manage observed notebook inputs.
---

# Manage the export repository

The export repository stores reusable prepared states, immutable export
generations, and observed input vectors for producers. It is private producer
storage. A notebook export written to `dist/` is a separate portable directory
and remains available when repository retention removes its reusable source.

## Select one repository

Use the same repository for planning, building, capturing, and maintenance.
Command-line selection follows this precedence:

1. `--repository DIR`
2. `MARIMO_EXPORT_REPOSITORY`
3. the operating system cache directory

Set a shared repository for several commands:

```bash
export MARIMO_EXPORT_REPOSITORY="$HOME/.cache/marimo-export/repository"

uv run marimo-export plan examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml
mkdir -p dist
uv run marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart
```

The platform defaults are:

| Platform                      | Default path                                                                                                          |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| macOS                         | `~/Library/Caches/marimo-export/repository`                                                                           |
| Windows                       | `%LOCALAPPDATA%\marimo-export\repository`, or `~/AppData/Local/marimo-export/repository` when `LOCALAPPDATA` is unset |
| Linux and other POSIX systems | `$XDG_CACHE_HOME/marimo-export/repository`, or `~/.cache/marimo-export/repository`                                    |

`ExportRepository.open(path)` uses the supplied path. Without `path`, it uses
the environment variable and platform default in the same order. The repository
path must be a real directory rather than a symbolic link. On POSIX,
marimo-export requires the current user to own it and sets user-only access.

```python
from marimo_export import ExportRepository

print(ExportRepository.default_path())

with ExportRepository.open(".exports") as repository:
    print(repository.status().to_dict())
```

`default_path()` reports the effective default without creating it.

## Inspect storage

Show repository use before pruning or changing limits:

```bash
uv run marimo-export repository status
uv run marimo-export repository status --json
```

The result contains:

| Field             | Meaning                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `path`            | Absolute repository root                                             |
| `producers`       | Producer identities with retained repository history                 |
| `observations`    | Retained distinct observed input vectors                             |
| `prepared_states` | Reusable state artifacts                                             |
| `identities`      | Exact producer, output-plan, and ExportSpec identities               |
| `generations`     | Retained immutable prepared exports                                  |
| `content_bytes`   | Accounted bytes in states, generations, and retired artifacts        |
| `active_leases`   | Artifact leases held by states, prepared exports, or detached assets |

Status removes expired leases before counting. `content_bytes` excludes catalog
files, observation rows, staging directories, and unindexed files.
`active_leases` excludes staging leases and preparation reservations. Status
does not open or verify every artifact. Artifact lookup validates the selected
files and retires a confirmed integrity failure.

## Preview and apply retention

Preview the artifacts that exceed the active retention policy:

```bash
uv run marimo-export repository prune --dry-run
```

The result reports removable prepared-state count, generation count, and bytes.
Apply the same policy after reviewing the preview:

```bash
uv run marimo-export repository prune
```

Pruning removes repository artifacts that fall outside retention. Active leases
protect their state or generation. Retained generations also keep the prepared
states they reference. The command does not change a notebook export already
written outside the repository.

Retention pins the current generation for each retained identity. An unleased
identity that falls beyond `retained_identities` can lose its current generation.
The next producer run can prepare it again.

marimo-export also applies retention before admitting a newly prepared state or
generation. If protected and retained content still exceeds a byte limit, the
write fails with `repository_limit_exceeded`. Close unused `PreparedExport` and
`PreparedAsset` handles before pruning when those artifacts no longer need
protection.

## Configure retention limits from Python

`RepositoryLimits` supplies the retention and byte policy used by one opened
repository:

```python
from marimo_export import ExportRepository
from marimo_export.repository import RepositoryLimits

limits = RepositoryLimits(
    retained_generations_per_identity=2,
    retained_generations=64,
    retained_prepared_states=1_024,
    repository_bytes=1 * 1024 * 1024 * 1024,
)

with ExportRepository.open(".exports", limits=limits) as repository:
    preview = repository.prune(dry_run=True)
    print(preview.to_dict())
```

Defaults:

| Limit                               |    Default | Effect                                                            |
| ----------------------------------- | ---------: | ----------------------------------------------------------------- |
| `observation_bytes`                 |      1 MiB | Maximum canonical size of one observed vector                     |
| `observations_per_producer`         |        256 | Retained observation rows for one producer                        |
| `observation_relation_bytes`        |     16 MiB | Retained observation bytes for one producer relation              |
| `retained_producers`                |         32 | Producer histories retained after their artifacts leave retention |
| `retained_identities`               |        128 | Exact export identities retained                                  |
| `retained_generations_per_identity` |          4 | Generations retained for one exact identity                       |
| `retained_generations`              |        128 | Generations retained across the repository                        |
| `retained_prepared_states`          |      4,096 | Prepared states retained across the repository                    |
| `metadata_bytes`                    |     16 MiB | Metadata retained across prepared states and generations          |
| `prepared_state_bytes`              |    512 MiB | Prepared-state content retained across the repository             |
| `generation_bytes`                  |      1 GiB | Generation content retained across the repository                 |
| `repository_bytes`                  |      2 GiB | State, generation, and retired content across the repository      |
| `lease_ttl_seconds`                 | 30 seconds | Lifetime of a lease without renewal                               |
| `lease_heartbeat_seconds`           |  5 seconds | Renewal interval for active leases                                |

All count and byte limits are positive integers. Lease durations are positive
finite numbers, and the heartbeat must be shorter than the lease lifetime.

## List observed input vectors

An observation is a complete portable input vector recorded from a successful
normal run of a matching saved notebook. List the observations relevant to an
ExportSpec:

```bash
uv run marimo-export observations list examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml
```

Add `--json` to receive the producer identity, inferred input names, monotonic
observation revision, and each projected vector with its fingerprint and
revision.

The command resolves an `ExportPlan` first. It can execute the notebook's initial
autorun when no exact prepared export supplies the plan. The returned vectors
are projected to that plan's input names, while repository storage remains keyed
by the complete producer identity.

Observations support authoring. They do not become published states until an
application or author places selected values in an explicit `ExportSpec`.

## Clear observations for a producer

::: warning Producer-wide deletion
`observations clear` deletes all retained observation vectors and event history
for the resolved producer. The deletion covers input shapes beyond the current
ExportSpec and runs immediately without a confirmation prompt or dry-run mode.
List and save the current JSON result before clearing when the history is needed.
:::

```bash
uv run marimo-export observations list examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --json > observations.json

uv run marimo-export observations clear examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --json
```

The clear result reports the producer identity, the prior observation revision,
and the number of retained distinct vectors removed. Clearing observations
preserves prepared states, prepared exports, and notebook exports written to a
destination. It also preserves the producer's monotonic revision counter, so a
later observation continues from the prior revision.

Repopulate the history through either supported recording path:

- Run the notebook normally in a host that installed an `ObservationLedger`.
  Each successful run that matches the saved notebook can record its complete
  portable inputs. Interrupted, cancelled, failed, and scratch-cell runs are
  excluded.
- Call `repository.record_observation(plan, inputs)` with a complete mapping
  whose keys exactly match `plan.inputs`.

```python
from marimo_export import ExportRepository, ExportSpec, plan

spec = ExportSpec.from_file("examples/quickstart/report.export.yaml")

with ExportRepository.open() as repository:
    resolved = plan(
        "examples/quickstart/report.py",
        spec=spec,
        repository=repository,
    )
    repository.record_observation(resolved, {"days": 14})
```

Building or capturing explicit ExportSpec states does not promote those states
into observation history. Use normal-run recording or
`record_observation()` when repopulation is intended.

The [CLI reference](../reference/cli.md) defines machine output and exit codes.
The [repository and observations reference](../reference/python/repository-and-observations.md)
defines the Python records and methods.
