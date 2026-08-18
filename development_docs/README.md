# Contributor guide

marimo-export connects selected marimo notebook executions to one verified
export for human-facing applications, agents, Python automation, browser
clients, and custom consumers. Start from the producer or consumer behavior
that must remain stable, then change the owner of that contract.

## Understand the product boundary

marimo owns notebook execution, the reactive graph, controls, and cache
serialization. marimo-export owns the finite state relation, output
representations, transfer, directory integrity, and typed Python and browser
consumption.

Use the architecture map that owns the change:

| Area                                            | Map                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------ |
| ExportSpec, states, descriptors, writer, reader | [Product model and export format](architecture/product-and-export.md)    |
| Private marimo APIs, execution, cache, transfer | [marimo integration](architecture/marimo-integration.md)                 |
| Browser parsing, loaders, mounts, disposal      | [Browser loaders and mounts](architecture/browser-loaders-and-mounts.md) |
| CLI, skills, example, docs, and packages        | [Agents and delivery](architecture/agents-and-delivery.md)               |

[Architecture](architecture.md) connects the four maps. [Development](development.md)
contains package workflows. [Validation](validation.md) maps changes to
evidence.

## Install the workspace

```bash
make bootstrap
```

The uv workspace owns Python packages and environments. The pnpm workspace
owns browser packages, documentation, and the Vite example. Vite+ owns
TypeScript formatting, linting, types, tests, builds, and task execution.

## Work in one owning slice

| Owner                       | Focused loop                                                                            |
| --------------------------- | --------------------------------------------------------------------------------------- |
| ExportSpec or export format | `uv run pytest packages/python/tests/test_spec.py packages/python/tests/test_export.py` |
| marimo adapter              | `uv run pytest packages/python/tests/test_marimo_compat.py`                             |
| Build or capture            | Managed server, build, client, or state integration test                                |
| Browser core                | `pnpm --filter @marimo-team/marimo-export test`                                         |
| One loader                  | `pnpm --filter @marimo-export/internal-loader-<name> test`                              |
| Skill scaffold              | `uv run pytest skills/notebook-to-static-app/tests`                                     |
| Documentation               | `make docs-build`                                                                       |

Add live producer or browser evidence when the contract crosses a process,
filesystem transaction, remote transport, or mounted document.

## Change a boundary deliberately

1. State the consumer-visible result and the identity that must remain stable.
2. Find the semantic owner in the architecture maps.
3. Cross packages or processes with a stable record, capability, or disposable
   handle.
4. Keep private marimo mechanics in one compatibility adapter.
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
