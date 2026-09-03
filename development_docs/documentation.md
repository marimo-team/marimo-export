# Documentation system

The public docset maps a reader's current state to one observable product
result. Contributor documentation records ownership, lifecycle, compatibility,
validation, and release mechanics needed to keep that public contract true.

## Source ownership

| Surface                    | Reader job                                                               | Owner                                                               |
| -------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| Root README                | Evaluate the project and reach one result                                | `README.md`                                                         |
| Registry README            | Adopt one published package                                              | Package `README.md`                                                 |
| Introduction               | Understand what the product is and why it exists                         | `docs/overview.md`, `docs/why.md`                                   |
| Concepts                   | Predict states, outputs, reuse, integrity, and trust                     | `docs/concepts/`                                                    |
| Guides                     | Complete a producer, consumer, integration, deployment, or recovery task | `docs/guide/`                                                       |
| Reference                  | Look up exact public contracts                                           | `docs/reference/`                                                   |
| Contributor entry          | Choose the code and validation owner                                     | `development_docs/README.md`                                        |
| Architecture               | Change an ownership or lifecycle boundary                                | `development_docs/architecture/`                                    |
| Development and validation | Run the workspace and prove a change                                     | `development_docs/development.md`, `development_docs/validation.md` |
| Release                    | Publish coordinated Python and npm packages                              | `development_docs/releasing.md`                                     |

Public docs define a term at first meaningful use and repeat one canonical noun.
The [terminology reference](../docs/reference/terminology.md) is the lookup owner.
[Identities and protocols](architecture/identities-and-protocols.md) owns the
more precise internal hash and schema vocabulary.

## Reader-state navigation

The public structure supports four primary paths:

```text
Introduction -> first export -> concepts -> guides -> reference
                          \-> integrations -> operations
                          \-> troubleshooting
```

`apps/docs/navigation.mjs` is the canonical route manifest. It drives top
navigation, sidebars, and the LLM text bundle. The companion check maps every
route to one Markdown file, rejects duplicates, and rejects unlisted pages.

Add a page only when it owns one stable reader task, concept, integration, or
reference contract. Update the route manifest in the same change.

## Claim ownership

| Claim                                      | Preferred source                                              |
| ------------------------------------------ | ------------------------------------------------------------- |
| Python name, signature, and default        | Public function, class, or package export                     |
| CLI command, option, output, and exit code | `_cli/arguments.py`, command result record, and render path   |
| TypeScript value, type, option, and error  | Public package barrel, implementation, and packed declaration |
| StateSpace or ExportSpec rule              | `spec.py` and generated JSON Schema                           |
| Durable export shape                       | `index.py`, `descriptors.py`, browser schema, and fixtures    |
| Loader result and lifecycle                | Loader source, package facade, and browser tests              |
| Compatibility                              | Package manifests, Marimo release record, and CI matrix       |
| Release behavior                           | Release scripts and publish workflow                          |

Author explanations and developed examples by hand. Derive or test exhaustive
inventories from their source owners.

## Examples are product contracts

The deterministic quickstart under `examples/quickstart/` owns the first
producer-to-reader result. `test_quickstart_example.py` builds it through the
public Python boundary and checks both exported states.

The market dashboard owns the multi-representation browser proof. Its
integration test verifies planning, preparation, exact reuse, live capture,
output loading, and export closure.

When a page uses a partial snippet, it must name the fixture, variable, DOM
host, server route, or framework context supplied by the surrounding page.

## Validate delivery

Run:

```bash
node apps/docs/scripts/check-navigation.mjs
make docs-build
make docs-serve
```

[Portless](https://portless.sh/) assigns the VitePress server an available port
and exposes it at `https://docs.marimo-export.localhost/`. Linked Git worktrees
receive a branch prefix, so each running workspace has its own URL. Use the URL
printed by `make docs-serve`. On its first HTTPS run, Portless may request local
administrator access to bind port 443 and trust its local certificate authority.

The development server uses an empty deployment base.

In the browser, check:

- introduction, concept, guide, and reference routes
- local search for canonical project nouns and public API names
- desktop and narrow layouts
- light and dark themes
- keyboard navigation and visible focus
- code copy controls, tables, diagrams, and callouts
- per-page Markdown, `llms.txt`, and `llms-full.txt`
- console errors, failed requests, local links, and fragments

Build with the production base path before Pages deployment. Generated
VitePress output remains untracked.
