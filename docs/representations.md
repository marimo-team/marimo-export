# Choose output formats

Pair each notebook result with the browser loader that will consume it.

| Notebook result | Exporter                            | Browser loader        |
| --------------- | ----------------------------------- | --------------------- |
| scalar          | none                                | `scalarLoader()`      |
| NumPy array     | none                                | `numpyLoader()`       |
| Arrow table     | none                                | `arrowTableLoader()`  |
| table rows      | `parquet.table`                     | `parquetRowsLoader()` |
| Altair chart    | `altair.vegalite`                   | `vegaLiteLoader()`    |
| chart image     | `altair.png`                        | `imageLoader()`       |
| AnyWidget       | `anywidget.bundle`                  | `anyWidgetLoader()`   |
| custom value    | function that returns a `BlobAsset` | custom loader         |

## Browser dependencies

Install the runtime used by each loader:

| Loader        | Additional package              |
| ------------- | ------------------------------- |
| scalar, image | none                            |
| NumPy         | none                            |
| Arrow         | `@uwdata/flechette` and `lz4js` |
| Parquet       | `hyparquet`                     |
| Vega-Lite     | `vega-embed`                    |
| AnyWidget     | `@anywidget/types`              |

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Import each loader from the public package:

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
```

## Exporter options

| Exporter           | Options                              |
| ------------------ | ------------------------------------ |
| `altair.vegalite`  | none                                 |
| `altair.png`       | `scale`                              |
| `anywidget.bundle` | none                                 |
| `parquet.table`    | `compression`, `filename`            |
| `blob.json`        | `media_type`, `filename`, `metadata` |
| `blob.text`        | `media_type`, `filename`, `metadata` |
| `blob.html`        | `filename`, `metadata`               |

Python helpers live under `marimo_export.exporters`.

## Custom output

A Python exporter converts the notebook result into a `BlobAsset`:

```python
import json

from marimo_export import BlobAsset


def encode_summary(value: object) -> BlobAsset:
    return BlobAsset(
        data=json.dumps(value).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

A browser loader handles the same media type:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

export const summaryLoader = defineBlobAssetLoader({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload }) {
    return JSON.parse(new TextDecoder().decode(payload.data));
  },
});
```

The loader may return data or a value with a browser `mount()` method.
