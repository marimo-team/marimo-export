from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from marimo._session.model import SessionMode
from marimo_export._marimo import context
from marimo_export.errors import UnsupportedProducerModeError


def test_notebook_path_preserves_lexical_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "target.py"
    target.write_text("import marimo\n", encoding="utf-8")
    link = tmp_path / "notebook.py"
    link.symlink_to(target)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        context,
        "root_context",
        lambda: SimpleNamespace(filename=link.name),
    )

    assert context.notebook_path() == link
    assert context.notebook_path() != target


def test_producer_rejects_run_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context,
        "root_context",
        lambda: SimpleNamespace(session_mode=SessionMode.RUN),
    )

    with pytest.raises(UnsupportedProducerModeError, match="marimo edit"):
        context.require_producer_context()


def test_producer_rejects_strict_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context,
        "root_context",
        lambda: SimpleNamespace(
            session_mode=SessionMode.EDIT,
            _kernel=SimpleNamespace(execution_type="strict"),
        ),
    )

    with pytest.raises(UnsupportedProducerModeError, match="relaxed execution"):
        context.require_producer_context()


def test_producer_accepts_relaxed_edit_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context,
        "root_context",
        lambda: SimpleNamespace(
            session_mode=SessionMode.EDIT,
            _kernel=SimpleNamespace(execution_type="relaxed"),
        ),
    )

    context.require_producer_context()
