# marimo-export

marimo-export precomputes selected states of a Marimo notebook and writes one
verified export for Python, browsers, agents, and custom applications.

The export contains canonical `index.json`, complete input vectors, named output
descriptors, and content-addressed assets. Static browser consumers load those
results with no live Python kernel.

## Build an export

Install the Python package:

```bash
uv add marimo-export
```

Create `report.export.yaml`:

```yaml
schema: marimo-export.spec.v1
default_state: baseline
states:
  baseline: {}
  weekly:
    interval: 1wk
outputs:
  summary:
    source: { kind: value, selector: report.summary }
  chart:
    source: { kind: value, selector: performance }
    exporter: altair.vegalite
```

Plan and build the export:

```bash
marimo-export plan report.py --spec report.export.yaml
marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report
marimo-export verify dist/report
```

`plan` infers the input definitions from the selected outputs and state rows. It
reports normalized states, the default, repository reuse, and work still needed.
`build` prepares missing states, atomically writes `dist/report`, and verifies the
complete file closure.

The first run executes notebook code with its file, credential, network, and
package access. A matching later run reuses the prepared export before notebook
startup. Marimo retains ownership of cell-cache keys, restoration, serialization,
and storage.

## Read the result

Python:

```python
from marimo_export import open_export, verify_export

export = open_export("dist/report")
summary = export.default_state.output("summary").json()
verified = verify_export("dist/report")
```

Browser:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const summary = await notebookExport.defaultState.output("summary").load(jsonLoader());
```

Applications that consume a changing prepared publication can use
`@marimo-team/marimo-export/prepared` for manifest validation, state transitions,
control routing, cancellation, refresh, and disposal.

## Prepare from a live session

`capture` borrows an active Marimo session and returns a leased `PreparedExport`:

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("report.export.yaml")
with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/report", replace=True)
```

Use `marimo-export inspect SERVER` to list sessions. The selected session remains
active after capture.

## Try the market dashboard

The repository example builds five result sets from live Yahoo Finance data:

```bash
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Network availability affects preparation. Open the URL printed by Vite to switch
between prepared states and interact with the saved chart and widget models.

## Public interfaces

- [How notebook exports work](docs/overview.md)
- [Build or capture](docs/guide/build-and-capture.md)
- [Choose states and outputs](docs/guide/choose-states.md)
- [Python API](docs/reference/python-api.md)
- [CLI](docs/reference/cli.md)
- [Browser API](docs/reference/browser-api.md)
- [Export format](docs/reference/export-format.md)
- [Develop and contribute](development_docs/README.md)

Mounting AnyWidget, Vega-Lite, or custom interactive output grants that code the
browser page's authority. Review mounted modules and apply the deployment's
Content Security Policy and origin rules.

Licensed under Apache-2.0.
