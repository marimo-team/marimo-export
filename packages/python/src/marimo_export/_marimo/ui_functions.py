"""Decide which Python functions of a projected UI object enter an export."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from marimo_export.errors import OutputError


def unavailable_functions(
    element: object,
    names: Iterable[str],
    args: Mapping[str, object],
    *,
    object_id: str,
    read_only: bool,
) -> list[str]:
    """Return the sorted functions an exported UI object cannot call.

    ``args`` are the component arguments marimo renders into the element.
    A form without a validator registers ``validate`` but never calls it.
    An eager table carries its first page in ``args``, so a read-only output
    keeps the table and lists the functions behind search, paging, sorting,
    summaries, and downloads. A lazy table loads its first page through
    ``search`` and needs a running notebook like every other function.
    """

    from marimo import ui

    functions = sorted(
        name
        for name in names
        if not (name == "validate" and isinstance(element, ui.form) and element.validate is None)
    )
    if not functions:
        return functions
    eager_table = isinstance(element, ui.table) and args.get("lazy") is False
    if read_only and eager_table:
        return functions
    hint = ". Set read_only on the output" if eager_table else ""
    raise OutputError(
        f"UI object {object_id!r} exposes Python functions that need a running notebook{hint}",
        code="output_not_portable",
        details={
            "object_id": object_id,
            "reason": "python_functions",
            "functions": functions,
        },
    )
