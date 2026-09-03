---
title: Serve a prepared publication
description: Connect a mutable application manifest to immutable prepared exports and atomic browser state transitions.
---

# Serve a prepared publication

A prepared publication lets a browser follow changing prepared exports. The
server exposes one mutable manifest route and immutable instance routes. The
browser opens each new instance before the application commits it.

```text
GET /reports/current
  -> prepared manifest
  -> /reports/<export identity>/index.json
  -> /reports/<export identity>/assets/...
```

## Produce the manifest document

The HTTP document uses snake-case fields:

```json
{
  "schema": "marimo-export.prepared.v1",
  "instance": "<notebook export identity>",
  "export_url": "./<notebook export identity>/",
  "inputs": { "days": 30 },
  "state_fingerprint": "<state fingerprint>",
  "refresh_interval_ms": 1000
}
```

`PreparedExport.manifest()` creates this value. `prepared_manifest_bytes()`
serializes it as canonical portable JSON and enforces the 256 KiB manifest
limit.

## Retain current and previous routes

`PreparedPublicationController` owns last-good prepared exports for
application-defined keys. The callback runs in a worker thread and receives the
controller's repository plus a cancellation predicate:

```python
from contextlib import asynccontextmanager

from marimo_export import ExportSpec, prepare
from marimo_export.manifest import prepared_manifest_bytes
from marimo_export.publication import (
    PreparedPublicationCandidate,
    PreparedPublicationController,
)

spec = ExportSpec.from_file("examples/quickstart/report.export.yaml")


def prepare_report(repository, cancelled):
    prepared = prepare(
        "examples/quickstart/report.py",
        spec=spec,
        repository=repository,
        cancelled=cancelled,
    )
    return PreparedPublicationCandidate(prepared, {"report": "sales"})


@asynccontextmanager
async def report_publication():
    controller = PreparedPublicationController[str, dict[str, str]]()
    try:
        await controller.prepare("sales", prepare_report)
        yield controller
    finally:
        await controller.close()


def current_manifest(controller):
    publication = controller.poll("sales")
    if publication is None:
        raise RuntimeError("The sales publication is unavailable")
    return prepared_manifest_bytes(
        publication.manifest(
            f"./{publication.identity}/",
            state="monthly",
            refresh_interval_ms=1_000,
        )
    )
```

Enter `report_publication()` from the server framework's asynchronous lifespan.
Keep that context open while the server handles requests. The yielded controller
supplies the request handlers. Keep the controller and its handlers on one
running `asyncio` event loop. A synchronous worker-thread handler cannot call
`poll()` because polling schedules observation refresh on that loop.

The example is an integration fragment. The server adapter must map the current
and immutable routes, response media types, cache policy, and leased response
lifetime. It must:

1. call `current_manifest(controller)` for every mutable `current` response
2. call `controller.asset("sales", instance, relative)` for an immutable
   instance request
3. keep the returned `PreparedAsset` open until its response finishes
4. let the current-route poll schedule observation-driven refresh. A later
   request sees the replacement after preparation commits it.
5. call `controller.release("sales")` when the application route closes
6. `await controller.close()` during server teardown

Replacing a publication preserves the last good value when preparation fails.
The previous instance remains routeable for the configured grace period so
in-flight browsers can finish their asset requests.

## Open the publication in a browser

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import {
  PreparedPublicationRefresh,
  PreparedStateController,
  type PreparedStatePort,
} from "@marimo-team/marimo-export/prepared";

const manifestUrl = new URL("/reports/current", location.href);
const port: PreparedStatePort = {
  async apply({ next }, signal) {
    const output = await next.state.output("summary").load(jsonLoader(), {
      signal,
    });
    signal.throwIfAborted();
    console.log(output);
  },
};

const controller = new PreparedStateController(port);
const refresh = new PreparedPublicationRefresh(manifestUrl, controller, {
  onError(error) {
    console.error(error);
  },
});

await refresh.start();
await controller.updateInputs({ days: 30 });
```

The browser parser converts snake-case wire fields into camel-case TypeScript
properties. It verifies the manifest identity, export base, complete inputs,
and state fingerprint before returning a `PreparedPublication`.

## Commit one complete visible state

`PreparedStatePort.apply()` owns loading, staged mounting, and the final visible
commit. A newer transition aborts the prior signal and removes its commit
authority. A non-cancellation application failure invokes `restore()` with the
last committed publication. Resolving back to the committed state also invokes
`restore()` so optimistic controls can resynchronize without another apply.

Polling errors call `onError`. An explicit `refresh.refresh()` rejects to its
caller. Dispose the refresh owner before the controller:

```ts
await refresh.dispose();
await controller.dispose();
```

Use the [Python delivery and publication
reference](../reference/python/delivery-and-publications) and [browser
prepared-publication reference](../reference/browser/prepared-publications)
for exact signatures and failure ownership.
