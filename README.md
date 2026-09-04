<p align="center">
  <a href="https://marimo-team.github.io/marimo-export/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="apps/docs/public/brand/marimo-export-lockup-stacked-dark.svg">
      <img alt="marimo-export" src="apps/docs/public/brand/marimo-export-lockup-stacked-light.svg" width="300">
    </picture>
  </a>
</p>

<p align="center">
  <strong>Prepare notebook results. Read them anywhere.</strong>
</p>

<p align="center">
  <a href="https://marimo-team.github.io/marimo-export/"><strong>Documentation</strong></a> ·
  <a href="docs/guide/getting-started.md"><strong>Getting started</strong></a> ·
  <a href="examples/vite-vanilla"><strong>Browser example</strong></a> ·
  <a href="docs/reference/index.md"><strong>Reference</strong></a>
</p>

<p align="center">
  <a href="https://github.com/marimo-team/marimo-export/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/marimo-team/marimo-export/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/marimo-export/"><img alt="PyPI" src="https://img.shields.io/pypi/v/marimo-export.svg"></a>
  <a href="https://www.npmjs.com/package/@marimo-team/marimo-export"><img alt="npm" src="https://img.shields.io/npm/v/%40marimo-team%2Fmarimo-export.svg?label=npm"></a>
  <a href="packages/python/pyproject.toml"><img alt="Tested on Python 3.10 through 3.14" src="https://img.shields.io/badge/python-3.10%E2%80%933.14-blue.svg"></a>
  <a href="LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache--2.0-6c6f78.svg"></a>
</p>

marimo-export runs selected states of a [marimo](https://marimo.io/) notebook
and writes their named results to a portable, verified **notebook export**.
Browser applications and agents can read that export after the producer stops,
without a Python runtime. Python and TypeScript clients use the same files.

## Build the quickstart

From a repository checkout, run the CLI directly from PyPI:

```bash
mkdir -p dist
uvx marimo-export build examples/quickstart/report.py \
  --spec examples/quickstart/report.export.yaml \
  --output dist/quickstart
uvx marimo-export verify dist/quickstart
```

`dist/quickstart/index.json` describes two exported states and one JSON output.
Read the monthly state:

```python
from marimo_export import open_export

export = open_export("dist/quickstart")
summary = export.state("monthly").output("summary").json()
print(dict(summary))
```

Expected output:

```text
{'days': 30, 'label': 'Last 30 days'}
```

Install the Python package in another project with `uv add marimo-export`. The
[getting-started guide](docs/guide/getting-started.md) creates the same notebook
and export from an empty directory.

## Follow the product model

```text
notebook + ExportSpec
  -> plan complete states and named outputs
  -> prepare missing results through marimo
  -> write index.json and declared assets
  -> open, resolve, and load from a consumer
```

An `ExportSpec` contains sparse state rows and named outputs. Planning completes
each row from the captured input baseline. Every exported state has the same
output-name set. A representation identifies how one output is stored and
decoded.

[What is marimo-export?](docs/overview.md) introduces each object and operation
in order. [Why export notebook states?](docs/why.md) compares finite prepared
results with live Python and browser Python execution.

## Choose the next task

| Goal                                        | Start here                                                        |
| ------------------------------------------- | ----------------------------------------------------------------- |
| Select states and output representations    | [Choose states and outputs](docs/guide/choose-states.md)          |
| Build from a file or capture a live session | [Build or capture](docs/guide/build-and-capture.md)               |
| Read from Python, a browser, or an agent    | [Consume an export](docs/guide/consume-an-export.md)              |
| Build state transitions in a browser        | [Build a browser application](docs/guide/browser-applications.md) |
| Publish and deploy immutable exports        | [Deploy an export](docs/guide/deploy.md)                          |
| Look up an exact contract                   | [Reference](docs/reference/index.md)                              |

## Compatibility and trust

The Python package requires Python 3.10 or newer, is tested on Python 3.10
through 3.14, and installs the marimo release pinned by its package metadata.
Browser applications install `@marimo-team/marimo-export` and any dependencies
required by their selected output loaders. See
[Compatibility](docs/reference/compatibility.md) for the complete matrix.

Preparing an export executes notebook code with the producer environment's
file, credential, network, and package access. Opening validates canonical
`index.json`. Loading verifies the selected asset. Mounting an interactive
representation grants its code the browser page's authority. See [Integrity and
trust](docs/concepts/integrity-and-trust.md) before publishing executable output.

Contributor architecture, validation, and release mechanics live in the
[contributor guide](development_docs/README.md). See [Contributing](CONTRIBUTING.md)
for the pull request path and [Security](SECURITY.md) for private vulnerability
reporting.

Licensed under Apache-2.0.
