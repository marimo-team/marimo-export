---
title: Delivery and publications
description: Commit complete application directories, create prepared manifests, and retain changing prepared exports.
---

# Delivery and publications

`stage()` commits application files and one or more notebook exports as a single
directory. `PreparedPublicationController` keeps a last-good prepared export
available while application inputs or observations change.

Use directory delivery for a static build. Use the publication controller for a
long-running Python service that serves prepared manifests and assets.

## Commit a complete application directory

```python
from pathlib import Path

from marimo_export import ExportSpec, prepare
from marimo_export.delivery import stage

spec = ExportSpec.from_file("report.export.yaml")
Path("dist").mkdir(exist_ok=True)

with prepare("report.py", spec=spec) as prepared:
    with stage("dist/site", replace=True) as staged:
        staged.path.joinpath("index.html").write_text(
            "<main id='app'></main>",
            encoding="utf-8",
        )
        staged.materialize(prepared, "data/report")
        delivered = staged.commit()

print(delivered.path, delivered.files)
```

The destination parent must exist. The staging directory is a sibling of the
destination, which keeps the final commit on one filesystem.

### `stage()`

```python
stage(
    destination: str | os.PathLike[str],
    *,
    replace: bool = False,
) -> StagedDelivery
```

`stage()` preflights the destination, records its identity, and creates one
writable sibling staging directory. An existing destination raises
`NotebookExportError` with code `destination_exists` unless `replace=True`.
Replacement installs the staged application as the complete destination and
removes files that exist only in the old directory.

Use `StagedDelivery` as a context manager. Exiting before a successful commit
attempts best-effort removal of the staging directory. `close()` is idempotent
and applies the same cleanup.

### `StagedDelivery`

```python
staged.path: Path
staged.materialize(
    prepared: PreparedExport,
    at: str | os.PathLike[str],
) -> ExportResult
staged.commit(*, guard: Callable[[], None] | None = None) -> DeliveryResult
staged.close() -> None
```

`path` is the writable application root. Write application-owned files there
before `commit()`.

`materialize()` writes and verifies one prepared export under the portable
relative directory `at`. The path cannot be absolute, empty, `.` or `..`, and
cannot overlap another materialized export root. Its target inside `staged.path`
must not already exist. The method returns the nested export's `ExportResult`.

`commit()` performs these observable steps:

1. Reverify every materialized export and its recorded directory identity.
2. Reject symbolic links and special files anywhere in the outer tree.
3. Count regular files.
4. Run `guard()` when supplied.
5. Install the complete application directory with rollback protection.
6. Synchronize the destination parent and remove the retired directory.

The guard runs after verification and before the directory becomes visible. It
must leave the staging tree unchanged. A
guard exception preserves the previous destination and leaves the staged
context available for cleanup.

Target creation or metadata drift after preflight raises a `destination_*`
error. A failed directory transaction preserves or restores the previous
destination when possible.

### `DeliveryResult`

```python
delivered.path: Path
delivered.files: int
delivered.warnings: tuple[ExportWarning, ...]
```

`path` is absolute. `files` counts verified regular files in the committed
outer tree.

Commit can succeed with either post-commit warning:

| Code                                 | Consequence                                                                  |
| ------------------------------------ | ---------------------------------------------------------------------------- |
| `export_parent_sync_failed`          | The delivery is visible, but its parent directory entry was not synchronized |
| `retired_destination_cleanup_failed` | The new delivery is visible and the previous directory remains beside it     |

Warnings describe work after the commit point. Do not treat them as a failed
delivery.

## Create and serialize a prepared manifest

A prepared manifest tells a browser which immutable export generation and state
to open.

```python
manifest = prepared.manifest(
    "/runtime/report/exports/INSTANCE/",
    state="baseline",
    refresh_interval_ms=1_000,
)
```

```python
prepared.manifest(
    export_url: str,
    *,
    state: str | Mapping[str, object] | None = None,
    refresh_interval_ms: int | None = None,
) -> dict[str, object]
```

The returned `marimo-export.prepared.v1` object contains:

```text
schema
instance
export_url
inputs
state_fingerprint
refresh_interval_ms  when configured
```

`state` accepts an authored alias, a complete input mapping, or `None` for the
export default. `export_url` must be a nonempty string of at most 8192 UTF-8
bytes. `refresh_interval_ms` accepts `0` or an integer from 250 through 60,000.
Zero disables polling while preserving an explicit refresh policy in the
manifest.

Serialize the response through the bounded public helper:

```python
from marimo_export.manifest import prepared_manifest_bytes

body = prepared_manifest_bytes(manifest)
```

`prepared_manifest_bytes(value)` returns canonical portable JSON up to
`MAX_PREPARED_MANIFEST_BYTES`, which is 256 KiB. Larger values raise
`PreparedManifestLimitError` with code `prepared_manifest_limit_exceeded` and
encoded `size_bytes` and `max_bytes` details.

## Retain a changing prepared publication

`PreparedPublicationController` associates an application-defined key with one
last-good `PreparedExport`. It runs synchronous preparation callbacks in worker
threads and commits replacements on the caller's event loop.

```python
import asyncio

from marimo_export import ExportSpec, OutputSpec, prepare
from marimo_export.publication import (
    PreparedPublicationCandidate,
    PreparedPublicationController,
)

spec = ExportSpec(
    default_state="baseline",
    states={"baseline": {}},
    outputs={"summary": OutputSpec.json("report.summary")},
)


def prepare_report(repository, cancelled):
    prepared = prepare(
        "report.py",
        spec=spec,
        repository=repository,
        cancelled=cancelled,
    )
    return PreparedPublicationCandidate(
        prepared=prepared,
        metadata={"title": "Report"},
    )


async def main() -> None:
    controller = PreparedPublicationController[str, dict[str, str]]()
    try:
        publication = await controller.prepare("report", prepare_report)
        manifest = publication.manifest(f"/runtime/report/{publication.identity}/")
        print(manifest["instance"])
    finally:
        await controller.close()


asyncio.run(main())
```

The callback signature is:

```python
Callable[
    [ExportRepository, Callable[[], bool]],
    PreparedPublicationCandidate[MetadataT],
]
```

`PreparePublication[MetadataT]` is the public type alias for this callback.

The callback receives the controller's repository and a cancellation predicate.
It must return `PreparedPublicationCandidate(prepared=..., metadata=...)`.
Closing the returned prepared handle remains the controller's responsibility
after the candidate is accepted.

Cancelling the task awaiting `prepare()` sets the callback's cancellation
predicate, waits for the worker-thread call to settle, and raises
`asyncio.CancelledError` to the caller.

### `PreparedPublicationController`

```python
PreparedPublicationController(
    *,
    repository: ExportRepository | None = None,
    supersession_key: Callable[[KeyT], Hashable] | None = None,
    route_key: Callable[[KeyT], Hashable] | None = None,
    route_grace_seconds: float = 60.0,
)
```

The controller opens the default repository lazily when `repository` is absent
and closes it during `close()`. A supplied repository stays caller-owned.

`supersession_key` groups keys whose preparation and current publication
replace one another. The default returns the complete key. Starting new work for
a group sets the cancellation predicate for older work in that group. The most
recent successful candidate becomes current. A failed candidate leaves the
previous current publication available.

`route_key` groups generations that share an asset route. The default returns
the complete key. A replaced generation remains eligible for `asset()` for
`route_grace_seconds`, which defaults to 60 seconds. This lets in-flight
manifest requests finish after a replacement commits.

Use a finite, nonnegative `route_grace_seconds`. A value of zero closes a
replaced publication during the replacement commit.

Methods and properties:

```python
controller.active: bool
controller.keys: tuple[KeyT, ...]
await controller.prepare(key, prepare) -> PreparedPublication
controller.current(key) -> PreparedPublication | None
controller.poll(key) -> PreparedPublication | None
controller.asset(route, instance, relative) -> PreparedAsset | None
controller.release(key) -> None
await controller.close() -> None
```

`active` reports whether repository, preparation, current publication, or route
grace state is retained. `keys` includes current, preparing, and retained keys.

`current()` returns the exact current publication after expiring elapsed route
grace entries. `poll()` returns the same current publication immediately and
schedules one asynchronous revision check when no refresh is active. That check
uses the last successful preparation callback only when the repository
observation revision has advanced. Refresh failure preserves the current
publication.

The controller and every call to `poll()` belong to one running `asyncio` event
loop. Call `poll()` from an asynchronous handler on that loop. The preparation
callback itself runs in a worker thread.

`asset()` matches the application route, notebook export identity, and
declared relative export file. It searches current and retained generations and
returns an independently leased `PreparedAsset`. It returns `None` when no live
generation matches. Close a returned asset after its response finishes.

`release(key)` cancels pending work and closes current and retained publications
in the key's supersession group. `close()` is asynchronous and idempotent. It
cancels preparation and refresh work, waits for tasks to settle, closes every
publication, and closes an owned repository.

### `PreparedPublication`

`controller.prepare()` and lookup methods return controller-owned
`PreparedPublication` values. Callers cannot construct or close them directly.

```python
publication.key: KeyT
publication.metadata: MetadataT
publication.identity: str
publication.plan: ExportPlan
publication.manifest(
    export_url: str,
    *,
    state: str | Mapping[str, object] | None = None,
    refresh_interval_ms: int | None = None,
) -> dict[str, object]
```

The controller retains the underlying `PreparedExport` until replacement,
release, route-grace expiry, or controller close.

Use the [browser prepared-publication API](../browser/prepared-publications)
to consume the manifest and coordinate state transitions in the page.
