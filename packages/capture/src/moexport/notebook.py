"""Export marimo notebooks by reference.

This module exports a notebook reference through marimo's resolver and script
runner. marimo resolves and loads the notebook. moexport injects one final export
side effect and lets `evaluate` handle scenario-specific notebook execution.
"""

from __future__ import annotations

import sys
import uuid
from collections import deque
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeAlias, TypedDict

from marimo._ast.app import App, InternalApp
from marimo._ast.cell import CellConfig
from marimo._ast.load import load_app
from marimo._cli.files.file_path import validate_name
from marimo._cli.utils import check_app_correctness
from marimo._runtime.app.script_runner import AppScriptRunner
from marimo._schemas.serialization import CellDef
from marimo._types.ids import CellId_t

from moexport.export import CaptureResult, SpecInput

NotebookReference: TypeAlias = str | Path
_RUN_OPTION_KEYS = frozenset({"args", "check"})


class NotebookRunOptions(TypedDict, total=False):
    """Options for the in-process notebook execution used by capture_notebook."""

    args: Sequence[str]
    check: bool


class NotebookSource(TypedDict):
    """Resolved notebook source returned by programmatic callers."""

    path: str
    name: str
    source: str


class NotebookDefs(TypedDict):
    """Defs and cell metadata discovered by marimo's notebook parser."""

    notebook: dict[str, str]
    defs: list[dict[str, object]]
    cells: list[dict[str, object]]
    root_defs: list[str]


@dataclass(frozen=True)
class ResolvedNotebook:
    """A marimo-resolved notebook path.

    Remote notebook references are materialized into a temporary directory by
    marimo's resolver. Holding `_temp_dir` keeps that file alive through run.
    """

    path: Path
    _temp_dir: TemporaryDirectory[str] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def read_notebook_source(notebook: NotebookReference) -> NotebookSource:
    """Resolve a notebook reference and return its source code."""

    resolved = _resolve_notebook(notebook)
    return {
        "path": str(resolved.path),
        "name": resolved.path.name,
        "source": resolved.path.read_text(encoding="utf-8"),
    }


def inspect_notebook_defs(notebook: NotebookReference) -> NotebookDefs:
    """Resolve and parse a notebook, returning defs and cells for export specs."""

    resolved = _resolve_notebook(notebook)
    app = _load_required_app(resolved.path)

    cells = []
    defs = []
    for cell_id, cell in app._cell_manager.valid_cells():
        impl = cell._cell
        cell_defs = sorted(impl.defs)
        cell_refs = sorted(impl.refs)
        name = getattr(impl, "name", None)
        cell_record = {
            "cell_id": str(cell_id),
            "name": name,
            "disabled": impl.config.disabled,
            "defs": cell_defs,
            "refs": cell_refs,
            "preview": _preview(impl.code),
        }
        cells.append(cell_record)
        defs.extend(
            {
                "name": name,
                "cell_id": str(cell_id),
                "cell_name": name,
                "disabled": impl.config.disabled,
                "refs": cell_refs,
                "preview": _preview(impl.code),
            }
            for name in cell_defs
        )

    return {
        "notebook": {
            "path": str(resolved.path),
            "name": resolved.path.name,
        },
        "defs": sorted(defs, key=lambda item: str(item["name"])),
        "cells": cells,
        "root_defs": sorted(
            {
                name
                for cell in cells
                if not cell["refs"] and not cell["disabled"]
                for name in cell["defs"]
                if isinstance(name, str)
            }
        ),
    }


def capture_notebook(
    notebook: NotebookReference,
    spec: SpecInput,
    *,
    to: str | Path | None = None,
    run: NotebookRunOptions | None = None,
) -> CaptureResult:
    """Resolve a marimo notebook, run one synthetic cell, and write a bundle.

    The implementation appends a hidden cell equivalent to:

    ```python
    import moexport as mox
    result = await mox.capture(spec, to=to)
    ```

    Only this synthetic cell is scheduled by the outer script runner.
    ``mox.evaluate`` runs scenario-specific notebook execution, so overrides
    such as ``symbols=["CRWV", "MSFT"]`` apply before expensive downstream
    cells run.
    """

    run_options = _run_options(run)
    resolved = _resolve_notebook(notebook)

    if run_options.get("check", True):
        check_app_correctness(str(resolved.path))

    app = _load_required_app(resolved.path)
    cell_id, result_name, spec_name, output_name = _append_export_cell(app)
    defs = _run_export_cell(
        app=app,
        notebook_path=resolved.path,
        cell_id=cell_id,
        spec_name=spec_name,
        spec=spec,
        output_name=output_name,
        to=to,
        args=run_options.get("args", ()),
    )
    result = defs[result_name]
    if not isinstance(result, CaptureResult):
        raise TypeError(
            f"expected synthetic capture cell to return CaptureResult, got {type(result)!r}"
        )
    return result


def _resolve_notebook(notebook: NotebookReference) -> ResolvedNotebook:
    # Reuse the same name resolver as `marimo run <name>`: local files,
    # GitHub/gist URLs, static marimo HTML, generic URLs, and R2 references.
    path, temp_dir = validate_name(
        str(notebook),
        allow_new_file=False,
        allow_directory=False,
    )
    return ResolvedNotebook(
        path=Path(path),
        _temp_dir=temp_dir,
    )


def _run_options(run: NotebookRunOptions | None) -> NotebookRunOptions:
    if run is None:
        return {}

    unsupported = set(run) - _RUN_OPTION_KEYS
    if unsupported:
        keys = ", ".join(sorted(unsupported))
        raise TypeError(f"unsupported notebook run option(s): {keys}")

    return run


@contextmanager
def _script_argv(
    notebook_path: Path,
    args: Sequence[str],
) -> Generator[None, None, None]:
    """Expose notebook args to `mo.cli_args()` during App.run.

    ScriptRuntimeContext reads CLI args lazily from `sys.argv`, so:

        run={"args": ["--symbol", "MSFT"]}

    becomes `sys.argv == ["finance.py", "--symbol", "MSFT"]` while the
    notebook executes.
    """

    previous = sys.argv
    sys.argv = [str(notebook_path), *args]
    try:
        yield
    finally:
        sys.argv = previous


def _load_required_app(notebook: Path) -> App:
    app = load_app(notebook)
    if app is None:
        raise ValueError(f"{notebook} is empty or does not contain a marimo app")
    return app


def _append_export_cell(app: App) -> tuple[CellId_t, str, str, str]:
    # The hidden cell receives `spec` and `to` through the script runner's
    # globals. It intentionally skips notebook defs. `mox.evaluate` owns
    # dependency planning and scenario overrides.
    token = uuid.uuid4().hex
    result_name = f"__moexport_result_{token}"
    spec_name = f"__moexport_spec_{token}"
    output_name = f"__moexport_to_{token}"
    code = _export_cell_code(
        result_name=result_name,
        spec_name=spec_name,
        output_name=output_name,
    )
    previous_ids = set(app._cell_manager.valid_cell_ids())
    app._cell_manager.register_ir_cell(
        CellDef(
            code=code,
            name="_moexport_notebook_capture",
            options=CellConfig(hide_code=True).asdict_without_defaults(),
        ),
        InternalApp(app),
    )
    added_ids = set(app._cell_manager.valid_cell_ids()) - previous_ids
    if len(added_ids) != 1:
        raise RuntimeError("failed to register synthetic export cell")
    return next(iter(added_ids)), result_name, spec_name, output_name


def _run_export_cell(
    *,
    app: App,
    notebook_path: Path,
    cell_id: CellId_t,
    spec_name: str,
    spec: SpecInput,
    output_name: str,
    to: str | Path | None,
    args: Sequence[str],
) -> dict[str, object]:
    app._maybe_initialize()

    glbls: dict[str, object] = {}
    if app._setup is not None:
        glbls.update(app._setup._glbls)

    reserved = {spec_name, output_name}
    if reserved & set(glbls):
        raise TypeError("synthetic export globals conflict with setup definitions")
    glbls.update({spec_name: spec, output_name: to})

    with _script_argv(notebook_path, args):
        runner = AppScriptRunner(
            InternalApp(app),
            filename=str(notebook_path),
            glbls=glbls,
        )
        # AppScriptRunner normally runs the whole notebook. For static export
        # we only need marimo's script runtime around the synthetic cell:
        #
        #   import moexport as __moexport
        #   __moexport_result_abcd = await __moexport.capture(
        #       __moexport_spec_abcd,
        #       to=__moexport_to_abcd,
        #   )
        #
        # Inside that cell, `mox.evaluate` performs the actual notebook graph
        # planning for each scenario.
        runner.cells_to_run = deque([cell_id])
        _outputs, defs = runner.run()

    return defs


def _preview(code: str, *, lines: int = 3, width: int = 100) -> list[str]:
    return [line.rstrip()[:width] for line in code.splitlines() if line.strip()][:lines]


def _export_cell_code(
    *,
    result_name: str,
    spec_name: str,
    output_name: str,
) -> str:
    """Build the hidden cell inserted at the end of the notebook.

    A generated cell roughly looks like:

        import moexport as __moexport
        __moexport_result_abcd = await __moexport.capture(
            __moexport_spec_abcd,
            to=__moexport_to_abcd,
        )
    """

    return f"""\
import moexport as __moexport
{result_name} = await __moexport.capture(
    {spec_name},
    to={output_name},
)"""
