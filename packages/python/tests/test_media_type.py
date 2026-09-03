from __future__ import annotations

import mimetypes

import pytest
from marimo_export._media_type import media_type_for_filename, validate_media_type


@pytest.mark.parametrize(
    ("filename", "system_value", "expected"),
    [
        ("app.js", "application/javascript", "text/javascript"),
        ("captions.vtt", None, "text/vtt"),
        ("module.wasm", None, "application/wasm"),
    ],
)
def test_web_media_types_are_independent_of_system_mappings(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    system_value: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(mimetypes, "guess_type", lambda _filename: (system_value, None))

    assert media_type_for_filename(filename, default="application/octet-stream") == expected


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
