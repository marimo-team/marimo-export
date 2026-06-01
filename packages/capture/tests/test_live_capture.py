from __future__ import annotations

from moexport.live_capture import notebook_matches, parse_sse


def test_parse_sse_decodes_scratchpad_events() -> None:
    events = parse_sse(
        'event: stdout\ndata: {"data": "hello"}\n\n'
        'event: done\ndata: {"success": true, "output": null}\n\n'
    )

    assert events == [
        {"event": "stdout", "data": {"data": "hello"}},
        {"event": "done", "data": {"success": True, "output": None}},
    ]


def test_notebook_matches_path_name_or_suffix() -> None:
    record = {"path": "/tmp/project/notebook.py", "name": "notebook.py"}

    assert notebook_matches(record, "/tmp/project/notebook.py")
    assert notebook_matches(record, "notebook.py")
    assert not notebook_matches(record, "other.py")
