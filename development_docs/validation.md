# Validation

Use the smallest check that proves the changed owner while developing. Finish
with the root gate and live evidence for process, repository, or browser
boundaries.

## Supported Python environments

Local development uses Python 3.14 from `.python-version`. The Python package
supports 3.10 through 3.14.

GitHub Actions classifies changed files before starting the quality, Python,
frontend, package, and documentation jobs. Each job runs when its owned inputs
change. The `Required` job accepts successful and skipped jobs, and fails when
change classification or an executed CI job fails.

The Python job runs the package contracts on:

```text
Ubuntu:  3.10, 3.11, 3.12, 3.13, 3.14
Windows: 3.10, 3.11, 3.12, 3.13, 3.14
```

The Ubuntu jobs check formatting, lint, Python and TypeScript types, frontend
tests and builds, packed npm consumers, and isolated wheel smoke. The Pages
workflow checks documentation changes on pull requests and builds the site when
its source or toolchain inputs change.

## Root gate

```bash
make check
```

The gate checks formatting, dependency direction, Python and TypeScript types,
unit and integration contracts, browser loader tests, package and documentation
builds, packed npm installation, and isolated Python wheel imports.

## Select evidence by boundary

| Changed boundary                          | Focused evidence                                       | Live or package evidence                                |
| ----------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------- |
| StateSpace, ExportSpec, and export format | State-space, spec, index, wire, fixture, reader tests  | Python and TypeScript fixture parity                    |
| Planning and preparation                  | Plan and preparation service tests                     | Exact repeat, one-state addition, file and live capture |
| Observations                              | Queue, ledger, source-match, SQLite observation tests  | Successful normal-run recording in a live kernel        |
| Repository                                | Repository, concurrency, integrity, lifecycle tests    | Multiprocess contention, owner death, Windows matrix    |
| Marimo cache adapter                      | Probe, patch, graph scope, receipt, cleanup tests      | Warm owned build and borrowed capture                   |
| Managed process lifecycle                 | Producer, managed server, startup, shutdown tests      | Source-preserving file preparation                      |
| Live transport and authentication         | Client, auth, SSE, remote response, redaction tests    | HTTPS borrowed capture and same-origin transfer         |
| Transfer, writer, local reader            | Limits, digest, framing, rollback, filesystem tests    | Public capture and `verify`                             |
| Prepared Python publication               | Manifest, publication, cancellation, route-grace tests | Mutable manifest and retained immutable routes          |
| Application directory delivery            | Delivery, directory security, race, rollback tests     | Complete staged application commit                      |
| Prepared browser control                  | Manifest, query, control, refresh, controller tests    | Rapid state changes and publication rotation            |
| Browser reader or loader                  | Parser, integrity, decode, cancellation, disposal      | Packed subpath with required peer                       |
| marimo-studio integration                 | Compiler, registry, route, static delivery tests       | Live Zero-Python preview and static bundle              |
| Visible application                       | Typecheck and production build                         | Desktop, narrow, rapid changes, mounted action          |
| Documentation                             | Prose, links, VitePress build                          | Navigation, search, source view, LLM text, browser      |

## Public Python and dependency direction

```bash
uv run pytest -q \
  packages/python/tests/test_public_modules.py \
  packages/python/tests/test_boundaries.py
```

These tests protect the package root, focused public modules, the
service-to-`PreparationRepository` boundary, private Marimo containment,
deferred imports, and package source identity.

## Planning, observations, and preparation

```bash
uv run pytest -q \
  packages/python/tests/test_planning.py \
  packages/python/tests/test_preparation_*.py \
  packages/python/tests/test_prepared_*.py \
  packages/python/tests/test_observations.py \
  packages/python/tests/test_marimo_observations.py
```

Required behavioral cases:

- exact repeat returns before notebook startup
- equal state vectors share one prepared state
- adding one state executes one missing vector
- changing the default reuses prepared states
- cancellation leaves the prior generation current
- a live producer change fails before publication
- progress counts match the missing-state partition
- observation queue pressure preserves revision order
- failed, interrupted, cancelled, and scratch runs record no vector
- public observation methods derive producer and input scope from `ExportPlan`

## Export repository

```bash
uv run pytest -q \
  packages/python/tests/test_repository.py \
  packages/python/tests/test_repository_*.py
```

Repository changes require evidence for:

- concurrent first open
- exact state and generation identity
- reservation owner and fencing token
- initial and replacement pointer compare-and-swap
- cross-spec reuse of one prepared state
- heartbeat during slow preparation and maintenance
- detached response leases
- expired owner recovery
- failure before and after filesystem installation
- corrupt row and artifact isolation
- abandoned staging and interrupted installation recovery
- retention pinning, byte accounting, and dry-run parity
- bounded reservation acquisition
- cleanup failure accounting
- plan-shaped public observation and exact-prepared lookup
- private observation and preparation capability containment

Use multiprocess tests for cross-process claims. The Windows CI matrix is the
authority for native Windows locking, paths, and process behavior.

## Marimo cache adapter

```bash
uv run pytest -q \
  packages/python/tests/test_marimo_cache_boundaries.py \
  packages/python/tests/test_marimo_cache_host.py \
  packages/python/tests/test_marimo_cache_patch.py \
  packages/python/tests/test_marimo_cache_probe.py \
  packages/python/tests/test_marimo_cache_side_effects.py \
  packages/python/tests/test_marimo_{child_run,exporter_compat,projection_recording,receipts,runtime_compat}.py
```

Run the public probe:

```bash
marimo-export doctor
```

Adapter evidence must cover the exact Marimo release, private source drift,
reversible overlapping leases, foreign patch conflicts, unrelated graph
isolation, native reuse across output plans, live complete-cell execution,
signed receipts, incomplete cache data,
unavailable restored values, write barriers, and cleanup.

After focused tests, run one warm file preparation and one borrowed capture.
Report authored and projection cache activity from the public result.

## Live transport and managed processes

```bash
uv run pytest -q \
  packages/python/tests/test_client.py \
  packages/python/tests/test_remote_auth.py \
  packages/python/tests/test_remote_client.py \
  packages/python/tests/test_remote_sse.py \
  packages/python/tests/test_managed_server_*.py \
  packages/python/tests/test_producer.py
```

Cover URL normalization, HTTPS policy, header roles, redirect rejection,
response bounds, no-retry uncertainty, source stability, process-tree cleanup,
and secret redaction.

## Application publication and delivery

```bash
uv run pytest -q \
  packages/python/tests/test_manifest.py \
  packages/python/tests/test_publication.py \
  packages/python/tests/test_delivery.py \
  packages/python/tests/test_directory_security.py \
  packages/python/tests/test_writer.py
```

Cover supersession, last-good preservation, observation-driven polling, route
grace, detached asset leases, nested export verification, directory identity,
native exchange, rollback replacement, recovery paths, and warnings emitted
after the new directory becomes visible.

## Cross-language and prepared browser contracts

Python produces canonical ExportIndex, prepared-manifest, projection, and
portable JSON fixtures consumed by browser tests. Both languages use the same
canonical JSON, state fingerprints, codecs, descriptors, media types, and
malformed-input cases.

```bash
pnpm --filter @marimo-team/portable-json test
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/portable-json test:package
pnpm --filter @marimo-team/marimo-export test:package
```

Prepared browser evidence must cover strict manifest parsing, base URL and
identity binding, default state, query and control updates, missing states,
supersession, restoration, publication refresh, selection preservation,
settlement, and idempotent disposal.

## marimo-studio consumer

Run Studio's Python, TypeScript, and browser gates in the Studio checkout using
the editable marimo-export workspace dependency. Required integration cases:

- view compilation produces public OutputSpec records and separate host bindings
- live preview uses `Session.plan()` and `Session.capture()`
- independently leased assets survive manifest rotation
- Studio source contains no repository schema or SQL
- static delivery calls public `prepare()` and copies the verified export
- the Studio renderer implements `PreparedStatePort`
- rapid transitions preserve the last complete view
- current-manifest refresh preserves a valid local selection
- notebook-scope close releases prepared handles and pending work
- Studio host-cache setup calls the public marimo-export integration capability

## Live application path

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run verify:export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Exercise every declared state and one interaction inside each mounted runtime.
Check rapid changes, final state, staging cleanup, console errors, failed
requests, duplicate IDs, page overflow, and desktop and narrow layouts.

A Zero-Python deployment check must confirm that prepared manifests, export
files, state transitions, and controls use the static origin and open no kernel
or WebSocket connection.

## Documentation delivery

```bash
node apps/docs/scripts/check-navigation.ts
make docs-build
make docs-serve
```

Check local links and fragments, navigation parity, local search, code block
rendering, `llms.txt`, `llms-full.txt`, desktop layout, and narrow layout.
VitePress build success alone does not prove these delivery properties.

## Open validation gaps

The following source behaviors need focused witnesses before their stronger
architectural claims can be treated as enforced:

- clearing observations does not advance the producer revision, so
  observation-driven publication polling does not react to the deletion
- public exact observation lookup and plan-time projection of stored supersets
  have different matching semantics
- combined repository-byte admission can reject a write before evicting every
  otherwise eligible historical artifact
- prepared-state corruption can delete dependent generation rows before the
  same recovery pass quarantines their directories
- a valid reservation has no focused negative test for a current-pointer change
  immediately before final publication
- internal and public projection cache summaries count different cell sets
- private Marimo transfer values and signature exceptions can cross the current
  capability boundary
