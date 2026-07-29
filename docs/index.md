# Publish marimo results for a Python-free client

marimo-export runs the notebook states you choose while Python is available,
then writes a static publication for a browser app. Deploy `index.json`, its
content-addressed assets, and your client to any static HTTP host.

The browser selects a published state and loads requested outputs through typed
loaders. Python has already finished. Client-side controls can switch among
precomputed states or interact with a published chart, image, table, array, or
AnyWidget.

[Publish a notebook from the wild](getting-started.md)

## Parameterize, execute, publish

[Papermill](https://github.com/nteract/papermill) describes a familiar Jupyter
workflow: parameterize a notebook, execute it, and save the executed notebook.
marimo-export follows that sequence for marimo and adds a publication step for
client applications:

1. **Parameterize:** `inputs` names marimo definitions and `states` supplies
   sparse override rows.
2. **Execute:** `build` starts the notebook, or `capture` attaches to a live
   kernel, and each state runs through marimo.
3. **Publish:** the chosen `outputs` become `index.json` entries and
   content-addressed assets.

Papermill writes an executed notebook for each run. marimo-export writes one
static publication containing the declared finite state matrix.

[Compare Papermill and ExportSpec](export-spec.md#for-papermill-users)

## A real notebook and a concrete ExportSpec

The getting-started guide runs a pinned snapshot of the public
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

Run the checked-in example from its locked uv project. The build leaves the
source notebook unchanged:

```bash
cd marimo-export/docs/examples

uv run marimo-export build 02_linear_program.py \
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

The [getting-started guide](getting-started.md) provides the exact environment,
build, verification, Vite client, and expected browser result.

## Build from a file or capture a live kernel

Use `build` when marimo-export should start the notebook, execute the declared
matrix, and stop its loopback server:

```bash
uv run marimo-export build 02_linear_program.py \
  --spec linear-program.export.yaml \
  --output publication
```

Use `capture` when a live kernel already holds credentials, configured
services, or expensive results:

```bash
uv run marimo-export capture http://127.0.0.1:2718 \
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
