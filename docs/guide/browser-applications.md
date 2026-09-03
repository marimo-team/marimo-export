---
title: Build a browser application
description: Open a notebook export, resolve states, load outputs, and replace mounted values safely.
---

# Build a browser application

The browser package reads a notebook export through HTTP. It validates
`index.json` when opening the export, verifies an asset when loading that output,
and gives the application control over rendering and mount disposal.

This guide starts from the [first notebook export](getting-started.md). Copy
`dist/report` into the static files served by your application at
`/export/`.

## Install the browser reader

Install the package in a TypeScript application. [Vite](https://vite.dev/) is
one bundler that can serve the application and its `public` directory.

```bash
pnpm add @marimo-team/marimo-export
```

Add one host and two state buttons to the page:

```html
<main>
  <div aria-label="Report period">
    <button type="button" data-state="weekly">Weekly</button>
    <button type="button" data-state="monthly">Monthly</button>
  </div>
  <pre id="summary" aria-live="polite"></pre>
</main>
```

Open the export and render its JSON output:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/export/");
const summary = document.querySelector<HTMLPreElement>("#summary");

if (summary === null) throw new Error("Summary host is missing");

const show = async (name: string): Promise<void> => {
  const state = notebookExport.state(name);
  const value = await state.output("summary").load(jsonLoader());
  summary.textContent = JSON.stringify(value, null, 2);
};

for (const button of document.querySelectorAll<HTMLButtonElement>("[data-state]")) {
  button.addEventListener("click", () => void show(button.dataset.state ?? "weekly"));
}

await show("weekly");
```

Selecting Monthly renders:

```json
{
  "days": 30,
  "label": "Last 30 days"
}
```

`state(name)` selects an authored state name. `resolve(inputs)` selects an exact
complete input vector. `state.resolve(patch)` applies a sparse patch to the
current vector and selects the matching exported state. Resolution never runs
notebook Python.

## Load several outputs as one transition

An application state can depend on several exported outputs. Give one
`AbortController` ownership of the pending transition, load every output, then
commit them together:

```ts
let pending: AbortController | undefined;

const selectState = async (name: string): Promise<void> => {
  pending?.abort("superseded");
  const current = new AbortController();
  pending = current;

  const state = notebookExport.state(name);
  const value = await state.output("summary").load(jsonLoader(), {
    signal: current.signal,
  });

  current.signal.throwIfAborted();
  summary.textContent = JSON.stringify(value, null, 2);
};
```

An abort signal removes stale work's authority to commit. Some third-party
decoders and browser module evaluations can finish after cancellation. Dispose
any value they created when it settles.

## Mount an interactive output

Some loaders return a value with `mount(element)`. A mount returns an idempotent
disposable handle. After building the [market dashboard](market-dashboard.md),
serve its export at `/market-export/`. Install the Vega-Lite loader's
[`vega-embed`](https://github.com/vega/vega-embed) peer runtime:

```bash
pnpm add vega-embed
```

Add a mount host to the page:

```html
<div id="chart"></div>
```

Load and mount the dashboard's `performance_chart` output:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const marketExport = await openExport("/market-export/");
const state = marketExport.defaultState;
const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Chart host is missing");

const chart = await state.output("performance_chart").load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(host, {
  renderer: "svg",
});

await mounted.dispose();
```

For a complete replacement, mount new values in connected offscreen hosts,
confirm that the transition remains current, replace the visible hosts, then
dispose the previous mount owner. A failed staged mount leaves the last
committed document visible.

Opening, resolving, loading, and verifying operate on inert records. Mounting a
chart, AnyWidget, or custom interactive value grants its code page authority.
Review the [integrity and trust boundary](../concepts/integrity-and-trust.md)
before deployment.

## Follow a changing publication

Use the `prepared` package subpath when a server exposes one mutable manifest
route and immutable export instances. [Serve a prepared
publication](prepared-publications.md) develops the producer and browser sides
of that handoff.

Use the [browser reader reference](../reference/browser/reader.md) for exact
methods and the [loader reference](../reference/browser/loaders.md) for loader
options, result types, peers, cancellation, and disposal.
