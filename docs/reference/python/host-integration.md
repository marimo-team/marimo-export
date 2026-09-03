---
title: Advanced host integration
description: Attach input observation and cache compatibility to an application-owned marimo kernel lifecycle.
---

# Advanced host integration

The host APIs attach marimo-export behavior to a marimo kernel that another
application owns. A host is an application that constructs or embeds marimo's
kernel runtime and controls its startup and shutdown.

Most producers should use `build()`, `prepare()`, or `capture()`. Those calls own
or borrow the required lifecycle. Use the APIs on this page when the application
already owns the kernel object, runtime context, hooks, and cleanup order.

## Ownership map

| Resource                          | Owner                                      |
| --------------------------------- | ------------------------------------------ |
| marimo kernel and runtime context | Host application                           |
| Cache compatibility lease         | Caller of `keep_cached_cells_compatible()` |
| Observation hook registration     | Caller of `install_observation_ledger()`   |
| Observation worker                | `ObservationLedger` until `close()`        |
| Supplied export repository        | Host application                           |

Release integrations in reverse construction order before closing the kernel or
repository.

::: info Host-owned objects
The snippets on this page are partial integration fragments. The host supplies
the `kernel`, `context`, `repository`, and application lifecycle functions they
reference.
:::

## Observe current kernel inputs

```python
from marimo_export.integration import observe_kernel_inputs

observed = observe_kernel_inputs(kernel)
```

```python
observe_kernel_inputs(kernel: object) -> KernelInputObservation
```

The function reads the running kernel graph and returns eligible portable UI
roots. It also returns every live control binding used to route a child UI
object back to its root input.

The supplied object must be a compatible running marimo kernel. Missing private
capabilities raise `CompatibilityError` with code `marimo_incompatible`.

### `KernelInputObservation`

```python
KernelInputObservation(
    values: Mapping[str, object],
    control_bindings: Mapping[str, ControlBinding],
)
```

The immutable record exposes:

```python
observed.values: Mapping[str, FrozenJsonValue]
observed.control_bindings: Mapping[str, ControlBinding]
observed.to_value() -> dict[str, object]
```

Input names must be non-keyword Python identifiers. Values must be portable
JSON. Each binding must name one input in `values`. Control IDs are nonempty and
limited to 1024 UTF-8 bytes.

`to_value()` returns detached values and control bindings in their portable wire
shape. This record describes the current kernel. Use `ObservationLedger` to
retain successful runs across time.

## Retain successful input vectors

```python
from marimo_export.observations import (
    ObservationLedger,
    install_observation_ledger,
)

ledger = ObservationLedger("report.py", repository=repository)
release_observations = install_observation_ledger(context, ledger)
try:
    run_host_application()
finally:
    release_observations()
    ledger.close()
```

```python
install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]
```

The context must expose a marimo kernel and its hook registry. Installation
verifies that the kernel remains bound to the ledger's saved source, registers a
final on-finish hook, and returns an idempotent release callback.

The hook queues an observation only when a normal run finishes with:

- no interruption
- no exception
- no cancelled cell
- no scratch cell in the completed graph
- the same live and saved notebook source binding

Hook-side inspection failure is logged and leaves the notebook run complete.
Persistence failure is retained by the ledger and raised by `flush()`,
`close()`, or the next `record()` call.

[Repository and observations](repository-and-observations.md) defines the
ledger's queue, repository ownership, errors, and close behavior.

## Keep restored cache values compatible

```python
from marimo_export.integration import keep_cached_cells_compatible

release_cache = keep_cached_cells_compatible()
try:
    run_host_application()
finally:
    release_cache()
```

```python
keep_cached_cells_compatible() -> Callable[[], None]
```

The function validates the installed marimo adapter, installs the cache repairs
required by an interactive host, and returns a release callback. The repairs
cover restored UI definitions and cache representations used by marimo-export's
supported producer boundary.

Keep the lease active for the complete host lifecycle that can restore those
values. Releasing it restores the prior marimo behavior after the final active
lease closes. If another owner replaces the patched behavior before release,
the operation raises `CompatibilityError` with code
`marimo_cache_patch_conflict`.

## Detect an owned producer session

```python
from marimo_export.integration import is_owned_session

if is_owned_session():
    configure_export_worker()
```

```python
is_owned_session() -> bool
```

The function returns `True` inside a process launched and owned by a
marimo-export file producer. Embedded hosts can use the signal to avoid applying
their ordinary interactive setup to that managed process.

## Installed marimo kernel entry point

The Python distribution registers a `marimo.kernel.lifespan` entry point named
`marimo-export`. A normal marimo kernel loads the entry point and continues
without activating managed producer behavior.

An owned file producer supplies a private activation handshake. In that process,
the entry point validates the supported marimo cache capabilities, installs the
managed cache lease and parent-stop tracking, reports activation to the parent,
and releases the integration during kernel shutdown.

Applications should use the public functions on this page. The activation
environment and entry-point callable belong to the managed producer lifecycle.

## Check adapter compatibility

```python
from marimo_export.diagnostics import marimo_compatibility

check = marimo_compatibility()
if check.status == "fail":
    print(check.message, check.details)
```

```python
marimo_compatibility() -> CheckResult
```

The function returns a result for expected and unexpected compatibility
failures. It does not raise `CompatibilityError` for an ordinary failed check.

`CheckResult` is immutable and contains:

```python
CheckResult(
    *,
    name: str,
    status: Literal["pass", "fail"],
    message: str,
    details: Mapping[str, JsonValue],
)

name: str
status: Literal["pass", "fail"]
message: str
details: dict[str, object]
```

`details` returns a detached portable object. `to_dict()` returns every field.
Use `status` as the branch condition and retain `details` for diagnostics.

Use [Format records and errors](format-records-and-errors.md) to handle
`CompatibilityError` and other typed failures from direct integration calls.
