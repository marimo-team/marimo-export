# @marimo-team/marimo-export

Load precomputed marimo notebook results in a browser without a live Python
kernel.
Select an exported state, then load its scalars, arrays, tables, charts, images,
widgets, or custom representations.

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
[browser API](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/browser-api.md),
[output representations](https://github.com/marimo-team/marimo-export/blob/main/docs/reference/representations.md),
[browser application guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/browser-applications.md),
and [deployment guide](https://github.com/marimo-team/marimo-export/blob/main/docs/guide/deploy.md).
