---
title: Manage the export repository
description: Inspect reusable artifacts, apply retention, and manage observed notebook inputs.
---

# Manage the export repository

The export repository stores reusable prepared states, immutable export
generations, observations, leases, and retention metadata for a Python producer.
Deploy the notebook export written to `dist/` or another destination. Do not
serve the repository tree, which also contains old generations, observation
history, staging data, and private SQLite coordination records.

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

`default_path()` reports the effective default without creating it. Opening a
repository attempts maintenance recovery. When another process holds the
maintenance transaction lock, opening continues without that pass. Every command
that opens the repository, including status and dry-run commands, can create the
directory, tighten its permissions, replace a corrupt or incompatible catalog,
or retire an invalid repository artifact.

### Understand catalog replacement

::: danger Catalog replacement resets reusable repository data
When opening confirms catalog corruption or an incompatible schema, it renames
the catalog files, opens a fresh catalog, then retires the prepared-state,
export-generation, and staging directories that the new catalog does not index.
Normal retirement cleanup removes the renamed catalog snapshot. The renamed
files are a transactional recovery step, not a backup. Preserve written notebook
exports separately and rebuild reusable repository data from the notebook and
`ExportSpec` after this reset.
:::

## Inspect storage

Show repository use before pruning or changing limits:

```bash
uv run marimo-export repository status
uv run marimo-export repository status --json
```

The result contains:

| Field             | Meaning                                                       |
| ----------------- | ------------------------------------------------------------- |
| `path`            | Absolute repository root                                      |
| `producers`       | Producer identities with retained repository history          |
| `observations`    | Retained distinct observed input vectors                      |
| `prepared_states` | Reusable state artifacts                                      |
| `identities`      | Repository identities used for exact prepared-export lookup   |
| `generations`     | Retained export generations                                   |
| `content_bytes`   | Accounted bytes in states, generations, and retired artifacts |
| `active_leases`   | Active durable owner-artifact lease rows                      |

Status removes expired leases before counting. `content_bytes` excludes catalog
files, observation rows, staging directories, and unindexed files.
`active_leases` excludes staging leases and preparation reservations. Status
does not count live Python handles because several handles can share one durable
lease row. Status also does not open or verify every artifact. Artifact lookup
validates the selected files and retires a confirmed integrity failure.

## Preview and apply retention

Preview the prepared states and export generations that exceed the active
retention policy:

```bash
uv run marimo-export repository prune --dry-run
```

The result reports removable prepared-state count, generation count, and bytes.
It does not report producer histories that may leave retention.

::: warning Prune can remove observation history
A live prune can remove producer records and cascade into their observations.
The dry-run result does not include those observation deletions. Export
observation data before a live prune when that authoring history must be kept.
:::

Apply retention after reviewing both the artifact preview and the observation
history:

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

marimo-export applies retention to current repository contents before admitting
a newly prepared state or generation. Final admission then adds the candidate's
content and metadata to the totals. That final check can raise
`repository_limit_exceeded` even when another unleased artifact could be evicted,
because the admission pass does not perform a second candidate-sized eviction.

Close unused `PreparedExport` and `PreparedAsset` handles, prune with the limits
you intend to enforce, then retry preparation. One `PreparedAsset` protects the
complete export generation that contains its file. If the candidate itself
exceeds `prepared_state_bytes` or `generation_bytes`, reduce the selected output
data or open the repository with a larger trusted limit.

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

The policy belongs to this handle and is not stored in the repository. Opening
the same path later with different limits applies the later handle's policy.
CLI maintenance commands open with `RepositoryLimits()` defaults.

Defaults:

| Limit                               |    Default | Effect                                                                                 |
| ----------------------------------- | ---------: | -------------------------------------------------------------------------------------- |
| `observation_bytes`                 |      1 MiB | Maximum canonical size of one observed vector                                          |
| `observations_per_producer`         |        256 | Retained rows per producer in each observation table                                   |
| `observation_relation_bytes`        |     16 MiB | Retained bytes per producer in each observation table, across its input-name relations |
| `retained_producers`                |         32 | Producer histories retained after their artifacts leave retention                      |
| `retained_identities`               |        128 | Exact export identities retained                                                       |
| `retained_generations_per_identity` |          4 | Generations retained for one exact identity                                            |
| `retained_generations`              |        128 | Generations retained across the repository                                             |
| `retained_prepared_states`          |      4,096 | Prepared states retained across the repository                                         |
| `metadata_bytes`                    |     16 MiB | Metadata retained across prepared states and generations                               |
| `prepared_state_bytes`              |    512 MiB | Maximum for one prepared state and its aggregate retained content                      |
| `generation_bytes`                  |      1 GiB | Maximum for one generation and its aggregate retained content                          |
| `repository_bytes`                  |      2 GiB | State, generation, and retired content across the repository                           |
| `lease_ttl_seconds`                 | 30 seconds | Lifetime of a lease without renewal                                                    |
| `lease_heartbeat_seconds`           |  5 seconds | Renewal interval for active leases                                                     |

All count and byte limits are positive integers. Lease durations are positive
finite numbers, and the heartbeat must be shorter than the lease lifetime.
`repository_bytes` is the accounted admission budget. Filesystem staging and a
replacement can temporarily require more disk space than that value while both
old and new directories exist.

## List observed input vectors

An observation is a portable input vector that is complete for the input-name
relation recorded during a successful normal run of a matching saved notebook.
List the observations relevant to an `ExportSpec`:

```bash
uv run marimo-export observations list examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml
```

Add `--json` to receive the producer identity, inferred input names, monotonic
observation revision, and each projected vector with its fingerprint and
revision.

The command resolves an `ExportPlan` first. It can execute the notebook's initial
autorun when no exact prepared export supplies the plan. The repository can hold
broader observations from the same producer. The command projects compatible
vectors to the plan's inferred input names and reports the snapshot revision.

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

The [CLI reference](../reference/cli) defines machine output and exit codes.
The [repository and observations reference](../reference/python/repository-and-observations)
defines the Python records and methods.
