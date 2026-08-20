# Planning and preparation

Planning resolves an ExportSpec into exact producer work. Preparation reuses or
captures the missing states, commits one immutable export, and returns a leased
`PreparedExport`.

```python
from marimo_export import ExportRepository, ExportSpec, plan, prepare

spec = ExportSpec.from_file("report.export.yaml")

with ExportRepository.open() as repository:
    work = plan("report.py", spec=spec, repository=repository)
    print(work.reusable_states, work.missing_states)

    with prepare("report.py", spec=spec, repository=repository) as prepared:
        prepared.write("dist/report", replace=True)
```

`build()` composes output preflight, `prepare()`, and
`PreparedExport.write()`. `capture()` performs the same preparation through an
existing authenticated Marimo session.

## ExportSpec is committed intent

An ExportSpec contains:

- one required `default_state`
- one or more sparse named state rows
- one or more named output declarations

The authored spec contains no input list. Planning infers inputs from the output
dependency closure and the state keys, then normalizes each sparse row against
the notebook baseline.

Equal complete vectors share one state fingerprint. Their authored names remain
aliases. The default alias resolves to one fingerprint before state execution.

Observations support authoring. They show complete input vectors that succeeded
in a matching saved notebook. Planning includes the revision-consistent
observation snapshot in `ExportPlan`, while the spec remains the source of
states that preparation must publish.

## ExportPlan reports exact work

`plan()` returns a frozen `ExportPlan` with:

- notebook document identity
- producer identity
- output-plan identity
- spec identity
- inferred input names
- normalized states and aliases
- explicit default alias and fingerprint
- output names
- reusable and missing state fingerprints
- observed vectors and their repository revision
- exact-generation reuse status

The identity of one exact prepared export is:

```text
sha256(producer_sha256, output_plan_sha256, spec_sha256)
```

Planning first computes that identity from stable source and runtime facts. If
the repository holds the exact verified generation, planning reconstructs the
plan from its canonical index and starts no notebook process.

When exact reuse is unavailable, file-backed planning opens one `OwnedNotebook`
for baseline inspection. It resolves the plan through the same kernel bridge
used by live sessions, queries reusable prepared states, and attaches one
observation snapshot. The owned notebook performs its initial autorun to produce
the baseline. Planning executes no transient state or output cells.

`Session.plan()` provides the borrowed-session form. It reads the live document
and baseline through the selected session, then applies the same repository
reuse rules.

## File preparation

`prepare(source, spec=...)` follows this sequence:

1. Compute the stable producer and exact repository identity.
2. Return the exact verified generation when it is present.
3. Claim the preparation reservation for that identity.
4. Recheck exact reuse after the claim.
5. Open one `OwnedNotebook` when work remains.
6. Resolve the plan against the running baseline.
7. Requery prepared states inside the reservation.
8. Capture each missing fingerprint through a single-state ExportSpec.
9. Verify and commit each captured prepared-state artifact.
10. Assemble the requested relation from reusable and captured states.
11. Verify and commit one immutable export generation.
12. Return a leased `PreparedExport`.

The producer source guard runs before generation publication. A notebook,
environment, or marimo-export implementation change fails preparation and
preserves the previous current generation.

Each missing state commits independently after its complete capture succeeds.
A later cancellation can therefore leave reusable state artifacts for the next
attempt while withholding the incomplete exact generation.

## Live-session preparation

`capture(server, session=..., spec=...)` and `Session.capture()` borrow one live
session. The caller retains server and session ownership.

Live preparation resolves the plan before waiting for the reservation, then
resolves it again after the reservation is held. A producer identity change
between those reads fails with `parent_document_changed`.

Within the reservation, live capture:

1. accepts an exact generation only when its verified relation matches the live plan
2. reuses matching prepared-state fingerprints
3. captures each missing state through the borrowed session
4. assembles the complete export
5. publishes against the exact current generation observed before replacement

The capture bridge verifies the parent document again after downloading every
asset. A changed live notebook fails the operation before repository commit.

Capture timeout is a per-operation budget. It bounds transport inactivity,
reservation acquisition, and each repository operation. The reservation lease
renews throughout preparation, so a multi-state capture can exceed that duration
when its individual operations continue to make progress.

## Incremental reuse

Repository reuse and Marimo computation caching solve different work:

| Change                         | Repository work                         | Marimo work when capture runs           |
| ------------------------------ | --------------------------------------- | --------------------------------------- |
| Exact repeat                   | Reuse exact generation                  | No notebook starts                      |
| HTML, CSS, or view-host change | Reuse exact generation                  | No notebook starts                      |
| Default alias change           | Reuse prepared states, assemble export  | No state needs new computation          |
| One added state                | Capture one missing state               | Native cache may restore its cells      |
| One removed state              | Reuse remaining states, assemble export | Zero state executions                   |
| Output declaration change      | New output-plan identity                | Native cache may restore notebook cells |
| Producer identity change       | New producer scope                      | Marimo decides native cache validity    |

State aliases share one prepared-state artifact. A generation records the exact
alias and default-state relation requested by its spec.

## Progress and cancellation

Planning, preparation, and writing emit frozen `ProgressEvent` values:

```text
inspection_started
plan_ready
prepared_reused
state_started
state_finished
prepared_committed
write_finished
```

State events carry completed count, total count, authored and projection cache
activity, state alias, and elapsed capture time when available. The CLI renders
the same records as human progress or JSONL. Applications can pass their own
callback.

The `cancelled` callback is checked before expensive transitions, before each
missing state, and before generation assembly. Cancellation releases the
reservation, closes state handles, removes staging, and preserves the current
generation.

Reservation acquisition is bounded. File preparation uses its public `timeout`.
Borrowed-session preparation uses the repository's 30-second reservation wait.
Expiry raises a repository failure with code `repository_reservation_timeout`.

After acquisition, the service combines caller cancellation with reservation
liveness. A lost or reclaimed reservation stops later state work and the fenced
commit rejects the stale owner.

## PreparedExport owns the consumer lease

`PreparedExport` exposes:

- immutable export identity and `ExportPlan`
- prepared and reused state fingerprints
- observed Marimo cache activity for work that ran
- `open()` for a verified `NotebookExport`
- `asset()` for one independently leased declared file
- `manifest()` for browser prepared-publication control
- `write()` for atomic caller-owned output
- `renew()` and idempotent `close()`

Use it as a context manager. When `prepare()` opened the default repository,
closing the handle closes that repository after the generation lease releases.

`manifest(export_url, state=...)` emits `marimo-export.prepared.v1`. It binds the
immutable export identity, export URL, complete selected inputs, state
fingerprint, and optional refresh interval. The default selection comes from
the export's explicit default state.

## Durable write

`PreparedExport.write(output, replace=...)` copies the immutable export through
the same writer and reader used by direct builds. The writer:

1. preflights destination ownership and replacement policy
2. rechecks the leased source index and reads each declared source asset securely
3. stages the complete directory
4. verifies `index.json` and every staged asset
5. commits the directory atomically where the host filesystem supports it
6. returns `ExportResult`

The destination contains the portable notebook export. Repository databases,
leases, reservations, staging records, and observation history remain in the
producer repository.
