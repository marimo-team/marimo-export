# Publish marimo notebook results for JavaScript

marimo-export evaluates a saved notebook in its prepared Python environment and publishes selected results as portable projection payloads. Frontend applications and agent tools read the publication through a typed TypeScript API or CLI after the producer stops.

Run the included cache-matrix workflow from [Getting started](./getting-started.md), then read one result:

```bash
node packages/client/dist/cli.mjs read \
  /tmp/cache-matrix-export large calculation \
  --format json \
  --ref /tmp/cache-matrix.build.json
```

## Feature map

### Execute a finite reactive matrix

An export plan declares public inputs and maps each one to a notebook definition or marimo UI element. Each scenario supplies one resolved input vector. The producer loads a fresh snapshot of the saved notebook, inserts definition overrides, initializes and updates bound UI elements, settles the valid authored graph through marimo's cache lifecycle, then evaluates the declared projections.

Use scenarios for parameter sweeps, preset reports, comparison states, or any finite set of frontend states. The index stores each scenario ID and its complete public input object, so a consumer can select by label or exact inputs.

### Reuse marimo's cell cache

Eligible authored cells and generated projection cells execute through marimo's native cell-cache machinery. The default file-backed store is the notebook-local `__marimo__/cache/` directory.

Interactive notebook execution can warm authored cells. Export projections are generated cells, so matching export builds without notebook user arguments warm and reuse those entries. User arguments disable native cell caching because they are process state outside marimo's cache identity. The projection-cell ABI, source selectors, exporter lineage, exporter versions, normalized options, and tracked dependencies participate in projection identity. Scenario IDs and public output labels do not.

Producer builds use marimo's default `relaxed` execution type. A notebook that has run with `strict` execution needs a fresh `__marimo__/cache` directory before production because marimo 0.23.14 shares native cache identity across the two execution types.

### Project notebook objects into frontend contracts

A notebook's authored display shape can differ from the data contract a frontend needs. An output selects a Python definition or evaluates a trusted expression. Each named format then calls an exporter that returns a `Projection` with bytes, a format ID, a media type, and JSON metadata.

The base producer includes JSON, text, HTML, bytes, and Vega-Lite exporters. Extras add Arrow, Parquet, PNG, and AnyWidget model graphs. Notebook-defined and importable exporters cover project-specific formats.

### Publish one immutable closure

A completed build returns an `ExportRef` for one immutable `marimo-export.index.v1` document. The index records the saved notebook digest, plan digest, producer versions, scenarios, outputs, and content-addressed payload references.

The Node transfer API pulls the index and its exact payload closure into:

```text
index.json
cache/<content-addressed payload key>
```

Incremental pulls verify existing payloads and transfer the missing or changed objects. The index is written after its referenced payloads pass verification.

### Consume from frontend and server runtimes

`openExport()` is the shared reader for browsers and server-side rendering. It validates the index, exposes immutable scenario and output objects, and verifies each payload before decoding it.

- Browser applications read a relative or absolute HTTP publication with `httpSource()`.
- Next.js and Astro read a local directory through the `/node` entrypoint during server or build execution.
- Node services use the same reader and directory source.
- In-memory systems implement `ExportSource` or call `memorySource()`.
- Format packages decode Arrow, Parquet, Vega-Lite, and AnyWidget projections through one `OutputLoader` contract.

### Give agents bounded data access

The CLI can inspect the available scenarios and output contracts, read one declared output under a byte limit, and verify the full publication. Structured commands return stable envelopes with `--json`, while `build --json` returns a raw build record that can be piped to `pull -`. Diagnostics stay on stderr.

An agent can ground an answer in the notebook source digest, plan digest, resolved inputs, format ID, payload digest, and payload size returned by `inspect` and `read`.

## Producer and consumer boundary

The producer runs beside the notebook:

```text
saved notebook + export plan
           |
           v
attached marimo kernel -> native marimo cache -> ExportRef
                                                |
                                                v
                                    staged projection closure
                                                |
                                                v
                                      published directory
                                                |
                              +-----------------+-----------------+
                              |                 |                 |
                           browser          SSR/build           agent
```

The prepared environment keeps ownership of Python packages, credentials, local data, GPU libraries, and marimo's execution cache. Transfer copies portable projection bytes and their index. Consumers need JavaScript and access to the published directory.

## Continue

- [Getting started](./getting-started.md)
- [Export plans](./export-plans.md)
- [Remote execution](./remote-execution.md)
- [Read exports](./read-exports.md)
- [AnyWidget](./anywidget.md)
- [CLI](./cli.md)
- [Trust and integrity](./trust.md)
