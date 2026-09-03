# Application publication and delivery

Applications publish immutable `PreparedExport` generations through a mutable
manifest route, then assemble those exports with application files in one staged
directory. Publication owns which generation is current. Delivery owns the
filesystem transaction that makes a complete application directory visible.

```text
PreparedExport
  -> PreparedPublicationController
  -> mutable current manifest + immutable instance routes
  -> StagedDelivery
  -> committed application directory
```

## Terms and ownership

| Term               | Contract                                                                                         | Owner                           |
| ------------------ | ------------------------------------------------------------------------------------------------ | ------------------------------- |
| Prepared export    | Leased immutable notebook export generation                                                      | `PreparedExport`                |
| Prepared manifest  | Selected state and immutable export location for a browser                                       | `marimo-export.prepared.v1`     |
| Publication key    | Application-defined identity for one requested publication                                       | Application                     |
| Supersession group | Application-defined set in which newer preparation cancels older work and replaces current state | `PreparedPublicationController` |
| Route group        | Application-defined namespace used to find immutable instance assets                             | `PreparedPublicationController` |
| Route grace        | Bounded retention of a replaced prepared export for in-flight or delayed asset requests          | `PreparedPublicationController` |
| Staged delivery    | Owned sibling directory containing application files and materialized notebook exports           | `StagedDelivery`                |
| Directory target   | Destination path plus the preflight identity of an existing directory                            | `_directory_target.py`          |

Publication is an application lifecycle over repository leases. Delivery is a
filesystem transaction. Neither changes the immutable notebook export format.

## Prepared manifest contract

`PreparedExport.manifest()` rechecks the prepared export index, opens the local
reader, selects one state, and returns:

```json
{
  "schema": "marimo-export.prepared.v1",
  "instance": "<export identity>",
  "export_url": "./<instance>/",
  "inputs": {},
  "state_fingerprint": "<state fingerprint>",
  "refresh_interval_ms": 1000
}
```

`state` accepts an alias, a complete input mapping, or `None`. `None` selects the
export's explicit default state. The producer resolves the selected mapping
against the immutable export before constructing the manifest.

| Field                 | Contract                                                   |
| --------------------- | ---------------------------------------------------------- |
| `schema`              | Exact `marimo-export.prepared.v1` identifier               |
| `instance`            | Lowercase SHA-256 of the canonical export index            |
| `export_url`          | Nonempty URL of at most 8192 UTF-8 bytes                   |
| `inputs`              | Complete portable JSON input object for the selected state |
| `state_fingerprint`   | Lowercase SHA-256 matching `inputs`                        |
| `refresh_interval_ms` | Optional `0`, or an integer from 250 through 60,000        |

`prepared_manifest_bytes()` serializes an application manifest as canonical
portable JSON and enforces the browser's 256 KiB limit. Applications may wrap
the core manifest with presentation metadata. The nested core record remains the
marimo-export contract.

The browser parser rejects unknown fields, invalid portable JSON, malformed
digests, invalid intervals, and oversized URLs. Opening a publication also
requires the fetched export identity and normalized base URL to match the
manifest. The complete inputs must resolve to the declared state fingerprint.

`PreparedPublicationRefresh` requests the mutable current manifest with fetch
cache mode `no-store`. Immutable instance routes can use content-addressed
caching. Read [Browser loaders and mounts](browser-loaders-and-mounts.md) for
browser-side refresh, state transition, and mount disposal.

## Publication controller

`PreparedPublicationController[KeyT, MetadataT]` retains the last-good
`PreparedExport` for application-defined keys. Its constructor accepts:

| Argument              | Default   | Contract                                                                                               |
| --------------------- | --------- | ------------------------------------------------------------------------------------------------------ |
| `repository`          | `None`    | Supplied repositories remain caller-owned. `None` creates one lazily and closes it with the controller |
| `supersession_key`    | Exact key | Maps a publication key to its cancellation and replacement group                                       |
| `route_key`           | Exact key | Maps a publication key to the namespace used by immutable asset routes                                 |
| `route_grace_seconds` | `60.0`    | Nonnegative retention duration for replaced routes                                                     |

`prepare(key, callback)` executes the synchronous callback in a worker thread.
The callback receives the selected `ExportRepository` and a cancellation
predicate, then returns `PreparedPublicationCandidate(prepared, metadata)`.

Preparation follows this order:

1. Validate that the publication key, supersession group, and route group are
   hashable.
2. Signal cancellation to pending work in the same supersession group.
3. Record a monotonically increasing desired-work token for that group.
4. Run the callback in a worker thread.
5. Wrap the returned prepared export and metadata as `PreparedPublication`.
6. Recheck controller state, callback cancellation, and the desired-work token.
7. Close a stale candidate, or commit the candidate as current.

Cancelling the coroutine sets the callback's cancellation signal and waits for
the worker task to settle before returning `CancelledError`. A callback must
cooperate with the predicate to stop expensive preparation promptly. A failed or
cancelled replacement leaves the previous current publication unchanged.

The controller exposes application metadata unchanged. It does not interpret
view bindings, presentation revisions, route documents, or renderer state.
`PreparedPublication` exposes the application key and metadata together with the
prepared export identity, `ExportPlan`, and manifest constructor. The controller
retains and closes the underlying `PreparedExport`.

## Supersession and route grace

Supersession and routing answer different questions:

- The supersession group chooses which preparation request is newest and which
  current publication it replaces.
- The route group chooses which current or retired publication may satisfy an
  immutable asset request.

When a new publication commits, the controller removes every current publication
in the same supersession group. A previous publication closes immediately when
its instance identity and route group both match the replacement. Otherwise it
enters route grace until `monotonic() + route_grace_seconds`.

The controller schedules the earliest retirement deadline on the running event
loop. Expired publications close even when no later request calls `current()`,
`poll()`, or `asset()`. A zero grace duration closes the replaced publication
during the replacement commit.

`asset(route, instance, relative)` searches current and route-grace publications
for the exact route group and immutable export identity. It returns `None` when
no retained publication can provide the declared file. A successful lookup
returns a `PreparedAsset` with an independently detached repository lease.

`PreparedAsset` verifies the declared member when borrowed and again when its
path or bytes are read. Closing or releasing the controller publication does not
invalidate an already detached response asset. The HTTP response owner closes
that asset after sending the response.

The route layout therefore has one mutable pointer and immutable instance paths:

```text
.../current
.../<instance>/index.json
.../<instance>/assets/<content-addressed-file>
```

The current route may advance before every client has observed it. Route grace
keeps the older instance path live during that interval.

## Polling, release, and close

`current(key)` returns the exact current publication after pruning expired route
grace. `poll(key)` returns that same publication immediately and schedules at
most one refresh task for the key when its supersession group has no preparation
in progress.

`active` is true while the controller retains a repository, current publication,
retired publication, or preparation task. `keys` returns each current,
route-grace, and preparing application key once, preserving its first occurrence
across those groups.

The refresh task compares the repository observation revision with the revision
captured in the publication plan. A newer revision calls the original prepare
callback again. Refresh cancellation and ordinary refresh errors preserve the
last-good publication and remain background outcomes.

`release(key)` acts on the key's complete supersession group. It signals pending
work, closes current and retired publications in that group, clears the desired
token, and reschedules retirement for remaining groups. Detached
`PreparedAsset` handles keep their own leases.

`close()` is asynchronous and idempotent. It cancels the retirement timer,
signals all preparation work, waits for preparation and refresh tasks, closes
every current and retired publication, then closes the lazily owned repository.
A supplied repository remains open. The first publication or owned-repository
close failure is raised after the controller has attempted the remaining closes.

## Staged application delivery

`marimo_export.delivery.stage(destination, replace=...)` preflights one
destination and creates an owned sibling staging directory. The application
writes its HTML, CSS, JavaScript, and other regular files through `staged.path`.
`staged.materialize(prepared, at)` writes a verified notebook export at one
portable relative directory inside that staging tree.

The destination parent must exist, be writable and searchable, and be a real
directory. The destination basename must identify a directory. An existing
destination must be a real directory, and `replace=True` is required to replace
it. Preflight resolves the real parent and records the destination identity
before the application starts staging work.

A materialization path:

- uses forward-slash portable components
- remains relative and nonempty after `PurePosixPath` normalization
- contains no parent segments, backslashes, or nonportable basenames
- may not equal, contain, or be contained by another materialization root

Every created materialization parent must remain a real directory. A symbolic
link or non-directory parent fails before the nested export is written.

`materialize()` delegates export creation to the same writer and reader used by
`PreparedExport.write()`. It records the export identity and a filesystem
identity for the resulting nested tree. `commit()` opens and verifies each
nested export again, then compares both identities. A changed nested export
fails before the outer application directory becomes visible. `materialize()`
returns the nested export's `ExportResult`. `commit()` returns a `DeliveryResult`
with the absolute destination path, regular-file count, and post-visibility
warnings.

## Directory identity and security

An existing destination's `DirectoryIdentity` records:

| Scope         | Recorded facts                                                                                                              |
| ------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Root inode    | Device, inode, mode, user ID, group ID                                                                                      |
| Root revision | Size, modification time, birth time when available, Windows file attributes, Windows reparse tag                            |
| Root security | BSD flags, extended-attribute names and value digests, POSIX access-control list digest, Windows security descriptor digest |
| Descendants   | Relative path, mode, modification time, change time, size, inode                                                            |

Extended attributes are read without following symbolic links. Unsupported
extended-attribute or access-control list facilities contribute an empty or
unavailable value. An operational inspection failure remains a commit failure
and is not classified as destination drift.

On macOS, root access-control list and extended-attribute bytes come from native
system calls. On Linux, the root access-control list comes from `libacl` when it
is available. On Windows, the root owner, group, and discretionary
access-control list contribute to the security descriptor digest.

Before installation, `commit()` walks the outer staging tree with `lstat`. Every
member must be a regular file or real directory. Symbolic links and special files
fail the delivery. The returned file count is the number of regular files seen
during this walk.

## Commit guard and transaction order

`StagedDelivery.commit(guard=...)` performs:

1. Nested export verification and identity comparison.
2. Outer-tree type validation and regular-file counting.
3. The optional guard callback.
4. Destination installation with change detection and rollback.
5. Parent-directory synchronization.
6. Replaced-directory cleanup.

The guard runs after verification and before the first destination rename. Use
it for cancellation, source revision, or other external precondition checks.
The guard should leave the staging tree unchanged because verification and file
counting have already completed.

An exception before successful installation leaves the `StagedDelivery` open so
its context exit or `close()` can remove staging. A successful installation marks
the handle closed and detaches its finalizer before synchronization and retired
directory cleanup.

## New destinations and replacement races

For a destination absent at preflight, the transaction first creates an empty
directory at the target name. `FileExistsError` proves another owner created the
destination and returns `destination_changed`. On POSIX, replacing that empty
sentinel with staging is one rename. On Windows, the empty sentinel is removed
before the staged directory is moved into place.

For an existing destination, the transaction compares the exact preflight
identity only after moving or exchanging that directory. A changed mode,
revision, security identity, or descendant record returns
`destination_changed` and restores the destination that was observed at commit
time. This prevents a staged operation from overwriting a concurrent owner's
replacement.

## Native exchange and rollback replacement

macOS and Linux first attempt a native directory exchange:

```text
staging <atomic exchange> destination
```

The exchange keeps the destination path continuously present. The previous
destination moves to the staging path, where its identity is compared with the
preflight target. A mismatch exchanges the directories back. If native recovery
cannot complete, the transaction preserves the previous or interrupted tree at
a named sibling recovery path and reports that path.

Platforms and filesystems without native exchange use rollback replacement:

```text
destination -> recovery sibling
verify recovery identity
staging -> destination
retain or remove recovery sibling
```

This path preserves failure rollback but has a rename interval in which the
destination name is absent. If installation fails, it restores the previous
directory. If restoration also fails, the error identifies the sibling that
retains the previous directory. The native exchange path is the continuous
availability contract. The fallback path is the recoverable replacement
contract.

## Visibility and warnings

Both the export writer and `StagedDelivery` retain the replaced directory until
the new destination is installed. After installation, they:

1. synchronize the destination's parent directory on POSIX
2. remove the replaced directory

These operations happen after the new directory is visible. Their failures are
typed warnings in the result:

| Warning                              | Visible state                                                                                   |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `export_parent_sync_failed`          | The new destination is visible, but its parent directory entry was not synchronized             |
| `retired_destination_cleanup_failed` | The new destination is visible, and the previous directory remains at the reported sibling path |

`PreparedExport.write()` returns these warnings through `ExportResult`.
`StagedDelivery.commit()` returns them through `DeliveryResult`. Callers should
report the warning and preserve its path for operator cleanup.

## Failure precedence and recovery

Directory transaction failures preserve the primary exception while attempting
rollback and interrupted-tree cleanup. A cancellation raised after installing a
candidate but before returning still restores the previous destination when the
rollback path can do so. Cleanup failure types attach to a primary cancellation
through bounded diagnostics.

When exchange recovery cannot restore the original name, or rollback restoration
cannot complete, the error reports the path that retains the previous or
interrupted tree. Operators should inspect those explicit sibling paths before
retrying or removing them.

Closing an uncommitted staged delivery removes its sibling staging directory on
a best-effort basis. Context exit and the finalizer use the same discard path,
which suppresses removal errors. Callers still close deterministically so the
staging lifetime ends at the application boundary.

## Validation

Run the focused publication and delivery suites:

```bash
uv run pytest -q \
  packages/python/tests/test_manifest.py \
  packages/python/tests/test_publication.py \
  packages/python/tests/test_delivery.py \
  packages/python/tests/test_directory_security.py
```

Run repository lifecycle and prepared-export tests when changing detached asset
leases. Exercise a native exchange on a supporting filesystem and keep the
Windows CI matrix as the authority for Job Object, file-handle, and replacement
behavior.
