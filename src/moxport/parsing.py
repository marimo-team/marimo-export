from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

from marimo._ast.load import get_notebook_status, load_app

from .errors import NotebookParseError
from .models import CellInfo, LiveCellInfo


def parse_notebook_cells(source: str) -> list[CellInfo]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(source)
        temp_path = Path(handle.name)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            load_result = get_notebook_status(str(temp_path))
            app = load_app(str(temp_path))

        if app is None:
            raise NotebookParseError(
                f"Unable to parse live source into marimo IR: {load_result.status}"
            )

        cells: list[CellInfo] = []
        for index, cell_data in enumerate(app._cell_manager.cell_data()):
            compiled = cell_data.cell._cell if cell_data.cell is not None else None
            cells.append(
                CellInfo(
                    index=index,
                    id=cell_data.cell_id,
                    name=cell_data.name or None,
                    code=cell_data.code,
                    kind=compiled.language if compiled is not None else "python",
                    config={
                        "column": cell_data.config.column,
                        "disabled": cell_data.config.disabled,
                        "hide_code": cell_data.config.hide_code,
                    },
                    defs=sorted(compiled.defs) if compiled is not None else [],
                    refs=sorted(compiled.refs) if compiled is not None else [],
                )
            )
        return cells
    finally:
        temp_path.unlink(missing_ok=True)


def overlay_live_cells(
    parsed_cells: list[CellInfo],
    live_cells: list[LiveCellInfo],
) -> list[CellInfo]:
    if len(parsed_cells) != len(live_cells):
        return parsed_cells

    return [
        parsed.model_copy(
            update={
                "id": live.id,
                "name": live.name,
                "code": live.code,
            }
        )
        for parsed, live in zip(parsed_cells, live_cells, strict=True)
    ]
