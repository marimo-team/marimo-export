# Publish marimo results for a Python-free client

marimo-export runs the notebook states you choose while Python is available,
then writes a static publication for a browser app. Deploy `index.json`, its
content-addressed assets, and your client to any static HTTP host.

The browser selects a published state and loads requested outputs through typed
loaders. Python has already finished. Client-side controls can switch among
precomputed states or interact with a published chart, image, table, array, or
AnyWidget.

[Publish a notebook from the wild](getting-started.md)

## A real notebook and a concrete ExportSpec

The getting-started guide downloads the public
[`02_linear_program.py`](https://github.com/marimo-team/learn/blob/477e2cbf7c31fc05dcf307b1e9c92c36514a32f3/optimization/02_linear_program.py)
notebook from the marimo learn repository. The notebook defines:

| Definition | Role in the publication                                    |
| ---------- | ---------------------------------------------------------- |
| `c_widget` | Matrix AnyWidget whose frontend value selects an objective |
| `c`        | NumPy objective vector derived from the widget             |
| `x_star`   | NumPy solution computed by CVXPY                           |

This complete ExportSpec publishes three widget states and two outputs:

<<< ./examples/linear-program.export.yaml

`inputs` names the notebook definition that may vary. Each row under `states`
provides a sparse frontend-value override. `outputs` gives a public name to
each notebook definition that the client can load.

The build leaves the source notebook unchanged:

```bash
marimo-export build 02_linear_program.py \
  --spec linear-program.export.yaml \
  --output publication
```

The resulting directory contains one canonical `index.json` plus
content-addressed NPY assets. A plain TypeScript client loads them with the
public NumPy loader:

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";

const publication = await openPublication("/linear-program/");
const state = publication.state("balanced");
const solution = await state.output("solution").load(numpyLoader());

console.log(solution.shape, solution.data);
```

The [getting-started guide](getting-started.md) provides the exact download,
environment, build, verification, Vite client, and expected browser result.

## Build from a file or capture a live kernel

Use `build` when marimo-export should start the notebook, execute the declared
matrix, and stop its loopback server:

```bash
marimo-export build 02_linear_program.py \
  --spec linear-program.export.yaml \
  --output publication
```

Use `capture` when a live kernel already holds credentials, configured
services, or expensive results:

```bash
marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec linear-program.export.yaml \
  --output publication
```

Both commands execute each state through marimo and read the resulting native
cache receipts. See the [command-line interface](cli.md) for session discovery,
credentials, replacement behavior, JSON output, and exit categories.

## Publish native values or authored representations

Scalar values, numeric NumPy arrays, and supported tables use marimo's native
cache codecs. Exporter functions convert object families such as Altair charts
and AnyWidgets into a versioned `BlobAsset` inside an ordinary notebook cell.

| Notebook value   | Browser entry point                           |
| ---------------- | --------------------------------------------- |
| Scalar           | `@marimo-team/marimo-export`                  |
| NumPy array      | `@marimo-team/marimo-export/loader/numpy`     |
| Arrow table      | `@marimo-team/marimo-export/loader/arrow`     |
| Parquet file     | `@marimo-team/marimo-export/loader/parquet`   |
| Vega-Lite chart  | `@marimo-team/marimo-export/loader/vegalite`  |
| PNG image        | `@marimo-team/marimo-export`                  |
| AnyWidget bundle | `@marimo-team/marimo-export/loader/anywidget` |

Continue with [ExportSpec](export-spec.md) for the input matrix or
[representations](representations.md) for exporter and loader contracts.
