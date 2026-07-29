from __future__ import annotations

import pytest
from marimo_export._media_type import validate_media_type


@pytest.mark.parametrize(
    "value",
    [
        "application/json",
        "text/plain; charset=utf-8",
        'application/example; title="one two"; version=1',
        'application/example; escaped="one\\"two"',
    ],
)
def test_media_type_accepts_the_browser_parameter_grammar(value: str) -> None:
    assert validate_media_type(value, "media type") == value


@pytest.mark.parametrize(
    "value",
    [
        "application/example;garbage",
        'application/example; title="unterminated',
        "application/example; version=1; VERSION=2",
        "application/example; version=",
    ],
)
def test_media_type_rejects_values_the_browser_cannot_read(value: str) -> None:
    with pytest.raises(ValueError, match="type/subtype syntax"):
        validate_media_type(value, "media type")
