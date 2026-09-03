"""Stable records and ports for marimo-owned execution."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from marimo_export._execution.plan import Baseline, ExecutionPlan, NormalizedState
from marimo_export._json import JsonObject
from marimo_export.descriptors import (
    JsonDescriptor,
    OutputCodec,
    OutputDescriptor,
    ScalarDescriptor,
    ScalarValue,
)
from marimo_export.index import ControlBinding
from marimo_export.integration import KernelInputObservation
from marimo_export.progress import CacheActivity
from marimo_export.result import StateRunTimings


@dataclass(frozen=True, slots=True)
class MarimoCapabilities:
    """marimo capabilities available in the selected kernel."""

    version: str
    names: tuple[str, ...]


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


@dataclass(frozen=True, slots=True)
class StateExecution:
    """Receipts and diagnostics from one state run."""

    receipts: tuple[NativeReceipt, ...]
    control_bindings: Mapping[str, ControlBinding]
    cache: CacheActivity
    timings: StateRunTimings

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


class KernelRuntime(CachedStateExecutor, Protocol):
    """Inspect and execute one attached marimo kernel."""

    def require_capabilities(self) -> MarimoCapabilities: ...

    def runtime_path(self) -> str | None: ...

    def validate_parent_state(self) -> None: ...

    async def inspect_baseline(self) -> Baseline: ...

    def observe_inputs(self) -> KernelInputObservation: ...

    async def declared_ui_values(self, names: tuple[str, ...]) -> JsonObject: ...

    def prepared_exporters(
        self,
        plan: ExecutionPlan,
        baseline: Baseline,
    ) -> AbstractContextManager[Mapping[str, PreparedExporter]]: ...

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
    "KernelAdapters",
    "KernelRuntime",
    "MarimoCapabilities",
    "NativeArrowReturn",
    "NativeBlobReturn",
    "NativeCacheReturn",
    "NativeNumpyReturn",
    "NativeReceipt",
    "NativeScalarReturn",
    "PreparedExporter",
    "StateExecution",
    "TransferRuntime",
]
