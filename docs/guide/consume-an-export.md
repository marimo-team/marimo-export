---
title: Consume an export
description: Open the same prepared notebook states and outputs from Python, a browser, an agent, or a custom client.
---

# Consume a notebook export

A notebook export exposes the same state and output names to human-facing
applications, agents, Python automation, and browser clients.

## Choose a consumer

| Job                                     | Interface                                      |
| --------------------------------------- | ---------------------------------------------- |
| Read and verify local files from Python | `open_export()`                                |
| Load prepared results in a browser      | `openExport()`                                 |
| Ground an agent answer in exported data | CLI JSON, Python reader, or export format      |
| Implement another client                | [Export format](../reference/export-format.md) |

## Open from Python

For an export with a `baseline` state and scalar `title` output:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
state = notebook_export.state("baseline")
title = state.output("title").scalar()

notebook_export.verify()
```

Opening validates `index.json`. Assets remain lazy until one output is read or
the complete export is verified.

## Inspect for an agent

```bash
marimo-export inspect dist/report --json
marimo-export verify dist/report --json
```

`inspect` reports notebook identity, prepared states, outputs,
representations, and declared asset size. `verify` reads the complete asset
closure. An agent can then select a state and read a structured output through
the Python reader or another implementation of the export format.

Bind data-driven claims to the selected state and output. Retain notebook,
producer, fingerprint, representation, and asset identity when the answer needs
an auditable source.

[Use notebook exports with agents](agents-and-automation.md) develops this
workflow and explains which representations are suitable for agent reasoning.

## Open from a browser

```ts
import { openExport, scalarLoader } from "@marimo-team/marimo-export";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const title = await state.output("title").load(scalarLoader());
```

Install each optional loader runtime used by the application. [Output
representations](../reference/representations.md) lists the exporter, loader,
result type, agent suitability, and peer dependency for every built-in family.

## Select a prepared state

Readers support three forms of selection:

- `state(name)` selects one authored state name.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` completes a sparse transition from the current state.

Resolution selects a state already present in the export. A request that needs
a new Python result requires another export or a Python service.

## Verify the complete export

Python:

```python
result = notebook_export.verify()
```

Browser:

```ts
const result = await notebookExport.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

Verification checks every declared asset and returns state, output, asset, and
byte counts. The loaded `index.json` is the integrity root.

Use [Build a browser application](browser-applications.md) when the consumer
mounts interactive representations or replaces several outputs as one view.
