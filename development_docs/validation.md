# Validation

Use the smallest check that proves the changed owner while developing. Finish
with the root gate and any required live browser evidence.

## Root gate

```bash
make check
```

The gate checks formatting, lint, dependency direction, Python and TypeScript
types, unit and integration contracts, browser loader tests, package and docs
builds, packed npm installation, and isolated Python wheel imports.

## Select evidence by boundary

| Changed boundary                | Focused evidence                                     | Live or package evidence                           |
| ------------------------------- | ---------------------------------------------------- | -------------------------------------------------- |
| ExportSpec and state plan       | Spec, plan, ordinary UI, and AnyWidget tests         | File build with representative states              |
| marimo capability or cache      | Probe, adapter, registry, receipt, and cleanup tests | Warm build and borrowed capture                    |
| Managed server lifecycle        | Startup, activation, ownership, shutdown tests       | Source-preserving file build                       |
| Transfer, writer, local reader  | Limits, digest, framing, rollback, filesystem tests  | Public capture and `verify`                        |
| Browser reader or loader        | Parser, integrity, decode, cancellation, disposal    | Packed subpath build with required peer            |
| State transition or visible app | Typecheck and production build                       | Desktop, narrow, rapid changes, mounted action     |
| Scaffold or agent workflow      | Source-preserving relocation test                    | Generated app install, export, typecheck, build    |
| Documentation                   | Prose, links, VitePress build                        | Navigation, search, source view, LLM text, browser |

## Cross-language contracts

Python produces the canonical `ExportIndex` fixture consumed by browser tests.
Both languages use the same canonical JSON, state fingerprints, codecs,
descriptors, media types, and malformed-input cases.

## Live finance path

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run verify:export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Exercise every saved view and one interaction inside the mounted widget. Check
rapid changes, final state, staging cleanup, console errors, failed requests,
duplicate IDs, page overflow, and desktop and narrow layouts.

## Documentation delivery

```bash
make docs-build
make docs-serve
```

Check local links and fragments, navigation parity, local search, code block
rendering, `llms.txt`, `llms-full.txt`, desktop layout, and narrow layout.
VitePress build success alone does not prove these delivery properties.
