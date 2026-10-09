---
title: Compatibility
description: Supported Python, marimo, browser, package, and protocol boundaries.
---

# Compatibility

marimo-export coordinates a Python producer, an optional live marimo session,
and one or more consumers. Check the boundary that matches your workflow.

## Python and marimo

| Component     | Contract                                                                                                                                                                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python        | The supported range and classifiers are declared in the [package metadata](https://github.com/marimo-team/marimo-export/blob/main/packages/python/pyproject.toml). Continuous integration runs each declared version on Ubuntu and Windows. |
| marimo        | The Python package metadata pins the exact supported marimo release.                                                                                                                                                                        |
| marimo-export | A live capture client and the selected kernel must load the same package version and implementation identity.                                                                                                                               |

Run the compatibility diagnostic in the producer environment:

```bash
uv run marimo-export doctor
```

`doctor` reports the Python executable, package version, effective repository,
and marimo adapter status. A failed marimo check means the environment cannot
construct the supported marimo adapter. Install the supported version before a
producer operation that needs notebook inspection or execution. Exact repository
reuse and consumer readers can complete without constructing that adapter.

## Browser package

`@marimo-team/marimo-export` uses
[ECMAScript modules](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules),
the browser-native JavaScript module format, and targets ES2022. Opening and
verifying an export require standard web APIs including `fetch`, `URL`, an
optional `AbortSignal`, and
[Web Crypto](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API).

Loading and mounting add representation-specific browser requirements:

| Representation                                    | Peer range or browser capability                                                                                                                                                                                         |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| JSON, scalar, text, HTML, NumPy, marimo snapshots | None beyond the browser package                                                                                                                                                                                          |
| Image                                             | Document Object Model, Blob, and object URL APIs                                                                                                                                                                         |
| Apache Arrow                                      | `@uwdata/flechette` and `lz4js`, using the versions in the [pnpm catalog](https://github.com/marimo-team/marimo-export/blob/main/pnpm-workspace.yaml)                                                                    |
| Parquet                                           | `hyparquet`, using the version in the [pnpm catalog](https://github.com/marimo-team/marimo-export/blob/main/pnpm-workspace.yaml)                                                                                         |
| Vega-Lite                                         | `vega-embed`, using the version in the [pnpm catalog](https://github.com/marimo-team/marimo-export/blob/main/pnpm-workspace.yaml)                                                                                        |
| AnyWidget                                         | `@anywidget/types`, using the version in the [pnpm catalog](https://github.com/marimo-team/marimo-export/blob/main/pnpm-workspace.yaml), for TypeScript types, plus embedded, data, HTTP, or HTTPS widget module support |

See [Output representations](representations) for install commands and
loader contracts. Browser mounts, Blob URLs, canvas rendering, dynamic imports,
and widget styles belong in a client-side boundary when the application also
uses server rendering.

## Protocol versions

marimo-export validates its closed public protocols by exact schema or codec
identifier. Custom BlobAsset representations use extensible, syntactically
validated media types.

| Boundary               | Current identifier                                                  |
| ---------------------- | ------------------------------------------------------------------- |
| Export specification   | `marimo-export.spec.v2`                                             |
| State space            | `marimo-export.states.v1`                                           |
| Notebook export        | `marimo-export.export.v1`                                           |
| Prepared manifest      | `marimo-export.prepared.v1`                                         |
| Native output envelope | Versioned codec listed in [Output representations](representations) |

A reader rejects an unknown closed schema or codec before exposing the affected
object. Update the producer and consumer together when one of those protocols
changes. Version a custom media type when its payload changes incompatibly.

## Release coordination

The public `marimo-export` Python and browser packages use coordinated release
versions. Keep those package versions aligned when one application produces and
consumes the same export. The export records the producer versions and
implementation identity for inspection and diagnostics.

Portable JSON is a protocol and value contract within those public packages.
Browser applications import its TypeScript types from
`@marimo-team/marimo-export`. Python applications import conversion and
canonical-encoding functions from `marimo_export.wire`.

### Try a preview build

The rolling [preview release](https://github.com/marimo-team/marimo-export/releases/tag/preview)
contains packages from `main` commits that passed CI and documentation checks.
Its notes include matching Python wheel and browser tarball URLs. Use those
exact URLs in a project or downstream CI:

```bash
uv add "marimo-export @ WHEEL_URL"
pnpm add "TARBALL_URL"
```

Python preview versions use `X.Y.Z.devN`; the matching browser package uses
`X.Y.Z-dev.N`. A live capture client and its kernel must use the same preview.
The release retains the newest 30 builds. Use registry releases for dependencies
that must remain available beyond that window.
