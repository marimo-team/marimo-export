# Agents and delivery

The delivery path starts with an unchanged notebook and ends with a verified
notebook export for people, agents, Python automation, browser applications,
and custom consumers. The repository also delivers two installable packages,
an agent workflow, a reference application, and documentation for the same
contracts.

## Delivery pipeline

| Stage                | Owner                                         | Durable result                                         |
| -------------------- | --------------------------------------------- | ------------------------------------------------------ |
| Inspect definitions  | `Session.inspect()`, bridge, CLI              | Stable definition and capability records               |
| Describe publication | ExportSpec                                    | Reviewable states, outputs, and representations        |
| Produce results      | `build` or `capture`                          | Export index, assets, and run diagnostics              |
| Verify               | Readers and writer                            | Complete verified notebook export                      |
| Consume              | Python reader, agents, browser, custom client | Grounded data, automation result, or application       |
| Package              | uv and pnpm workspaces                        | Python wheel, source archive, npm package subpaths     |
| Explain              | README, public docs, contributor docs, skill  | Human, agent, and maintainer paths through the product |

Each stage consumes the durable result from the previous stage. Operation
paths, process handles, credentials, temporary virtual files, and mounted
browser resources remain owned by their runtime lifecycle.

## Inspection precedes expensive execution

`marimo-export session` and `Session.inspect()` return notebook identity,
definitions, UI domains, input mode, version, and capability records. A person
or agent can therefore use real definition names before executing a state
matrix.

ExportSpec supports YAML, JSON, and Python construction through one validation
model. Humans and agents can review the finite publication surface before
`build` or `capture` begins.

## Exports ground agent answers

An agent can inspect prepared states and output representations, verify the
asset closure, read structured data, and retain notebook, state, producer, and
asset identity with its answer. Agent-oriented exports pair concise summaries
with inspectable tables, arrays, or versioned JSON. Visual and interactive
representations remain companion evidence for human review.

The same export can ground a coding agent that creates a bespoke frontend. The
frontend consumes exported values rather than inventing fixtures or
reimplementing notebook computation.

## Producer choice follows source ownership

| Source context                                                   | Producer  |
| ---------------------------------------------------------------- | --------- |
| The workflow owns notebook startup, execution, and cleanup       | `build`   |
| A live session already owns the environment or completed results | `capture` |

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

`build`, `capture`, `session`, `inspect`, and `verify` provide human output.
`--json` returns one bounded success or failure record with stable error codes
and exit categories. The CLI layer parses, calls the importable product API,
and renders results. Product modules own no terminal behavior.

## Package boundaries match public distribution

The uv workspace builds one `marimo-export` wheel and source archive. The pnpm
workspace builds one `@marimo-team/marimo-export` package. Loader
implementations remain private workspaces and appear through public
`loader/*` subpaths with optional peer dependencies.

Package smoke verifies:

- Python root exports and console command
- managed kernel lifespan entry point
- browser core installation without specialized peers
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
