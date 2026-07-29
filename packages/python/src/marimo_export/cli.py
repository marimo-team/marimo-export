from __future__ import annotations

import argparse
import json
import math
import secrets
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NoReturn, cast

from marimo_export._build import build
from marimo_export.client import Client, capture
from marimo_export.errors import (
    CodecError,
    CompatibilityError,
    ExecutionError,
    IntegrityError,
    MarimoExportError,
    OutputError,
    PublicationError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.publication import ScalarDescriptor
from marimo_export.reader import Publication, VerificationResult, open_publication
from marimo_export.spec import ExportSpec

EXIT_INPUT = 2
EXIT_TRANSPORT = 3
EXIT_SESSION = 4
EXIT_EXECUTION = 5
EXIT_INTEGRITY = 6
EXIT_FILESYSTEM = 7
EXIT_BROKEN_PIPE = 141


@dataclass(frozen=True, slots=True)
class _CommandResult:
    value: object
    human: str


class _ArgumentParser(argparse.ArgumentParser):
    json_errors = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        if self.json_errors:
            _write_json(
                {
                    "error": {
                        "code": "invalid_arguments",
                        "message": _bounded(message),
                    },
                    "ok": False,
                }
            )
        else:
            self.print_usage(sys.stderr)
            self._print_message(f"{self.prog}: error: {_bounded(message)}\n", sys.stderr)
        raise SystemExit(EXIT_INPUT)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the marimo-export command-line interface."""

    try:
        return _parse_and_execute(argv)
    except BrokenPipeError:
        with suppress(OSError):
            sys.stdout.close()
        return EXIT_BROKEN_PIPE


def _parse_and_execute(argv: Sequence[str] | None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    _ArgumentParser.json_errors = "--json" in raw
    arguments = _parser().parse_args(raw)
    return _execute(arguments)


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="marimo-export",
        description="Publish finite marimo state matrices for Python-free clients.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    build_parser = commands.add_parser(
        "build",
        help="publish a notebook through an owned loopback server",
        description=(
            "Start a loopback marimo server, execute every state, and publish every output."
        ),
        epilog=(
            "Example:\n"
            "  marimo-export build stocks.py --spec stocks.export.yaml "
            "--output dist/stocks\n\n"
            "Exit categories: 2 input, 4 server, 5 execution, 7 filesystem."
        ),
    )
    build_parser.add_argument("notebook", metavar="NOTEBOOK", help="marimo Python notebook")
    _add_publication_options(build_parser)
    _add_timeout(build_parser, "server readiness and network inactivity timeout")

    capture_parser = commands.add_parser(
        "capture",
        help="publish from an existing marimo session",
        description="Execute every state in a borrowed live session and publish every output.",
        epilog=(
            "Example:\n"
            "  marimo-export capture http://127.0.0.1:2718 --session s_01 "
            "--spec stocks.export.yaml --output dist/stocks\n\n"
            "Credentials also use MARIMO_EXPORT_ACCESS_TOKEN and "
            "MARIMO_EXPORT_SERVER_TOKEN.\n"
            "Exit categories: 2 input, 3 transport, 4 session, 5 execution, 7 filesystem."
        ),
    )
    capture_parser.add_argument("server", metavar="SERVER", help="absolute marimo server URL")
    _add_publication_options(capture_parser)
    _add_session_selection(capture_parser)
    _add_connection_options(capture_parser)

    session_parser = commands.add_parser(
        "session",
        help="list sessions or inspect one session",
        description="Discover live sessions and the notebook definitions available as inputs.",
        epilog=(
            "Example:\n"
            "  marimo-export session http://127.0.0.1:2718 --session s_01\n\n"
            "Credentials also use MARIMO_EXPORT_ACCESS_TOKEN and "
            "MARIMO_EXPORT_SERVER_TOKEN.\n"
            "Exit categories: 2 input, 3 transport, 4 session."
        ),
    )
    session_parser.add_argument("server", metavar="SERVER", help="absolute marimo server URL")
    _add_session_selection(session_parser)
    _add_connection_options(session_parser)
    _add_json(session_parser)

    inspect_parser = commands.add_parser(
        "inspect",
        help="inspect publication metadata",
        description="Validate index.json and summarize states, inputs, outputs, and assets.",
        epilog=(
            "Example:\n"
            "  marimo-export inspect dist/stocks\n\n"
            "Exit categories: 2 input, 6 publication, 7 filesystem."
        ),
    )
    inspect_parser.add_argument("publication", metavar="PUBLICATION", help="publication directory")
    _add_json(inspect_parser)

    verify_parser = commands.add_parser(
        "verify",
        help="verify every publication asset",
        description="Read every asset and verify hashes, lengths, codecs, and BlobAsset envelopes.",
        epilog=(
            "Example:\n"
            "  marimo-export verify dist/stocks\n\n"
            "Exit categories: 2 input, 6 integrity, 7 filesystem."
        ),
    )
    verify_parser.add_argument("publication", metavar="PUBLICATION", help="publication directory")
    _add_json(verify_parser)
    return parser


def _add_publication_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spec", required=True, metavar="FILE", help="JSON or YAML ExportSpec")
    parser.add_argument("--output", required=True, metavar="DIR", help="publication destination")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="atomically replace an existing real directory",
    )
    _add_json(parser)


def _add_session_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", dest="session_id", metavar="ID", help="live session ID")


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--access-token", metavar="TOKEN", help="browser access token")
    parser.add_argument("--server-token", metavar="TOKEN", help="server authentication token")
    _add_timeout(parser, "connection and network inactivity timeout")


def _add_timeout(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=30.0,
        metavar="SECONDS",
        help=f"{help_text}; progress resets it (default: 30)",
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit one machine-readable result")


def _execute(arguments: argparse.Namespace) -> int:
    json_mode = bool(arguments.json)
    try:
        result = _run(arguments)
        if json_mode:
            _write_json({"ok": True, "result": _json_value(result.value)})
        else:
            sys.stdout.write(result.human)
            if result.human and not result.human.endswith("\n"):
                sys.stdout.write("\n")
        return 0
    except BrokenPipeError:
        raise
    except KeyboardInterrupt:
        return _failure(json_mode, 130, "interrupted", "operation interrupted")
    except Exception as error:
        exit_code = _exit_code(error)
        if exit_code is None:
            request_id = secrets.token_hex(6)
            return _failure(
                json_mode,
                1,
                "internal_error",
                f"internal failure; request ID {request_id}",
            )
        code = error.code if isinstance(error, MarimoExportError) else "invalid_arguments"
        details = error.details if isinstance(error, MarimoExportError) else None
        return _failure(json_mode, exit_code, code, _bounded(str(error)), details)


def _run(arguments: argparse.Namespace) -> _CommandResult:
    if arguments.command == "build":
        result = build(
            arguments.notebook,
            spec=ExportSpec.from_file(arguments.spec),
            output=arguments.output,
            timeout=arguments.timeout,
            replace=arguments.replace,
        )
        return _CommandResult(result.to_dict(), _publication_human(result, "Published"))

    if arguments.command == "capture":
        result = capture(
            arguments.server,
            spec=ExportSpec.from_file(arguments.spec),
            output=arguments.output,
            session=arguments.session_id,
            access_token=arguments.access_token,
            server_token=arguments.server_token,
            timeout=arguments.timeout,
            replace=arguments.replace,
        )
        lead = f"Captured session {result.session_id}\n" if result.session_id is not None else ""
        return _CommandResult(
            result.to_dict(),
            lead + _publication_human(result, "Published"),
        )

    if arguments.command == "session":
        with Client(
            arguments.server,
            access_token=arguments.access_token,
            server_token=arguments.server_token,
            timeout=arguments.timeout,
        ) as client:
            if arguments.session_id is None:
                sessions = client.sessions()
                value = {
                    "sessions": [
                        {"id": item.id, "filename": item.filename, "path": item.path}
                        for item in sessions
                    ]
                }
                return _CommandResult(value, _sessions_human(value["sessions"]))
            description = client.session(arguments.session_id).inspect()
            return _CommandResult(description.to_dict(), _session_human(description.to_dict()))

    publication = open_publication(arguments.publication)
    if arguments.command == "inspect":
        summary = _publication_summary(publication)
        return _CommandResult(summary, _inspect_human(summary))
    if arguments.command == "verify":
        verified = publication.verify()
        return _CommandResult(
            verified.to_dict(),
            _verify_human(verified, len(publication.states())),
        )
    raise AssertionError(f"unknown command {arguments.command!r}")


def _publication_summary(publication: Publication) -> dict[str, object]:
    states = publication.states()
    first = states[0]
    representations = {
        output.name: {"codec": output.codec, "media_type": output.media_type}
        for output in first.outputs()
    }
    unique_assets: dict[tuple[str, str], int] = {}
    for state in states:
        for output in state.outputs():
            descriptor = output.descriptor
            if isinstance(descriptor, ScalarDescriptor):
                continue
            unique_assets[(descriptor.codec, descriptor.asset.sha256)] = descriptor.asset.size
    return {
        "asset_bytes": sum(unique_assets.values()),
        "assets": len(unique_assets),
        "inputs": list(publication.input_names),
        "notebook": publication.notebook.to_value(),
        "outputs": list(publication.output_names),
        "path": str(publication.path),
        "producer": publication.producer.to_value(),
        "representations": representations,
        "schema": "marimo-export.publication.v1",
        "states": [
            {
                "fingerprint": state.fingerprint,
                "inputs": _json_value(state.inputs),
                "name": state.name,
            }
            for state in states
        ],
    }


def _publication_human(result: object, verb: str) -> str:
    from marimo_export.publication import PublicationResult

    assert isinstance(result, PublicationResult)
    lines = [
        f"{verb} {len(result.states)} states and {len(result.outputs)} outputs to {result.path}",
        f"Assets: {result.assets} files, {_bytes(result.asset_bytes)}",
        (
            "Projection cache: "
            f"{_count(result.projection_cache.hits, 'hit')}, "
            f"{_count(result.projection_cache.misses, 'miss')}"
        ),
        (
            "Upstream cache activity: "
            f"{_count(result.upstream_cache.hits, 'hit')}, "
            f"{_count(result.upstream_cache.misses, 'miss')}"
        ),
    ]
    phase_values = []
    for label, value in (
        ("server start", result.timings.server_start_seconds),
        ("initial autorun", result.timings.initial_autorun_seconds),
        ("capture", result.timings.capture_seconds),
        ("server shutdown", result.timings.server_shutdown_seconds),
        ("publication write", result.timings.publication_write_seconds),
        ("total", result.timings.total_seconds),
    ):
        if value is not None:
            phase_values.append(f"{label} {_seconds(value)}")
    lines.append("Phase timings: " + ", ".join(phase_values))
    child = result.timings.fresh_children
    lines.append(
        f"Fresh-child timings ({child.states} states): "
        f"construction {_seconds(child.construction_seconds)}, "
        f"upstream execution {_seconds(child.upstream_execution_seconds)}, "
        f"UI application {_seconds(child.ui_application_seconds)}, "
        f"projection execution {_seconds(child.projection_execution_seconds)}, "
        f"cleanup {_seconds(child.cleanup_seconds)}"
    )
    lines.extend(f"warning: {warning.message}" for warning in result.warnings)
    return "\n".join(lines)


def _sessions_human(sessions: object) -> str:
    assert isinstance(sessions, list)
    lines = ["ID\tNotebook\tPath"]
    for value in sessions:
        assert isinstance(value, Mapping)
        lines.append(
            f"{value.get('id', '')}\t{value.get('filename') or ''}\t{value.get('path') or ''}"
        )
    return "\n".join(lines)


def _session_human(value: Mapping[str, object]) -> str:
    capabilities = _strings(value["capabilities"], "capabilities")
    lines = [
        f"Session: {value['session_id']}",
        f"Notebook: {value.get('filename') or '(unknown)'}",
        f"Document: {value['document_sha256']}",
        f"Runtime: marimo {value['marimo_version']}, marimo-export "
        f"{value['marimo_export_version']}",
        "Capabilities: " + ", ".join(capabilities),
        "Definitions:",
    ]
    definitions = _list(value["definitions"], "definitions")
    for definition in definitions:
        item = _object(definition, "definition")
        status = "portable" if item["portable_input"] else "producer-only"
        lines.append(f"  {item['name']}  {item['kind']}  {item['python_type']}  {status}")
    return "\n".join(lines)


def _inspect_human(value: Mapping[str, object]) -> str:
    notebook = _object(value["notebook"], "notebook")
    producer = _object(value["producer"], "producer")
    inputs = _strings(value["inputs"], "inputs")
    outputs = _strings(value["outputs"], "outputs")
    lines = [
        f"Notebook: {notebook.get('filename') or '(unknown)'}",
        f"Document: {notebook['document_sha256']}",
        f"Producer: marimo {producer['marimo']}, marimo-export {producer['marimo_export']}",
        "Inputs: " + ", ".join(inputs),
        "Outputs: " + ", ".join(outputs),
        "Representations:",
    ]
    representations = _object(value["representations"], "representations")
    for name, representation in representations.items():
        item = _object(representation, "representation")
        lines.append(f"  {name}  {item['codec']}  {item['media_type']}")
    lines.append("States:")
    states = _list(value["states"], "states")
    for state in states:
        item = _object(state, "state")
        lines.append(f"  {item['name']}  {item['fingerprint']}")
    lines.extend(
        [
            f"Assets declared: {value['assets']}",
            f"Bytes declared: {_bytes(_integer(value['asset_bytes'], 'asset_bytes'))}",
        ]
    )
    return "\n".join(lines)


def _verify_human(result: VerificationResult, states: int) -> str:
    return (
        f"Verified {result.assets} assets and {_bytes(result.bytes_verified)} for {states} states"
    )


def _failure(
    json_mode: bool,
    exit_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> int:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error["details"] = _json_value(details)
    if json_mode:
        _write_json({"error": error, "ok": False})
    else:
        print(f"error: {message}", file=sys.stderr)
    return exit_code


def _exit_code(error: BaseException) -> int | None:
    if isinstance(error, (SpecError, TypeError, ValueError)):
        return EXIT_INPUT
    if isinstance(error, TransportError):
        return EXIT_TRANSPORT
    if isinstance(error, (SessionError, CompatibilityError)):
        return EXIT_SESSION
    if isinstance(error, IntegrityError):
        return EXIT_INTEGRITY
    if isinstance(error, PublicationError):
        if error.code.startswith("destination_") or error.code in {
            "publication_commit_failed",
            "replacement_unavailable",
        }:
            return EXIT_FILESYSTEM
        return EXIT_INTEGRITY
    if isinstance(error, (ExecutionError, OutputError, CodecError)):
        if error.code.startswith("server_"):
            return EXIT_SESSION
        return EXIT_EXECUTION
    if isinstance(error, OSError):
        return EXIT_FILESYSTEM
    return None


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return timeout


def _write_json(value: Mapping[str, object]) -> None:
    sys.stdout.write(
        json.dumps(
            _json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"CLI result contains unsupported {type(value).__name__}")


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


def _bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    amount = float(value)
    for unit in ("KiB", "MiB", "GiB"):
        amount /= 1024
        if amount < 1024 or unit == "GiB":
            return f"{amount:.1f} {unit}"
    raise AssertionError


def _seconds(value: float) -> str:
    return f"{value:.3f}s"


def _count(value: int, noun: str) -> str:
    plural = noun + ("es" if noun.endswith("s") else "s")
    return f"{value} {noun if value == 1 else plural}"


def _bounded(value: str) -> str:
    if len(value) <= 2_048:
        return value
    return value[:2_045] + "..."


def _package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0.0.0"


__all__ = [
    "EXIT_BROKEN_PIPE",
    "EXIT_EXECUTION",
    "EXIT_FILESYSTEM",
    "EXIT_INPUT",
    "EXIT_INTEGRITY",
    "EXIT_SESSION",
    "EXIT_TRANSPORT",
    "main",
]
