# @marimo-team/marimo-export-loader-anywidget

`anyWidgetLoader()` validates an AnyWidget `BlobAsset` snapshot and returns an
isolated browser model graph.

```bash
pnpm add @marimo-team/marimo-export \
  @marimo-team/marimo-export-loader-anywidget
```

```ts
import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";

const widget = await output.load(anyWidgetLoader());
const mounted = await widget.mount(host);

mounted.model.set("metric", "Open");
mounted.model.save_changes();

await mounted.dispose();
```

`widget.initialState` is a detached frozen snapshot. Each mount clones the
model graph, binary buffers, child registry, styles, listeners, and module
URLs. Disposal is asynchronous and idempotent.

Mounting executes notebook-authored JavaScript with page authority. Configure
Content Security Policy and CORS for embedded or remote module URLs. Static
models support local change events and frontend exports. A Python callback
request through `experimental.invoke()` rejects.
