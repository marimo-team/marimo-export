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

| Term                      | Meaning                                                                                                                                                                                                                                                                     |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Definition                | A name created by a notebook cell. Planning inspects definitions to find output sources and eligible inputs.                                                                                                                                                                |
| Input                     | A definition whose value can vary between exported states. The `ExportSpec` omits an `inputs` field because planning infers the input names.                                                                                                                                |
| Captured baseline         | The complete values of inferred inputs before an authored state row is applied. It comes from the initial autorun or selected live session. An alias named `baseline` has no special behavior.                                                                              |
| State row                 | The sparse object written under one state name in an `ExportSpec`. Omitted inputs keep their captured-baseline values.                                                                                                                                                      |
| State space               | A finite declaration of explicit state rows, a Cartesian input matrix, or both. `StateSpace` can be composed with an application-owned output plan.                                                                                                                         |
| Complete input vector     | One value for every inferred input, produced by completing and normalizing a state row.                                                                                                                                                                                     |
| Exported state            | One complete input vector, its aliases, and the named outputs stored for that vector.                                                                                                                                                                                       |
| State-output relation     | The finite table that pairs each complete exported state with the same named output set.                                                                                                                                                                                    |
| State alias               | An authored state name that selects one state fingerprint. Several aliases can select the same state.                                                                                                                                                                       |
| Default alias             | The authored alias named by `StateSpace.default_state` or `ExportSpec.default_state`.                                                                                                                                                                                       |
| Default state fingerprint | The fingerprint stored in `index.json.default_state`.                                                                                                                                                                                                                       |
| Default exported state    | The `ExportState` returned by Python `NotebookExport.default_state` or browser `NotebookExport.defaultState`.                                                                                                                                                               |
| State fingerprint         | The lowercase SHA-256 digest of the canonical portable JSON for one complete input vector.                                                                                                                                                                                  |
| Observation               | One successful input vector, complete for its recorded input-name relation, retained as authoring evidence. Planning can project a broader relation to its inferred inputs. Observations remain repository history until an author writes chosen rows into an `ExportSpec`. |
| Observation revision      | The monotonically increasing repository revision for a producer's observations. Hosts can use it to detect new authoring evidence.                                                                                                                                          |
| Initial autorun           | marimo's first dependency execution after opening a notebook. Planning uses it to inspect definitions and capture baseline input values.                                                                                                                                    |
| Dependency closure        | A selected notebook result and every definition required to compute it. Planning uses this closure to infer inputs.                                                                                                                                                         |
| Input mode                | The inspection field that reports whether a state row replaces a complete input value or applies a sparse AnyWidget patch.                                                                                                                                                  |
| Sensitive input           | A marimo UI root that contains a password control. Planning excludes sensitive inputs from exported states.                                                                                                                                                                 |

## Outputs and storage

| Term                     | Meaning                                                                                                                                                                                |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Output                   | One published name and representation available for every exported state.                                                                                                              |
| Output source            | The `json`, `native`, `export`, `output`, or `cell` selection declared by an output spec.                                                                                              |
| Selector                 | A path from one Python definition through supported attribute or item steps to a selected notebook result.                                                                             |
| Exporter                 | A producer-side converter that returns a `BlobAsset` for one selected value.                                                                                                           |
| Output plan              | The complete set of authored output declarations. Its identity changes when an output source, exporter, option, or declared dependency changes.                                        |
| Output representation    | The codec and media type that define how one output is stored and decoded. One output name keeps the same representation across every state.                                           |
| Codec                    | A versioned identifier for the native storage envelope, such as `marimo.json.v1` or `numpy.npy.v1`.                                                                                    |
| Media type               | The standard content label for data inside a `BlobAsset` envelope. Custom representations should use a versioned media type.                                                           |
| Output descriptor        | The `index.json` record that declares an output's representation, provenance, and inline value or asset reference.                                                                     |
| Rendered-output snapshot | An inert `marimo.output.v1` record containing formatted output and replay resources for one selected result.                                                                           |
| Complete-cell snapshot   | An inert `marimo.cell.v1` record containing cell identity, outcome, terminal output, console records, and replay resources.                                                            |
| BlobAsset                | Representation bytes with a media type, optional portable filename, and portable JSON metadata.                                                                                        |
| Asset                    | A content-addressed file referenced by an output descriptor. Its path follows from its codec and SHA-256 digest.                                                                       |
| Asset reference          | The SHA-256 and byte size recorded for one asset. Python names this record `AssetRef`. TypeScript names it `AssetDescriptor`.                                                          |
| Asset closure            | The distinct set of assets declared by every output descriptor in a notebook export.                                                                                                   |
| Repository artifact      | A prepared-state or export-generation directory managed by the export repository. A repository artifact can contain several output assets.                                             |
| Projection               | The isolated output-specific capture used to produce one planned output. Projection-scoped identifiers prevent resources from separate outputs from colliding.                         |
| Control binding          | A durable mapping from one projection-scoped UI object ID to an exported input and a typed path within that input. `PreparedStateController` uses it to route accepted control values. |
| Output loader            | A consumer-side decoder selected explicitly for an output's codec and media type. A browser loader receives verified data and returns inert data or a mountable value.                 |
| Mount                    | The browser lifecycle that attaches a mountable value to a document element and returns a disposable view.                                                                             |

## Planning and preparation

| Term                        | Meaning                                                                                                                                                          |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ExportSpec                  | The authored declaration of one default state alias, sparse named state rows, and named output specs.                                                            |
| ExportPlan                  | The immutable result of planning. It contains identities, inferred inputs, normalized states, outputs, observations, and reusable or missing state fingerprints. |
| Plan                        | Resolve an `ExportSpec` against a producer and repository to expose normalized states, reusable work, and missing work.                                          |
| Prepare                     | Start a saved notebook when needed, execute missing states, and return a leased `PreparedExport`.                                                                |
| Capture                     | Prepare through one named live marimo session while leaving that session active.                                                                                 |
| Build                       | Prepare from a saved notebook, write the notebook export, verify it, and close the preparation handle.                                                           |
| Write                       | Copy a `PreparedExport` to a destination, verify the staged files, and commit the complete directory.                                                            |
| Prepared state              | A reusable portable result for one producer identity, output-plan identity, and state fingerprint.                                                               |
| Prepared export             | A leased immutable export generation for one exact `ExportSpec`. The Python `PreparedExport` handle exposes it.                                                  |
| Export generation           | One immutable prepared-export directory retained by the export repository.                                                                                       |
| Export repository           | Private local storage for observations, prepared states, export generations, leases, and retention metadata.                                                     |
| Artifact lease              | A live ownership record that protects one prepared state or export generation from retention. A detached prepared asset owns a generation lease.                 |
| Staging lease               | A live ownership record that protects an incomplete repository staging directory.                                                                                |
| Preparation reservation     | A fenced claim that gives one producer operation commit authority for a repository identity.                                                                     |
| Exact prepared-export reuse | Reuse of a prepared export whose producer, output plan, and complete `ExportSpec` identities all match. It can avoid notebook startup.                           |
| Producer identity           | The SHA-256 identity that binds the notebook document, relevant source and installed environment, runtime facts, and producer implementation.                    |
| Output-plan identity        | The SHA-256 identity of the complete authored `outputs` declaration.                                                                                             |
| ExportSpec identity         | The `spec_sha256` digest of the exact canonical `ExportSpec`.                                                                                                    |
| Repository identity         | The digest that combines producer, output-plan, and `ExportSpec` identities for exact repository lookup. `ExportPlan.identity` exposes it.                       |
| Prepared asset              | A file-scoped handle to one declared file in a `PreparedExport`, backed by an independently owned lease that protects the complete export generation.            |
| Repository retention        | The bounded policy that selects unleased prepared states and export generations for pruning. Active leases protect their artifacts.                              |

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
| Transition generation               | The browser controller's monotonic sequence value for superseding stale application work. It is distinct from a repository export generation.                                   |

## Integrity and trust

| Term                     | Meaning                                                                                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Canonical JSON           | JSON encoded under one closed set of ordering, number, string, and tag rules so the same supported value has one byte representation.                                          |
| Portable JSON            | Null, booleans, Unicode strings, JavaScript-safe finite numbers, arrays, and string-keyed objects accepted with matching Python and JavaScript semantics.                      |
| Verification             | Validation of the canonical index and every declared asset against sizes, SHA-256 digests, framing, representation invariants, and descriptor agreement.                       |
| Integrity root           | The loaded canonical `index.json` whose declarations determine the files and identities that verification checks.                                                              |
| Authentication           | Evidence supplied by a delivery channel or another trust system that establishes who published the integrity root. Export verification establishes consistency with that root. |
| marimo computation cache | marimo-owned storage for notebook cell results, invalidation, restoration, serialization, signing, and cache stores. It is separate from the export repository.                |

Read [States](../concepts/states-and-inputs),
[Outputs](../concepts/outputs-and-representations),
[Reuse](../concepts/preparation-and-reuse), and [Verification and
trust](../concepts/integrity-and-trust) for worked explanations.
