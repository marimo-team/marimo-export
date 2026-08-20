from __future__ import annotations

import secrets
import sys
from collections.abc import Sequence
from contextlib import suppress

from marimo_export._cli.arguments import (
    UsageError,
    argv_credential_secrets,
    environment_secrets,
    namespace_output_mode,
    output_mode,
    parser,
)
from marimo_export._cli.commands import run
from marimo_export._cli.errors import (
    EXIT_BROKEN_PIPE,
    EXIT_ENVIRONMENT,
    EXIT_EXECUTION,
    EXIT_INTEGRITY,
    EXIT_INTERRUPT,
    EXIT_PLANNING,
    EXIT_REPOSITORY,
    EXIT_USAGE,
    exit_code,
)
from marimo_export._cli.render import (
    progress_callback,
    render_failure,
    render_result,
    render_usage_error,
)
from marimo_export.errors import MarimoExportError


def main(argv: Sequence[str] | None = None) -> int:
    """Run the marimo-export command-line interface."""

    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(raw)
    except BrokenPipeError:
        with suppress(OSError):
            sys.stdout.close()
        return EXIT_BROKEN_PIPE


def _main(raw: list[str]) -> int:
    mode = output_mode(raw)
    hidden = (*environment_secrets(), *argv_credential_secrets(raw))
    command = raw[0] if raw else ""
    try:
        arguments = parser().parse_args(raw)
        mode = namespace_output_mode(arguments)
        command = arguments.command
        result = run(arguments, progress_callback(mode, secrets=hidden))
        render_result(result, mode)
        return result.exit_code
    except UsageError as error:
        render_usage_error(error, mode, secrets=hidden)
        raise SystemExit(EXIT_USAGE) from None
    except KeyboardInterrupt:
        render_failure(
            mode,
            "interrupted",
            "operation interrupted",
            secrets=hidden,
        )
        return EXIT_INTERRUPT
    except Exception as error:
        category = exit_code(error, command=command)
        if category is None:
            render_failure(
                mode,
                "internal_error",
                f"internal failure, request ID {secrets.token_hex(6)}",
                secrets=hidden,
            )
            return 1
        code = error.code if isinstance(error, MarimoExportError) else "invalid_arguments"
        details = error.details if isinstance(error, MarimoExportError) else None
        render_failure(mode, code, str(error), details, secrets=hidden)
        return category


__all__ = [
    "EXIT_BROKEN_PIPE",
    "EXIT_ENVIRONMENT",
    "EXIT_EXECUTION",
    "EXIT_INTEGRITY",
    "EXIT_INTERRUPT",
    "EXIT_PLANNING",
    "EXIT_REPOSITORY",
    "EXIT_USAGE",
    "main",
]
