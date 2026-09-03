---
title: Browser API reference
description: Routes to the TypeScript reader, prepared publication, loader, snapshot, error, and limit contracts.
---

# Browser API reference

`@marimo-team/marimo-export` opens a notebook export over HTTP, selects results
that are already present, verifies output bytes before decoding them, and mounts
interactive values when the application requests a mount.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const title = await notebookExport.defaultState.output("title").load(jsonLoader());

document.querySelector("#title")!.textContent = String(title);
```

Opening validates canonical `index.json`. Output assets remain lazy until
`load()` or complete verification requests them.

## Choose a reference

| Need                                                                              | Reference                                                 |
| --------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Open an export, select a state, load an output, or supply authenticated fetch     | [Browser reader](browser/reader.md)                       |
| Follow a changing manifest, route inputs, or own an atomic application transition | [Prepared publications](browser/prepared-publications.md) |
| Choose a built-in loader, mount an interactive result, or write a custom loader   | [Output loaders](browser/loaders.md)                      |
| Consume rendered-output and complete-cell replay records                          | [marimo snapshots](browser/snapshots.md)                  |
| Handle errors, choose byte limits, or check browser requirements                  | [Errors and limits](browser/errors-and-limits.md)         |
| Convert or parse the common Python and JavaScript JSON subset                     | [Portable JSON](portable-json.md)                         |

## Browser nouns

| Noun                       | Contract                                                                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------ |
| Notebook export            | One canonical `index.json` and its declared content-addressed assets                             |
| Exported state             | One complete input vector whose outputs are present in the notebook export                       |
| State alias                | An authored state name that selects an exported state. Several aliases can select the same state |
| Output                     | One published name and representation in every exported state                                    |
| Representation             | The codec, media type, metadata, and inline value or asset that store an output                  |
| Loader                     | A codec-aware decoder selected explicitly by the application                                     |
| Mount                      | The resource-owning step that attaches an interactive loaded value to a document element         |
| Prepared manifest document | Snake-case JSON that points to one immutable export and selected state                           |
| Prepared publication       | A verified manifest, immutable notebook export, and selected exported state                      |

The browser API resolves finite exported states. A request for an input vector
outside the notebook export requires another preparation run or a Python
service.

## Integrity and execution

The browser applies three separate checks:

1. `openExport()` validates the index and state relation.
2. `ExportOutput.load()` verifies one selected asset before its loader runs.
3. `NotebookExport.verify()` verifies every unique declared asset.

These checks establish consistency with the loaded index. They do not
authenticate who produced the index. Opening, selecting, loading inert records,
and verifying do not import notebook-authored browser modules. Mounting an
AnyWidget, Vega-Lite chart, or custom interactive value grants that code the
page's authority.

[Build a browser application](../guide/browser-applications.md) applies these
contracts to staged loading, visible commit, and mount disposal.
