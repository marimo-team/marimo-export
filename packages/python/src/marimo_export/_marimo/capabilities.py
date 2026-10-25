"""Stable records and ports for marimo-owned execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import CodeType, MappingProxyType
from typing import Literal, Protocol, TypeVar

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._execution.plan import Baseline, ExecutionPlan, NormalizedState, PlannedOutput
from marimo_export._json import JsonObject
from marimo_export._limits import MAX_UNAVAILABLE_MESSAGE_BYTES
from marimo_export.descriptors import (
    JsonDescriptor,
    OutputCodec,
    OutputDescriptor,
    ScalarDescriptor,
    ScalarValue,
    UnavailableOutput,
    UnavailableReason,
)
from marimo_export.errors import OutputError
from marimo_export.index import ControlBinding
from marimo_export.integration import KernelInputObservation
from marimo_export.progress import CacheActivity
from marimo_export.result import StateRunTimings

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class MarimoCapabilities:
    """marimo capabilities available in the selected kernel."""

    version: str
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NotebookCell:
    """One authored cell of a marimo notebook document.

    `id` is canonical. `name` is `None` when marimo generated the cell name.
    """

    id: str
    code: str
    name: str | None
    config: JsonObject


@dataclass(frozen=True, slots=True)
class KernelCell:
    """One cell registered in a live kernel graph, with a canonical `id`."""

    id: str
    code: str
    config: JsonObject


RunOutcome = Literal["completed", "interrupted", "failed"]


@dataclass(frozen=True, slots=True)
class KernelRun:
    """Facts about one finished pass of the kernel's cell runner.

    One UI update or cell run can take several passes when cells set
    `mo.state`. `outcome` is `interrupted` when the user interrupted the pass,
    `failed` when a cell raised, including `mo.stop`, and `completed`
    otherwise. `scratch` marks a scratchpad run. `filename` is the notebook
    path the kernel reports after the pass.

    `read_cells()` lists the kernel graph in registration order, and
    `read_inputs()` observes the portable UI inputs. Call both inside the
    subscription callback, before the kernel starts its next pass.
    """

    outcome: RunOutcome
    scratch: bool
    filename: str | None
    read_cells: Callable[[], tuple[KernelCell, ...]]
    read_inputs: Callable[[], KernelInputObservation]


@dataclass(frozen=True, slots=True)
class NativeReceipt:
    """One verified native cache return ready for transfer."""

    output: str
    descriptor: OutputDescriptor
    payload: bytes | None
    disposition: Literal["hit", "miss"]

    @property
    def asset_identity(self) -> tuple[OutputCodec, str] | None:
        if isinstance(self.descriptor, (ScalarDescriptor, JsonDescriptor)):
            return None
        return self.descriptor.codec, self.descriptor.asset.sha256


@dataclass(frozen=True, slots=True)
class NativeScalarReturn:
    """One verified inline scalar from Marimo's cache manifest."""

    python_type: str
    value: ScalarValue


@dataclass(frozen=True, slots=True)
class NativeNumpyReturn:
    """One verified NumPy payload from Marimo's cache store."""

    python_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class NativeArrowReturn:
    """One verified Arrow payload from Marimo's cache store."""

    python_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class NativeBlobReturn:
    """One verified native BlobAsset envelope and decoded payload."""

    python_type: str
    envelope: bytes
    data: bytes
    media_type: str
    filename: str | None
    metadata: JsonObject


NativeCacheReturn = NativeScalarReturn | NativeNumpyReturn | NativeArrowReturn | NativeBlobReturn


@dataclass(frozen=True, slots=True)
class PreparedExporter:
    """One resolved exporter bound to deterministic transient source."""

    identity: str
    token: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity, str)
            or len(self.identity) != 64
            or any(character not in "0123456789abcdef" for character in self.identity)
        ):
            raise ValueError("prepared exporter identity must be a lowercase SHA-256 digest")
        if self.token is not None and (
            not self.token.isidentifier() or not self.token.startswith("marimo_export_exporter_")
        ):
            raise ValueError("prepared exporter token is invalid")


class UnavailableOutputError(OutputError):
    """One output has no value in one state run.

    The error fails a required output with code ``output_execution_failed``.
    An optional output records ``unavailable`` in its state entry instead.
    ``reason_message`` omits the state alias because prepared states are
    shared by every alias of one complete input vector.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: UnavailableReason,
        reason_message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.unavailable = UnavailableOutput(
            reason=reason,
            message=safe_diagnostic(
                reason_message, maximum_chars=MAX_UNAVAILABLE_MESSAGE_BYTES
            ).strip()
            or "output is unavailable",
        )


def unavailable_output(
    planned_output: PlannedOutput,
    error: OutputError,
) -> UnavailableOutput | None:
    """Return the entry an optional output records for ``error``, or None to fail."""

    if planned_output.optional and isinstance(error, UnavailableOutputError):
        return error.unavailable
    return None


@dataclass(frozen=True, slots=True)
class StateExecution:
    """Receipts and diagnostics from one state run.

    ``receipts`` cover the available outputs and ``unavailable`` names the
    optional outputs without a value, so together they cover every output.
    """

    receipts: tuple[NativeReceipt, ...]
    control_bindings: Mapping[str, ControlBinding]
    cache: CacheActivity
    timings: StateRunTimings
    unavailable: Mapping[str, UnavailableOutput] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.cache, CacheActivity):
            raise TypeError("state execution cache must be CacheActivity")
        if not isinstance(self.control_bindings, Mapping):
            raise TypeError("state execution control_bindings must be a mapping")
        parsed: dict[str, ControlBinding] = {}
        for object_id, binding in self.control_bindings.items():
            if not isinstance(object_id, str) or not object_id:
                raise TypeError("state execution control binding IDs must be non-empty strings")
            if not isinstance(binding, ControlBinding):
                raise TypeError("state execution control bindings must contain ControlBinding")
            parsed[object_id] = binding
        object.__setattr__(self, "control_bindings", MappingProxyType(parsed))
        object.__setattr__(self, "unavailable", MappingProxyType(dict(self.unavailable)))


class CachedStateExecutor(Protocol):
    """Execute normalized states through Marimo's native cell cache."""

    async def execute_state(
        self,
        state: NormalizedState,
        plan: ExecutionPlan,
        exporters: Mapping[str, PreparedExporter],
        implementation_sha256: str,
        producer_identity: str,
    ) -> StateExecution: ...


class ExporterRuntime(Protocol):
    """Read the attached kernel for exporter preparation."""

    def kernel_globals(self) -> Mapping[str, object]: ...

    def kernel_state(self, key: str, factory: Callable[[], _T]) -> _T: ...

    def code_sha256(self, code: CodeType) -> str: ...


class KernelRuntime(CachedStateExecutor, ExporterRuntime, Protocol):
    """Inspect and execute one attached marimo kernel."""

    def require_capabilities(self) -> MarimoCapabilities: ...

    def runtime_path(self) -> str | None: ...

    def validate_parent_state(self) -> None: ...

    async def inspect_baseline(self) -> Baseline: ...

    def observe_inputs(self) -> KernelInputObservation: ...

    async def declared_ui_values(self, names: tuple[str, ...]) -> JsonObject: ...

    def flush_native_caches(self) -> None: ...


class TransferRuntime(Protocol):
    """Host temporary export bytes in the attached marimo runtime."""

    def context(self) -> object: ...

    def create_virtual_file(self, data: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class KernelAdapters:
    """marimo adapters consumed by one bridge request."""

    kernel: KernelRuntime
    transfer: TransferRuntime


__all__ = [
    "CacheActivity",
    "CachedStateExecutor",
    "ExporterRuntime",
    "KernelAdapters",
    "KernelCell",
    "KernelRun",
    "KernelRuntime",
    "MarimoCapabilities",
    "NativeArrowReturn",
    "NativeBlobReturn",
    "NativeCacheReturn",
    "NativeNumpyReturn",
    "NativeReceipt",
    "NativeScalarReturn",
    "NotebookCell",
    "PreparedExporter",
    "RunOutcome",
    "StateExecution",
    "TransferRuntime",
    "UnavailableOutputError",
    "unavailable_output",
]
