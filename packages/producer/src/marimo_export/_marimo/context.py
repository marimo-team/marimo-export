from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marimo._ast.load import load_notebook_ir
from marimo._runtime.context.kernel_context import KernelRuntimeContext
from marimo._runtime.context.types import get_context
from marimo._session.model import SessionMode
from marimo._session.notebook.serializer import get_notebook_serializer

from marimo_export.errors import UnsupportedProducerModeError


@dataclass(frozen=True)
class NotebookSnapshot:
    name: str
    source_sha256: str
    path: Path
    source: bytes


def root_context() -> KernelRuntimeContext:
    context = get_context()
    while context.parent is not None:
        context = context.parent
    if not isinstance(context, KernelRuntimeContext):
        raise RuntimeError("marimo-export requires an attached marimo kernel session")
    return context


def notebook_snapshot() -> NotebookSnapshot:
    path = notebook_path()
    source = path.read_bytes()
    contents = source.decode("utf-8", errors="replace")
    notebook = get_notebook_serializer(path).deserialize(contents, filepath=str(path))
    if notebook is None or not notebook.valid:
        raise RuntimeError(f"failed to load attached notebook: {path}")
    return NotebookSnapshot(
        name=path.name,
        source_sha256=hashlib.sha256(source).hexdigest(),
        path=path,
        source=source,
    )


def require_producer_context() -> None:
    context = root_context()
    if context.session_mode != SessionMode.EDIT:
        raise UnsupportedProducerModeError(
            "marimo-export builds require an edit-mode kernel using relaxed execution. "
            "Start the producer with `marimo edit`. Relaxed execution is marimo's default."
        )
    if context._kernel.execution_type != "relaxed":
        raise UnsupportedProducerModeError(
            "marimo-export builds require relaxed execution because marimo 0.23.14 "
            "shares native cell-cache identity across execution types. Remove the "
            "strict override or set `[tool.marimo.experimental] execution_type = "
            '"relaxed"`. Use a fresh `__marimo__/cache` directory after any strict run.'
        )


def notebook_path() -> Path:
    filename = root_context().filename
    if not filename or filename == "<unknown>":
        raise RuntimeError("the attached notebook must have a file path")
    return Path(os.path.abspath(filename))


def snapshot_app(snapshot: NotebookSnapshot) -> Any:
    contents = snapshot.source.decode("utf-8", errors="replace")
    notebook = get_notebook_serializer(snapshot.path).deserialize(
        contents, filepath=str(snapshot.path)
    )
    if notebook is None or not notebook.valid:
        raise RuntimeError(f"failed to load notebook snapshot: {snapshot.path}")
    return load_notebook_ir(notebook, filepath=str(snapshot.path))


def assert_snapshot_current(snapshot: NotebookSnapshot) -> None:
    if snapshot.path.read_bytes() != snapshot.source:
        raise RuntimeError("the notebook changed while the export was being built")
