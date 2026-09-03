# Contributing

marimo-export accepts focused changes that preserve the notebook, preparation,
repository, export-format, reader, and browser ownership boundaries.

## Before you start

- Read [README.md](README.md) for the product model.
- Read the [contributor guide](development_docs/README.md) and
  [architecture map](development_docs/architecture.md) before changing lifecycle
  or ownership.
- Report suspected vulnerabilities through [SECURITY.md](SECURITY.md).

## Set up the workspace

The repository uses Python, uv, Node.js, pnpm, Vite+, VitePress, and Chromium.
Supported versions are declared in `pyproject.toml`, `package.json`, package
manifests, and lockfiles.

```console
make bootstrap
```

Use focused package checks while working. Run the complete local gate before
asking for review:

```console
make check
```

`make check` verifies formatting, lint, Python and TypeScript types, unit and
integration tests, package builds, documentation, and isolated release
artifacts. [Validation](development_docs/validation.md) maps each product
boundary to a smaller command.

For public documentation changes, also inspect the rendered site:

```console
make docs-build
make docs-serve
```

The development command prints the local Portless URL. Check desktop and narrow
layouts, light and dark themes, search, links, code blocks, and browser errors.

## Keep changes inside the owning boundary

- marimo owns notebook parsing, reactive execution, computation caching, UI
  updates, and native serialization. Private marimo imports stay under
  `marimo_export._marimo.compat`.
- Producer operations call services under `marimo_export._services`. Services
  use focused private repository capabilities.
- `packages/portable-json` owns cross-language JSON values.
  `packages/browser` owns export reading and prepared-publication control. Each
  loader workspace owns one representation runtime.
- `docs/` owns public workflows and reference. `development_docs/` owns
  architecture, lifecycle, compatibility seams, validation, and release work.
- Add dependencies to the smallest workspace member that imports them.

Read [Development](development_docs/development.md) for package workflows and
[Architecture](development_docs/architecture.md) for the complete dependency
map.

## Pull requests

- Keep the diff tied to one product or ownership goal.
- Include tests for supported behavior and distinct failure boundaries.
- Update public docs when commands, APIs, formats, or user workflows change.
- List skipped validation commands and the reason.
- Keep generated build directories out of the commit.

First-time contributors may be asked to sign the
[marimo contributor license agreement](https://marimo.io/cla).
