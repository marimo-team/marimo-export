# Contributor guide

marimo-export connects selected marimo notebook executions to one verified
export for human-facing applications, agents, Python automation, browser
clients, and custom consumers. Start from the producer or consumer behavior
that must remain stable, then change the code that owns that contract.

## Understand the product boundary

marimo owns notebook execution, the reactive graph, controls, and native
computation caching. marimo-export owns observations, the state-output relation,
prepared-state reuse, repository coordination, output representations,
directory integrity, and typed Python and browser consumption.

Use the architecture map that owns the change:

| Area                                                             | Map                                                                                          |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| StateSpace, ExportSpec, writer, and reader                       | [Product model and export format](architecture/product-and-export.md)                        |
| State planning, preparation, progress                            | [Planning and preparation](architecture/preparation.md)                                      |
| SQLite, artifacts, leases, fencing, retention                    | [Export repository](architecture/repository.md)                                              |
| Public records, services, ports, composition                     | [Ports and composition](architecture/ports.md)                                               |
| marimo cache ownership and contained adaptation                  | [Execution and caching](architecture/execution-and-caching.md)                               |
| Child execution, projections, transfer                           | [marimo integration](architecture/marimo-integration.md)                                     |
| Browser parsing, exported states, mounts                         | [Browser loaders and mounts](architecture/browser-loaders-and-mounts.md)                     |
| Live HTTP, authentication, server-sent events, process ownership | [Live transport and processes](architecture/live-transport-and-processes.md)                 |
| Prepared routes and application directory commits                | [Application publication and delivery](architecture/application-publication-and-delivery.md) |
| Hash scopes, schemas, and versioned records                      | [Identities and protocols](architecture/identities-and-protocols.md)                         |
| Cross-language JSON values and parsing                           | [Portable JSON](architecture/portable-json.md)                                               |
| Live server, WebAssembly, and prepared execution                 | [Runtime profiles](architecture/runtime-profiles.md)                                         |
| CLI, skills, examples, docs, and packages                        | [Product surfaces and distribution](architecture/agents-and-delivery.md)                     |
| Cross-cutting failure and concurrency                            | [Failure and concurrency](architecture/failure-and-concurrency.md)                           |

[Architecture](architecture.md) connects these ownership maps.
[Development](development.md) contains package workflows.
[Validation](validation.md) maps changes to evidence.
[Releasing](releasing.md) builds and publishes the coordinated Python and npm
packages.
[Documentation](documentation.md) defines the public learning spine, route
manifest, source owners, and rendered validation contract.

Proposed integrations and upstream APIs live under
[Proposals](proposals/README.md).
They are design targets, not current architecture or validation requirements.

## Install the workspace

```bash
make bootstrap
```

The uv workspace owns Python packages and environments. The pnpm workspace owns
browser packages, documentation, and the Vite example.
[Vite+](https://viteplus.dev/guide) is the unified TypeScript toolchain for
formatting, linting, type checks, tests, builds, packaging, and workspace tasks.

## Work in one owning slice

| Owner                                    | Focused loop                                                                                                                                                               |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| StateSpace, ExportSpec, or export format | `uv run pytest packages/python/tests/test_state_space.py packages/python/tests/test_spec.py packages/python/tests/test_protocol_fixtures.py`                               |
| Public API and dependency direction      | `uv run pytest packages/python/tests/test_public_modules.py packages/python/tests/test_boundaries.py`                                                                      |
| Observations                             | `uv run pytest packages/python/tests/test_observations.py packages/python/tests/test_marimo_observations.py`                                                               |
| Planning and preparation                 | `uv run pytest packages/python/tests/test_planning.py packages/python/tests/test_preparation_*.py packages/python/tests/test_prepared_*.py`                                |
| Export repository                        | `uv run pytest packages/python/tests/test_repository*.py`                                                                                                                  |
| marimo cache adapter                     | `uv run pytest packages/python/tests/test_marimo_cache_*.py packages/python/tests/test_marimo_{child_run,exporter_compat,projection_recording,receipts,runtime_compat}.py` |
| Managed or borrowed producer             | Managed server, producer, build, client, or state integration test                                                                                                         |
| Live transport and authentication        | `uv run pytest packages/python/tests/test_{client,remote_auth,remote_client,remote_sse}.py`                                                                                |
| Application publication and delivery     | `uv run pytest packages/python/tests/test_{manifest,publication,delivery,directory_security}.py`                                                                           |
| Browser prepared controller              | `pnpm --filter @marimo-team/marimo-export test -- prepared`                                                                                                                |
| Browser core                             | `pnpm --filter @marimo-team/marimo-export test`                                                                                                                            |
| One loader                               | `pnpm --filter @marimo-export/internal-loader-<name> test`                                                                                                                 |
| Skill scaffold                           | `uv run pytest skills/notebook-to-static-app/tests`                                                                                                                        |
| Documentation                            | `node apps/docs/scripts/check-navigation.ts && make docs-build`                                                                                                            |

Add live producer or browser evidence when the contract crosses a process,
filesystem transaction, remote transport, or mounted document.

## Change a boundary deliberately

1. State the consumer-visible result and the identity that must remain stable.
2. Find the semantic owner in the architecture maps.
3. Cross packages or processes with a stable record, capability, or disposable
   handle.
4. Keep SQL under `_repository/sqlite` and private marimo mechanics under
   `_marimo/compat`.
5. Update producers, consumers, diagnostics, docs, and focused tests together
   when a boundary shape changes.
6. Validate through the command, export directory, package, or browser state
   that consumes the result.

## Keep source and generated output distinct

Edit source under `packages/`, `apps/`, `docs/`, `development_docs/`,
`examples/`, `scripts/`, and `skills/`. Package `dist/` directories,
VitePress output, caches, and example exports are generated. Rebuild them
through repository commands and keep them untracked.

## Finish at the consumer boundary

```bash
make check
```

`make check` runs formatting, linting, types, tests, builds, packed npm
installation, and isolated Python wheel smoke. Use agent-browser for rendered
layout, navigation, responsive behavior, rapid transitions, charts, and
widgets.

Public workflows live under `docs/guide/`. Exact user contracts live under
`docs/reference/`. Internal ownership and lifecycle stay in
`development_docs/`.
