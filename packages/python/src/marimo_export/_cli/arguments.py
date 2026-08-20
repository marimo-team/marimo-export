from __future__ import annotations

import argparse
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NoReturn

_ACCESS_TOKEN_ENV = "MARIMO_EXPORT_ACCESS_TOKEN"
_SERVER_TOKEN_ENV = "MARIMO_EXPORT_SERVER_TOKEN"
_REPOSITORY_ENV = "MARIMO_EXPORT_REPOSITORY"
_LIVE_AUTH_EPILOG = (
    f"Live server authentication reads {_ACCESS_TOKEN_ENV} and {_SERVER_TOKEN_ENV} "
    "from the environment."
)
_REDACTED_CREDENTIAL_OPTIONS = ("--access-token", "--server-token")


class OutputMode(Enum):
    HUMAN = "human"
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True, slots=True)
class UsageError(Exception):
    prog: str
    usage: str
    message: str


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise UsageError(self.prog, self.format_usage(), message)


def parser() -> ArgumentParser:
    result = ArgumentParser(
        prog="marimo-export",
        description="Prepare and read verified Marimo notebook exports.",
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    commands = result.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan",
        help="resolve states and reusable work",
        description="Inspect NOTEBOOK and resolve the exact work described by FILE.",
    )
    plan.add_argument("notebook", metavar="NOTEBOOK", help="Marimo Python notebook")
    _add_spec(plan)
    _add_repository(plan)
    _add_timeout(plan, "notebook inspection timeout")
    _add_machine_output(plan, jsonl=False)

    build = commands.add_parser(
        "build",
        help="write a notebook export from a file",
        description="Prepare NOTEBOOK and atomically write its verified export to DIR.",
    )
    build.add_argument("notebook", metavar="NOTEBOOK", help="Marimo Python notebook")
    _add_spec(build)
    _add_output(build)
    _add_repository(build)
    _add_timeout(build, "server readiness and inactivity timeout")
    _add_machine_output(build, jsonl=True)

    capture = commands.add_parser(
        "capture",
        help="write a notebook export from a live session",
        description="Prepare one live Marimo session and atomically write its verified export.",
        epilog=_LIVE_AUTH_EPILOG,
    )
    capture.add_argument("server", metavar="SERVER", help="absolute Marimo server URL")
    capture.add_argument(
        "--session",
        dest="session_id",
        required=True,
        metavar="ID",
        help="live session ID",
    )
    _add_spec(capture)
    _add_output(capture)
    _add_repository(capture)
    _add_timeout(capture, "connection and inactivity timeout")
    _add_machine_output(capture, jsonl=True)

    inspect = commands.add_parser(
        "inspect",
        help="discover notebook definitions and live sessions",
        description=(
            "Inspect a notebook file or discover one Marimo server. File inspection "
            "executes the notebook's initial autorun."
        ),
        epilog=_LIVE_AUTH_EPILOG,
    )
    inspect.add_argument(
        "source",
        metavar="NOTEBOOK_OR_SERVER",
        help="Marimo Python notebook or absolute Marimo server URL",
    )
    inspect.add_argument(
        "--session",
        dest="session_id",
        metavar="ID",
        help="live session ID to inspect",
    )
    _add_timeout(inspect, "connection and inactivity timeout")
    _add_machine_output(inspect, jsonl=False)

    verify = commands.add_parser(
        "verify",
        help="verify a notebook export",
        description="Read index.json and verify every declared export asset.",
    )
    verify.add_argument("export", metavar="EXPORT", help="notebook export directory")
    _add_machine_output(verify, jsonl=False)

    observations = commands.add_parser(
        "observations",
        help="inspect or clear observed input states",
    ).add_subparsers(dest="observations_command", required=True)
    observations_list = observations.add_parser(
        "list",
        help="list observed input states for one notebook export plan",
    )
    _add_observation_plan(observations_list)
    observations_clear = observations.add_parser(
        "clear",
        help="clear observed input states for one notebook export plan",
    )
    _add_observation_plan(observations_clear)

    repository = commands.add_parser(
        "repository",
        help="inspect or prune prepared export storage",
    ).add_subparsers(dest="repository_command", required=True)
    repository_status = repository.add_parser("status", help="show repository usage")
    _add_repository(repository_status)
    _add_machine_output(repository_status, jsonl=False)
    repository_prune = repository.add_parser("prune", help="apply repository retention")
    repository_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="report removable artifacts without deleting them",
    )
    _add_repository(repository_prune)
    _add_machine_output(repository_prune, jsonl=False)

    doctor = commands.add_parser(
        "doctor",
        help="check the local export environment",
        description="Report the effective repository and Marimo compatibility.",
    )
    _add_repository(doctor)
    _add_machine_output(doctor, jsonl=False)
    return result


def output_mode(argv: Sequence[str]) -> OutputMode:
    if "--jsonl" in argv:
        return OutputMode.JSONL
    if "--json" in argv:
        return OutputMode.JSON
    return OutputMode.HUMAN


def namespace_output_mode(arguments: argparse.Namespace) -> OutputMode:
    if bool(getattr(arguments, "jsonl", False)):
        return OutputMode.JSONL
    if bool(getattr(arguments, "json", False)):
        return OutputMode.JSON
    return OutputMode.HUMAN


def environment_secrets() -> tuple[str, ...]:
    values = [
        os.environ.get(_ACCESS_TOKEN_ENV),
        os.environ.get(_SERVER_TOKEN_ENV),
    ]
    return tuple(value for value in values if isinstance(value, str) and value)


def argv_credential_secrets(argv: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for index, argument in enumerate(argv):
        for option in _REDACTED_CREDENTIAL_OPTIONS:
            if argument == option and index + 1 < len(argv):
                values.append(argv[index + 1])
            elif argument.startswith(option + "="):
                values.append(argument.split("=", 1)[1])
    return tuple(value for value in values if value)


def package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0.0.0"


def _add_spec(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spec", required=True, metavar="FILE", help="JSON or YAML ExportSpec")


def _add_repository(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository",
        metavar="DIR",
        help=f"prepared export repository (then {_REPOSITORY_ENV}, then platform default)",
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        required=True,
        metavar="DIR",
        help="notebook export destination",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="atomically replace an existing export directory",
    )


def _add_observation_plan(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("notebook", metavar="NOTEBOOK", help="Marimo Python notebook")
    _add_spec(parser)
    _add_repository(parser)
    _add_timeout(parser, "notebook inspection timeout")
    _add_machine_output(parser, jsonl=False)


def _add_timeout(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=30.0,
        metavar="SECONDS",
        help=f"{help_text} (default: 30)",
    )


def _add_machine_output(parser: argparse.ArgumentParser, *, jsonl: bool) -> None:
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit one JSON result")
    if jsonl:
        output.add_argument(
            "--jsonl",
            action="store_true",
            help="emit progress and the terminal result as JSON Lines",
        )


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return timeout


__all__ = [
    "ArgumentParser",
    "OutputMode",
    "UsageError",
    "argv_credential_secrets",
    "environment_secrets",
    "namespace_output_mode",
    "output_mode",
    "package_version",
    "parser",
]
