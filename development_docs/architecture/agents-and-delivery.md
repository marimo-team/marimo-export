# Product surfaces and distribution

The product workflow starts with an unchanged notebook and ends with a verified
notebook export for people, agents, Python automation, browser applications,
and custom consumers. The repository also delivers three installable packages,
an agent workflow, a reference application, and documentation for the same
contracts.

## Delivery pipeline

| Stage               | Owner                                         | Result                                                   |
| ------------------- | --------------------------------------------- | -------------------------------------------------------- |
| Inspect definitions | `Session.inspect()`, bridge, CLI              | Stable definition and capability records                 |
| Describe export     | ExportSpec                                    | Reviewable default, states, outputs, and representations |
| Plan preparation    | `plan` or `Session.plan()`                    | Reusable and missing work                                |
| Prepare results     | `prepare`, `capture`, or `Session.capture()`  | Leased immutable prepared export                         |
| Build from file     | `build`                                       | Prepared and written notebook export                     |
| Write prepared      | `PreparedExport.write()`                      | Export index, assets, and run diagnostics                |
| Verify              | Readers and writer                            | Complete verified notebook export                        |
| Consume             | Python reader, agents, browser, custom client | Grounded data, automation result, or application         |
| Package             | uv and pnpm workspaces                        | Python wheel, source archive, and npm packages           |
| Explain             | README, public docs, contributor docs, skill  | Human, agent, and maintainer paths through the product   |

Each stage consumes a bounded contract. `build()` composes file preparation and
write into one operation. The notebook export is the durable consumer artifact.
Operation paths, process handles, credentials, temporary virtual files,
repository leases, and mounted browser resources remain owned by their runtime
lifecycle.

## Inspection resolves definitions before state preparation

File inspection through `marimo-export inspect` starts an owned notebook, runs
its initial autorun, and returns notebook identity, definitions, UI domains,
input mode, version, and capability records. Server inspection borrows a live
session. `Session.inspect()` returns the live-session contract. A person or
agent can therefore use real definition names before preparing a state space.

`plan()` takes a separate exact-reuse fast path. When the repository already
contains the matching prepared export, it reconstructs the public plan from the
verified artifact and starts no notebook.

ExportSpec supports YAML, JSON, and Python construction through one validation
model. Humans and agents can review the finite export relation before `build`
or `capture` begins.

## Exports ground agent answers

An agent can inspect exported states and output representations, verify the
asset closure, read structured data, and retain notebook, state, producer, and
asset identity with its answer. Agent-oriented exports pair concise summaries
with inspectable tables, arrays, or versioned JSON. Visual and interactive
representations remain companion evidence for human review.

The same export can ground a coding agent that creates a bespoke frontend. The
frontend uses exported values as its fixtures and notebook-computation results.

## Producer choice follows source ownership

| Source context                                                   | Preparation operation | Prepare-and-write command |
| ---------------------------------------------------------------- | --------------------- | ------------------------- |
| The workflow owns notebook startup, execution, and cleanup       | `prepare`             | `build`                   |
| A live session already owns the environment or completed results | `capture`             | CLI `capture`             |

Both modes run the same ExportSpec through the same bridge and export format.
The live finance acceptance path exercises both modes.

## The app scaffold keeps presentation separate

`skills/notebook-to-static-app/scripts/scaffold_app.py` creates a relocatable uv
and Vite workspace. It vendors the reviewed Python wheel and npm tarball,
copies notebook dependency metadata, intersects the Python range with the
package floor, and stores source filename plus SHA-256 provenance.

The scaffold stages the complete directory before commit. The notebook remains
unchanged. The generated `src/main.ts` is a loading shell that the agent
replaces with the audience-facing application.

The skill workflow requires agents to:

1. inspect notebook definitions
2. author sparse named states and focused outputs
3. select `build` or `capture` from source ownership
4. verify the export and build the browser application
5. exercise every state and one mounted interaction in the browser
6. return evidence bound to notebook, export, app build, and rendered result

## The CLI supports people and agents

`plan`, `build`, `capture`, `inspect`, `verify`, `observations`, `repository`,
and `doctor` provide human output. `--json` returns one bounded result.
Preparation commands can emit ordered progress through `--jsonl`. Failures
carry stable codes and exit categories. The CLI parses arguments, calls the
importable Python SDK, and renders results. Product modules own no terminal
behavior.

The Python `capture()` operation returns `PreparedExport`. The CLI `capture`
command requires `--output` and composes that operation with
`PreparedExport.write()` so one invocation writes the verified live-session
export.

`observations list` and `observations clear` require a notebook and ExportSpec.
Each command resolves an `ExportPlan` first. `list` renders the plan's
revision-consistent observation snapshot. `clear` passes that plan to
`ExportRepository.clear_observations(plan)`.

## Package boundaries match public distribution

The uv workspace builds one `marimo-export` wheel and source archive. The pnpm
workspace builds one public `@marimo-team/marimo-export` package. Browser core
implements the root scalar and image loaders plus JSON, text, HTML, and marimo
snapshot subpaths. The workspace-owned portable JSON implementation and private
Arrow, NumPy, Parquet, Vega-Lite, and AnyWidget loaders are bundled behind
public browser subpaths with their required peer dependencies.

Workspace builds and package smoke verify:

- Python root exports and console command
- managed kernel lifespan entry point
- standalone browser core installation
- every loader subpath with its required peer runtime
- docs and example builds

## Documentation follows reader ownership

| Reader job                                         | Owner                                  |
| -------------------------------------------------- | -------------------------------------- |
| Decide whether the product fits                    | Root README and docs home              |
| Reach one working result                           | `docs/guide/getting-started.md`        |
| Consume an export from Python or the browser       | `docs/guide/consume-an-export.md`      |
| Ground an answer or build through an agent         | `docs/guide/agents-and-automation.md`  |
| Build a purpose-specific browser application       | `docs/guide/browser-applications.md`   |
| Complete a producer or deployment task             | Remaining `docs/guide/` pages          |
| Look up an authored, durable, CLI, or API contract | `docs/reference/`                      |
| Change code ownership or lifecycle                 | `development_docs/architecture/`       |
| Run and validate the workspace                     | Contributor guide and development docs |
| Execute the complete agent application workflow    | `skills/notebook-to-static-app`        |

VitePress builds the rendered site, local search, `llms.txt`, and
`llms-full.txt` from the same authored pages.

## Evidence follows the changed boundary

| Change                | Focused evidence                  | Complete evidence                                 |
| --------------------- | --------------------------------- | ------------------------------------------------- |
| CLI or Python API     | Targeted pytest                   | Wheel import and command smoke                    |
| Build, capture, cache | Integration test                  | Live file build and session capture               |
| Scaffold              | Source-preserving relocation test | Generated workspace install and build             |
| Browser application   | Typecheck and production build    | Desktop, narrow, rapid transition, mounted action |
| Documentation         | Prose, links, and docs build      | Search, source view, LLM bundle, rendered browser |
| npm facade or loader  | Package types and tests           | Isolated packed core and loader builds            |

[Validation](../validation.md) lists the root commands and required browser
checks.
