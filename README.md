<p align="center">
  <a href="https://marimo-team.github.io/marimo-export/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="apps/docs/public/brand/marimo-export-lockup-stacked-dark.svg">
      <img alt="marimo-export" src="apps/docs/public/brand/marimo-export-lockup-stacked-light.svg" width="300">
    </picture>
  </a>
</p>

<p align="center">
  <strong>Prepare notebook results. Share them anywhere.</strong>
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

Precompute selected input combinations from a [marimo](https://marimo.io/)
reactive Python notebook and share the results as a **notebook export**.
Applications and agents read its static files without running Python or
receiving the notebook source.

- **Choose the results.** Publish named data, tables, charts, rendered cells,
  and interactive widgets for a finite set of states.
- **Reuse the computation.** Reuse prepared states across exports and marimo's
  native cell cache when a new state needs to run.
- **Build your own application.** Read through Python, TypeScript, or the
  documented export format. Your application owns presentation and deployment.
- **Verify what you read.** Readers validate the index and check asset sizes and
  hashes before decoding.

[Try the exported application](https://marimo-team.github.io/marimo-export/overview)
or [compare deployment options](docs/why.md).

## Get started

Install with [uv](https://docs.astral.sh/uv/) in a Python project:

```bash
uv add marimo-export
```

The [quickstart](docs/guide/getting-started.md) creates `report.py` and
`report.export.yaml`, selecting weekly and monthly reports. Build and verify them:

```bash
mkdir -p dist
uv run marimo-export build report.py --spec report.export.yaml --output dist/report
uv run marimo-export verify dist/report
```

Building executes notebook code with your Python environment's access. The
result is `index.json` and its assets, ready to read or serve from a static host.

```python
from marimo_export import open_export

export = open_export("dist/report")
print(dict(export.state("monthly").output("summary").json()))
# {'days': 30, 'label': 'Last 30 days'}
```

## Explore

- [Choose states and outputs](docs/guide/choose-states.md),
  [build or capture a live session](docs/guide/build-and-capture.md),
  and [understand caching](docs/concepts/caching.md).
- [Read an export](docs/guide/consume-an-export.md),
  [build a browser application](docs/guide/browser-applications.md),
  or [deploy updates](docs/guide/deploy.md).
- Look up the [Python API](docs/reference/python-api.md),
  [TypeScript API](docs/reference/browser-api.md),
  [CLI](docs/reference/cli.md), and [export format](docs/reference/export-format.md).

See [compatibility](docs/reference/compatibility.md) for runtime and loader
requirements, and [integrity and trust](docs/concepts/integrity-and-trust.md)
before mounting executable widgets or custom modules.

[Contributing](CONTRIBUTING.md) · [Architecture](development_docs/architecture.md) ·
[Security](SECURITY.md) · [Apache-2.0](LICENSE)
