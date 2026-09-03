from __future__ import annotations

from dataclasses import dataclass

from marimo_export._repository.models import RepositoryIdentity


@dataclass(frozen=True, slots=True)
class StateRow:
    state_key: str
    producer_sha256: str
    output_plan_sha256: str
    state_fingerprint: str
    instance: str
    metadata: bytes
    metadata_bytes: int
    content_bytes: int


@dataclass(frozen=True, slots=True)
class GenerationRow:
    identity: RepositoryIdentity
    identity_key: str
    instance: str
    metadata: bytes
    metadata_bytes: int
    captured_observation_revision: int
    content_bytes: int


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    corrupt_producers: frozenset[str]
    active_staging: frozenset[str]
    states: tuple[StateRow, ...]
    generations: tuple[GenerationRow, ...]


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("The export repository integer is invalid")
    return value


def blob(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("The export repository blob is invalid")
    return value


def state_row(row: tuple[object, ...]) -> StateRow:
    return StateRow(
        state_key=str(row[0]),
        producer_sha256=str(row[1]),
        output_plan_sha256=str(row[2]),
        state_fingerprint=str(row[3]),
        instance=str(row[4]),
        metadata=blob(row[5]),
        metadata_bytes=integer(row[6]),
        content_bytes=integer(row[7]),
    )


def generation_row(row: tuple[object, ...]) -> GenerationRow:
    identity = RepositoryIdentity(
        producer_sha256=str(row[1]),
        output_plan_sha256=str(row[2]),
        spec_sha256=str(row[3]),
    )
    if identity.key != str(row[0]):
        raise ValueError("The export repository identity key is stale")
    return GenerationRow(
        identity=identity,
        identity_key=str(row[0]),
        instance=str(row[4]),
        metadata=blob(row[5]),
        metadata_bytes=integer(row[6]),
        captured_observation_revision=integer(row[7]),
        content_bytes=integer(row[8]),
    )


__all__ = [
    "GenerationRow",
    "RecoverySnapshot",
    "StateRow",
    "blob",
    "generation_row",
    "integer",
    "state_row",
]
