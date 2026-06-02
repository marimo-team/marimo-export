# Metrics Readout V3 Papercuts

## Decisions

- The plan is curated to cells that produce portable report artifacts in the
  weekly readout.
- The bundle records only the notebook source hash. The metrics notebook can
  contain private paths or credentials, so source text is not copied into the
  bundle by this use case.
- The exporter writes one typed JSON artifact. Markdown and HTML report policy
  live in v3 because grouping, labels, and presentation belong to the weekly
  report product.
- The browser reader check is separate from the report. It keeps the coworker
  flow clean while still proving the TypeScript `readExport(...)` path.

## Framework Boundaries

- A package-level display artifact should cover MIME payloads, diagnostics, and
  report ordering when more use cases need the same contract.
- A shared Vega-Lite image policy should own report width, scale, and renderer
  metadata. V3 applies a fixed report width before rasterization.
- The export and lazy-cache specs should use shared vocabulary for typed
  payloads, content-addressed blobs, source policy, integrity metadata, and
  timing or trace records.
- `run.py --strict` exits nonzero when captured cells report errors. The
  default writes diagnostics outside the main report body so the weekly readout
  stays focused on available metrics.
