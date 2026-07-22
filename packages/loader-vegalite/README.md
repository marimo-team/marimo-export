# @marimo-team/marimo-export-vegalite

`vegaLite()` loads a verified `vegalite.v1` specification and mounts it with Vega-Embed.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-vegalite
```

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { vegaLite } from "@marimo-team/marimo-export-vegalite";

const published = await openExport(httpSource("/published"));
const output = published.scenario("baseline").output("chart", "vegalite");
const chart = await output.load(vegaLite({ actions: false }));

const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Missing #chart mount point");

const mounted = await chart.mount(host, { renderer: "svg" });
window.addEventListener("pagehide", () => mounted.finalize(), { once: true });
```

`chart.spec` contains the parsed JSON specification. Reading the specification works in browser, Node, and server-rendered code. `chart.mount()` imports Vega-Embed on demand and requires a browser DOM.

The producer derives the output media type from the major version in an official Vega-Lite `$schema` URL. For example, a v6 specification uses `application/vnd.vegalite.v6+json`. The loader selects the stable `vegalite.v1` format ID across Vega-Lite schema majors.

Call `finalize()` for every successful mount. Vega-Embed uses it to release event listeners and view resources.
