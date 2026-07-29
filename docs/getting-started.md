# Getting started

Publish a public marimo optimization notebook as three precomputed states, then
switch among its CVXPY results from a plain Vite and TypeScript page. The
notebook source stays unchanged. The browser reads static NPY assets after the
Python build exits.

This guide uses a pinned revision of
[`02_linear_program.py`](https://github.com/marimo-team/learn/blob/477e2cbf7c31fc05dcf307b1e9c92c36514a32f3/optimization/02_linear_program.py)
from the marimo learn repository. It contains a Matrix AnyWidget, NumPy data, a
CVXPY solve, and a Matplotlib view.

::: info Development preview
The commands install the Python package from a checkout and pack the browser
package from the same checkout.
:::

## Run the checked-in example

Install Git, uv, Node 22.18 or newer, and pnpm 11.15.1. Clone the
repository and install its locked workspace:

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
cd docs/examples
```

The example directory is a locked uv project. It selects Python 3.13, installs
the notebook dependencies, and installs the checkout's `packages/python`
package. `02_linear_program.py` is an exact snapshot of the pinned public
notebook.

`build` executes the notebook's Python code. Review the notebook before running
it. The [trust guide](trust.md) defines the producer and browser boundaries.

## Define the input matrix and outputs

`linear-program.export.yaml` contains:

<<< ./examples/linear-program.export.yaml

The contract maps directly onto definitions in the checked-in notebook:

- `c_widget` is the input. Each state supplies the frontend value consumed by
  the Matrix AnyWidget.
- `balanced`, `favor-first-variable`, and `favor-second-variable` are the
  available browser states.
- `objective` publishes the notebook's `c` definition.
- `solution` publishes the notebook's `x_star` definition.

The state rows are sparse. marimo-export reads the baseline for every declared
input, applies each authored override, and records the complete vector in the
publication.

## Build and verify the publication

Run the notebook through the example's locked environment:

```bash
uv run marimo-export build 02_linear_program.py \
  --spec linear-program.export.yaml \
  --output publication
```

A successful first run reports the publication shape:

```text
Published 3 states and 2 outputs to .../publication
```

The command also reports asset and cache counts. Solver bytes and prior
executions determine those values. Each state runs through normal marimo
execution, so eligible cells restore through marimo's cache.

Add `--replace` when rebuilding into an existing `publication` directory.

Verify every declared asset before serving the directory:

```bash
uv run marimo-export verify publication
```

The verification result ends with `for 3 states` after reading every unique
asset.

`publication/index.json` now identifies the notebook, producer versions,
complete input vectors, output codecs, asset lengths, and SHA-256 digests. The
files under `publication/assets` are the unique NPY payloads referenced by the
six state-output pairs.

## Load the results in a Vite client

Pack the current browser package, scaffold a vanilla TypeScript app, and copy
the static publication into Vite's public directory:

```bash
pnpm --dir ../../packages/browser pack \
  --pack-destination "$PWD"

pnpm create vite client --no-interactive --template vanilla-ts
cd client
pnpm --ignore-workspace add ../marimo-team-marimo-export-0.0.0.tgz

cp -R ../publication public/linear-program
```

Replace `index.html` with one state selector and one result element:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Linear program publication</title>
  </head>
  <body>
    <main>
      <h1>Linear program publication</h1>
      <label>
        Objective
        <select id="state">
          <option value="balanced">[0.1, -0.2]</option>
          <option value="favor-first-variable">[-0.6, -0.2]</option>
          <option value="favor-second-variable">[0.1, -0.8]</option>
        </select>
      </label>
      <pre id="result">Loading…</pre>
    </main>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

Replace `src/main.ts` with the client that opens `index.json`, selects the named
state, verifies the two assets, and decodes them with the NumPy loader:

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { numpyLoader, type NumpyArray } from "@marimo-team/marimo-export/loader/numpy";

const publication = await openPublication("/linear-program/");
const stateSelect = document.querySelector<HTMLSelectElement>("#state")!;
const result = document.querySelector<HTMLPreElement>("#result")!;

function numbers(array: NumpyArray): number[] {
  if (!(array.data instanceof Float64Array)) {
    throw new TypeError(`Expected float64, received ${array.dtype.descriptor}`);
  }
  return Array.from(array.data, (value) => Number(value.toFixed(6)));
}

async function render(): Promise<void> {
  const state = publication.state(stateSelect.value);
  const [objective, solution] = await Promise.all([
    state.output("objective").load(numpyLoader()),
    state.output("solution").load(numpyLoader()),
  ]);
  result.textContent = JSON.stringify(
    {
      objective: numbers(objective),
      solution: numbers(solution),
    },
    null,
    2,
  );
}

stateSelect.addEventListener("change", () => void render());
await render();
```

Build the client, then start Vite:

```bash
pnpm build
pnpm dev
```

Open the local URL printed by Vite. The initial state renders:

```json
{
  "objective": [0.1, -0.2],
  "solution": [-0.433286, 2.056522]
}
```

Select `[-0.6, -0.2]`. The page loads the matching static state and renders:

```json
{
  "objective": [-0.6, -0.2],
  "solution": [0.144044, 1.454274]
}
```

The page has no Python process or notebook server. It fetches the static
publication through the NumPy loader shipped by the browser package.

## Publish the notebook's chart or widget

The tutorial publishes the two named NumPy definitions exposed by the source
notebook. To publish the Matplotlib view, add an ordinary cell that returns a
named PNG `BlobAsset`, then add that definition to `outputs`. To mount the
Matrix AnyWidget itself, return
`marimo_export.exporters.anywidget.bundle(c_widget)` from an ordinary cell and
load it through `@marimo-team/marimo-export/loader/anywidget`.

See [representations](representations.md) for the built-in exporter contracts
and peer dependencies. See [ExportSpec](export-spec.md) to add input
definitions and state rows.
