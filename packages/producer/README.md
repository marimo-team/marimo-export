# marimo-export Python producer

`marimo-export` executes export plans inside an attached marimo kernel and provides `Projection` for notebook-defined frontend formats. Install it in the prepared environment that runs the notebook.

```bash
uv add marimo-export
```

The package requires Python 3.10 or newer and pins its supported marimo version. Its base runtime dependency is marimo.

## Run a producer

Start marimo from the project environment that contains the notebook dependencies and `marimo-export`:

```bash
uv run marimo edit notebook.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300
```

`--no-sandbox` keeps the kernel in that prepared environment. The TypeScript remote client or CLI connects to the server, executes a `marimo-export.plan.v1` document, and pulls the portable projection closure.

Producer builds require `marimo edit` with marimo's default `relaxed` execution type. An edit server gives each attached kernel its own process and exposes marimo's edit-scoped scratchpad control endpoint. Run mode does not guarantee process isolation, and the producer cannot derive its hosting topology from the kernel context.

Set a project override to the supported execution type with:

```toml
[tool.marimo.experimental]
execution_type = "relaxed"
```

When switching a notebook from `strict` execution, begin with a fresh `__marimo__/cache` directory. marimo 0.23.14 gives both execution types the same native cache identity, so an existing strict entry could otherwise restore during a relaxed build. An incompatible kernel fails with `unsupported_mode` before the producer reads the notebook snapshot or writes cache objects.

From the repository checkout, use the local package directly:

```bash
uv run --package marimo-export marimo edit \
  examples/_notebooks/cache_matrix.py \
  --headless \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 2718 \
  --session-ttl 300 \
  --no-token \
  --no-skew-protection
```

The two `--no-*` security flags are for this loopback source-checkout workflow. Omit them when another machine can reach the server.

Follow [Getting started](https://github.com/marimo-team/marimo-export/blob/main/docs/getting-started.md) for the complete publication command.

## Python API

The package root exports `Projection` and `__version__`. Notebook exporters return `Projection` values:

```python
from marimo_export import Projection

Projection(
    b'{"answer":42}',
    format_id="project.answer.v1",
    media_type="application/vnd.project.answer+json",
    metadata={"schema": 1},
)
```

### `Projection(payload, *, format_id, media_type="application/octet-stream", metadata={})`

Describes one portable frontend payload.

- `payload`: Bytes stored under a content-addressed cache key.
- `format_id`: Nonempty codec identifier used by the TypeScript loader.
- `media_type`: Nonempty media type. Default `application/octet-stream`.
- `metadata`: JSON object recorded beside the payload reference. Default `{}`.

Metadata numbers must be finite. Integral values must fit the JavaScript safe integer range.

`__version__` reports the installed `marimo-export` package version.

## Notebook-defined exporters

Define a callable in the notebook when the frontend needs a shape that differs from the authored notebook output:

```python
import json

from marimo_export import Projection


def export_summary(value, *, label: str) -> Projection:
    payload = json.dumps(
        {"label": label, "summary": value},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return Projection(
        payload,
        format_id="project.summary.v1",
        media_type="application/vnd.project.summary+json",
        metadata={"label": label},
    )
```

Reference the callable from the plan:

```yaml
schema: marimo-export.plan.v1
outputs:
  summary:
    source: summary
    formats:
      card:
        exporter:
          definition: export_summary
          version: "1"
        options:
          label: Market summary
```

The producer calls `export_summary(value, label="Market summary")`. Exporters may return a `Projection` directly or through an awaitable. The matching frontend loader declares `formatId: "project.summary.v1"`.

Use a versioned `module:object` reference when the exporter lives in an installed package:

```yaml
exporter:
  ref: project_exporters:export_summary
  version: "1"
```

See [Custom projections](https://github.com/marimo-team/marimo-export/blob/main/docs/export-plans.md#custom-projections) for source selectors, options, and cache identity.

## Built-in exporters and extras

| Plan exporter | Format ID              | Installation               |
| ------------- | ---------------------- | -------------------------- |
| `json`        | `json.v1`              | Base package               |
| `text`        | `text.v1`              | Base package               |
| `html`        | `html.v1`              | Base package               |
| `bytes`       | `bytes.v1`             | Base package               |
| `vegalite`    | `vegalite.v1`          | Base package               |
| `arrow`       | `dataframe.arrow.v1`   | `marimo-export[dataframe]` |
| `parquet`     | `dataframe.parquet.v1` | `marimo-export[dataframe]` |
| `png`         | `vegalite.png.v1`      | `marimo-export[png]`       |
| `anywidget`   | `anywidget.v1`         | `marimo-export[anywidget]` |

Arrow and Parquet accept a PyArrow table, a Narwhals-compatible eager dataframe, or a list of row mappings.

The HTML exporter publishes a standalone HTML fragment. It converts through `marimo.as_html()`, embeds virtual files referenced by `img`, `audio`, and `video` `src` attributes as data URLs, then rejects remaining `@file` references and `<marimo-...>` runtime elements. Ordinary static `Html` and `mo.md()` values work directly. Use Arrow or Parquet for dataframes, Vega-Lite or PNG for charts, and bytes or a custom `Projection` plus loader for other frontend contracts.

The AnyWidget exporter accepts a raw AnyWidget or `mo.ui.anywidget(...)` value and captures its reachable static model graph. Install the matching TypeScript loader to inspect its initial state during server rendering and mount it in a browser. See [Publish AnyWidget outputs](https://github.com/marimo-team/marimo-export/blob/main/docs/anywidget.md).

Install every serializer used by a plan in the producer environment:

```bash
uv add "marimo-export[anywidget,dataframe,png]"
```

The remote `describe` operation reports each built-in exporter and whether its optional producer dependency is available.

## Cache contract

The producer runs authored cells and generated projection cells through marimo's native cell cache. The default file-backed location is the notebook-local `__marimo__/cache/` directory.

Native cell caching is enabled when the notebook runs with its filename as the complete argument vector. User arguments are available to root and nested notebook cells, but they are ambient process state outside marimo's cache identity. The producer disables native cell caching for that build so a result created under one argument vector cannot restore under another.

Interactive notebook execution can warm authored dependency cells. Export builds warm synthetic projection cells. An export projection is reusable when its projection-cell ABI, source, exporter lineage, exporter version, normalized options, and tracked dependencies match. Scenario IDs, public input names, output names, format names, and plan ordering stay outside projection cache identity. Resolved input values still participate through the tracked dependency state they affect.

State-writing cells use a conservative cache policy. A direct state setter is cache eligible when the same cell also directly references its paired state getter. Setter-only references and setters reached through another callable run live.

A marimo `Html` value referenced directly or inside a dictionary, list, or tuple, including `mo.md()`, contributes its concrete type and portable rendered text to projection cache identity. When cached HTML refers to virtual media from an earlier child runner, the producer reruns the defining closure to recover the media before projection lookup. Unchanged rendered HTML can then restore its cached `Projection`.

Custom source objects retain marimo's native cache semantics. When marimo can represent an unpicklable source by execution identity, a warm terminal projection can restore the cached `Projection` without executing that source again. A miss still executes the source and exporter, and marimo determines cache eligibility for each object graph.

The complete `Projection` is stored as the synthetic cell's cached return value. Its portable payload is also stored under `marimo-export/payloads/sha256/<digest>` for JavaScript consumers.

Each scenario runs from a fresh deserialization of one saved notebook snapshot. Save editor changes before building. After scenario and payload verification, the producer rereads the saved file once before writing the index and requires its bytes to match the captured snapshot.

The [export plan guide](https://github.com/marimo-team/marimo-export/blob/main/docs/export-plans.md) defines the full plan contract. [Remote execution](https://github.com/marimo-team/marimo-export/blob/main/docs/remote-execution.md) defines session and transfer behavior.
