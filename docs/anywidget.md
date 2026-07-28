# Publish AnyWidget outputs

The `anywidget` exporter captures a selected widget's static model graph as an `anywidget.v1` projection. The browser loader restores local model interaction and mounts the notebook-authored frontend module after the Python kernel stops.

Install the exporter and loader in their owning environments:

```bash
uv sync --all-extras --locked
pnpm add \
  @marimo-team/marimo-export \
  @marimo-team/marimo-export-loader-anywidget
```

Select a live AnyWidget value in the export specification:

```yaml
schema: marimo-export.spec.v1

outputs:
  counter:
    source: counter
    formats:
      anywidget: {}
```

The notebook must have produced `counter` in the running kernel before capture.

## Load and mount

Create the loader, then pass it to the selected format:

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";

interface CounterState {
  count: number;
  label: string;
}

const loader = anyWidgetLoader<CounterState>();
const publication = await openPublication("/exports/widgets/");

const counter = publication.variant("current").output("counter").format("anywidget");

const host = document.querySelector<HTMLElement>("#counter");
if (host === null) throw new Error("Missing #counter mount point");

const widget = await counter.load(loader);
const mounted = await widget.mount(host);
mounted.model.set("count", mounted.model.get("count") + 1);
mounted.model.save_changes();

window.addEventListener("pagehide", () => void mounted.dispose(), {
  once: true,
});
```

`CounterState` is a caller-supplied TypeScript shape. Validate application-specific state at runtime when the publication comes from an untrusted producer.

`widget.initialState` is a detached snapshot of the published root state. Its object and array containers are frozen. Binary views remain mutable browser values, but their buffers are detached from every mounted model graph.

`model.set()` updates the mount-local model and dispatches change events. `model.save_changes()` clears local dirty bookkeeping. Each mount starts from the published snapshot. Store browser changes in the host application when they must survive a remount.

The static graph has no Python peer. Python trait observers, comm handlers, and notebook recomputation cannot run after publication. A frontend call to `experimental.invoke()` returns a rejected promise.

## Captured graph

The projection contains:

- The selected root model.
- Reachable child models.
- Synchronized state and binary buffers.
- Frontend module descriptors.
- Embedded module files and widget styles.

Runtime model IDs are canonicalized before the projector caches the `BlobAsset`. Equivalent graphs therefore produce stable portable bytes even when the live kernel assigned different UUIDs.

## Browser trust

Loading validates the static graph and exposes its initial root state. Mounting imports and executes the notebook-authored ECMAScript module.

Embedded modules use `blob:` URLs and require that scheme in `script-src`. Direct HTTP or HTTPS module dependencies are fetched during mounting and require an allowed origin plus cross-origin resource sharing headers. Widget styles follow the application's `style-src` policy.

Direct HTTP and HTTPS module URLs accept at most 8,192 UTF-8 bytes. The media-type segment of an embedded `data:` URL accepts at most 1,024 UTF-8 bytes. The publication asset limit bounds the complete captured graph and its embedded bodies.

Call `dispose()` for every successful mount. Disposal aborts root and child view signals, runs cleanup callbacks, removes mounted styles and content, and revokes embedded module URLs.

Publish and mount widgets from notebook environments trusted by the host application. [Trust and integrity](./trust.md) defines the verification and executable-content boundaries.
