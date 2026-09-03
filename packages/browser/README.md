# @marimo-team/marimo-export

`@marimo-team/marimo-export` opens notebook exports in browser applications. The
Python package or CLI produces the export directory before the browser reads it.

[pnpm](https://pnpm.io/) adds the package to a TypeScript project:

```bash
pnpm add @marimo-team/marimo-export
```

## Open one immutable export

Assume the application serves a notebook export at `/exports/report/` and its
document contains `<h1 id="title"></h1>`:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const title = await notebookExport.defaultState.output("title").load(jsonLoader());

document.querySelector("#title")!.textContent = String(title);
```

`openExport()` validates canonical `index.json`. Output assets remain lazy until
a loader or complete verification requests them. Each load verifies the selected
asset before decoding it.

The reader exposes the export identity, spec SHA-256, default state, notebook and
producer facts, input names, control bindings, output names, aliases, and
normalized states.

## Load and mount an output

The next fragment continues with the `notebookExport` opened in the first
example. That export also contains a `weekly` state and a `chart` output stored
as Vega-Lite. The document contains an element with `id="chart"`.

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const state = notebookExport.state("weekly");
const chart = await state.output("chart").load(vegaLiteLoader());
const host = document.querySelector<HTMLElement>("#chart")!;
const mounted = await chart.mount(host, { renderer: "svg" });

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

Keep the mount alive while it is visible. Dispose it during route teardown or
after a replacement commits.

Install the peer runtime required by each specialized loader. JSON, scalar,
text, HTML, image, NumPy, and marimo snapshot loaders have no peer dependency.
The [output representations reference](https://marimo-team.github.io/marimo-export/reference/representations)
lists current public loader subpaths and peer runtimes.

## Follow a prepared publication

A **prepared publication** combines one immutable notebook export with the state
selected by a `marimo-export.prepared.v1` manifest. Applications use it when a
server may publish a newer export generation or select another exported state.

The following fragment assumes the application serves the manifest at
`/runtime/prepared.json` and provides the same `#title` element as the first
example. The manifest's export contains an `interval` input and a `title`
output:

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import {
  PreparedPublicationRefresh,
  PreparedStateController,
  type PreparedStatePort,
} from "@marimo-team/marimo-export/prepared";

const port: PreparedStatePort = {
  async apply({ next }, signal) {
    const title = await next.state.output("title").load(jsonLoader(), { signal });
    signal.throwIfAborted();
    document.querySelector("#title")!.textContent = String(title);
  },
};

const controller = new PreparedStateController(port);
const manifestUrl = new URL("/runtime/prepared.json", location.href);
const refresh = new PreparedPublicationRefresh(manifestUrl, controller);

await refresh.start();
await controller.updateInputs({ interval: "1wk" });

await refresh.dispose();
await controller.dispose();
```

The `prepared` subpath validates manifests, resolves input changes, routes saved
control bindings, removes superseded transitions' commit authority, refreshes
export instances, and disposes application-owned state. The controller retains
the last committed publication after a failed transition. Implement the port's
optional `restore()` method when the application also needs to restore its DOM
or other application-owned state.

## Apply the browser trust boundary

HTML loaders return verified source text without inserting it into the document.
Mounting charts, widgets, or custom interactive values grants the mounted code
the page's authority. Apply the application's rendering policy,
[Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP), and origin rules before
mounting executable output.

- [Build a browser application](https://marimo-team.github.io/marimo-export/guide/browser-applications)
- [Browser API](https://marimo-team.github.io/marimo-export/reference/browser-api)
- [Output representations](https://marimo-team.github.io/marimo-export/reference/representations)
- [Export format](https://marimo-team.github.io/marimo-export/reference/export-format)
