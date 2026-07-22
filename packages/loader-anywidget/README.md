# @marimo-team/marimo-export-anywidget

`anywidget()` validates an `anywidget.v1` projection and returns a mountable static model graph. Loading is inert and works during server-side rendering. Mounting runs the notebook-authored frontend module in a browser.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-anywidget
```

Treat mounted projections as executable application code. Add `blob:` to the application's `script-src` content security policy for embedded modules. A projection that uses a `data:`, `http:`, or `https:` ESM URL also requires that scheme or origin in `script-src`. External modules must permit cross-origin ESM loading.

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { anywidget } from "@marimo-team/marimo-export-anywidget";

interface MapState {
  zoom: number;
}

interface MapExports {
  reset(): void;
}

const published = await openExport(httpSource("/published"));
const output = published.scenario("baseline").output("map", "anywidget");
const widget = await output.load(anywidget<MapState, MapExports>());

const host = document.querySelector<HTMLElement>("#map");
if (host === null) throw new Error("Missing #map mount point");

const mounted = await widget.mount(host);
try {
  mounted.model.set("zoom", 8);
  mounted.model.save_changes();
} finally {
  await mounted.dispose();
}
```

`widget.initialState` exposes the root model state for inspection during server-side rendering. Each `mount()` call owns an isolated model graph, child widget registry, styles, listeners, and module URLs. Call `dispose()` for every successful mount.

The static graph supports local model changes and composed child views. `experimental.invoke()` raises because the exported snapshot has no Python peer.
