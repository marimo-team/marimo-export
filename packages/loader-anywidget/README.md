# AnyWidget loader

Load an exported AnyWidget and mount it with browser-local interaction:

```ts
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";

const widget = await output.load(anyWidgetLoader());
const mounted = await widget.mount(host);

mounted.model.set("metric", "Open");
mounted.model.save_changes();

await mounted.dispose();
```

Install `@anywidget/types` beside `@marimo-team/marimo-export`. Widget changes
stay in the browser and do not call Python.
