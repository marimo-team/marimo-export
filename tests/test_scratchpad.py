from __future__ import annotations

import pytest

from moxport import ScratchpadProtocolError
from moxport.client import RemoteRef
from moxport.models import CellInfo
from moxport.scratchpad import parse_execute_stream


def test_parse_execute_stream_parses_stdout_stderr_and_done() -> None:
    result = parse_execute_stream(
        [
            "event: stdout",
            'data: {"data": "hello\\n"}',
            "event: stderr",
            'data: {"data": "warn\\n"}',
            "event: done",
            'data: {"success": true, "output": {"mimetype": "text/plain", "data": "4"}}',
        ]
    )
    assert result.success is True
    assert result.stdout == "hello\n"
    assert result.stderr == "warn\n"
    assert result.output == "4"
    assert result.output_mimetype == "text/plain"


def test_parse_execute_stream_parses_failure() -> None:
    result = parse_execute_stream(
        [
            "event: done",
            'data: {"success": false, "error": {"type": "Boom", "msg": "bad"}}',
        ]
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.type == "Boom"
    assert result.error.msg == "bad"


def test_parse_execute_stream_requires_done_event() -> None:
    with pytest.raises(ScratchpadProtocolError):
        parse_execute_stream(["event: stdout", 'data: {"data": "hi"}'])


def test_remote_ref_query_json_uses_cell_lookup_script() -> None:
    client = _MockNotebookClient()
    ref = RemoteRef(client=client, kind="cell", selector="cid")
    result = ref.query_json("{'rows': 2}")
    assert result == {"rows": 2}
    assert "_resolve_cell_value('cid'" in client.last_body


class _MockNotebookClient:
    def __init__(self) -> None:
        self.last_body = ""

    def get_cell(self, target: str) -> CellInfo:
        assert target == "cid"
        return CellInfo(index=0, id="cid", name="demo", code="value\n")

    def _run_json(self, body: str) -> object:
        self.last_body = body
        return {"data": {"rows": 2}}
