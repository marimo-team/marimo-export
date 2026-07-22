# Publish AnyWidget outputs

The `anywidget` exporter captures a widget's synchronized model graph, frontend module descriptors, embedded module files, styles, and binary buffers as an `anywidget.v1` projection. The `@marimo-team/marimo-export-anywidget` loader validates that projection and mounts it in a browser after the Python producer stops.

Install the producer extra in the environment that already runs the notebook:

```bash
uv add "marimo-export[anywidget]"
```

Select a raw `anywidget.AnyWidget` or a `mo.ui.anywidget(...)` value in the export plan:

```yaml
schema: marimo-export.plan.v1
outputs:
  counter:
    source: counter
    formats:
      anywidget: {}
```

The `anywidget` exporter accepts no options. Its payload contains the root model and every reachable child model. Each build prepares canonical graph bytes, then a cacheable terminal cell restores the complete projection when those bytes and the tracked notebook lineage match.

## Publish from an existing marimo server

The marimo server owns the prepared Python environment, kernel process, data access, and native cache. Start it through the notebook project, then attach marimo-export by URL.

The repository includes a self-contained [AnyWidget notebook](https://github.com/marimo-team/marimo-export/blob/main/examples/_notebooks/widgets.py) and [export plan](https://github.com/marimo-team/marimo-export/blob/main/examples/_notebooks/widgets.plan.yaml). Install the workspace packages from the repository root:

```bash
pnpm install --frozen-lockfile
```

Start the server in one terminal:

```bash
uv run --package marimo-export --extra anywidget marimo edit \
  examples/_notebooks/widgets.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300 \
  --no-token \
  --no-skew-protection \
  --skip-update-check
```

The relaxed authentication flags are for this loopback workflow. Keep marimo authentication and skew protection enabled when another machine can reach the server.

Build and pull the publication from another terminal:

```bash
pnpm --filter @marimo-team/marimo-export build
node packages/client/dist/cli.mjs publish \
  --server http://127.0.0.1:2718/ \
  --notebook examples/_notebooks/widgets.py \
  --plan examples/_notebooks/widgets.plan.yaml \
  --out /tmp/widgets-export
```

The publication remains readable after the marimo server stops. Use an SSH tunnel or an authenticated HTTPS endpoint when the prepared environment runs on another machine. Set `MARIMO_TOKEN` and `MARIMO_SERVER_TOKEN` in the publishing process when the server requires them. [Remote execution](./remote-execution.md) defines credentials, notebook and session targets, and transfer leases.

## Load and mount

Install the universal reader and AnyWidget loader in the frontend application:

```bash
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-anywidget
```

Loading parses and validates the static graph. Mounting executes the notebook-authored ECMAScript module (ESM) and requires a browser document. Treat the mounted module as trusted application code. Embedded modules require `blob:` in the application's `script-src` content security policy.

Embedded module files and direct `data:` module URLs are carried by the verified projection. An embedded module may import literal `data:`, HTTP, or HTTPS dependencies. Bundle package names, path-relative imports, computed imports, and computed `new URL(..., import.meta.url)` dependencies into the module before export. A direct HTTP or HTTPS module and HTTP dependencies are fetched during `mount()`. The publication verifies each URL string, while each response remains a runtime network input. Use one self-contained embedded module when the widget must remain immutable and available offline.

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { anywidget } from "@marimo-team/marimo-export-anywidget";

interface CounterState {
  count: number;
  label: string;
  payload: DataView;
}

interface CounterExports {
  reset(): void;
}

const published = await openExport(httpSource("/export/"));
const output = published.scenario("baseline").output("raw_counter", "anywidget");
const widget = await output.load(anywidget<CounterState, CounterExports>());

const host = document.querySelector<HTMLElement>("#counter");
if (host === null) throw new Error("Missing #counter mount point");

const mounted = await widget.mount(host);
mounted.model.set("count", mounted.model.get("count") + 1);
mounted.model.save_changes();
mounted.exports.reset();

window.addEventListener("pagehide", () => void mounted.dispose(), {
  once: true,
});
```

`model.get()` reads synchronized state. `model.set()` updates the mount-local model and dispatches change events. `model.save_changes()` clears the mount's local dirty bookkeeping after related `set()` calls. The publication remains immutable, and each new mount starts from its published snapshot. Persist browser edits in the host application when they must survive a remount.

The static graph has no Python peer, so Python trait observers, comm handlers, and notebook recomputation do not run. `experimental.invoke()` rejects.

An AnyWidget `initialize()` function can return an exports object. The loader exposes that object as `mounted.exports`, typed by the second `anywidget<State, Exports>()` parameter. The checked-in counter returns `reset()`, while the composed dashboard returns `rename(title)`.

## Read during server rendering

`output.load(anywidget())` is inert and works in Node, Next.js Server Components, and Astro frontmatter. It validates the payload and exposes a separate root-state snapshot through `initialState`.

```tsx
import { anywidget } from "@marimo-team/marimo-export-anywidget";
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

interface CounterState {
  count: number;
  label: string;
}

export default async function Page() {
  const published = await openExport(directorySource(process.env.MARIMO_EXPORT_DIR!));
  const output = published.scenario("baseline").output("raw_counter", "anywidget");
  const widget = await output.load(anywidget<CounterState>());

  return (
    <p>
      {widget.initialState.label}: {widget.initialState.count}
    </p>
  );
}
```

Render `initialState` on the server. Load the projection from the browser's HTTP publication path in a Client Component, then call `mount()`. Each mount creates an isolated model graph, child registry, style lifecycle, and widget bindings.

## Nested widgets and binary state

The exporter follows the synchronized child references used by AnyWidget and ipywidgets from the selected root. Reachable child models, their module descriptors, embedded module files, styles, and binary buffers join the same projection.

Widget frontend code can compose that graph through the standard host API:

```js
async render({ model, el, host, signal }) {
  const childRef = model.get("child");
  const childModel = await host.getModel(childRef);
  const childWidget = await host.getWidget(childRef);

  await childWidget.render({ el, signal });
  childModel.set("count", childModel.get("count") + 1);
  childModel.save_changes();
}
```

Binary trait values are restored as `DataView` instances at their original state paths. Each mount clones the buffers, so changes in one mounted graph do not affect another mount or `initialState`.

## Mount lifecycle and trust

Call `dispose()` for every successful mount. Disposal aborts the root and child view signals, runs `initialize()` and `render()` cleanup callbacks, removes mounted styles and content, and revokes embedded module URLs. Passing `{ signal }` to `mount()` ties the same lifecycle to an application abort signal. An abort also settles a mount whose browser module evaluation remains pending. A module that finishes later cannot initialize or render into the released mount.

A widget that declares a direct `data:`, `http:`, or `https:` ESM URL loads it during `mount()` and needs that scheme or origin in `script-src`. Cross-origin HTTP modules must return cross-origin resource sharing headers that allow the application origin. Widget `_css` is inserted through a style element and follows the application's `style-src` policy. Marimo `@file` resources referenced by `_css` become data URLs in the verified payload. Bundle other relative CSS resources and virtual stylesheet imports before export. Root-relative, HTTP, or HTTPS resources are fetched at runtime and sit outside the publication's verified payload closure.

Publish and mount widgets from trusted notebook environments. [Trust and integrity](./trust.md) covers active formats, publication integrity, and host policy.
