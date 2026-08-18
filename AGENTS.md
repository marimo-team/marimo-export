# AGENTS.md

Guidance for coding agents working in this uv, pnpm, and Vite+ workspace.
marimo-export runs selected marimo notebook states and writes one verified
export for applications, agents, Python, and custom clients.

## Commands

Use Python 3.11 or newer, Node 22.18, pnpm 11.15.1, uv, and Vite+.

| Task                | Command           | Expected result                                            |
| ------------------- | ----------------- | ---------------------------------------------------------- |
| Install             | `make bootstrap`  | Locked Python and TypeScript workspaces sync               |
| Format              | `make format`     | Authored source is formatted                               |
| Lint                | `make lint`       | Python and TypeScript boundaries pass                      |
| Type-check          | `make typecheck`  | Python and TypeScript types pass                           |
| Test                | `make test`       | Python, browser, loader, skill, and app tests pass         |
| Build               | `make build`      | Python, npm, docs, and example packages build              |
| Build docs          | `make docs-build` | VitePress site and LLM text bundles build                  |
| Serve docs          | `make docs-serve` | Documentation runs at `127.0.0.1:4173`                     |
| Complete local gate | `make check`      | Format, lint, types, tests, builds, and package smoke pass |

Run focused package commands while developing, then finish with `make check`.

## Architecture in seven rules

1. marimo owns notebook parsing, reactive execution, dependency pruning, cell
   hashing, cache persistence, UI updates, and native serialization.
2. marimo-export owns ExportSpec normalization, output representation,
   transfer, export integrity, and typed Python and browser consumption.
3. Stable Python policy depends on records and ports under `_execution` and
   `_marimo`. Private `marimo._*` imports stay under `_marimo/compat`.
4. `build` owns a temporary notebook copy, loopback server, session, process
   groups, and cleanup. `capture` borrows one active edit session.
5. `packages/browser` owns export parsing, integrity, immutable readers, and
   loader contracts. Each `packages/loader-*` owns one representation runtime.
6. Python, browser, agent, and custom clients consume one durable export
   contract. Browser applications load a complete state before commit, and
   every interactive mount returns an idempotent disposable handle.
7. `docs/` owns user workflows and reference. `development_docs/` owns code
   ownership, lifecycle, compatibility seams, and contributor validation.

Read [Architecture](development_docs/architecture.md) before changing an
ownership or lifecycle boundary.

## Dependency rule

Dependencies point from policy toward stable records, then composition roots,
then private adapters:

```text
Python API and CLI -> product records -> marimo ports -> compat adapters

Browser app -> browser core -> one loader facade -> one loader runtime
```

- `_marimo/composition.py`, `_marimo/anywidget.py`, `_marimo/blob.py`, and
  `_marimo/entrypoints.py` are composition roots.
- `_remote` owns HTTP, authentication, scratchpad transport, server-sent
  events, and managed process ownership.
- Browser core imports no specialized chart, table, array, or widget runtime.
- Loader packages import browser contracts and their own runtime dependency.
  They do not import one another.
- Add dependencies to the smallest workspace member that imports them.

Ruff and repository boundary tests enforce private marimo containment. Vite+
enforces browser and loader direction.

## Product language

marimo-export creates a **notebook export**. Use **export** after defining the
noun once.

| Action                                 | Verb            |
| -------------------------------------- | --------------- |
| Create from a notebook file            | build           |
| Create from a running session          | capture         |
| Read in Python or TypeScript           | open            |
| Select a state by name or inputs       | resolve         |
| Decode an output for browser use       | load            |
| Attach an interactive value to the DOM | mount           |
| Put static files on a host             | deploy or serve |
| Release a package to a registry        | publish         |

A state is one complete assignment for the ExportSpec inputs. Authors may
write sparse rows because one captured baseline supplies omitted values. An
output is one published name and representation for every state. An asset is a
content-addressed file referenced by an output descriptor. A consumer is a
Python reader, browser reader, agent, or another implementation of the export
format.

## Change routing

| Change                            | Primary owner                                              | Required companions                                         |
| --------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------- |
| ExportSpec or state normalization | `spec.py`, `_execution/plan.py`                            | YAML, JSON, programmatic, and live-state tests              |
| Export format or local reader     | `export.py`, `reader.py`, `_writer.py`                     | Browser schema and canonical fixtures                       |
| marimo integration or cache       | `_marimo/capabilities.py`, composition, one compat adapter | Probe, adapter, build, and capture tests                    |
| Managed process lifecycle         | `_build.py`, `_remote/managed.py`, managed entry points    | Startup, shutdown, source, and descendant tests             |
| Browser reader or loader contract | `packages/browser`                                         | TypeScript and cross-language tests                         |
| One output representation         | `packages/loader-*` and exporter runtime                   | Peer dependency, malformed input, abort, and disposal tests |
| CLI or public Python API          | `cli.py`, package root, public records                     | Human output, JSON, exit, and wheel smoke                   |
| Example or browser transition     | `examples/vite-vanilla`                                    | Typecheck, build, desktop, and narrow browser proof         |
| Public documentation              | `docs/`, VitePress config                                  | Examples, links, search, LLM bundles, and rendered proof    |

## Core invariants

- Every output runs through one transient marimo leaf for every state.
- State vectors are complete and fingerprinted before execution.
- Ordinary overrides and UI updates stay local to one child state run.
- AnyWidget patches record the complete serializer-owned model state.
- The authored notebook source remains byte-for-byte unchanged.
- The client and attached kernel use the same marimo-export version and source
  identity.
- One failed state, output, transfer, verification, or cleanup fails the
  complete producer operation.
- `index.json` is canonical UTF-8 JSON and the single export entry point.
- Readers verify the declared asset closure before decoding output data.
- Opening and verification execute no notebook-authored browser module.
  Mounting an interactive value grants it page authority.

## Validation

- Test through public APIs, command results, files, protocol records, package
  imports, or browser state. Avoid assertions on private helper trivia.
- Run live build and capture evidence after changing marimo integration,
  process ownership, state execution, cache behavior, or transfer.
- Use browser inspection for layout, responsive behavior, rapid state changes,
  charts, AnyWidgets, cancellation, and mount disposal.
- Rebuild generated package and documentation output from its owning source.
  Do not commit raw build directories.

## Reference

- [Contributor guide](development_docs/README.md)
- [Architecture](development_docs/architecture.md)
- [Product model and export format](development_docs/architecture/product-and-export.md)
- [marimo integration](development_docs/architecture/marimo-integration.md)
- [Browser loaders and mounts](development_docs/architecture/browser-loaders-and-mounts.md)
- [Agents and delivery](development_docs/architecture/agents-and-delivery.md)
- [Development](development_docs/development.md)
- [Validation](development_docs/validation.md)
