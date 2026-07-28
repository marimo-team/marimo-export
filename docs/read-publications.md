# Read publications

A publication contains `index.json` and the marimo cache objects selected by that index. Python and browser readers expose the same navigation model:

```text
Publication
  -> variant(name)
      -> output(name)
          -> format(name)
```

Each read verifies the stored `BlobAsset` envelope before decoding it.

## Python

```python
from marimo_export import open_publication

publication = open_publication("dist/finance")
description = publication.describe()
summary = (
    publication
    .variant("current")
    .output("summary")
    .format("json")
    .json()
)
```

`publication.describe()` returns detached notebook, producer, variant, control, output, and format metadata. Cache keys and integrity records remain inside the reader.

`variant.controls` contains the control names declared anywhere in the export specification. Each variant records its applied value for that set, including the starting value when the variant did not override a declared control.

List the publication surface before selecting a value:

```python
for variant_name in publication.variant_names:
    variant = publication.variant(variant_name)
    print(variant.name, variant.controls)
    for output_name in variant.outputs:
        output = variant.output(output_name)
        print(output.name, output.formats)
```

`PublishedFormat` provides byte, text, and JSON operations. Python reads the optional portable base filename through the `filename` property. The browser exposes the same value through `filename()`. Python and browser `text()` accept a media type with no charset or an explicit UTF-8 charset. Read other encodings with `bytes()` and decode them in the consuming application. Browser loader-context `text()` follows the same rule.

Python `json(max_values=100000)` accepts a positive safe-integer bound for the selected projected document. Browser `json({ maxJsonValues: 100000 })`, `load()`, and `mount()` accept the corresponding read option.

`open_publication()` bounds `index.json` at 16 MiB, each outer cache asset at 64 MiB, and the complete index-plus-unique-asset closure at 512 MiB by default. It checks the complete declared closure before reading an asset. Pass larger positive limits when a trusted publication intentionally exceeds them:

```python
publication = open_publication(
    "dist/finance",
    max_index_bytes=32 * 1024 * 1024,
    max_asset_bytes=1024 * 1024 * 1024,
    max_publication_bytes=4 * 1024 * 1024 * 1024,
)
```

The index and format metadata accept up to 100,000 JSON units and 256 nesting levels. Projected JSON uses the same defaults. Python callers can pass `max_values` to `PublishedFormat.json()`. Browser callers can pass `maxJsonValues` to `json()`, `load()`, or `mount()`. The count includes containers, scalar values, and object keys.

The raw `BlobAsset` `metadata_json` field accepts at most 262,144 exact bytes. Readers enforce the bound before slicing, UTF-8 decoding, or JSON parsing.

Call `publication.verify()` to read and verify every unique asset referenced by the index.

On Windows, keep the publication directory tree unchanged until the Python reader completes its second file-identity check. The reader uses path-based file opens on that platform, rejects reparse points, and fails when those validation checks detect a changed path.

## Browser

Install the browser reader:

```bash
pnpm add @marimo-team/marimo-export
```

Serve the publication through HTTP and open its directory URL:

```ts
import { openPublication } from "@marimo-team/marimo-export";

const publication = await openPublication("/exports/finance/");
const summary = await publication.variant("current").output("summary").format("json").json();
```

`openPublication()` accepts an absolute HTTP or HTTPS root or a browser-relative root. The root identifies a directory and must end without a query, fragment, or embedded credentials. Options provide registered `loaders`, a custom `fetch`, request `headers`, an initial-load `signal`, `maxIndexBytes`, and `maxAssetBytes`.

Browser objects are immutable. `openPublication()` limits `index.json` to 16 MiB and each outer cache asset to 64 MiB by default. `maxIndexBytes` and `maxAssetBytes` accept positive safe integers and are validated before the first request. Configure them when a trusted publication needs larger objects. A format read accepts an abort signal and a non-negative safe-integer `maxBytes` limit for the decoded inner data. The source bounds the HTTP body, then the reader verifies the envelope size and SHA-256 before MessagePack decoding.

`openPublication()` resolves after the index loads and validates. Format assets load lazily. Concurrent reads of the same asset share one in-flight request and return independent byte arrays. A caller's signal cancels that caller. The shared request stops after its last waiting caller cancels. An opened publication remains bound to that index. Open the URL again to observe a later publication replacement.

Publication objects have no close step. Dispose every `MountedView` when its host leaves the page. An abort signal cancels an initial load, format read, or mount at the boundary where it is passed.

SHA-256 verification uses the browser Web Crypto API. Serve deployed publications through HTTPS or use localhost during development so `crypto.subtle` is available in a secure context.

## Core format methods

```ts
const variant = publication.variant("current");

const jsonValue = await variant.output("summary").format("json").json();
const htmlValue = await variant.output("market_note").format("html").text();
const pngBlob = await variant.output("chart").format("png").blob();
const vegaLiteBytes = await variant.output("chart").format("vegalite").bytes();
```

The reader confirms that the decoded `BlobAsset` media type, format identifier, and metadata match the index. It validates the envelope filename as a portable base name. Convenience methods consume the inner `data` bytes.

Arrow and Parquet projections are portable file representations exposed through `bytes()` and `blob()`. An application that owns a bounded decoder can wrap it in a custom `FormatLoader` for a trusted publication.

## Format loaders

Dedicated packages own decoders and renderers for larger format dependencies. A loader receives bytes after publication integrity verification, then parses or executes format-specific content. Use loaders with publications whose producer you trust. Register mounting loaders when opening the publication:

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

const publication = await openPublication("/exports/finance/", {
  loaders: [vegaLiteLoader()],
});

const chart = publication.variant("aapl").output("chart").format("vegalite");

const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Missing #chart mount point");

const mounted = await chart.mount(host);
window.addEventListener("pagehide", () => mounted.dispose(), { once: true });
```

`format(name)` selects the label declared under `formats` in the export specification. A loader's `formatId` matches the stored projection identifier, such as `vegalite.v1`.

Mounting crosses an executable-content boundary for loaders such as Vega-Lite and AnyWidget. Configure the host application's network and content security policy for the selected loader.

## Custom loaders

A custom loader matches the Python `Projection.format_id` and receives verified inner bytes:

```ts
import type { FormatLoader, PublishedFormat } from "@marimo-team/marimo-export";

interface GeoJSON {
  readonly type: "FeatureCollection";
  readonly features: readonly unknown[];
}

const geojsonLoader: FormatLoader<GeoJSON> = {
  formatId: "geojson.v1",
  async load(context) {
    const value = await context.json();
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new TypeError("GeoJSON projection has an invalid shape");
    }
    const record = value as Record<string, unknown>;
    if (record.type !== "FeatureCollection" || !Array.isArray(record.features)) {
      throw new TypeError("GeoJSON projection has an invalid shape");
    }
    return record as unknown as GeoJSON;
  },
};
```

Pass the loader to `format.load()` for a decoded value:

```ts
async function readGeoJSON(format: PublishedFormat): Promise<GeoJSON> {
  return format.load(geojsonLoader);
}
```

Register a loader through `openPublication()` when its `mount()` method should be available through `format.mount()`. Keep parsing, rendering, and teardown dependencies in the package that owns the format.

## Publication layout

```text
publication/
  index.json
  cache/
    <opaque marimo key>/return.bin
```

`return.bin` is a MessagePack envelope:

```text
{
  data: bytes,
  media_type: string,
  filename: string | null,
  metadata: {
    format_id: string,
    metadata_json: bytes
  }
}
```

`metadata_json` contains UTF-8 JSON bytes. Readers strict-parse those bytes and compare the resulting JSON value with the index metadata. The comparison is semantic and does not require Python and browser encoders to produce identical JSON bytes.

The envelope is a marimo cache object. Use the publication API when reading it. The index supplies the exact cache key, size, SHA-256, and public format metadata required for verification.
