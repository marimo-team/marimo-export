from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marimo_export._repository.files import VerifiedClosure, verify_export
from marimo_export._repository.leases import LeaseManager
from marimo_export._repository.models import RepositoryLimits
from marimo_export._repository.paths import prepared_state_path
from marimo_export._repository.sqlite.catalog import SqliteCatalog
from marimo_export._repository.sqlite.records import StateRow


@dataclass(frozen=True, slots=True)
class ArtifactContext:
    root: Path
    catalog: SqliteCatalog
    leases: LeaseManager
    limits: RepositoryLimits

    def verify_export(self, path: Path) -> tuple[str, VerifiedClosure]:
        return verify_export(path)

    def state_path(self, row: StateRow) -> Path:
        return prepared_state_path(
            self.root,
            row.producer_sha256,
            row.output_plan_sha256,
            row.state_fingerprint,
            row.instance,
        )


__all__ = ["ArtifactContext"]
