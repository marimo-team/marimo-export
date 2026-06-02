# Metrics Readout V3

V3 turns the weekly metrics notebook into a report in one command. It leaves
`master-metrics.py` unchanged and uses the running notebook only as the capture
boundary.

Run this while `master-metrics.py` is open at `http://localhost:8787/`:

```bash
python nogit/use-cases/metrics-readout-v3/run.py
```

The command:

1. reads `readout_plan.json`
2. writes `metrics-readout.spec.json`
3. installs the local capture package into the running kernel
4. asks the running marimo session to export the selected cells
5. reads the finished bundle through `open_export` from `moexport.query`
6. writes `output/index.html`, `output/metrics-readout.md`, and assets
7. writes `output/reader-check.html`, which exercises `readExport(...)` from
   `@marimo-team/export-reader` in a browser

The report is meant to be the weekly artifact. The generated spec, bundle,
render report, and reader check stay inspectable for review and debugging.

## Output

- `bundle/`: static export root with `index.json`, bundle manifests, traces,
  and content-addressed blobs.
- `metrics-readout.spec.json`: exact request sent to the notebook.
- `output/index.html`: browser-ready weekly report.
- `output/metrics-readout.md`: Markdown version of the same report.
- `output/assets/`: PNG charts and Vega-Lite specs.
- `output/reader-check.html`: browser check that loads the latest bundle with
  the TypeScript reader.
- `run-report.json`: capture, render, and output paths.

## Browser Check

Build the reader package, serve the repository, then open the report and reader
check with agent-browser:

```bash
pnpm --filter @marimo-team/export-reader build
python -m http.server 8799 --bind 127.0.0.1
```

In another shell:

```bash
agent-browser --session metrics-readout-v3 open \
  http://127.0.0.1:8799/nogit/use-cases/metrics-readout-v3/output/index.html
agent-browser --session metrics-readout-v3 wait --load networkidle
agent-browser --session metrics-readout-v3 snapshot -i

agent-browser --session metrics-readout-v3 open \
  http://127.0.0.1:8799/nogit/use-cases/metrics-readout-v3/output/reader-check.html
agent-browser --session metrics-readout-v3 wait --text "readExport loaded the latest metrics bundle."
agent-browser --session metrics-readout-v3 snapshot -i
```

`reader-check.html` is intentionally small. It proves the browser can load the
latest bundle through `readExport({ root })`, read the `readout` value, and see
the same scenario, values, formats, and source-spec hash recorded in the bundle.
