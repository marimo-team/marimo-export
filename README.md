# marimo-export

Precompute notebook results. Use them anywhere.

marimo-export runs selected notebook states through marimo and packages the
results as one verified export for applications, agents, Python automation,
and custom clients.

Serve the export as static files. Browser applications need no live Python
kernel. Agents can use the same export for grounded answers or bespoke
frontends. A new Python result requires another export or a Python service.

## Use one export in several ways

| Consumer      | What it receives                                                       |
| ------------- | ---------------------------------------------------------------------- |
| Application   | Prepared states, typed outputs, and browser-local interactive values   |
| Agent         | Structured outputs, provenance, state identity, and verification facts |
| Python        | Immutable reader, state resolution, output bytes, and verification     |
| Custom client | Versioned format, media types, and content-addressed assets            |

## Try the market dashboard

This development preview runs from the repository and uses live Yahoo Finance
data:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run dev
```

Open the URL printed by Vite. The five market views switch among prepared
notebook states while charts and the quote explorer remain interactive in the
browser. Network availability can affect the export run.

## Create an export

Choose the notebook states and outputs to include, then build the export:

```bash
marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
```

Use `capture` when a running notebook already owns the configured environment
or completed computation. Both producer paths leave the notebook source
unchanged.

Every selected state and output representation runs through marimo. Reactive
dependencies, controls, native serialization, and cell-cache reuse retain
their marimo semantics.

## Use with agents

Agents can inspect states and outputs, verify the export, read structured
representations, and retain notebook, state, producer, and asset identity.
Include scalars, tables, arrays, or versioned JSON for agent analysis. Keep
images and widgets as companion evidence.

Use the [agent and automation guide](docs/guide/agents-and-automation.md) to
consume an existing export or create a grounded bespoke frontend.

## Build a frontend

The browser package opens an export directly from static hosting. A frontend
can resolve a prepared state and load its outputs with HTML, CSS, TypeScript,
or a frontend framework.

Use the [consumer guide](docs/guide/consume-an-export.md) to choose Python,
browser, agent, or custom-client access. Use the [browser application
guide](docs/guide/browser-applications.md) for loading, mounting, cancellation,
and complete state replacement.

## Scope and trust

- Consumers resolve results already present in the export.
- `build`, `capture`, and notebook inspection execute notebook code with the
  producer environment's file, credential, network, and package access.
- `verify` checks the complete export against its `index.json`.
- Mounting an AnyWidget, Vega-Lite chart, or custom interactive output grants
  that code the browser page's authority.

## Learn more

- [See how notebook exports work](docs/overview.md)
- [Follow the user guide](docs/guide/)
- [Use exports with agents](docs/guide/agents-and-automation.md)
- [Look up the CLI and APIs](docs/reference/)
- [Develop and contribute](development_docs/README.md)

Licensed under Apache-2.0.
