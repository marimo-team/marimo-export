# Getting started

This workflow captures the live state of a regular marimo notebook, reads one selected result from Python, then reads the same publication from a browser after the notebook server stops.

## Start the notebook environment

From this checkout, prepare the Python environment and start the finance notebook:

```bash
uv sync --all-extras --locked
uv run --with altair==6.0.0 marimo edit examples/_notebooks/finance.py \
  --no-sandbox \
  --host 127.0.0.1 \
  --port 3456
```

Open the notebook, run it, and set its controls to the state you want as `current`. Capture uses the running kernel. Unsaved executed edits and current UI values are part of that live state.

`--no-sandbox` keeps the kernel in the prepared uv environment. That environment must contain the same `marimo-export` version as the capture client and every requested exporter extra. The session needs edit permission because capture uses marimo's code execution endpoint. Keep the server running through the capture command.

The current Python dependency and lockfile pin `peter-gy/marimo` commit `0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`, which supplies the required `BlobAsset` lazy-cache codec. Python package publication requires a compatible marimo core release and a corresponding released dependency bound.

## Inspect the active session

List the active sessions:

```bash
export MARIMO_EXPORT_TOKEN="<token>"
uv run marimo-export session http://localhost:3456/ --json
```

The result lists session IDs, filenames, and server-side paths. Inspect one session to discover selectable globals with their qualified Python type descriptors, frozen cell-output descriptors, UI controls, built-in exporter availability, the producer versions, and the live document digest:

```bash
uv run marimo-export session http://localhost:3456/ \
  --session SESSION_ID \
  --json
```

Each inspected control reports its type, current detached value, sensitivity, and available JSON domain information. Password values are redacted as `null`. Variants can target nonsensitive controls.

Pass `--session SESSION_ID` to capture when the server hosts several sessions. A local one-off command may carry one `access_token` query value in the server URL. Prefer `MARIMO_EXPORT_TOKEN` for shell history, scripts, and shared examples.

## Select outputs and variants

Use the adjacent `examples/_notebooks/finance.export.yaml` specification:

```yaml
schema: marimo-export.spec.v1

variants:
  current: {}
  aapl:
    symbol_picker: [AAPL]
  nvda:
    symbol_picker: [NVDA]

outputs:
  summary:
    source: summary
    formats:
      json: {}

  chart:
    source:
      expression: price_chart.properties(width=800)
    formats:
      vegalite: {}
      png:
        options:
          scale: 2

  market_note:
    source:
      cell: market_note
    formats:
      html: {}
```

`summary` selects a live global. The chart expression runs against the live globals inside the kernel. `market_note` selects the rendered payload data from a named cell.

The variant keys target existing marimo UI controls. A plain Python assignment has no live input channel, so expose a parameter as a marimo control when it needs finite variants.

## Capture the publication

```bash
uv run marimo-export capture \
  http://localhost:3456/ \
  --spec examples/_notebooks/finance.export.yaml \
  --output dist/finance \
  --json
```

Capture performs this transaction:

1. Select the active session and record its document digest and starting UI vector.
2. Resolve every exporter and preflight named global and cell selectors against the starting live state.
3. Apply one variant through marimo's UI update path.
4. Resolve selected globals, variant-time expressions, and rendered cell payloads.
5. Run or restore each projector through marimo's persistent cache.
6. Restore the starting UI vector and stale-cell set.
7. Transfer and verify the selected projected results.
8. Commit the cache objects, then `index.json`.
9. Release server-side capture resources.

The command validates the specification and destination before connecting to the notebook server. For replacement, it verifies the current publication and every referenced asset during this preflight. A new destination commits through an atomic no-replace directory rename. Add `--replace` to update an existing publication at the same path. Replacement revalidates the destination before commit, links verified new cache assets into the existing cache, retains old assets for readers that already loaded the previous index, and atomically replaces `index.json` last. A cache key collision with different bytes fails the replacement.

## Read from Python

```python
from marimo_export import open_publication

publication = open_publication("dist/finance")

print(publication.variant_names)
summary = (
    publication
    .variant("current")
    .output("summary")
    .format("json")
    .json()
)
print(summary)
```

The Python reader verifies the asset envelope's size and SHA-256 before decoding the `BlobAsset` and parsing the inner JSON bytes.

## Read from a browser

Serve `dist` through your application's static file path, then install the browser package:

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openPublication } from "@marimo-team/marimo-export";

const publication = await openPublication("/finance/");
const summary = await publication.variant("current").output("summary").format("json").json();

console.log(summary);
```

Stop the marimo server and reload the application. The publication contains the index and selected portable cache objects required by the browser.

Continue with [Export specifications](./export-specification.md) for sources, formats, variants, and custom exporters.
