# @marimo-team/marimo-export

`@marimo-team/marimo-export` reads a static marimo publication over HTTP. It validates `index.json`, verifies each marimo cache asset, decodes its `BlobAsset` envelope, and exposes immutable variants, outputs, and formats.

```sh
pnpm add @marimo-team/marimo-export
```

```ts
import { openPublication } from "@marimo-team/marimo-export";

const publication = await openPublication("/exports/finance/");
const summary = await publication.variant("current").output("summary").format("json").json();
```

### `openPublication(root, options?)`

Fetches and validates `index.json`, then returns an immutable publication snapshot.

| Option          | Contract                                                      |
| --------------- | ------------------------------------------------------------- |
| `loaders`       | Format loaders available through `PublishedFormat.mount()`    |
| `fetch`         | Fetch implementation used for index and cache-object requests |
| `headers`       | Headers copied onto every publication request                 |
| `signal`        | Abort signal for the initial index request                    |
| `maxIndexBytes` | Positive safe-integer index limit, default 16 MiB             |
| `maxAssetBytes` | Positive safe-integer cache-envelope limit, default 64 MiB    |

Format bytes load lazily. Concurrent reads of the same asset share one in-flight request and return independent byte arrays. A caller's signal cancels that caller. The shared request stops after its last waiting caller cancels. Each format read accepts `{ signal, maxBytes, maxJsonValues }`. `maxBytes` is a non-negative safe-integer limit for decoded projection data. `maxJsonValues` is a positive safe-integer projected JSON unit limit used by `json()` and loader-context `json()`. An opened publication remains bound to the index it loaded. A later replacement at the same URL becomes visible after calling `openPublication()` again.

Publication objects have no close step. Dispose every `MountedView` returned by `mount()` when its host leaves the page. Aborting a mount also disposes a view that finishes mounting after cancellation.

Asset digest verification uses `crypto.subtle`. Serve deployed publications through HTTPS or use localhost during development so the browser provides Web Crypto in a secure context.

## Navigation

The reader uses one explicit selection step for each publication dimension:

```ts
const variants = publication.variants();
const outputs = publication.variant("current").outputs();
const formats = publication.variant("current").output("chart").formats();
const chart = publication.variant("current").output("chart").format("vegalite");
```

`filename()`, `bytes()`, `text()`, `json()`, and `blob()` read the decoded projection. Each method verifies the complete cache asset before decoding it. `text()` accepts a media type with no charset or an explicit UTF-8 charset. Read other encodings with `bytes()` and decode them in the application. `json()` reads UTF-8 JSON.

The index and format metadata accept up to 100,000 JSON units and 256 nesting levels. Projected JSON uses the same defaults. Pass `maxJsonValues` to `json()`, `load()`, or `mount()` when a trusted projected document requires a larger unit budget. The count includes containers, scalar values, and object keys.

The raw `BlobAsset` `metadata_json` field accepts at most 262,144 exact bytes. The reader enforces the bound before slicing, UTF-8 decoding, or JSON parsing.

Every read verifies the complete outer envelope before decoding it.

## Format loaders

A `FormatLoader<T>` receives verified bytes and converts one format ID into an application value or mounted view. Format decoding and mounting process format-specific content beyond the generic publication checks. Use loaders with publications whose producer you trust.

`format(name)` selects the label declared by the export specification. `FormatLoader.formatId` matches the stable identifier stored by the Python `Projection`. The loader context exposes that identifier, media type, detached metadata, verified filename, decoded byte size, caller signal, and `bytes()`, `text()`, `json()`, and `blob()` convenience methods. Context methods return defensive values and retain the read's JSON-unit budget.

```ts
import type { FormatLoader, PublishedFormat } from "@marimo-team/marimo-export";

interface Summary {
  readonly label: string;
}

const summaryLoader: FormatLoader<Summary> = {
  formatId: "project.summary.v1",
  async load(format) {
    return format.json((value) => {
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new TypeError("Summary must be a JSON object");
      }
      const label = (value as Record<string, unknown>).label;
      if (typeof label !== "string") throw new TypeError("Summary label must be a string");
      return { label };
    });
  },
};

async function loadSummary(format: PublishedFormat): Promise<Summary> {
  return format.load(summaryLoader);
}
```

Register mounting loaders when opening a publication. `mount()` returns a `MountedView` whose `dispose()` method releases the mounted resources.

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

const publication = await openPublication("/exports/finance/", {
  loaders: [vegaLiteLoader({ actions: false })],
});
const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Missing #chart mount point");

const mounted = await publication.variant("current").output("chart").format("vegalite").mount(host);

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

## Errors

`PublicationError` exposes a stable `code` and optional JSON `details`.

| Code                  | Contract                                                  |
| --------------------- | --------------------------------------------------------- |
| `publication_invalid` | `index.json` violates the publication schema              |
| `asset_invalid`       | A cache envelope is malformed or disagrees with the index |
| `integrity_failed`    | Asset size or digest verification failed                  |
| `read_failed`         | An HTTP publication read failed                           |
| `read_limit_exceeded` | A configured byte limit was exceeded                      |
| `not_found`           | A variant, output, or format name is missing              |
| `loader_unavailable`  | No compatible loader can perform the request              |
| `decode_failed`       | Verified format data failed convenience decoding          |

Invalid roots, limits, and loader registrations raise `TypeError` before a request is sent. Request headers are snapshotted when the publication opens. An aborted operation rejects with the signal's reason. A loader's own decoding or mounting error propagates to its caller.

A `not_found` error reports bounded `details` with `kind`, `name`, `name_truncated`, `available`, `available_count`, and `available_truncated` so an application or agent can recover from a missing selector.
