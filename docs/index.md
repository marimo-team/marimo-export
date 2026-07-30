# Turn notebook results into a static web app

marimo-export precomputes selected marimo notebook states for an interactive
browser app. The deployed app needs no Python server or Python WebAssembly
runtime.

## Start

1. [Run the market dashboard](getting-started.md).
2. [Choose the states and results](export-spec.md) your app needs.
3. [Build from a file or capture an open notebook](cli.md).
4. [Load the export in the browser](browser-api.md).

Use the [Python API](python-api.md) for automation, review the available
[output formats](representations.md), and follow the [deployment
guide](trust.md).

Apps that compute new Python results for arbitrary visitor input still need a
Python backend.

Papermill users can start with the [workflow
comparison](export-spec.md#coming-from-papermill).
