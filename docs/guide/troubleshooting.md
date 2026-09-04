---
title: Troubleshoot notebook exports
description: Diagnose producer, repository, integrity, browser, and mount failures from the smallest observable boundary.
---

# Troubleshoot notebook exports

Start with the command or consumer that failed. Preserve its stable error code,
details, and cause before changing files or clearing repository state.

| Symptom or code                                                                         | Start here                                                                |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `runtime_distribution_unavailable`                                                      | [Install the exporter package](#an-exporter-package-is-missing)           |
| `state_input_invalid`, `state_not_found`, `state_unavailable`                           | [Inspect state selection](#a-state-cannot-be-resolved)                    |
| `destination_exists`, `destination_changed`, `export_commit_failed`                     | [Inspect the destination](#the-destination-cannot-commit)                 |
| `repository_limit_exceeded`, `repository_reservation_timeout`, `repository_fence_stale` | [Inspect repository coordination](#the-repository-is-busy-or-unavailable) |
| `observation_rejected`, `observation_persistence_failed`                                | [Inspect observation recording](#an-observation-was-not-retained)         |
| `manifest_invalid`, `manifest_read_failed`, `query_ambiguous`, `query_miss`             | [Inspect the prepared publication](#a-prepared-publication-cannot-update) |
| `read_failed`, `export_noncanonical`, `integrity_failed`, `read_limit_exceeded`         | [Inspect browser requests](#the-browser-cannot-fetch-the-export)          |
| `export_parent_sync_failed`, `retired_destination_cleanup_failed`                       | [Inspect a post-commit warning](#a-commit-succeeded-with-a-warning)       |

## Check the local environment

Run:

```bash
uv run marimo-export doctor --json
```

The result reports the effective export repository, Python executable and
version, marimo-export version, and pinned marimo compatibility. A failed
compatibility check exits with code `4` and keeps diagnostic details in the
result.

## An exporter package is missing

Symptom: preparation reports `runtime_distribution_unavailable`.

Install the extra owned by that producer representation:

```bash
uv add "marimo-export[charts]"     # Altair and PNG
uv add "marimo-export[parquet]"   # Parquet
uv add "marimo-export[anywidget]" # AnyWidget
```

Run `uv run marimo-export plan` again, then rebuild the missing state.

## A state input is invalid or nonportable

Inspect the notebook boundary:

```bash
uv run marimo-export inspect report.py --json
```

Use the definition's reported `value`, `domain`, `input_mode`,
`portable_input`, and `sensitive` fields. A UI element's portable frontend value
can differ from the Python value returned by its `.value` property. Copy the
inspected shape into the state row.

Planning rejects sensitive inputs, binary AnyWidget state, non-finite portable
numbers, missing definitions, and ordinary assignments that compete with a
selected final named expression.

If preparation fails in a cell outside the selected output dependency closure,
inspect the full notebook run. Each state executes every available authored
cell before the producer operation completes.

## A state cannot be resolved

`state_not_found` means an authored state alias is absent.
`state_input_invalid` means a complete input mapping has the wrong keys or
shape. `state_unavailable` means the complete vector is valid but was not
exported.

List `notebookExport.states()` or inspect `ExportPlan.states`, then select an
available name or vector. Computing a new vector requires another preparation
run or a Python service.

## The destination cannot commit

Start with the destination path and the operation's error details.

- `destination_invalid` means the path, parent, file type, ownership, or link
  boundary failed before notebook execution. Choose a real directory entry under
  a writable parent.
- `destination_exists` means the command preserved the existing directory. Verify
  that it is the intended export target, then retry with `--replace`.
- `destination_changed` means another process changed the path after preflight.
  Inspect the new path and run the producer operation again from a fresh preflight.
- `export_commit_failed` means the staged export could not replace the destination.
  The error message identifies any previous or interrupted sibling directory that
  still needs operator review.

Verify whichever directory remains visible before using it:

```bash
uv run marimo-export verify dist/report --json
```

Do not move a recovery sibling over the destination until its own `index.json`
and declared asset closure pass verification.

## The repository is busy or unavailable

Inspect it before pruning:

```bash
uv run marimo-export repository status --json
uv run marimo-export repository prune --dry-run --json
```

`repository_busy` usually means another healthy process holds a filesystem
maintenance lock. `repository_reservation_timeout` means preparation could not
acquire its exact-identity reservation within the configured timeout. Retry
after the owning operation completes.

`repository_fence_stale` means a newer owner has commit authority. Discard the
stale result and run `plan` or `prepare` again. A lost artifact lease or confirmed
repository integrity error also requires reopening or preparing the export again.

The dry run covers prepared states, export generations, and bytes. A live prune can
also remove producer records and their observation history. Export that history
before pruning when it must be retained. Active artifact leases protect their
files. [Manage repository reuse](manage-repository) describes the retained
data and clear scope.

For `repository_limit_exceeded`, close unused prepared exports and prepared
assets, apply retention, then retry. One prepared asset handle protects its
complete export generation. Admission can still reject a candidate after the
retention pass, so reduce the selected output data or use larger trusted limits
when the candidate itself exceeds a category bound.

Opening the repository can replace a corrupt or incompatible catalog and retire
all reusable data that the fresh catalog no longer indexes. Read the
[catalog reset scope](manage-repository#understand-catalog-replacement) before treating a
renamed catalog file as a backup.

## An observation was not retained

`observation_rejected` means the candidate exceeded the configured observation
shape or size policy. `observation_persistence_failed` means the background
ledger could not store the accepted candidate.

Check the host log, repository path, permissions, and configured limits. Then
list the projected observations and revision:

```bash
uv run marimo-export observations list report.py \
  --spec report.export.yaml \
  --json
```

Observation rejection and queue pressure can advance the producer revision even
when the vector is not retained. Observations remain authoring evidence. Add a
state row to the `ExportSpec` when a consumer must receive a specific vector.

## Verification fails

Run:

```bash
uv run marimo-export verify dist/report --json
```

Do not edit `index.json` or files under `assets/`. Rebuild from the notebook and
ExportSpec when canonical JSON, size, digest, framing, or descriptor agreement
fails. Verification proves consistency with `index.json`, so obtain the export
again from a trusted publisher when its origin is uncertain.

## Live capture cannot connect

Check the server URL and credentials:

- plain HTTP is accepted for loopback hosts
- remote hosts require HTTPS
- the URL cannot contain credentials, a query, or a fragment
- `MARIMO_EXPORT_ACCESS_TOKEN` supplies the bearer credential
- `MARIMO_EXPORT_SERVER_TOKEN` supplies marimo's server-token header
- redirects are rejected

List sessions before capture:

```bash
uv run marimo-export inspect https://notebooks.example --json
```

A timeout means the client stopped waiting. The remote scratchpad operation may
still be running, so marimo-export does not retry it automatically.

`bridge_version_mismatch` means the client and selected kernel loaded different
marimo-export versions or source identities. Restart the server in the same
environment as the client. `implementation_changed` means local package source
changed during the operation. Restart the client process and repeat the check.

## The browser cannot fetch the export

Inspect the failed request for `index.json` first.

- `read_failed` with `404` means the export base path is wrong.
- A browser CORS error means the export origin does not allow the application
  origin.
- `export_noncanonical` means the server or a build step changed `index.json`.
- `integrity_failed` means an asset differs from its descriptor.
- `read_limit_exceeded` means the configured or default byte bound rejected the
  response.

Use a custom `fetch` implementation for supported credentials or request
policy. Keep the export URL free of user information and bearer secrets.

If the browser reports `integrity_failed` before a digest comparison, verify that
the page runs in a secure context with Web Crypto. Use HTTPS or a
browser-recognized loopback development origin. Raise a byte limit only after
the declared size and publisher are trusted and the application has enough
memory.

## A prepared publication cannot update

Inspect the manifest request and document before changing controller state:

- `manifest_read_failed` covers the request, response status, stream, or manifest
  byte limit. Check the URL, authentication, Cross-Origin Resource Sharing
  headers, and response size.
- `manifest_invalid` means the manifest fields do not agree with one another or
  with the opened immutable export. Republish the manifest and matching export.
- `query_ambiguous` means one query value matches more than one typed exported
  value. Route the control through an unambiguous value or narrow the exported
  domain.
- `query_miss` means a recognized parameter was repeated or matched no exported
  state. Restore a value present in the current export.

Keep the last committed browser publication visible while repairing the next
manifest or state. `PreparedPublicationRefresh` reports polling failures through
`onError`. An explicit `refresh()` rejects to its caller.

## A loader or mount fails

`loader_unavailable` means no supplied loader accepts the output codec and media
type. Install the loader's peer runtime and import the matching public subpath.
`loader_ambiguous` means more than one supplied loader accepted the output.

A CSP error during mount identifies the blocked capability. Embedded AnyWidget
modules can need `script-src blob:`, images can need `img-src blob:`, and remote
modules or chart resources need their declared origins.

Dispose failed staged mounts and keep the prior committed view. An aborted
transition can leave non-cancellable decoder or module work settling in the
background, but that work must not receive commit authority.

## A commit succeeded with a warning

`export_parent_sync_failed` and `retired_destination_cleanup_failed` accompany a
successful result after the new destination became visible. Verify the committed
destination first. For `export_parent_sync_failed`, `details.path` names the
visible destination whose parent-directory durability could not be confirmed.
For `retired_destination_cleanup_failed`, it names the previous sibling tree.
Remove that sibling only after confirming that no process still needs it.

```bash
uv run marimo-export verify dist/report --json
```

Preserve the warning with deployment logs. A successful verification confirms
the new export bytes, while the warning still records durability or cleanup work
that the operator must resolve.

Use the [error and limit reference](../reference/browser/errors-and-limits)
for browser codes and [Python records and errors](../reference/python/format-records-and-errors)
for Python failure families.
