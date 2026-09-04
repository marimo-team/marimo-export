---
title: Browser applications
description: Open a notebook export, resolve states, load outputs, and replace mounted values safely.
---

# Browser applications

The browser package reads a notebook export through HTTP. Build the
[deterministic quickstart](getting-started) first. Its `dist/report`
directory contains `weekly` and `monthly` exported states, an inline `summary`,
and an asset-backed rendered `report`.

## Create the Vite application

Install [Node.js](https://nodejs.org/) and [pnpm](https://pnpm.io/), then create
a [Vite](https://vite.dev/) TypeScript application beside `dist/`:

```bash
pnpm create vite browser --template vanilla-ts
cd browser
pnpm install
pnpm add @marimo-team/marimo-export
mkdir -p public/export
cp -R ../dist/report/. public/export/
```

The copy makes the complete notebook export available at `/export/` through
Vite's static-file server. Copy the complete directory so `index.json` and every
declared asset remain together.

Replace `index.html` with two state buttons and hosts for both outputs:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Quickstart report</title>
  </head>
  <body>
    <main id="app">
      <div aria-label="Report period">
        <button type="button" data-state="weekly">Weekly</button>
        <button type="button" data-state="monthly">Monthly</button>
      </div>
      <p id="status" role="status" aria-live="polite">Loading weekly</p>
      <pre id="summary"></pre>
      <pre id="report"></pre>
    </main>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

Replace `src/main.ts` with the browser reader:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";

const notebookExport = await openExport("/export/");
const summaryHost = required<HTMLPreElement>("#summary");
const reportHost = required<HTMLPreElement>("#report");
const status = required<HTMLParagraphElement>("#status");

const show = async (name: "weekly" | "monthly"): Promise<void> => {
  status.textContent = `Loading ${name}`;
  const state = notebookExport.state(name);
  const [summary, report] = await Promise.all([
    state.output("summary").load(jsonLoader()),
    state.output("report").load(marimoOutputLoader()),
  ]);
  if (report.output?.mimetype !== "text/markdown" || typeof report.output.data !== "string") {
    throw new Error("The report output is not rendered Markdown");
  }

  summaryHost.textContent = JSON.stringify(summary, null, 2);
  const parsed = new DOMParser().parseFromString(report.output.data, "text/html");
  reportHost.textContent = parsed.body.textContent?.trim() ?? "";
  status.textContent = `${name} ready`;
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-state]")) {
    button.setAttribute("aria-pressed", String(button.dataset.state === name));
  }
};

for (const button of document.querySelectorAll<HTMLButtonElement>("[data-state]")) {
  button.addEventListener("click", () => {
    const name = button.dataset.state === "monthly" ? "monthly" : "weekly";
    void show(name).catch(showError);
  });
}

function required<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`${selector} is missing`);
  return element;
}

function showError(error: unknown): void {
  console.error(error);
  status.textContent = "The notebook export could not be read";
}

await show("weekly").catch(showError);
```

Start the application:

```bash
pnpm dev --host 127.0.0.1
```

Open the printed loopback URL. The page first shows:

```text
weekly ready
{
  "days": 7,
  "label": "Last 7 days"
}
Last 7 days
Selected window: 7 days
```

Selecting Monthly changes both outputs:

```text
{
  "days": 30,
  "label": "Last 30 days"
}
Last 30 days
Selected window: 30 days
```

`openExport()` validates canonical `index.json`. The JSON loader reads the inline
summary. The rendered-output loader verifies and decodes the selected report
asset as an inert snapshot. The example presents its Markdown as text, so it
does not attach notebook-authored markup to the page.

`state(name)` selects an authored alias. `resolve(inputs)` selects an exact
complete input vector. `state.resolve(patch)` applies a sparse root-input patch
to the current vector and selects the matching exported state. Resolution runs
no notebook Python.

## Cancel a stale state transition

Give one `AbortController` ownership of the pending transition. Check its signal
after loading and immediately before the visible commit:

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
  summaryHost.textContent = JSON.stringify(value, null, 2);
};
```

An abort signal removes stale work's authority to commit. Some third-party
decoders and browser module evaluations can finish after cancellation. Dispose
any value they created when it settles.

## Mount an interactive output

Some loaders return a value with `mount(element)`. A mount returns an idempotent
disposable handle. After building the [market dashboard](market-dashboard),
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

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

Keep the mounted view alive while it is visible. Dispose it during route
teardown or after a replacement commits. For a complete replacement, mount new
values in connected offscreen hosts,
confirm that the transition remains current, replace the visible hosts, then
dispose the previous mount owner. A failed staged mount leaves the last
committed document visible.

The image loader uses the portable filename as alternative text when one is
available. The application must provide a meaningful accessible name or nearby
text when that filename does not describe the image.

Opening, resolving, verifying, and built-in data loaders operate on inert
records. A custom loader executes application-supplied code during `load()`.
Mounting a chart, AnyWidget, or custom interactive value can execute more code
with page authority. Review the [integrity and trust
boundary](../concepts/integrity-and-trust) before deployment.

## Follow a changing publication

Use the `prepared` package subpath when a server exposes one mutable manifest
route and immutable export instances. [Serve a prepared
publication](prepared-publications) develops the producer and browser sides
of that handoff.

Use the [browser reader reference](../reference/browser/reader) for exact
methods and the [loader reference](../reference/browser/loaders) for loader
options, result types, peers, cancellation, and disposal.
