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

A managed build reports these fields:

```text
Projection cache: 20 hits, 0 misses
Upstream cache activity: 75 hits, 40 misses
Phase timings: server start 0.766s, initial autorun 2.249s, capture 2.618s, server shutdown 0.210s, publication write 0.061s, total 5.919s
Fresh-child timings (5 states): construction 0.115s, upstream execution 0.713s, UI application 0.889s, projection execution 0.481s, cleanup 0.161s
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
