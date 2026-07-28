from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NoReturn, TextIO

from marimo_export._diagnostics import safe_diagnostic
from marimo_export.client import Client, _prepare_destination
from marimo_export.errors import (
    CaptureError,
    IntegrityError,
    PublicationError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.reader import open_publication
from marimo_export.spec import load_spec

EXIT_INPUT = 2
EXIT_TRANSPORT = 3
EXIT_SESSION = 4
EXIT_CAPTURE = 5
EXIT_INTEGRITY = 6
EXIT_FILESYSTEM = 7
EXIT_BROKEN_PIPE = 141
_MAX_SAFE_INTEGER = 2**53 - 1


class _CliInputError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    json_errors = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        message = _redact_cli_message(message)
        if self.json_errors:
            _write_json(
                sys.stdout,
                {
                    "ok": False,
                    "error": {"code": "invalid_arguments", "message": message},
                },
            )
        else:
            self.print_usage(sys.stderr)
            self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        raise SystemExit(EXIT_INPUT)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="marimo-export",
        description="Capture and read static publications from a running marimo notebook.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    session = commands.add_parser("session", help="list sessions or inspect one session")
    session.add_argument("server", help="marimo server URL")
    session.add_argument(
        "--session",
        dest="session_id",
        type=_session_id,
        help="session identifier",
    )
    _add_connection_options(session)
    _add_json_option(session)

    capture = commands.add_parser("capture", help="capture selected notebook results")
    capture.add_argument("server", help="marimo server URL")
    capture.add_argument("--spec", required=True, help="export specification file")
    capture.add_argument("--output", required=True, help="destination publication directory")
    capture.add_argument(
        "--session",
        dest="session_id",
        type=_session_id,
        help="session identifier",
    )
    capture.add_argument("--replace", action="store_true", help="replace an existing publication")
    _add_index_limit(capture)
    _add_asset_limit(capture)
    _add_publication_limit(capture)
    _add_connection_options(capture)
    _add_json_option(capture)

    inspect = commands.add_parser("inspect", help="inspect a local publication")
    inspect.add_argument("publication", help="publication directory")
    _add_index_limit(inspect)
    _add_asset_limit(inspect)
    _add_publication_limit(inspect)
    _add_json_option(inspect)

    read = commands.add_parser("read", help="read one published format")
    read.add_argument("publication", help="publication directory")
    read.add_argument("output_name", metavar="OUTPUT", help="published output name")
    read.add_argument("--variant", required=True, help="variant name")
    read.add_argument("--format", dest="format_name", required=True, help="format name")
    read.add_argument("--to", dest="output_file", help="write bytes to this file")
    _add_index_limit(read)
    _add_asset_limit(read)
    _add_publication_limit(read)
    _add_json_option(read)

    verify = commands.add_parser("verify", help="verify every publication asset")
    verify.add_argument("publication", help="publication directory")
    _add_index_limit(verify)
    _add_asset_limit(verify)
    _add_publication_limit(verify)
    _add_json_option(verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _parse_and_execute(argv)
    except BrokenPipeError:
        with suppress(OSError):
            sys.stdout.close()
        return EXIT_BROKEN_PIPE


def _parse_and_execute(argv: Sequence[str] | None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    _ArgumentParser.json_errors = "--json" in raw_arguments
    parser = _build_parser()
    arguments = parser.parse_args(raw_arguments)
    json_mode = bool(arguments.json)
    return _execute(arguments, json_mode=json_mode)


def _execute(arguments: argparse.Namespace, *, json_mode: bool) -> int:
    try:
        result = _run(arguments, json_mode=json_mode)
        if json_mode:
            _write_json(sys.stdout, {"ok": True, "result": _json_value(result)})
        elif result is not None:
            _write_human_result(result)
    except BrokenPipeError:
        raise
    except KeyboardInterrupt:
        return _fail(
            json_mode,
            130,
            "interrupted",
            "operation interrupted",
        )
    except Exception as error:
        exit_code = _exit_code(error)
        if exit_code is None:
            return _fail(
                json_mode,
                1,
                "internal_error",
                "marimo-export encountered an unexpected internal error",
            )
        return _fail(
            json_mode,
            exit_code,
            _error_code(error),
            _error_message(error),
            _error_details(error),
        )
    return 0


def _run(arguments: argparse.Namespace, *, json_mode: bool) -> object:
    if arguments.command == "session":
        with _client(
            arguments.server,
            access_token=os.environ.get("MARIMO_EXPORT_TOKEN") or None,
            server_token=os.environ.get("MARIMO_EXPORT_SERVER_TOKEN") or None,
            timeout=arguments.timeout,
        ) as client:
            if arguments.session_id is None:
                return {
                    "sessions": [
                        {
                            "id": session.id,
                            "filename": session.filename,
                            "path": session.path,
                        }
                        for session in client.sessions()
                    ]
                }
            return client.session(arguments.session_id).inspect().to_dict()

    if arguments.command == "capture":
        spec = load_spec(Path(arguments.spec).expanduser())
        try:
            destination = _prepare_destination(
                arguments.output,
                replace=arguments.replace,
                max_index_bytes=arguments.max_index_bytes,
                max_asset_bytes=arguments.max_asset_bytes,
                max_publication_bytes=arguments.max_publication_bytes,
            )
        except (TypeError, ValueError) as error:
            raise _CliInputError(str(error)) from error
        with _client(
            arguments.server,
            access_token=os.environ.get("MARIMO_EXPORT_TOKEN") or None,
            server_token=os.environ.get("MARIMO_EXPORT_SERVER_TOKEN") or None,
            timeout=arguments.timeout,
            max_index_bytes=arguments.max_index_bytes,
            max_asset_bytes=arguments.max_asset_bytes,
            max_publication_bytes=arguments.max_publication_bytes,
        ) as client:
            result = client.session(arguments.session_id).capture(
                spec=spec,
                into=destination,
                replace=arguments.replace,
            )
        return result.to_dict()

    if arguments.command == "inspect":
        publication = open_publication(
            arguments.publication,
            max_index_bytes=arguments.max_index_bytes,
            max_asset_bytes=arguments.max_asset_bytes,
            max_publication_bytes=arguments.max_publication_bytes,
        )
        return publication.describe()

    if arguments.command == "verify":
        publication = open_publication(
            arguments.publication,
            max_index_bytes=arguments.max_index_bytes,
            max_asset_bytes=arguments.max_asset_bytes,
            max_publication_bytes=arguments.max_publication_bytes,
        )
        verified = publication.verify()
        return {
            "path": str(Path(arguments.publication).expanduser().absolute()),
            "verified": True,
            "assets": verified,
        }

    if arguments.command == "read":
        publication = open_publication(
            arguments.publication,
            max_index_bytes=arguments.max_index_bytes,
            max_asset_bytes=arguments.max_asset_bytes,
            max_publication_bytes=arguments.max_publication_bytes,
        )
        variant = publication.variant(arguments.variant)
        output = variant.output(arguments.output_name)
        published_format = output.format(arguments.format_name)
        media_type = published_format.media_type
        if arguments.output_file is not None:
            payload = published_format.bytes()
            output_path = _write_output(Path(arguments.output_file), payload)
            return {
                "path": str(output_path.absolute()),
                "variant": arguments.variant,
                "output": arguments.output_name,
                "format": arguments.format_name,
                "format_id": published_format.format_id,
                "media_type": media_type,
                "bytes": len(payload),
            }
        if not _is_textual(media_type):
            raise _CliInputError("binary output requires --to FILE")
        if json_mode:
            value: object = (
                published_format.json() if _is_json(media_type) else published_format.text()
            )
            return {
                "variant": arguments.variant,
                "output": arguments.output_name,
                "format": arguments.format_name,
                "format_id": published_format.format_id,
                "media_type": media_type,
                "value": value,
            }
        return _RawText(published_format.text())

    raise AssertionError(f"unknown command: {arguments.command}")


class _RawText(str):
    pass


def _client(
    server: str,
    *,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 300.0,
    max_index_bytes: int = 16 * 1024 * 1024,
    max_asset_bytes: int = 64 * 1024 * 1024,
    max_publication_bytes: int = 512 * 1024 * 1024,
) -> Client:
    try:
        return Client(
            server,
            access_token=access_token,
            server_token=server_token,
            timeout=timeout,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        )
    except (TypeError, ValueError) as error:
        raise _CliInputError(str(error)) from error


def _write_output(path: Path, payload: bytes) -> Path:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    staging = Path(staging_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.link(staging, path)
        _sync_directory_best_effort(path.parent)
    finally:
        with suppress(OSError):
            staging.unlink()
    return path


def _write_human_result(result: object) -> None:
    if isinstance(result, _RawText):
        sys.stdout.write(result)
        return
    if isinstance(result, Mapping):
        encoded = json.dumps(_json_value(result), allow_nan=False, ensure_ascii=False, indent=2)
        sys.stdout.write(encoded + "\n")
        return
    print(result)


def _fail(
    json_mode: bool,
    exit_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> int:
    message = _redact_cli_message(message)
    details = _redact_cli_value(details)
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = _json_value(details)
    if json_mode:
        _write_json(sys.stdout, {"ok": False, "error": error})
    else:
        print(f"error: {message}", file=sys.stderr)
    return exit_code


def _exit_code(error: BaseException) -> int | None:
    if isinstance(error, (SpecError, _CliInputError)):
        return EXIT_INPUT
    if isinstance(error, PublicationError) and error.code == "not_found":
        return EXIT_INPUT
    if isinstance(error, TransportError):
        return EXIT_TRANSPORT
    if isinstance(error, SessionError):
        return EXIT_SESSION
    if isinstance(error, CaptureError):
        return EXIT_CAPTURE
    if isinstance(error, (IntegrityError, PublicationError)):
        return EXIT_INTEGRITY
    if isinstance(error, OSError):
        return EXIT_FILESYSTEM
    return None


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(error, FileExistsError):
        return "destination_exists"
    if isinstance(error, OSError):
        return "filesystem_error"
    return "invalid_input"


def _error_message(error: BaseException) -> str:
    message = str(error)
    return _redact_cli_message(message if message else type(error).__name__)


def _error_details(error: BaseException) -> object | None:
    details = getattr(error, "details", None)
    return _redact_cli_value(details) if isinstance(details, Mapping) and details else None


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("result object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"result contains unsupported {type(value).__name__}")


def _write_json(stream: TextIO, value: object) -> None:
    encoded = json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write(encoded + "\n")


def _redact_cli_message(message: str) -> str:
    return safe_diagnostic(message, secrets=_cli_secrets())


def _redact_cli_value(value: object) -> object:
    if isinstance(value, str):
        return _redact_cli_message(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            redacted_key = _redact_cli_message(str(key))
            suffix = 2
            unique_key = redacted_key
            while unique_key in result:
                unique_key = f"{redacted_key}#{suffix}"
                suffix += 1
            result[unique_key] = _redact_cli_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_cli_value(item) for item in value]
    return value


def _cli_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for name in ("MARIMO_EXPORT_TOKEN", "MARIMO_EXPORT_SERVER_TOKEN")
        if (value := os.environ.get(name))
    )


def _sync_directory_best_effort(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _is_json(media_type: str) -> bool:
    base = media_type.partition(";")[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def _is_textual(media_type: str) -> bool:
    base = media_type.partition(";")[0].strip().lower()
    return base.startswith("text/") or _is_json(base) or base.endswith("+xml")


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.epilog = (
        "Authentication uses MARIMO_EXPORT_TOKEN and MARIMO_EXPORT_SERVER_TOKEN when set."
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=300.0,
        help="request timeout in seconds (default: 300)",
    )


def _add_index_limit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-index-bytes",
        type=_positive_integer,
        default=16 * 1024 * 1024,
        help="maximum bytes accepted for index.json (default: 16777216)",
    )


def _add_asset_limit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-asset-bytes",
        type=_positive_integer,
        default=64 * 1024 * 1024,
        help="maximum bytes accepted for one cache asset (default: 67108864)",
    )


def _add_publication_limit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-publication-bytes",
        type=_positive_integer,
        default=512 * 1024 * 1024,
        help="maximum bytes accepted for one publication (default: 536870912)",
    )


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="emit one machine-readable result object"
    )


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    if parsed > _MAX_SAFE_INTEGER:
        raise argparse.ArgumentTypeError(f"must be at most {_MAX_SAFE_INTEGER}")
    return parsed


def _session_id(value: str) -> str:
    if (
        not value
        or len(value) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise argparse.ArgumentTypeError("must be a non-empty marimo session ID")
    return value


def _package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0+unknown"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
