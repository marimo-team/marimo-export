from __future__ import annotations

import pytest
from marimo_export._remote.sse import SSEError, SSEEvent, SSEParser


def test_parser_handles_split_crlf_and_multiline_data() -> None:
    parser = SSEParser(1024)

    assert parser.feed(b"event: std") == ()
    assert parser.feed(b"out\r") == ()
    assert parser.feed(b"\ndata: first\r\ndata: second\r") == ()
    assert parser.feed(b"\n\r\n") == (SSEEvent(event="stdout", data="first\nsecond"),)


def test_parser_dispatches_default_event_at_end_of_stream() -> None:
    parser = SSEParser(1024)
    assert parser.feed(b"data: ready") == ()

    assert parser.close() == (SSEEvent(event="message", data="ready"),)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"data: bad\0value\n\n", "NUL"),
        (b"event: \ndata: value\n\n", "name"),
        (b"event: stdout\ndata: \xff\n\n", "UTF-8"),
    ],
)
def test_parser_rejects_malformed_streams(payload: bytes, message: str) -> None:
    parser = SSEParser(1024)
    with pytest.raises(SSEError, match=message):
        parser.feed(payload)


def test_parser_bounds_an_incomplete_event() -> None:
    parser = SSEParser(8)

    with pytest.raises(SSEError, match="transport limit"):
        parser.feed(b"x" * 9)


def test_parser_bounds_complete_comment_and_unknown_fields() -> None:
    comment = SSEParser(8)
    with pytest.raises(SSEError, match="transport limit"):
        comment.feed(b":123456789\n")

    unknown = SSEParser(16)
    with pytest.raises(SSEError, match="transport limit"):
        unknown.feed(b"ignored: 123456\nignored: more\n")
