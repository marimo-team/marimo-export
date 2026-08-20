from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from marimo_export.progress import ProgressEvent

from .arguments import OutputMode, UsageError
from .commands import CommandResult


def progress_callback(
    mode: OutputMode,
    *,
    secrets: tuple[str, ...] = (),
) -> Callable[[ProgressEvent], None]:
    def progress(event: ProgressEvent) -> None:
        if mode is OutputMode.JSON:
            return
        if mode is OutputMode.JSONL:
            progress_value = _redact_value(event.to_dict(), secrets)
            write_json({"type": "progress", "progress": progress_value})
            return
        print(redact(_progress_human(event), secrets), file=sys.stderr)

    return progress


def render_result(result: CommandResult, mode: OutputMode) -> None:
    if mode is OutputMode.JSON:
        write_json({"ok": result.ok, "result": json_value(result.value)})
        return
    if mode is OutputMode.JSONL:
        write_json(
            {
                "type": "result",
                "ok": result.ok,
                "result": json_value(result.value),
            }
        )
        return
    human = _human(result.kind, _object(result.value, "command result"))
    sys.stdout.write(human)
    if human and not human.endswith("\n"):
        sys.stdout.write("\n")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def render_usage_error(
    error: UsageError,
    mode: OutputMode,
    *,
    secrets: tuple[str, ...],
) -> None:
    message = bounded(redact(error.message, secrets))
    if mode is OutputMode.JSON:
        write_json(_error_envelope("invalid_arguments", message))
    elif mode is OutputMode.JSONL:
        write_json(_jsonl_error_envelope("invalid_arguments", message))
    else:
        sys.stderr.write(error.usage)
        sys.stderr.write(f"{error.prog}: error: {message}\n")


def render_failure(
    mode: OutputMode,
    code: str,
    message: str,
    details: object | None = None,
    *,
    secrets: tuple[str, ...] = (),
) -> None:
    safe_message = bounded(redact(message, secrets))
    safe_details = _redact_value(details, secrets)
    if mode is OutputMode.JSON:
        write_json(_error_envelope(code, safe_message, safe_details))
    elif mode is OutputMode.JSONL:
        write_json(_jsonl_error_envelope(code, safe_message, safe_details))
    else:
        print(f"error: {safe_message}", file=sys.stderr)


def write_json(value: Mapping[str, object]) -> None:
    sys.stdout.write(
        json.dumps(
            json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    raise TypeError(f"CLI result cannot serialize {type(value).__name__}")


def redact(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in sorted(set(secrets), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    return result


def bounded(value: str) -> str:
    if len(value) <= 2_048:
        return value
    return value[:2_045] + "..."


def _human(kind: str, value: Mapping[str, object]) -> str:
    if kind == "plan":
        return _plan_human(value)
    if kind == "build":
        return _build_human(value)
    if kind == "capture":
        return _capture_human(value)
    if kind == "inspect":
        return _inspection_human(value)
    if kind == "sessions":
        return _sessions_human(value["sessions"])
    if kind == "verify":
        return _verify_human(value)
    if kind == "observations-list":
        return _observations_human(value)
    if kind == "observations-clear":
        return "\n".join(
            (
                f"Producer: {value['producer_sha256']}",
                f"Observation revision: {value['observation_revision']}",
                f"Cleared {_count(_integer(value['cleared'], 'cleared'), 'observation')}",
            )
        )
    if kind == "repository-status":
        return _status_human(value)
    if kind == "repository-prune":
        return _prune_human(value)
    if kind == "doctor":
        return _doctor_human(value)
    raise AssertionError(f"unknown command result {kind!r}")


def _progress_human(event: ProgressEvent) -> str:
    label = event.kind.replace("_", " ").capitalize()
    if event.state is not None:
        label += f": {event.state}"
    if event.completed is not None and event.total is not None:
        label += f" ({event.completed}/{event.total})"
    if event.cache is not None:
        cache = event.cache
        label += (
            f" | authored {cache.authored_hits} hit, {cache.authored_misses} miss"
            f" | projections {cache.projection_hits} hit, {cache.projection_misses} miss"
        )
    if event.elapsed_seconds is not None:
        label += f" | {event.elapsed_seconds:.3f}s"
    if event.message:
        label += f" | {event.message}"
    return label


def _plan_human(value: Mapping[str, object]) -> str:
    return "\n".join(
        (
            f"Producer: {value['producer_sha256']}",
            "Inputs: " + ", ".join(_strings(value["inputs"], "inputs")),
            f"States: {len(_list(value['states'], 'states'))}",
            f"Default: {value['default_alias']}",
            f"Outputs: {len(_list(value['outputs'], 'outputs'))}",
            f"Reusable: {len(_list(value['reusable_states'], 'reusable states'))}",
            f"To prepare: {len(_list(value['missing_states'], 'missing states'))}",
            f"Observed: {len(_list(value['observations'], 'observations'))}",
        )
    )


def _build_human(value: Mapping[str, object]) -> str:
    return _export_human(value, action="Built")


def _capture_human(value: Mapping[str, object]) -> str:
    return _export_human(value, action="Captured")


def _export_human(value: Mapping[str, object], *, action: str) -> str:
    plan = _object(value["plan"], "plan")
    cache = _object(value["cache_activity"], "cache activity")
    return "\n".join(
        (
            f"{action} notebook export at {value['path']}",
            f"Identity: {value['identity']}",
            f"States: {len(_list(plan['states'], 'states'))}",
            f"Prepared: {len(_list(value['prepared_states'], 'prepared states'))}",
            f"Reused: {len(_list(value['reused_states'], 'reused states'))}",
            f"Outputs: {len(_list(plan['outputs'], 'outputs'))}",
            (
                f"Assets: {value['assets']} files, "
                f"{_bytes(_integer(value['asset_bytes'], 'asset bytes'))}"
            ),
            (
                "Marimo cache: "
                f"{cache['authored_hits']} authored hits, "
                f"{cache['authored_misses']} authored misses"
            ),
            (
                "Projection cache: "
                f"{cache['projection_hits']} hits, {cache['projection_misses']} misses"
            ),
            f"Elapsed: {_number(value['elapsed_seconds'], 'elapsed seconds'):.3f}s",
        )
    )


def _inspection_human(value: Mapping[str, object]) -> str:
    lines = [
        f"Session: {value['session_id']}",
        f"Notebook: {value.get('filename') or '(unknown)'}",
        f"Document: {value['document_sha256']}",
        f"Runtime: marimo {value['marimo_version']}, marimo-export "
        f"{value['marimo_export_version']}",
        "Capabilities: " + ", ".join(_strings(value["capabilities"], "capabilities")),
        "Definitions:",
    ]
    for definition in _list(value["definitions"], "definitions"):
        item = _object(definition, "definition")
        status = "input-capable" if item["portable_input"] else "output-only"
        lines.append(f"  {item['name']}  {item['kind']}  {item['python_type']}  {status}")
    return "\n".join(lines)


def _sessions_human(sessions: object) -> str:
    lines = ["ID\tNotebook\tPath"]
    for value in _list(sessions, "sessions"):
        item = _object(value, "session")
        lines.append(
            f"{item.get('id', '')}\t{item.get('filename') or ''}\t{item.get('path') or ''}"
        )
    return "\n".join(lines)


def _verify_human(value: Mapping[str, object]) -> str:
    return (
        f"Verified {value['assets']} assets and "
        f"{_bytes(_integer(value['bytes_verified'], 'bytes verified'))} "
        f"for {value['states']} states"
    )


def _observations_human(value: Mapping[str, object]) -> str:
    rows = _list(value["observations"], "observations")
    lines = [
        f"Producer: {value['producer_sha256']}",
        "Inputs: " + ", ".join(_strings(value["inputs"], "inputs")),
        f"Observation revision: {value['observation_revision']}",
        f"Observations: {len(rows)}",
    ]
    for row in rows:
        item = _object(row, "observation")
        lines.append(
            f"  {item['fingerprint']}  revision {item['revision']}  "
            + json.dumps(json_value(item["values"]), ensure_ascii=False, sort_keys=True)
        )
    return "\n".join(lines)


def _status_human(value: Mapping[str, object]) -> str:
    return "\n".join(
        (
            f"Repository: {value['path']}",
            f"Producers: {value['producers']}",
            f"Observations: {value['observations']}",
            f"Prepared states: {value['prepared_states']}",
            f"Export identities: {value['identities']}",
            f"Generations: {value['generations']}",
            f"Content: {_bytes(_integer(value['content_bytes'], 'content bytes'))}",
            f"Active leases: {value['active_leases']}",
        )
    )


def _prune_human(value: Mapping[str, object]) -> str:
    dry_run = value["dry_run"]
    if not isinstance(dry_run, bool):
        raise TypeError("dry_run must be a boolean")
    action = "Would prune" if dry_run else "Pruned"
    states = _integer(value["prepared_states"], "prepared states")
    generations = _integer(value["generations"], "generations")
    return "\n".join(
        (
            f"Repository: {value['repository']}",
            f"{action} {_count(states, 'prepared state')}",
            f"{action} {_count(generations, 'generation')}",
            f"Bytes released: {_bytes(_integer(value['bytes_released'], 'bytes released'))}",
        )
    )


def _doctor_human(value: Mapping[str, object]) -> str:
    repository = _object(value["repository"], "repository")
    marimo = _object(value["marimo"], "marimo")
    python = _object(value["python"], "python")
    package = _object(value["marimo_export"], "marimo_export")
    details = _object(marimo["details"], "Marimo compatibility details")
    lines = [
        f"Repository: {repository['path']}",
        f"Python: {python['version']} ({python['executable']})",
        f"marimo-export: {package['version']}",
        f"Marimo compatibility: {marimo['status']}",
    ]
    if details.get("version") is not None:
        lines.append(f"Marimo version: {details['version']}")
    if details.get("release_commit") is not None:
        lines.append(f"Marimo release: {details['release_commit']}")
    if marimo.get("message"):
        lines.append(f"Diagnostic: {marimo['message']}")
    return "\n".join(lines)


def _error_envelope(
    code: str,
    message: str,
    details: object | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error["details"] = json_value(details)
    return {"error": error, "ok": False}


def _jsonl_error_envelope(
    code: str,
    message: str,
    details: object | None = None,
) -> dict[str, object]:
    value = _error_envelope(code, message, details)
    return {"type": "error", **value}


def _redact_value(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        return {
            redact(str(key), secrets): _redact_value(item, secrets) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return cast(list[object], value)


def _strings(value: object, label: str) -> list[str]:
    items = _list(value, label)
    if any(not isinstance(item, str) for item in items):
        raise TypeError(f"{label} must contain strings")
    return cast(list[str], items)


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return value


def _number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{label} must be a number")
    return float(value)


def _bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    amount = float(value)
    for unit in ("KiB", "MiB", "GiB"):
        amount /= 1024
        if amount < 1024 or unit == "GiB":
            return f"{amount:.1f} {unit}"
    raise AssertionError


def _count(value: int, noun: str) -> str:
    return f"{value} {noun if value == 1 else noun + 's'}"


__all__ = [
    "bounded",
    "json_value",
    "progress_callback",
    "redact",
    "render_failure",
    "render_result",
    "render_usage_error",
    "write_json",
]
