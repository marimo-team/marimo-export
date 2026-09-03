"""Validate the installed Python distribution and released marimo dependency."""

from __future__ import annotations

import sys
from importlib import metadata

_MARIMO_REQUIREMENT = "marimo==0.24.0"
_ROOT_API = {
    "ExportPlan",
    "ExportRepository",
    "ExportResult",
    "ExportSpec",
    "NotebookExport",
    "OutputSpec",
    "PreparedExport",
    "ProgressEvent",
    "StateSpace",
    "VerificationResult",
    "build",
    "capture",
    "open_export",
    "plan",
    "prepare",
    "verify_export",
}
_FOCUSED_NAMES = {
    "BlobAsset",
    "CaptureLimitError",
    "CaptureLimits",
    "Client",
    "OwnedNotebook",
    "Session",
    "DeliveryResult",
    "StagedDelivery",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "document_sha256",
    "inspect_notebook",
    "open_notebook",
    "parse_canonical_json",
    "portable_json",
    "state_fingerprint",
    "stage",
}


def main() -> None:
    requirements = metadata.requires("marimo-export") or []
    if _MARIMO_REQUIREMENT not in requirements:
        raise RuntimeError(f"marimo-export wheel must require {_MARIMO_REQUIREMENT}")

    import marimo_export

    if set(marimo_export.__all__) != _ROOT_API:
        raise RuntimeError("installed marimo-export distribution has an unexpected root API")
    if "sqlite3" in sys.modules or any(name.startswith("marimo._") for name in sys.modules):
        raise RuntimeError("importing marimo-export root must not load runtime implementations")
    for name in _ROOT_API:
        getattr(marimo_export, name)
    for name in _FOCUSED_NAMES:
        try:
            getattr(marimo_export, name)
        except AttributeError:
            pass
        else:
            raise RuntimeError(f"installed marimo-export root must not expose {name}")

    if marimo_export.ExportPlan.__module__ != "marimo_export.planning":
        raise RuntimeError("installed ExportPlan must use its public planning module")
    if marimo_export.ExportRepository.__module__ != "marimo_export.repository":
        raise RuntimeError("installed ExportRepository must use its public repository module")
    if marimo_export.PreparedExport.__module__ != "marimo_export.prepared":
        raise RuntimeError("installed PreparedExport must use its public prepared module")
    if marimo_export.StateSpace.__module__ != "marimo_export.spec":
        raise RuntimeError("installed StateSpace must use its public spec module")

    from marimo_export.delivery import StagedDelivery, stage
    from marimo_export.observations import ObservedInputs
    from marimo_export.outputs import BlobAsset
    from marimo_export.sessions import Client, Session, connect
    from marimo_export.wire import canonical_json_bytes, state_fingerprint

    if BlobAsset.__module__ != "marimo_export.outputs":
        raise RuntimeError("installed BlobAsset must use its focused output module")
    if Client.__module__ != "marimo_export.client" or Session.__module__ != "marimo_export.client":
        raise RuntimeError("installed session records must use the session implementation")
    if not callable(connect):
        raise RuntimeError("installed sessions module must expose connect")
    if ObservedInputs.__module__ != "marimo_export.observations":
        raise RuntimeError("installed observations module must expose ObservedInputs")
    if StagedDelivery.__module__ != "marimo_export.delivery" or not callable(stage):
        raise RuntimeError("installed delivery module must expose staging")
    if len(state_fingerprint({"state": "ready"})) != 64:
        raise RuntimeError("installed wire module must expose state_fingerprint")
    if canonical_json_bytes({"state": "ready"}) != b'{"state":"ready"}':
        raise RuntimeError("installed wire module must expose canonical JSON")

    lifespans = {
        (entry.name, entry.value)
        for entry in metadata.entry_points(group="marimo.kernel.lifespan")
        if entry.dist is not None and entry.dist.name == "marimo-export"
    }
    if lifespans != {("marimo-export", "marimo_export._marimo.entrypoints:kernel_lifespan")}:
        raise RuntimeError("marimo-export wheel must register its managed kernel lifespan")


if __name__ == "__main__":
    main()
