# @marimo-team/marimo-export

Open verified Marimo notebook exports and drive prepared state transitions in a
browser application.

```bash
pnpm add @marimo-team/marimo-export
```

## Open one immutable export

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const title = await notebookExport.defaultState.output("title").load(jsonLoader());

document.querySelector("#title")!.textContent = String(title);
```

`openExport()` validates canonical `index.json`. Output assets remain lazy until
one loader or complete verification requests them. The reader exposes the export
identity, spec SHA-256, default state, notebook and producer facts, input names,
control bindings, output names, aliases, and normalized states.

Load projection records and interactive values from public loader subpaths:

```ts
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const state = notebookExport.state("weekly");
const report = await state.output("report").load(marimoOutputLoader());
const chart = await state.output("chart").load(vegaLiteLoader());
const mounted = await chart.mount(document.querySelector("#chart")!);

await mounted.dispose();
```

Install the peer runtime required by each specialized loader. Text, HTML, JSON,
scalar, image, NumPy, and Marimo snapshot loaders need no peer dependency.

Apply the application's rendering and trust policy before inserting verified
HTML into the document. Mounting charts, widgets, or custom interactive values
grants that code page authority.

## Drive a prepared publication

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

const state = new PreparedStateController(port);
const manifestUrl = new URL("/runtime/prepared.json", location.href);
const refresh = new PreparedPublicationRefresh(manifestUrl, state);

await refresh.start();
await state.updateInputs({ interval: "1wk" });

await refresh.dispose();
await state.dispose();
```

The `prepared` subpath validates manifests, opens immutable exports, resolves
semantic input changes, routes saved control bindings, cancels stale work,
restores the last committed publication after failure, refreshes generations,
and disposes application-owned state.

See the [browser API](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/browser-api.md),
[output representations](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/representations.md),
and [browser application guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/browser-applications.md).
