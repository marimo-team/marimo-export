---
title: Build a browser application
description: Load immutable exports or drive prepared publications with atomic state transitions and disposal.
---

# Build a browser application

Use browser core for one immutable export. Use the `prepared` subpath when an
application follows a repository-backed publication or routes saved Marimo
control events.

## Load one immutable export

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

```ts
import { openExport } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const notebookExport = await openExport("./export/");
const state = notebookExport.defaultState;

const [rows, chart] = await Promise.all([
  state.output("prices").load(parquetRowsLoader()),
  state.output("chart").load(vegaLiteLoader({ actions: false })),
]);
```

Opening validates `index.json`. Each output load verifies its asset before the
selected loader decodes it.

## Drive a prepared publication

`PreparedStateController` accepts a `PreparedStatePort`. The port owns loading
and committing one complete application state:

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import {
  fetchPreparedExportManifest,
  openPreparedPublication,
  PreparedStateController,
  type PreparedStatePort,
} from "@marimo-team/marimo-export/prepared";

const manifestUrl = new URL("/runtime/prepared.json", location.href);
const manifest = await fetchPreparedExportManifest(manifestUrl);
const publication = await openPreparedPublication(manifest, manifestUrl);

const port: PreparedStatePort = {
  async apply({ next }, signal) {
    const title = await next.state.output("title").load(jsonLoader(), { signal });
    signal.throwIfAborted();
    document.querySelector("#title")!.textContent = String(title);
  },
};

const controller = new PreparedStateController(port);
await controller.start(publication);
await controller.updateInputs({ interval: "1wk" });
```

For a view with several outputs, `apply` should load and mount every required
output in staging hosts, confirm the signal remains active, then commit the
complete replacement. A rejected transition invokes the optional `restore`
method with the last committed publication.

## Route controls and URL queries

The export's `controlBindings` maps projection-scoped object IDs to semantic
input paths. Route an accepted frontend value through the controller:

```ts
const handled = await controller.updateControl("cell-region", "Northeast");
```

`handled` is false when the object ID has no exported binding. Query-driven
applications can call `updateQuery(location.search)` to resolve a complete saved
state from URL parameters.

## Refresh the publication

`PreparedPublicationRefresh` fetches the manifest, reuses an already opened
immutable export when its identity matches, and replaces the controller's
publication atomically:

```ts
import { PreparedPublicationRefresh } from "@marimo-team/marimo-export/prepared";

const liveController = new PreparedStateController(port);
const refresh = new PreparedPublicationRefresh(manifestUrl, liveController, {
  onError: console.error,
});

await refresh.start();
```

`refresh_interval_ms` in the manifest enables polling from 250 through 60,000
milliseconds. A value of zero disables polling. Call `refresh.refresh()` for an
explicit update. `syncPolling()` reschedules polling from the current manifest.

## Dispose the complete lifecycle

```ts
await refresh.dispose();
await liveController.dispose();
```

Mounted output values return their own idempotent disposal handles:

```ts
const mounted = await chart.mount(document.querySelector("#chart")!);
await mounted.dispose();
```

Dispose staged work after failure and committed mounts after replacement or page
teardown. AnyWidget definitions use page-lifetime module ownership, while each
mount owns its model and view state.

## Review executable browser code

Opening, resolving, loading, and verifying parse inert records. Mounting
AnyWidget, Vega-Lite, or custom interactive output grants that code the page's
authority. Review mounted modules and configure Content Security Policy, allowed
origins, byte limits, cancellation, and teardown for the deployment.

Use the [Browser API](../reference/browser-api.md) for exact core and prepared
subpath contracts.
