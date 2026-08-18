---
title: Build a browser application
description: Create a purpose-specific frontend from prepared notebook results with explicit loading, mounting, cancellation, and disposal.
---

# Build a browser application

Build a frontend from prepared notebook results without translating the
notebook's Python computation into JavaScript. The application selects exported
states and presents their stored representations with HTML, CSS, TypeScript, or
a frontend framework.

## Install the consumer and loader runtimes

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Install the optional peers for the representations the application loads.
[Output representations](../reference/representations.md) lists the complete
mapping.

## Open and load one prepared state

```ts
import { openExport } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const notebookExport = await openExport("./export/");
const state = notebookExport.state("baseline");

const [rows, chart] = await Promise.all([
  state.output("prices").load(parquetRowsLoader()),
  state.output("chart").load(vegaLiteLoader({ actions: false })),
]);
```

Opening validates `index.json`. Each output load verifies its asset before the
representation runtime decodes it.

## Give one owner to each state transition

A state change has two lifetimes:

1. The load generation owns fetch and decoding until a newer request aborts it.
2. The committed mount owner remains active until a complete replacement is
   ready.

Use this transition order:

1. Abort stale loads.
2. Load every output required by the selected state.
3. Create connected, offscreen staging hosts for interactive values.
4. Mount charts, images, or widgets with a separate mount controller.
5. Confirm that the selected generation is still current.
6. Commit all new hosts and data.
7. Abort and dispose the previous mount owner.

A failed load or mount should dispose staged work and leave the last complete
view visible.

## Dispose mounted representations

```ts
const mounted = await chart.mount(document.querySelector("#chart")!);

// Replace or tear down the view.
await mounted.dispose();
```

Mount handles own their DOM nodes, listeners, object URLs, renderer resources,
widget models, modules, styles, and cleanup callbacks. Disposal is idempotent.

## Review executable browser code

Opening, resolving, loading, and verifying execute no notebook-authored browser
module. Mounting an AnyWidget, Vega-Lite chart, or custom interactive
representation grants that code the page's authority.

Review mounted modules and configure Content Security Policy, allowed origins,
load limits, cancellation, and teardown for the deployment environment.

The [market dashboard source](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/src/main.ts)
implements a complete transition across Parquet data, a custom summary,
Vega-Lite, an image, and AnyWidget.

Use the [Browser API](../reference/browser-api.md) for exact types and methods.
