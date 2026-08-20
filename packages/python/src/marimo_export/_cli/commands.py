from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

import marimo_export as sdk
import marimo_export._delivery as delivery
import marimo_export.diagnostics as diagnostics
import marimo_export.inspection as inspection
import marimo_export.sessions as sessions
from marimo_export.progress import ProgressEvent
from marimo_export.repository import PruneResult, RepositoryStatus

from .arguments import package_version
from .errors import EXIT_PLANNING


@dataclass(frozen=True, slots=True)
class CommandResult:
    kind: str
    value: object
    warnings: tuple[str, ...] = ()
    ok: bool = True
    exit_code: int = 0


def run(
    arguments: argparse.Namespace,
    progress: Callable[[ProgressEvent], None],
) -> CommandResult:
    """Delegate parsed arguments to public SDK and repository operations."""

    if arguments.command == "plan":
        with _open_repository(arguments.repository) as repository:
            resolved = sdk.plan(
                arguments.notebook,
                spec=sdk.ExportSpec.from_file(arguments.spec),
                repository=repository,
                timeout=arguments.timeout,
                progress=progress,
            )
        return CommandResult("plan", resolved.to_dict())

    if arguments.command == "build":
        with _open_repository(arguments.repository) as repository:
            result = sdk.build(
                arguments.notebook,
                spec=sdk.ExportSpec.from_file(arguments.spec),
                output=arguments.output,
                repository=repository,
                timeout=arguments.timeout,
                replace=arguments.replace,
                progress=progress,
            )
        return CommandResult(
            "build",
            result.to_dict(),
            tuple(warning.message for warning in result.warnings),
        )

    if arguments.command == "capture":
        delivery.preflight_export_destination(
            arguments.output,
            replace=arguments.replace,
        )
        with (
            _open_repository(arguments.repository) as repository,
            sdk.capture(
                arguments.server,
                session=arguments.session_id,
                spec=sdk.ExportSpec.from_file(arguments.spec),
                repository=repository,
                timeout=arguments.timeout,
                progress=progress,
            ) as prepared,
        ):
            result = prepared.write(
                arguments.output,
                replace=arguments.replace,
                progress=progress,
            )
        return CommandResult(
            "capture",
            result.to_dict(),
            tuple(warning.message for warning in result.warnings),
        )

    if arguments.command == "inspect":
        return _inspect(arguments)

    if arguments.command == "verify":
        verified = sdk.verify_export(arguments.export)
        return CommandResult("verify", verified.to_dict())

    if arguments.command == "observations":
        with _open_repository(arguments.repository) as repository:
            resolved = sdk.plan(
                arguments.notebook,
                spec=sdk.ExportSpec.from_file(arguments.spec),
                repository=repository,
                timeout=arguments.timeout,
                progress=progress,
            )
            if arguments.observations_command == "list":
                return CommandResult(
                    "observations-list",
                    {
                        "producer_sha256": resolved.producer_sha256,
                        "observation_revision": resolved.observation_revision,
                        "inputs": list(resolved.inputs),
                        "observations": [
                            {
                                "fingerprint": observed.fingerprint,
                                "revision": observed.revision,
                                "values": observed.values,
                            }
                            for observed in resolved.observations
                        ],
                    },
                )
            if arguments.observations_command == "clear":
                cleared = repository.clear_observations(resolved)
                return CommandResult(
                    "observations-clear",
                    {
                        "producer_sha256": resolved.producer_sha256,
                        "observation_revision": resolved.observation_revision,
                        "cleared": cleared,
                    },
                )

    if arguments.command == "repository":
        with _open_repository(arguments.repository) as repository:
            if arguments.repository_command == "status":
                return CommandResult("repository-status", _status_value(repository.status()))
            if arguments.repository_command == "prune":
                return CommandResult(
                    "repository-prune",
                    _prune_value(
                        repository.prune(dry_run=arguments.dry_run),
                        repository.path,
                    ),
                )

    if arguments.command == "doctor":
        with _open_repository(arguments.repository) as repository:
            status = repository.status()
            compatibility = cast(dict[str, object], diagnostics.marimo_compatibility().to_dict())
            compatible = compatibility["status"] == "pass"
            value = {
                "ok": compatible,
                "repository": _status_value(status),
                "marimo": compatibility,
                "python": {
                    "executable": sys.executable,
                    "version": ".".join(str(part) for part in sys.version_info[:3]),
                },
                "marimo_export": {"version": package_version()},
            }
        return CommandResult(
            "doctor",
            value,
            ok=compatible,
            exit_code=0 if compatible else EXIT_PLANNING,
        )

    raise AssertionError(f"unknown command {arguments.command!r}")


def _inspect(arguments: argparse.Namespace) -> CommandResult:
    if not _is_server(arguments.source):
        if arguments.session_id is not None:
            raise ValueError("--session applies only to a live server")
        description = inspection.inspect_notebook(arguments.source, timeout=arguments.timeout)
        return CommandResult("inspect", description.to_dict())

    with sessions.Client(
        arguments.source,
        timeout=arguments.timeout,
    ) as client:
        if arguments.session_id is None:
            values = client.sessions()
            return CommandResult(
                "sessions",
                {
                    "sessions": [
                        {"id": item.id, "filename": item.filename, "path": item.path}
                        for item in values
                    ]
                },
            )
        description = client.session(arguments.session_id).inspect()
        return CommandResult("inspect", description.to_dict())


def _open_repository(path: str | None) -> sdk.ExportRepository:
    return sdk.ExportRepository.open(path)


def _status_value(status: RepositoryStatus) -> dict[str, object]:
    return {
        "path": str(status.path),
        "producers": status.producers,
        "observations": status.observations,
        "prepared_states": status.prepared_states,
        "identities": status.identities,
        "generations": status.generations,
        "content_bytes": status.content_bytes,
        "active_leases": status.active_leases,
    }


def _prune_value(result: PruneResult, repository: Path) -> dict[str, object]:
    value = cast(dict[str, object], asdict(result))
    value["repository"] = str(repository)
    return value


def _is_server(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


__all__ = ["CommandResult", "run"]
