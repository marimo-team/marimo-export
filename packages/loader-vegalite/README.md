# @marimo-team/marimo-export-loader-vegalite

`vegaLiteLoader()` loads a `vegalite.v1` specification and mounts it with Vega-Embed.

The publication reader verifies the indexed cache asset before Vega-Lite parses it. Use this loader with a publication whose producer you trust.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-loader-vegalite
```

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

const loader = vegaLiteLoader({ actions: false });
const publication = await openPublication("/exports/finance/", { loaders: [loader] });
const format = publication.variant("current").output("chart").format("vegalite");

const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Missing #chart mount point");

const mounted = await format.mount(host);
window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

Use `format.load(loader)` to inspect the parsed specification or apply mount-specific options:

```ts
const chart = await format.load(loader);
console.log(chart.spec);

const mounted = await chart.mount(host, { renderer: "svg" });
await mounted.dispose();
```

The producer derives the media type from the major version in the Vega-Lite `$schema` URL. The loader uses `vegalite.v1` as the stable representation ID across Vega-Lite schema majors.

Vega specifications can load data, images, and other resources from external URLs. Apply the same origin and content security policy checks you use for application-authored Vega specifications. Pass an `AbortSignal` to `chart.mount(host, { signal })` when the caller needs cancellable mounting.

Browser embedding cannot be cancelled after Vega-Embed starts its own work. A cancelled mount settles immediately, then finalizes a result that arrives later. If that late finalization fails, the loader reports the failure through `console.error`.
