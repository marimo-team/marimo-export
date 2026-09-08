# Validation

Use the smallest check that proves the changed owner while developing. Finish
with the root gate and live evidence for process, repository, or browser
boundaries.

## Supported Python environments

Local development uses Python 3.14 from `.python-version`. The Python package
supports 3.10 through 3.14.

GitHub Actions classifies changed files before starting the quality, Python,
frontend, package, and documentation jobs. Each job runs when its owned inputs
change. The `Required` job requires success for selected jobs and a skipped
result for unselected jobs. Missing classification, failure, cancellation, or
an unexpected skip fails the gate.

The Python job runs the package contracts on:

```text
Ubuntu:  3.10, 3.11, 3.12, 3.13, 3.14
Windows: 3.10, 3.11, 3.12, 3.13, 3.14
```

The `Release contracts` job tests publication tooling on Ubuntu with Python
3.12. Release-script changes select that job and package verification. Changes
to the shared Python test setup select both release and SDK suites.

The Ubuntu jobs check formatting, lint, Python and TypeScript types, frontend
tests and builds, packed npm consumers, and isolated wheel smoke. The Pages
workflow checks documentation changes on pull requests and builds the site when
its source or toolchain inputs change.

Tests isolate the default export repository and Marimo cache signing key.
Notebook processes within a test inherit the same key, keeping warm-cache
checks independent of another process initializing its signing identity.

## Root gate

```bash
make check
```

The gate checks formatting, dependency direction, Python and TypeScript types,
unit and integration contracts, browser loader tests, package and documentation
builds, packed npm installation, and isolated Python wheel imports. The package
stage verifies the release archives, rebuilds the wheel from the source archive,
and compares both wheel payloads.

## Select evidence by boundary

| Changed boundary                          | Focused evidence                                       | Live or package evidence                                |
| ----------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------- |
| StateSpace, ExportSpec, and export format | State-space, spec, index, wire, fixture, reader tests  | Python and TypeScript fixture parity                    |
| Planning and preparation                  | Plan and preparation service tests                     | Exact repeat, one-state addition, file and live capture |
| Observations                              | Queue, ledger, source-match, SQLite observation tests  | Successful normal-run recording in a live kernel        |
| Repository                                | Repository, concurrency, integrity, lifecycle tests    | Multiprocess contention, owner death, Windows matrix    |
| marimo cache adapter                      | Probe, patch, graph scope, receipt, cleanup tests      | Warm owned build and borrowed capture                   |
| Managed process lifecycle                 | Producer, managed server, startup, shutdown tests      | Source-preserving file preparation                      |
| Live transport and authentication         | Client, auth, SSE, remote response, redaction tests    | HTTPS borrowed capture and same-origin transfer         |
| Transfer, writer, local reader            | Limits, digest, framing, rollback, filesystem tests    | Public capture and `verify`                             |
| Prepared Python publication               | Manifest, publication, cancellation, route-grace tests | Mutable manifest and retained immutable routes          |
| Application directory delivery            | Delivery, directory security, race, rollback tests     | Complete staged application commit                      |
| Prepared browser control                  | Manifest, query, control, refresh, controller tests    | Rapid state changes and publication rotation            |
| Browser reader or loader                  | Parser, integrity, decode, cancellation, disposal      | Packed subpath with required peer                       |
| Visible application                       | Typecheck and production build                         | Desktop, narrow, rapid changes, mounted action          |
| Documentation                             | Prose, links, VitePress build                          | Navigation, search, source view, LLM text, browser      |

## Public Python and dependency direction

```bash
uv run pytest -q \
  packages/python/tests/test_public_modules.py \
  packages/python/tests/test_boundaries.py
```

These tests protect the package root, focused public modules, the
service-to-`PreparationRepository` boundary, private marimo containment,
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
- adding one state executes one missing vector when inferred input names stay equal
- changing the default reuses prepared states
- cancellation leaves the prior generation current
- a live producer change fails before publication
- state progress counts cover the complete normalized plan, including reused states
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

## marimo cache adapter

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

Adapter evidence must cover the exact marimo release, private source drift,
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
and secret redaction. Plan-scoped input observation must return the complete
ordinary and UI input vector, retain applicable control bindings, and reject
a changed live producer.

## Application publication and delivery

```bash
uv run pytest -q \
  packages/python/tests/test_manifest.py \
  packages/python/tests/test_publication.py \
  packages/python/tests/test_delivery.py \
  packages/python/tests/test_directory_security.py \
  packages/python/tests/test_writer.py
```

Cover supersession, admission rejection and cancellation, last-good preservation,
application-directed polling, route
grace, detached response generation leases, nested export verification, directory identity,
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
settlement, and idempotent disposal. A rejected selection must cancel earlier
loading and restore controls through the same serialized application queue.

Output-set loading requires exact output-name resolution, inferred loader result
types, sibling cancellation, and preservation of the primary load failure. Keep
application binding and visible-commit tests in the consuming application.

## External consumer acceptance

marimo-studio composes the public producer, publication, delivery, and browser
APIs. Its [integration record](proposals/studio-prepared-runtime.md) identifies
the composition boundaries and cross-repository acceptance conditions.

An external acceptance run must install candidate wheel and npm artifacts into
a pinned consumer checkout and record both revisions, the Marimo version, and
the exact setup, test, static-export, and browser commands. The consumer owns
its integration assertions. Keep the framework's generic contracts independently
covered by this repository's tests and examples.

## Live application path

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run verify:export
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Exercise every declared state and one interaction inside each mounted runtime.
Check rapid changes, final state, staging cleanup, console errors, failed
requests, duplicate IDs, page overflow, and desktop and narrow layouts.

A prepared-runtime deployment check must confirm that prepared manifests, export
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

## Validation limits and implementation constraints

The following boundaries retain implementation constraints or incomplete
acceptance coverage. Use them to choose additional evidence when changing the
affected owner:

- adding or removing a state-row key can change the inferred input-name set and
  invalidate every state fingerprint
- exact prepared-export reuse can bypass exporter import and exporter-source
  checks when that source lies outside the discovered producer environment
- clearing observations leaves the producer revision unchanged
- public exact observation lookup and plan-time projection of stored supersets
  use different matching and revision semantics
- repository admission can reject a candidate before an otherwise eligible
  historical artifact is explicitly pruned
- prepared-state corruption can delete dependent generation rows while their
  directories await a later orphan-artifact recovery pass
- a valid reservation has no focused negative test for a current-pointer change
  immediately before final publication
- background refresh failures require reporting inside application callbacks
  because the controller has no dedicated error callback or status channel
- a scratchpad `done` event ends reading, so later events are not rejected
- AnyWidget inner JSON parsing lacks duplicate-key, depth, and value bounds
- strict BlobAsset MessagePack has thin malformed-token coverage
- Parquet uses an injected decoder in tests, and image and Vega-Lite lack real
  browser runtime acceptance
- the market dashboard has no automated browser journey for rapid transitions,
  mounted interaction, disposal, or narrow layout
- internal and public projection cache summaries count different cell sets
- private marimo transfer values and signature exceptions can cross the current
  capability boundary
