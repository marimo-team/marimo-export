# @marimo-team/marimo-export

Load prepared marimo notebook results into an app with no Python runtime after
deployment.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openExport, scalarLoader } from "@marimo-team/marimo-export";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const title = await state.output("title").load(scalarLoader());

document.querySelector("#title")!.textContent = String(title);
```

Charts, tables, arrays, and widgets use loader subpaths:

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await state.output("chart").load(vegaLiteLoader());
const mounted = await chart.mount(document.querySelector("#chart")!);

await mounted.dispose();
```

See the
[browser API](https://github.com/marimo-team/marimo-export/blob/main/docs/browser-api.md),
[output formats](https://github.com/marimo-team/marimo-export/blob/main/docs/representations.md),
and [deployment guide](https://github.com/marimo-team/marimo-export/blob/main/docs/trust.md).
