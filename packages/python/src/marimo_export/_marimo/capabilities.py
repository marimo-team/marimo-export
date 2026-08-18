"""Stable records and ports for marimo-owned execution."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from marimo_export._execution.plan import Baseline, ExportPlan, NormalizedState
from marimo_export._json import JsonObject
from marimo_export.export import OutputCodec, OutputDescriptor, ScalarDescriptor
from marimo_export.result import CacheSummary, StateRunTimings


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
        if isinstance(self.descriptor, ScalarDescriptor):
            return None
        return self.descriptor.codec, self.descriptor.asset.sha256


@dataclass(frozen=True, slots=True)
class StateExecution:
    """Receipts and diagnostics from one state run."""

    receipts: tuple[NativeReceipt, ...]
    notebook_cache: CacheSummary
    timings: StateRunTimings


class KernelRuntime(Protocol):
    """Inspect and execute one attached marimo kernel."""

    def require_capabilities(self) -> MarimoCapabilities: ...

    def runtime_path(self) -> str | None: ...

    async def inspect_baseline(self) -> Baseline: ...

    async def declared_ui_values(self, names: tuple[str, ...]) -> JsonObject: ...

    def prepared_exporters(
        self,
        plan: ExportPlan,
    ) -> AbstractContextManager[Mapping[str, str]]: ...

    def flush_native_caches(self) -> None: ...

    async def execute_state(
        self,
        state: NormalizedState,
        plan: ExportPlan,
        exporter_identities: Mapping[str, str],
    ) -> StateExecution: ...


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
    "KernelAdapters",
    "KernelRuntime",
    "MarimoCapabilities",
    "NativeReceipt",
    "StateExecution",
    "TransferRuntime",
]
