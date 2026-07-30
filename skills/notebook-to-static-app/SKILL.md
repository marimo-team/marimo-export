---
name: notebook-to-static-app
description: Turn an existing marimo Python notebook into a polished static HTML, CSS, and TypeScript application by designing an ExportSpec, creating and capturing finite notebook states with marimo-export, loading outputs through @marimo-team/marimo-export, and validating the deployed browser experience. Use when a user points to a marimo .py notebook and wants a CDN-deployable interactive app that runs with no Python server or Python WebAssembly runtime.
---

# Notebook to Static App

Create a user-facing web application from completed marimo executions. Keep the
notebook unchanged. Precompute the choices the audience needs, then make the
browser experience feel authored for their task.

## Read first

Read:

- [references/local-workflow.md](references/local-workflow.md) for the current
  local package setup and exact build and capture commands
- [references/contracts.md](references/contracts.md) for ExportSpec, exporter,
  loader, and state-transition contracts
- `/Users/petergy/Projects/opensource/marimo-team/marimo/DESIGN.md` for the
  default visual system

Inspect the current marimo-export docs and source when a command or type differs
from the references. The checkout is authoritative.

## Invariants

- Leave the source notebook byte-for-byte unchanged.
- Use notebook definition names in `inputs` and `outputs`.
- Run every selected output through normal marimo execution.
- Treat browser choices as a finite product surface, not a free-form Python
  prompt.
- Build and capture must create valid exports from the same ExportSpec.
- The deployed app must run from static files with no Python process.
- Freeze every notebook data dependency into the export. Mounted charts must
  not fetch their source dataset at runtime.
- Use exported values as the data source. Do not replace failed notebook data
  with fixtures or invented values.
- Keep implementation terminology out of the visible app. Users should not see
  export, state, codec, loader, cache, notebook, or Python unless the product
  itself teaches those concepts.

## Workflow

### 1. Understand the notebook

Read the complete notebook before choosing outputs. Identify:

- the audience and decision the notebook can support
- authored variables and UI controls that change meaningful results
- expensive or environment-dependent calculations
- definitions that contain data, metrics, charts, images, or widgets
- browser interactions that can continue without another Python execution

Write one internal product sentence:

> `<audience>` uses this app to `<decision or task>` using `<notebook result>`.

Reject an app concept that merely displays notebook outputs or demonstrates
marimo-export features.

### 2. Choose the finite interaction model

Design three to seven named states unless the notebook supports a genuinely
different useful range. Each state should represent a recognizable scenario,
policy, cohort, threshold, or comparison.

Use sparse overrides. Let omitted inputs come from the captured baseline.
Prefer a small set of high-value choices over a Cartesian product of every
possible control.

Choose outputs that let the browser answer the product question. A strong app
usually combines:

- one or two scalar summaries
- one primary visual or interactive value
- one inspectable table, array, or detail view

Export only values the application loads.

### 3. Create the app workspace

Use `scripts/scaffold_app.py` to create local Python and Vite plumbing from the
notebook's PEP 723 dependencies. Select only the browser loader families the app
will import.

Treat the generated `src/main.ts` as a loading shell. Replace it with the real
application after the ExportSpec is known.

### 4. Inspect a live session

Start the notebook from the app's uv environment. Open it in a browser so the
initial execution completes, then use `marimo-export session --json` to inspect
the exact available definitions.

Do not infer a definition from displayed prose or a local variable whose name
starts with `_`. Use the session inspection result.

### 5. Author and preflight the ExportSpec

Create `<name>.export.yaml` beside the app. Use domain names for states and
browser-facing output names.

Run session inspection before expensive state execution. Correct missing input
or output names before build or capture.

Choose the narrowest representation that preserves the browser experience:

- keep supported scalars, NumPy arrays, Arrow tables, and `BlobAsset` values
  native
- use Parquet for browser-readable table rows
- use Vega-Lite for interactive Altair charts
- use PNG when the audience needs a fixed chart image
- use AnyWidget bundles when the widget's saved browser model provides useful
  local interaction
- add a focused custom exporter and paired loader when no built-in
  representation fits

Inspect exported Vega-Lite data references. When a notebook chart points to a
remote dataset, use an export-time representation that embeds the data or
pair the chart with an exported table and rebuild the chart in the browser.

### 6. Prove both producer modes

Run `build` into `public/export` and verify it. This is the export included in
the production app.

Run `capture` against the open session into `.exports/capture` and verify it.
Keep the source server running until capture and verification finish.

Keep proof exports outside `public/`. Vite copies everything under `public/`
into the production build. Do not hand-edit `index.json` or its assets.

### 7. Build the browser application

Open `./export/`, resolve named states, and load outputs explicitly. Keep one
transition owner that:

1. aborts stale loads
2. disposes mounted charts or widgets
3. loads all outputs for the selected state
4. commits the new view only if the transition is still current
5. renders a recoverable error when loading fails

Make state controls shareable through a URL hash or query parameter.

Follow the marimo design system:

- use compact controls, slate borders, white or near-black work surfaces, and
  restrained blue interaction
- use PT Sans for UI text and Lora for editorial headings
- use borders before shadows
- keep charts and tables full-width and overflow-safe
- avoid gradients, oversized marketing heroes, nested cards, decorative
  animation, and one-off palettes

Use one clear visual direction for the domain. Keep the app inspectable and
small. Prefer vanilla DOM code until a real composition problem justifies a
framework.

### 8. Validate the user boundary

Run:

- Python export verification for both build and capture
- TypeScript type checking and production build
- a static preview with the final export
- browser checks at desktop and mobile widths

Use a dedicated `agent-browser --session` name. Exercise every state control and
at least one interaction inside each mounted chart or widget. Confirm:

- visible results change with the selected domain scenario
- rapid changes do not leave stale content
- no console or page errors occur
- no page-level horizontal overflow occurs
- the final app makes no request to a Python server
- notebook result data loads from the static app origin
- the UI contains no implementation commentary

Close the browser session and notebook server after validation.

## Failure discipline

Treat failures as product evidence.

- Missing definition: inspect the live session and correct the spec.
- Missing Python package: add the notebook's declared dependency to the app
  environment.
- Unsupported output: choose a data definition or write a focused exporter and
  loader pair.
- Custom exporter import failure: keep its module in the app and run the
  notebook, build, and capture with the app directory on `PYTHONPATH`.
- Capture package mismatch: run the notebook from the app environment
  containing the same local marimo-export checkout.
- Loader resolution failure: consume the packed browser package, then install
  the peer dependency for the imported loader.
- Blank or duplicated mount: make disposal and abort ownership explicit.
- Unhelpful state matrix: return to the audience decision and redesign the
  states rather than adding more.

Record the failed command, error, cause, and correction while working. Keep
that diagnostic record out of the visible app.
