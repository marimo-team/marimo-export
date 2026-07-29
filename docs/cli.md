# Command-line interface

marimo-export has five noninteractive commands:

```text
marimo-export build NOTEBOOK --spec FILE --output DIR
marimo-export capture SERVER --spec FILE --output DIR
marimo-export session SERVER
marimo-export inspect PUBLICATION
marimo-export verify PUBLICATION
```

Add `--json` to receive one canonical machine-readable result.

## `build`

```bash
marimo-export build notebook.py \
  --spec notebook.export.yaml \
  --output dist/notebook \
  --timeout 30
```

`build` owns an authenticated loopback server and its notebook process tree.
Its initial notebook autorun uses marimo's native cell cache.
`--replace` atomically replaces an existing real directory.

## `capture`

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec notebook.export.yaml \
  --output dist/notebook
```

Credentials use `--access-token`, `--server-token`,
`MARIMO_EXPORT_ACCESS_TOKEN`, or `MARIMO_EXPORT_SERVER_TOKEN`.

## Publication diagnostics

A finance build reports these fields:

```text
Projection cache: 42 hits, 0 misses
Upstream cache activity: 138 hits, 12 misses
Phase timings: server start 0.689s, initial autorun 0.783s, capture 4.245s, server shutdown 0.230s, publication write 0.094s, total 6.055s
Fresh-child timings (6 states): construction 0.104s, upstream execution 1.345s, UI application 0.504s, projection execution 1.699s, cleanup 0.200s
```

Durations and cache counts reflect the current run.
Projection counts cover the state and output relation. Upstream activity covers
native cache lookups by non-projection cells in fresh state children. A hit
means marimo found a matching cache entry. Restoration failures and cells that
define session-local UI elements can still execute live after a hit.

Server start includes loopback startup, session connection, and kernel
readiness. Initial autorun starts with the instantiate request and ends at the
corresponding completed run. UI timing includes reactive execution triggered by
child-local UI values.

`--json` returns the same data under `projection_cache`, `upstream_cache`, and
`timings`. These run-local diagnostics stay outside `index.json`.

## `session`

```bash
marimo-export session http://127.0.0.1:2718
marimo-export session http://127.0.0.1:2718 --session SESSION_ID --json
```

The list form reports active session IDs, filenames, and paths. Selecting a
session reports the notebook definitions available for input and output
authoring.

## `inspect`

```bash
marimo-export inspect dist/notebook --json
```

`inspect` validates canonical `index.json` and reports notebook identity,
producer versions, complete state vectors, codecs, media types, and declared
asset totals. It leaves assets unread.

## `verify`

```bash
marimo-export verify dist/notebook --json
```

`verify` reads every unique asset and validates length, SHA-256, native file
framing, and `BlobAsset` envelope agreement.

## Exit categories

| Exit | Category                        |
| ---- | ------------------------------- |
| 0    | success                         |
| 2    | arguments, spec, or local input |
| 3    | remote transport                |
| 4    | session or managed server       |
| 5    | state or output execution       |
| 6    | publication or integrity        |
| 7    | filesystem commit               |
| 141  | broken output pipe              |
