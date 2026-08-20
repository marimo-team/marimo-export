from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias, cast

from marimo_export._json import JsonObject, JsonValue, canonical_bytes, decode_json_object
from marimo_export.errors import MarimoExportError
from marimo_export.wire import portable_json, state_fingerprint

_DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_SQLITE_INTEGER = (1 << 63) - 1


class RepositoryError(MarimoExportError):
    """An export repository record or operation is invalid."""

    code = "repository_error"


class RepositoryLimitError(RepositoryError):
    """An export repository operation exceeds its configured limits."""

    code = "repository_limit_exceeded"


class RepositoryIntegrityError(RepositoryError):
    """A durable repository record or artifact failed integrity validation."""

    code = "repository_integrity_failed"


class RepositoryUnavailableError(RepositoryError):
    """The export repository storage is unavailable."""

    code = "repository_unavailable"


class RepositoryBusyError(RepositoryUnavailableError):
    """The export repository remained locked past its bounded wait."""

    code = "repository_busy"


class RepositoryFenceError(RepositoryError):
    """A stale preparation owner attempted to publish after losing its reservation."""

    code = "repository_fence_stale"


class RepositoryReservationTimeoutError(RepositoryBusyError):
    """A preparation reservation was not acquired before its deadline."""

    code = "repository_reservation_timeout"


def digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    if value > MAX_SQLITE_INTEGER:
        raise ValueError(f"{label} exceeds SQLite's integer range")
    return value


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Stable identity for one exact prepared export."""

    producer_sha256: str
    output_plan_sha256: str
    spec_sha256: str

    def __post_init__(self) -> None:
        for name in ("producer_sha256", "output_plan_sha256", "spec_sha256"):
            digest(getattr(self, name), name)

    @property
    def key(self) -> str:
        from marimo_export.planning import export_plan_identity

        return export_plan_identity(
            producer_sha256=self.producer_sha256,
            output_plan_sha256=self.output_plan_sha256,
            spec_sha256=self.spec_sha256,
        )


@dataclass(frozen=True, slots=True, init=False)
class ObservedState:
    """One canonical portable input vector observed from a producer."""

    producer_sha256: str
    revision: int
    fingerprint: str
    _values_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        producer_sha256: str,
        revision: int,
        values: Mapping[str, object],
    ) -> None:
        digest(producer_sha256, "producer_sha256")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
            or revision > MAX_SQLITE_INTEGER
        ):
            raise ValueError("revision must be a non-negative SQLite integer")
        parsed = portable_json(values, "observed state")
        if not isinstance(parsed, dict):
            raise TypeError("observed state must be an object")
        encoded = canonical_bytes(parsed)
        object.__setattr__(self, "producer_sha256", producer_sha256)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "fingerprint", state_fingerprint(parsed))
        object.__setattr__(self, "_values_bytes", encoded)

    @property
    def values(self) -> Mapping[str, JsonValue]:
        return MappingProxyType(decode_json_object(self._values_bytes, "observed state"))

    @property
    def input_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))

    @property
    def canonical_values(self) -> bytes:
        return self._values_bytes

    @property
    def byte_count(self) -> int:
        return len(self._values_bytes)

    def to_dict(self) -> JsonObject:
        return {
            "producer_sha256": self.producer_sha256,
            "revision": self.revision,
            "fingerprint": self.fingerprint,
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class SnapshotObservation:
    input_names: tuple[str, ...]
    state: ObservedState


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """Revision-consistent observed inputs for one producer."""

    producer_sha256: str
    revision: int
    rows: tuple[SnapshotObservation, ...]
    latest_rows: tuple[SnapshotObservation, ...]

    def observations(self, inputs: tuple[str, ...]) -> tuple[ObservedState, ...]:
        requested = frozenset(inputs)
        projected: dict[str, ObservedState] = {}
        for row in self.rows:
            if not requested <= frozenset(row.input_names):
                continue
            state = _project_state(row.state, requested, self.revision)
            projected.pop(state.fingerprint, None)
            projected[state.fingerprint] = state
        return tuple(projected.values())

    def latest(self, inputs: tuple[str, ...]) -> ObservedState | None:
        requested = frozenset(inputs)
        candidates = (row for row in self.latest_rows if requested <= frozenset(row.input_names))
        row = max(candidates, key=lambda item: item.state.revision, default=None)
        if row is None:
            return None
        return _project_state(row.state, requested, row.state.revision)

    def to_dict(self) -> JsonObject:
        return {
            "producer_sha256": self.producer_sha256,
            "revision": self.revision,
            "observations": [row.state.to_dict() for row in self.rows],
            "latest": [row.state.to_dict() for row in self.latest_rows],
        }


def _project_state(
    state: ObservedState,
    names: frozenset[str],
    revision: int,
) -> ObservedState:
    return ObservedState(
        producer_sha256=state.producer_sha256,
        revision=revision,
        values={name: value for name, value in state.values.items() if name in names},
    )


@dataclass(frozen=True, slots=True)
class RepositoryLimits:
    """Bounded storage and lifecycle policy for an export repository."""

    observation_bytes: int = 1024 * 1024
    observations_per_producer: int = 256
    observation_relation_bytes: int = 16 * 1024 * 1024
    retained_producers: int = 32
    retained_identities: int = 128
    retained_generations_per_identity: int = 4
    retained_generations: int = 128
    retained_prepared_states: int = 4096
    metadata_bytes: int = 16 * 1024 * 1024
    prepared_state_bytes: int = 512 * 1024 * 1024
    generation_bytes: int = 1024 * 1024 * 1024
    repository_bytes: int = 2 * 1024 * 1024 * 1024
    lease_ttl_seconds: float = 30.0
    lease_heartbeat_seconds: float = 5.0

    def __post_init__(self) -> None:
        integers = (
            "observation_bytes",
            "observations_per_producer",
            "observation_relation_bytes",
            "retained_producers",
            "retained_identities",
            "retained_generations_per_identity",
            "retained_generations",
            "retained_prepared_states",
            "metadata_bytes",
            "prepared_state_bytes",
            "generation_bytes",
            "repository_bytes",
        )
        for name in integers:
            positive_integer(getattr(self, name), name)
        for name in ("lease_ttl_seconds", "lease_heartbeat_seconds"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")
        if self.lease_heartbeat_seconds >= self.lease_ttl_seconds:
            raise ValueError("lease heartbeat must be shorter than its TTL")


@dataclass(frozen=True, slots=True)
class PreparedStateRecord:
    producer_sha256: str
    output_plan_sha256: str
    state_fingerprint: str
    instance: str
    path: Path
    metadata: Mapping[str, JsonValue]
    files: frozenset[str]
    content_bytes: int

    def __post_init__(self) -> None:
        digest(self.producer_sha256, "producer_sha256")
        digest(self.output_plan_sha256, "output_plan_sha256")
        digest(self.state_fingerprint, "state_fingerprint")
        digest(self.instance, "prepared state instance")
        if not self.path.is_absolute():
            raise ValueError("prepared state path must be absolute")
        parsed = portable_json(self.metadata, "prepared state metadata")
        if not isinstance(parsed, dict):
            raise TypeError("prepared state metadata must be an object")
        object.__setattr__(self, "metadata", _freeze_mapping(parsed))


@dataclass(frozen=True, slots=True)
class ExportGenerationRecord:
    identity: RepositoryIdentity
    instance: str
    path: Path
    state_fingerprints: tuple[str, ...]
    captured_observation_revision: int
    content_bytes: int

    def __post_init__(self) -> None:
        digest(self.instance, "export generation instance")
        if not self.path.is_absolute():
            raise ValueError("export generation path must be absolute")
        if self.state_fingerprints != tuple(sorted(set(self.state_fingerprints))):
            raise ValueError("state_fingerprints must be sorted and unique")
        for fingerprint in self.state_fingerprints:
            digest(fingerprint, "state_fingerprints item")
        if (
            not isinstance(self.captured_observation_revision, int)
            or isinstance(self.captured_observation_revision, bool)
            or self.captured_observation_revision < 0
        ):
            raise ValueError("captured_observation_revision must be non-negative")
        if (
            not isinstance(self.content_bytes, int)
            or isinstance(self.content_bytes, bool)
            or self.content_bytes < 0
        ):
            raise ValueError("content_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    path: Path
    producers: int
    observations: int
    prepared_states: int
    identities: int
    generations: int
    content_bytes: int
    active_leases: int

    def to_dict(self) -> JsonObject:
        return {
            "path": str(self.path),
            "producers": self.producers,
            "observations": self.observations,
            "prepared_states": self.prepared_states,
            "identities": self.identities,
            "generations": self.generations,
            "content_bytes": self.content_bytes,
            "active_leases": self.active_leases,
        }


@dataclass(frozen=True, slots=True)
class PruneResult:
    prepared_states: int
    generations: int
    bytes_released: int
    dry_run: bool

    def to_dict(self) -> JsonObject:
        return {
            "prepared_states": self.prepared_states,
            "generations": self.generations,
            "bytes_released": self.bytes_released,
            "dry_run": self.dry_run,
        }


JsonMapping: TypeAlias = Mapping[str, JsonValue]


def thaw_mapping(value: Mapping[str, JsonValue]) -> JsonObject:
    return cast(JsonObject, portable_json(value, "mapping"))


def _freeze_mapping(value: JsonObject) -> Mapping[str, JsonValue]:
    return MappingProxyType({name: _freeze(item) for name, item in value.items()})


def _freeze(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return cast(
            JsonValue,
            MappingProxyType({name: _freeze(item) for name, item in value.items()}),
        )
    if isinstance(value, list):
        return cast(JsonValue, tuple(_freeze(item) for item in value))
    return value


__all__ = [
    "MAX_SQLITE_INTEGER",
    "ExportGenerationRecord",
    "ObservationSnapshot",
    "ObservedState",
    "PreparedStateRecord",
    "PruneResult",
    "RepositoryBusyError",
    "RepositoryError",
    "RepositoryFenceError",
    "RepositoryIdentity",
    "RepositoryIntegrityError",
    "RepositoryLimitError",
    "RepositoryLimits",
    "RepositoryReservationTimeoutError",
    "RepositoryStatus",
    "RepositoryUnavailableError",
    "SnapshotObservation",
    "digest",
    "positive_integer",
    "thaw_mapping",
]
