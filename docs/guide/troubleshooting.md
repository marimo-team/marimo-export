---
title: Troubleshoot notebook exports
description: Diagnose producer, repository, integrity, browser, and mount failures from the smallest observable boundary.
---

# Troubleshoot notebook exports

Start with the command or consumer that failed. Preserve its stable error code,
details, and cause before changing files or clearing repository state.

## Check the local environment

Run:

```bash
marimo-export doctor --json
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

Run `marimo-export plan` again, then rebuild the missing state.

## A state input is invalid or nonportable

Inspect the notebook boundary:

```bash
marimo-export inspect report.py --json
```

Use the definition's reported `value`, `domain`, `input_mode`,
`portable_input`, and `sensitive` fields. A UI element's portable frontend value
can differ from the Python value returned by its `.value` property. Copy the
inspected shape into the state row.

Planning rejects sensitive inputs, binary AnyWidget state, non-finite portable
numbers, missing definitions, and ordinary assignments that compete with a
selected final named expression.

## A state cannot be resolved

`state_not_found` means an authored state name is absent.
`state_input_invalid` means a complete input mapping has the wrong keys or
shape. `state_unavailable` means the complete vector is valid but was not
exported.

List `notebookExport.states()` or inspect `ExportPlan.states`, then select an
available name or vector. Computing a new vector requires another preparation
run or a Python service.

## The repository is busy or unavailable

Inspect it before pruning:

```bash
marimo-export repository status --json
marimo-export repository prune --dry-run --json
```

`repository_busy` usually means another healthy writer holds a reservation or
filesystem maintenance lock. Retry after that operation completes. A lost
lease or confirmed integrity error requires reopening or preparing the export
again.

Use live prune only after reviewing the dry-run counts. Active artifact leases
protect their files. [Manage repository reuse](manage-repository.md) describes
the retained data and clear scope.

## Verification fails

Run:

```bash
marimo-export verify dist/report --json
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
marimo-export inspect https://notebooks.example --json
```

A timeout means the client stopped waiting. The remote scratchpad operation may
still be running, so marimo-export does not retry it automatically.

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

Use the [error and limit reference](../reference/browser/errors-and-limits.md)
for browser codes and [Python records and errors](../reference/python/format-records-and-errors.md)
for Python failure families.
