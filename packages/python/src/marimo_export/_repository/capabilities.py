from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

ArtifactKey = tuple[str, str, str]
ArtifactRelease = tuple[ArtifactKey, int]


@dataclass(frozen=True, slots=True)
class LostLifecycle:
    artifacts: frozenset[ArtifactKey]
    staging: frozenset[str]
    reservations: frozenset[str]


class LeaseCatalog(Protocol):
    """Persistence operations required by repository lifecycle ownership."""

    def claim_reservation(
        self,
        owner: str,
        identity_key: str,
        producer_sha256: str,
        output_plan_sha256: str,
        spec_sha256: str,
        expires_at_us: int,
        now_us: int,
        timeout_seconds: float,
    ) -> int | None: ...

    def acquire_staging(
        self,
        owner: str,
        relative_path: str,
        expires_at_us: int,
    ) -> None: ...

    def renew_lifecycle(
        self,
        *,
        owner: str,
        artifacts: Sequence[ArtifactKey],
        staging: Sequence[str],
        reservations: Sequence[str],
        expires_at_us: int,
    ) -> LostLifecycle: ...

    def release_lifecycle(
        self,
        *,
        owner: str,
        artifacts: Sequence[ArtifactRelease] | None = None,
        staging: Sequence[str] | None = None,
        reservations: Sequence[str] | None = None,
    ) -> None: ...


__all__ = ["ArtifactKey", "ArtifactRelease", "LeaseCatalog", "LostLifecycle"]
