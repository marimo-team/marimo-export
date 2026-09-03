---
title: Terminology
description: Canonical names for notebook states, outputs, preparation, repository artifacts, readers, integrity, and browser publications.
---

# Terminology

Use these nouns when reading the guides, APIs, command output, and export format.
The concept pages introduce them through worked examples.

## Product objects

| Term            | Meaning                                                                                                                                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Notebook result | A Python value, rendered output, or complete cell produced by the notebook before export.                                                  |
| Producer        | The Python environment that inspects a notebook, executes selected states, and creates portable results.                                   |
| Consumer        | A Python reader, browser reader, agent, or custom implementation that reads a completed notebook export.                                   |
| Notebook export | One canonical `index.json` and the content-addressed assets declared by that index. Use **export** after the full noun is clear.           |
| Export identity | The lowercase SHA-256 digest of the exact canonical `index.json` bytes. Python and browser readers expose it as `NotebookExport.identity`. |
| Provenance      | Records that identify the source notebook, producer implementation, selected state, output representation, and asset bytes.                |

## States and inputs

| Term                  | Meaning                                                                                                                                                                                        |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Definition            | A name created by a notebook cell. Planning inspects definitions to find output sources and eligible inputs.                                                                                   |
| Input                 | A definition whose value can vary between exported states. The `ExportSpec` omits an `inputs` field because planning infers the input names.                                                   |
| Captured baseline     | The complete values of inferred inputs before an authored state row is applied. It comes from the initial autorun or selected live session. An alias named `baseline` has no special behavior. |
| State row             | The sparse object written under one state name in an `ExportSpec`. Omitted inputs keep their captured-baseline values.                                                                         |
| Complete input vector | One value for every inferred input, produced by completing and normalizing a state row.                                                                                                        |
| State                 | One complete assignment for every inferred input.                                                                                                                                              |
| State-output relation | The finite table that pairs each complete exported state with the same named output set.                                                                                                       |
| State alias           | An authored state name that selects one state fingerprint. Several aliases can select the same state.                                                                                          |
| Default state         | The state selected when a reader receives no explicit selection. `default_state` names an alias in the `ExportSpec` and becomes a fingerprint in `index.json`.                                 |
| State fingerprint     | The lowercase SHA-256 digest of the canonical portable JSON for one complete input vector.                                                                                                     |
| Observation           | One successful complete input vector retained as authoring evidence. Observations remain repository history until an author writes chosen rows into an `ExportSpec`.                           |
| Observation revision  | The monotonically increasing repository revision for a producer's observations. Hosts can use it to detect new authoring evidence.                                                             |
| Initial autorun       | marimo's first dependency execution after opening a notebook. Planning uses it to inspect definitions and capture baseline input values.                                                       |
| Dependency closure    | A selected notebook result and every definition required to compute it. Planning uses this closure to infer inputs.                                                                            |
| Input mode            | The inspection field that reports whether a state row replaces a complete input value or applies a sparse AnyWidget patch.                                                                     |
| Sensitive input       | A marimo UI root that contains a password control. Planning excludes sensitive inputs from exported states.                                                                                    |

## Outputs and storage

| Term                  | Meaning                                                                                                                                                                                        |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Output                | One published name and representation available for every exported state.                                                                                                                      |
| Output source         | The `json`, `native`, `export`, `output`, or `cell` selection declared by an output spec.                                                                                                      |
| Selector              | A path from one Python definition through supported attribute or item steps to a selected notebook result.                                                                                     |
| Exporter              | A producer-side converter that returns a `BlobAsset` for one selected value.                                                                                                                   |
| Output plan           | The complete set of authored output declarations. Its identity changes when an output source, exporter, option, or declared dependency changes.                                                |
| Output representation | The stored form of one output. One output name keeps the same codec and media type across every state.                                                                                         |
| Codec                 | A versioned identifier for the native storage envelope, such as `marimo.json.v1` or `numpy.npy.v1`.                                                                                            |
| Media type            | The standard content label for data inside a `BlobAsset` envelope. Custom representations should use a versioned media type.                                                                   |
| Descriptor            | The `index.json` record that declares an output's codec, media type, provenance, and inline value or asset reference.                                                                          |
| BlobAsset             | Representation bytes with a media type, optional portable filename, and portable JSON metadata.                                                                                                |
| Asset                 | A content-addressed file referenced by an output descriptor. Its path follows from its codec and SHA-256 digest.                                                                               |
| Asset closure         | The distinct set of assets declared by every output descriptor in a notebook export.                                                                                                           |
| Repository artifact   | A prepared-state or export-generation directory managed by the export repository. A repository artifact can contain several output assets.                                                     |
| Projection            | The isolated output-specific capture used to produce one planned output. Projection-scoped identifiers prevent resources from separate outputs from colliding.                                 |
| Control binding       | A durable mapping from one projection-scoped UI object ID to an exported input and a typed path within that input. Browser prepared-state controllers use it to route accepted control values. |
| Loader                | A consumer-side decoder selected explicitly for an output's codec and media type. A browser loader receives verified data and returns inert data or a mountable value.                         |
| Mount                 | The browser lifecycle that attaches a mountable value to a document element and returns a disposable view.                                                                                     |

## Planning and preparation

| Term                 | Meaning                                                                                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ExportSpec           | The authored declaration of one default state alias, sparse named state rows, and named output specs.                                                            |
| ExportPlan           | The immutable result of planning. It contains identities, inferred inputs, normalized states, outputs, observations, and reusable or missing state fingerprints. |
| Plan                 | Resolve an `ExportSpec` against a producer and repository to expose normalized states, reusable work, and missing work.                                          |
| Prepare              | Start a saved notebook when needed, execute missing states, and return a leased `PreparedExport`.                                                                |
| Capture              | Prepare through one named live marimo session while leaving that session active.                                                                                 |
| Build                | Prepare from a saved notebook, write the notebook export, verify it, and close the preparation handle.                                                           |
| Write                | Copy a `PreparedExport` to a destination, verify the staged files, and commit the complete directory.                                                            |
| Prepared state       | A reusable portable result for one producer identity, output-plan identity, and state fingerprint.                                                               |
| Prepared export      | A leased immutable repository generation for one exact `ExportSpec`. The Python `PreparedExport` handle exposes it.                                              |
| Export generation    | One immutable prepared-export directory retained by the export repository.                                                                                       |
| Export repository    | Private local storage for observations, prepared states, export generations, leases, and retention metadata.                                                     |
| Lease                | A live ownership record that keeps a prepared export, prepared asset, staging directory, or preparation claim protected for its owner.                           |
| Exact reuse          | Reuse of a prepared export whose producer, output plan, and complete `ExportSpec` identities all match. Exact reuse can avoid notebook startup.                  |
| Producer identity    | The SHA-256 identity that binds the notebook document, relevant source and installed environment, runtime facts, and producer implementation.                    |
| Output-plan identity | The SHA-256 identity of the complete authored `outputs` declaration.                                                                                             |
| ExportSpec identity  | The `spec_sha256` digest of the exact canonical `ExportSpec`.                                                                                                    |
| Plan identity        | The repository identity that combines producer, output-plan, and `ExportSpec` identities.                                                                        |
| Prepared asset       | An independently leased handle to one declared file in a `PreparedExport`.                                                                                       |
| Repository retention | The bounded policy that selects unleased prepared states and export generations for pruning. Active leases protect their artifacts.                              |

## Reading and publication

| Term                                | Meaning                                                                                                                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Open                                | Parse and validate `index.json`, construct immutable reader objects, and leave assets lazy.                                                                                     |
| Resolve                             | Select a state already present in the notebook export by alias, complete input vector, or sparse patch from an existing state.                                                  |
| Load                                | Verify one output asset when present, then decode its representation through an explicit browser loader.                                                                        |
| Prepared manifest                   | A bounded `marimo-export.prepared.v1` JSON record that names one notebook export identity, export URL, complete input vector, state fingerprint, and optional refresh interval. |
| Prepared publication in Python      | A controller-owned `PreparedExport` and application metadata selected for one application key.                                                                                  |
| Prepared publication in the browser | The value that joins a validated prepared manifest, its opened notebook export, and its selected state.                                                                         |
| Prepared publication controller     | The Python coordinator that keeps the last successful prepared export available while application inputs or observations change.                                                |
| Prepared state controller           | The browser coordinator that applies semantic input changes, cancels stale transitions, and retains the last committed publication after a rejected transition.                 |
| Publication refresh                 | The browser lifecycle that fetches prepared manifests, reuses an already opened export when possible, and replaces the controller's publication.                                |

## Integrity and trust

| Term                     | Meaning                                                                                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Canonical JSON           | JSON encoded under one closed set of ordering, number, string, and tag rules so the same supported value has one byte representation.                                          |
| Portable JSON            | Null, booleans, Unicode strings, JavaScript-safe finite numbers, arrays, and string-keyed objects accepted with matching Python and JavaScript semantics.                      |
| Verification             | Validation of the canonical index and every declared asset against sizes, SHA-256 digests, framing, representation invariants, and descriptor agreement.                       |
| Integrity root           | The loaded canonical `index.json` whose declarations determine the files and identities that verification checks.                                                              |
| Authentication           | Evidence supplied by a delivery channel or another trust system that establishes who published the integrity root. Export verification establishes consistency with that root. |
| marimo computation cache | marimo-owned storage for notebook cell results, invalidation, restoration, serialization, signing, and cache stores. It is separate from the export repository.                |

Read [States and inputs](../concepts/states-and-inputs.md), [Outputs and
representations](../concepts/outputs-and-representations.md), [Preparation and
reuse](../concepts/preparation-and-reuse.md), and [Integrity and
trust](../concepts/integrity-and-trust.md) for the worked model.
