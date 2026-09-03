# Identities and protocols

marimo-export uses separate identities for authored intent, reusable work, and
immutable bytes. A protocol name identifies a data shape. A package version
identifies one implementation release. Keep those roles distinct when changing
planning, repository storage, or a cross-language boundary.

## Identity map

| Name                       | Covers                                                                                                            | Primary use                                         |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| Source SHA-256             | Exact saved notebook bytes                                                                                        | Detect source changes during a file operation       |
| Document SHA-256           | Canonical parsed notebook cells and configuration                                                                 | Match saved and live notebook documents             |
| Implementation SHA-256     | Installed marimo-export Python source manifest                                                                    | Invalidate work after an implementation change      |
| Environment SHA-256        | Installed distributions and relevant local Python sources                                                         | Bind results to imported code and package versions  |
| Producer SHA-256           | Notebook source and document, Python and operating system, Marimo, marimo-export, implementation, and environment | Scope observations and reusable prepared states     |
| Output-plan SHA-256        | Canonical `ExportSpec.outputs`                                                                                    | Reuse prepared states across state-relation changes |
| Spec SHA-256               | Complete canonical `ExportSpec`                                                                                   | Bind aliases, default state, states, and outputs    |
| State fingerprint          | Canonical complete input object                                                                                   | Address one normalized state vector                 |
| Repository identity        | Producer, output-plan, and spec SHA-256 values                                                                    | Find the current exact export generation            |
| Prepared-state instance    | Exact canonical prepared-state manifest                                                                           | Name one immutable prepared-state directory         |
| Notebook export identity   | Exact canonical `index.json` bytes                                                                                | Verify one portable notebook export                 |
| Export generation instance | Notebook export identity                                                                                          | Name one immutable repository export directory      |

`ExportPlan.identity` is the repository identity. It is SHA-256 over a canonical
object containing `producer_sha256`, `output_plan_sha256`, and `spec_sha256`.
`output_plan_sha256` is the public field name. An **output plan** is the complete
authored `outputs` mapping, and its digest is the **output-plan identity**.

`NotebookExport.identity` and `PreparedExport.identity` are the notebook export
identity. The repository stores that value as the export generation instance.
The `instance` field in `marimo-export.prepared.v1` carries the same notebook
export identity so a browser can reject a manifest that points at different
index bytes.

A prepared-state instance has another digest. It is SHA-256 over
`prepared-state.json`, which declares the producer, output plan, state
fingerprint, metadata, and file closure for one reusable state. It never appears
as `PreparedExport.identity`.

## Identity controls reuse

```text
producer + output plan + state fingerprint
  -> reusable prepared state

producer + output plan + complete ExportSpec
  -> repository identity
  -> current pointer
  -> generation instance = notebook export identity
```

Changing a state alias or the default state changes the spec and repository
identity while preserving matching prepared states. Changing an output
plan changes both the output-plan and repository identities.
Changing notebook source, relevant local source, an installed distribution, the
Python runtime, Marimo, or marimo-export changes the producer identity.

Presentation HTML, Cascading Style Sheets, browser JavaScript, route names, and
view host IDs stay outside these identities. An application can change its
presentation while reusing the same prepared notebook results.

### Producer environment sources

Producer identity includes installed distribution versions and relevant local
Python files. Discovery follows the notebook directory, project roots, `src`
layouts, imported local modules, and native extension paths. The source manifest
records stable path and file facts before hashing. Moving or changing a relevant
local source can therefore invalidate reuse even when the notebook bytes stay
equal. Unrelated files outside the discovered roots stay out of the identity.

## Protocol registry

| Contract                               | Visibility                      | Owner                                                              | Consumer and compatibility rule                                                                     |
| -------------------------------------- | ------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| StateSpace and ExportSpec JSON Schemas | Public authoring contracts      | `spec.py`                                                          | Reusable states and application outputs must compose into the same normalized `ExportSpec`          |
| `marimo-export.export.v1`              | Public durable protocol         | `index.py`, `descriptors.py`, `wire.py`                            | Python and browser readers must accept the same canonical index, descriptors, names, and limits     |
| `marimo.output.v1`                     | Public durable asset protocol   | Marimo projection adapter and browser snapshot parser              | A descriptor with codec `marimo.output.v1` references one canonical rendered-output snapshot        |
| `marimo.cell.v1`                       | Public durable asset protocol   | Marimo projection adapter and browser snapshot parser              | A descriptor with codec `marimo.cell.v1` references one canonical complete-cell snapshot            |
| `marimo-export.prepared.v1`            | Public browser-control protocol | `PreparedExport.manifest()` and `packages/browser/src/prepared`    | The manifest selects one exact state in one immutable notebook export                               |
| Prepared manifest bytes                | Public producer boundary        | `manifest.py`                                                      | Python emits canonical portable JSON no larger than 256 KiB before an application serves it         |
| `marimo-export.bridge.v1`              | Private process protocol        | `_marimo/bridge.py`, `_remote`, `_client_protocol.py`              | The installed client and kernel must use the same marimo-export version and implementation identity |
| `marimo-export.prepared-state.v1`      | Private repository protocol     | `_repository/files.py`                                             | Repository opening and reuse verify the manifest and complete file closure                          |
| `marimo-export.repository-export.v1`   | Private repository protocol     | `_repository/artifact_commit.py`, `_repository/artifact_access.py` | Generation metadata binds one export instance to its prepared-state instances                       |
| SQLite repository schema version 1     | Private local storage contract  | `_repository/sqlite/schema.py`                                     | An incompatible schema is quarantined before a fresh catalog opens                                  |

Media type identifies a BlobAsset representation inside the closed
`marimo.blob-asset.msgpack.v1` codec. Codec, media type, and schema identifier are
different fields. A new custom representation usually adds a media type,
exporter, and loader while retaining the existing BlobAsset codec.

Read [Portable JSON](portable-json.md) for the value and canonical byte rules
shared by these protocols.

## Error and limit ownership

Python operation failures derive from `MarimoExportError` and carry a stable
`code` plus portable `details`. Repository adapters add availability, busy,
limit, and integrity categories. `_cli/errors.py` maps those codes to CLI exit
categories without changing the underlying operation contract. Browser reader
and prepared-controller errors use separate versioned brands so checks survive
realms and independently bundled package copies.

Resource limits belong to the parser or lifecycle that allocates the bounded
value:

| Boundary                  | Primary owner                          | Required parity                              |
| ------------------------- | -------------------------------------- | -------------------------------------------- |
| StateSpace and ExportSpec | `spec.py`                              | JSON Schema and spec tests                   |
| Portable JSON             | `_json.py`, `packages/portable-json`   | Cross-language fixtures                      |
| Capture transfer          | `limits.py`, bridge transport          | Client, kernel, and transfer tests           |
| Export index and assets   | `index.py`, readers                    | Python, browser, and malformed fixtures      |
| Repository retention      | `_repository/models.py`                | Admission, lease, prune, and recovery tests  |
| Prepared manifest         | `manifest.py`, browser prepared parser | Python serialization and TypeScript parsing  |
| Representation decoder    | Owning loader                          | Allocation, abort, and malformed-input tests |

A change that tightens a public limit must update every producer and consumer
that validates the same bytes. Error-code changes require the CLI renderer,
public reference, and matching browser or Python consumers to change together.

## Protocol changes

Classify a change before editing a parser or serializer:

1. A compatible implementation change preserves the existing accepted values
   and canonical bytes.
2. A stricter security or resource bound needs matching Python and browser
   behavior plus malformed-input fixtures.
3. A public wire-shape change needs a new schema or codec identifier unless every
   previously published value retains its meaning.
4. A private repository shape change needs an explicit decision between a
   migration and the current quarantine-and-rebuild behavior.
5. A bridge change updates the client, kernel entrypoint, response decoder, and
   version and implementation checks together.

Cross-language changes require canonical fixtures consumed by both Python and
TypeScript. Package-version coordination does not replace protocol versioning.
