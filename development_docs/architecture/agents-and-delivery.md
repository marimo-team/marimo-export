# Product surfaces and distribution

The product workflow starts with an unchanged notebook and ends with a verified
notebook export for people, agents, Python automation, browser applications,
and custom consumers. The repository delivers two public packages, one Python
source distribution, private TypeScript workspaces, one agent skill, a
deterministic example, a browser application, and documentation for the same
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

## Product surface owners

| Surface                    | Internal owner                                       | Public contract                                                                   |
| -------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------- |
| Inspection                 | `inspection.py`, `producer.py`, live-session bridge  | [Sessions and inspection](../../docs/reference/python/sessions-and-inspection.md) |
| State and output authoring | `spec.py`, `_execution/plan.py`                      | [StateSpace and ExportSpec](../../docs/reference/export-spec.md)                  |
| File and live production   | `_build.py`, `_services`, `producer.py`, `client.py` | [Build and capture](../../docs/guide/build-and-capture.md)                        |
| Agent workflow             | `skills/notebook-to-static-app`                      | [Agents and automation](../../docs/guide/agents-and-automation.md)                |
| Command-line interface     | `_cli`                                               | [CLI reference](../../docs/reference/cli.md)                                      |

The application scaffold owns local Python and Vite plumbing, package artifact
provenance, and staged directory creation. The generated application owns its
presentation. The skill owns the complete inspect, author, produce, verify,
build, browser, and evidence workflow.

An installed skill pins the generated Python and browser projects to its
marimo-export release. A checkout build vendors the current Python wheel and
npm tarball. Both paths intersect notebook metadata with the package's Python
range and retain the notebook filename plus SHA-256 provenance.

The Python wheel carries the workflow as an
[Agent Plugin](https://agent-plugins.org/) and registers `marimo_export.agent`
in marimo's `marimo.agent.capability` entry-point group.

## Code mode discovers the Python SDK and packaged skill

Marimo reads `marimo.agent.capability` metadata and maps `marimo-export` to
`marimo_export.agent` before importing the provider module. Importing the
module gives a code-mode agent dynamic help built from the active installation.
The help begins with inspection, planning, build, capture, and verification
through the canonical public Python modules.

`agent_plugin()` locates the Agent Plugin installed by the active
`marimo-export` distribution. `agent_skill()` selects its
`notebook-to-static-app` skill. The returned paths identify resources from the
same package version as the imported Python SDK.

`agent.py` owns discovery, resource access, and code-mode guidance. Producer,
reader, repository, inspection, and diagnostic behavior stays in the public
SDK modules used by the CLI and Python applications.

## Package boundaries match public distribution

The uv workspace builds one `marimo-export` wheel and source archive. The pnpm
workspace builds one public `@marimo-team/marimo-export` package. Browser core
implements the root scalar and image loaders plus JSON, text, HTML, and marimo
snapshot subpaths. The workspace-owned portable JSON implementation and private
Arrow, NumPy, Parquet, Vega-Lite, and AnyWidget loaders are bundled behind
public browser subpaths with their required peer dependencies.

Workspace builds and package smoke verify:

- Python root exports and console command
- code-mode capability metadata and the complete packaged Agent Skill
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

| Change                | Focused evidence                  | Complete evidence                                                                  |
| --------------------- | --------------------------------- | ---------------------------------------------------------------------------------- |
| CLI or Python API     | Targeted pytest                   | Wheel import and command smoke                                                     |
| Build, capture, cache | Integration test                  | Live file build and session capture                                                |
| Scaffold              | Source-preserving relocation test | Generated workspace install and build                                              |
| Browser application   | Typecheck and production build    | Separate browser journey for desktop, narrow, rapid transition, and mounted action |
| Documentation         | Prose, links, and docs build      | Search, source view, LLM bundle, rendered browser                                  |
| npm facade or loader  | Package types and tests           | Isolated packed core and loader builds                                             |

[Validation](../validation.md) lists the root commands and required browser
checks.
