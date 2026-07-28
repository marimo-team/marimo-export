# @marimo-team/marimo-export-loader-anywidget

`anyWidgetLoader()` validates an `anywidget.v1` projection and returns a mountable static model graph.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-loader-anywidget
```

Mounting executes the notebook-authored frontend module. Treat the publication as application code. Embedded modules require `blob:` in the application's `script-src` content security policy. A module with a `data:`, `http:`, or `https:` URL also requires that scheme or origin in `script-src`. Cross-origin modules must allow the application origin through CORS. Captured widget CSS is mounted in a `style` element, so the application's content security policy must permit that style.

Direct HTTP and HTTPS module URLs accept at most 8,192 UTF-8 bytes. The media-type segment of an embedded `data:` URL accepts at most 1,024 UTF-8 bytes. The publication asset limit bounds the complete snapshot.

```ts
import type { PublishedFormat } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";

interface MapState {
  zoom: number;
}

interface MapExports {
  reset(): void;
}

async function mountMap(format: PublishedFormat, host: HTMLElement) {
  const widget = await format.load(anyWidgetLoader<MapState, MapExports>());
  const mounted = await widget.mount(host);
  mounted.model.set("zoom", 8);
  mounted.model.save_changes();
  mounted.exports.reset();
  return mounted;
}
```

`widget.initialState` is a detached snapshot of the root model state. Its object and array containers are frozen. Binary views remain mutable browser values, but their buffers are detached from every mounted model graph. Each `mount()` call owns an isolated model graph, child widget registry, styles, listeners, and module URLs. Dispose the current mount before reusing its host element. Local model changes and composed child views continue to work from the captured graph.

The state and exports type parameters describe notebook-authored values to TypeScript. Validate application-specific fields at runtime when a publication comes from an untrusted producer. Browser module evaluation cannot be cancelled after it starts. If a late module cleanup fails after disposal settles, the runtime reports that failure through `console.error`.

`experimental.invoke()` returns a rejected promise when a frontend module requests a Python callback. A static publication has no Python kernel peer.
