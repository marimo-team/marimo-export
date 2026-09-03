---
title: Preparation and reuse
description: Predict notebook startup, state reuse, repository storage, leases, and the effects of changing an ExportSpec.
---

# Preparation and reuse

Planning separates states that can be reused from states that must run. Suppose
an export repository already contains prepared states for `daily` and `weekly`:

| Authored change                             | Reusable states             | Missing states       |
| ------------------------------------------- | --------------------------- | -------------------- |
| Run the same exact spec                     | `daily`, `weekly`           | None                 |
| Add another alias for `daily`               | `daily`, `weekly`           | None                 |
| Change the default alias                    | `daily`, `weekly`           | None                 |
| Add a new `monthly` input vector            | `daily`, `weekly`           | `monthly`            |
| Add or change an output                     | None in the new output plan | Every distinct state |
| Change the notebook or producer environment | None                        | Every distinct state |

`plan()` reports this split before preparation. Exact reuse can return a plan
from a matching prepared export before notebook startup.

Because exact reuse returns before notebook execution, a changed external data
response does not invalidate that export by itself. Change a producer-identity
input or the output plan when the application requires deterministic fresh state
execution. Changing an alias, the default state, or generation retention can
still reuse matching prepared states.
`mo.watch.file` and other marimo cache keys take effect after preparation reaches
notebook execution. They cannot force an exact repository hit to start the
notebook.

## Three identities determine reuse

marimo-export computes three identities before it decides which artifacts match:

| Identity             | Bound facts                                                                                                                           | Effect of a change                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Producer identity    | Notebook document, relevant source and installed environment, Python and platform facts, marimo version, marimo-export implementation | Prepared states belong to a new producer    |
| Output-plan identity | The complete `outputs` declaration                                                                                                    | Prepared states belong to a new output plan |
| State fingerprint    | One canonical complete input vector                                                                                                   | That state becomes distinct                 |

A prepared state is reusable when producer identity, output-plan identity, and
state fingerprint match. It contains the portable output descriptors and assets
for one state.

A prepared export adds the exact `ExportSpec` identity. This means alias or
default changes can reuse prepared states while producing a new exact export
generation.

## `plan` returns the work description

`plan()` returns an immutable `ExportPlan` with:

- producer, output-plan, spec, and plan identities
- inferred input names
- normalized states and aliases
- the default alias and fingerprint
- output names
- projected observations and their revision
- reusable and missing state fingerprints
- `exact_reuse`

When no exact prepared export exists, file-based planning starts the notebook,
runs its initial autorun, captures the baseline, and closes the owned session.
Execution of the requested state-output relation begins during `prepare` or
`capture`.
Every inferred baseline value must be portable before authored state-row
overrides apply. Supplying that input in every row cannot make a sensitive or
nonportable captured baseline eligible.

## `prepare` and `capture` execute missing states

Choose the producer operation from the notebook's current location:

| Operation                | Source                                   | Session ownership                                 | Result                               |
| ------------------------ | ---------------------------------------- | ------------------------------------------------- | ------------------------------------ |
| `prepare()`              | Saved notebook file                      | marimo-export owns and closes a temporary session | Leased `PreparedExport`              |
| `capture()`              | Named session on a running marimo server | Caller keeps the server and session active        | Leased `PreparedExport`              |
| `build()`                | Saved notebook file plus destination     | Composes `prepare()` and `write()`                | Written and verified notebook export |
| `PreparedExport.write()` | Existing prepared handle                 | Keeps the handle open                             | Written and verified notebook export |

Each state runs through isolated output leaves. One failed state, output, or
verification step fails the complete producer operation.

File preparation runs from a temporary copy and checks that the authored
notebook and producer identity remain stable. Capture requires the client and
selected live kernel to use the same marimo-export implementation and source
identity.

## Where marimo's content-addressed cache fits

marimo's [caching API](https://docs.marimo.io/api/caching/) covers explicit
function and block caches plus notebook-wide automatic cell caching. Automatic
cell caching derives reusable work from the reactive dependency graph and can
restore supported cell definitions from persistent storage.

The SciPy 2026 article [Content-Addressed Caching for Reactive
Notebooks](https://dmadisetti.github.io/scipy_proceedings_2026/) develops that
design. It explains recursive cache keys over the reactive graph, parent-cell
hashes for values that cannot be content-addressed directly, lazy restoration
through typed stubs, and the same cache artifacts in browser WebAssembly
exports.

marimo-export uses automatic cell caching while it prepares each requested
state:

```text
complete ExportSpec state
  -> marimo reactive graph
  -> native cell-cache lookup
       hit  -> restore the cell definitions
       miss -> execute the cell and persist its result
  -> transient output leaf
  -> verified native cache receipt
  -> portable prepared state
```

The producer-owned session and every isolated state child enable marimo's native
cell cache. A captured live session keeps its existing parent-session policy.
The state children still enable caching so their selected outputs produce
verified native receipts. Author-written `mo.cache` and `mo.persistent_cache`
blocks continue to follow their normal marimo semantics.

Marimo also owns invalidation. Create `mo.watch.file` in an upstream cell and
read it from the dependent cell when a prepared value uses file contents that
can change independently of notebook source. Give network, database, and other
mutable external inputs an explicit author-owned cache key. A complete-cell
output runs its owner live because console messages are side effects outside
Marimo's cache contract.

Three persisted layers answer different questions:

| Storage                  | Owns                                                                               | Reuse question                                     |
| ------------------------ | ---------------------------------------------------------------------------------- | -------------------------------------------------- |
| marimo computation cache | Cell keys, invalidation, restoration, serialization, signing, and cache stores     | Can this cell result be restored during execution? |
| export repository        | Observations, prepared states, immutable export generations, leases, and retention | Can this portable state or exact export be reused? |
| notebook export          | Canonical `index.json` and declared consumer assets                                | Which prepared results can this consumer select?   |

An exact prepared-export hit returns before notebook startup. A prepared-state
miss can still reuse native cell-cache entries while marimo reconstructs that
state. A written notebook export remains usable after both producer-side stores
are unavailable.

marimo's cached WebAssembly export uses a different delivery boundary. It ships
native cache manifests and blobs with a browser Python runtime, which re-derives
cache keys and restores values. marimo-export consumes verified native receipts
in the producer and writes a finite, language-neutral state-output relation for
Python, browser, agent, and custom readers.

## A PreparedExport owns a lease

`prepare()` and `capture()` return `PreparedExport`, a Python handle to one
immutable export generation. The lease protects that generation from
retention while the handle remains open.

Given the `report.py` notebook and matching `report.export.yaml` used throughout
the concept pages, retain the handle with a context manager:

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("report.export.yaml")
with prepare("report.py", spec=spec) as prepared:
    notebook_export = prepared.open()
    prepared.write("dist/report")
```

`PreparedExport.asset(relative)` creates an independently leased
`PreparedAsset`. Use it when an HTTP response can outlive the parent request,
then close the asset after the response completes.

`PreparedExport.manifest()` creates a prepared manifest for browser
applications. The manifest identifies one immutable notebook export and one
selected state. It can also declare a refresh interval for a changing
publication.

Use [Build or capture](../guide/build-and-capture) for commands and
authentication. Use the [Python API](../reference/python-api) for handle
methods, repository configuration, and application directory delivery.
