from __future__ import annotations

import gc
import json
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from marimo_export.exporters import _anywidget_payload as payload_validation
from marimo_export.exporters._anywidget_payload import validate_anywidget_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "anywidget-v1.json"
_LARGE_DIAGNOSTIC_VALUE = "x" * (1024 * 1024)


def _document() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


def _payload(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def _bounded_error(document: dict[str, Any], expected: str) -> str:
    with pytest.raises((TypeError, ValueError)) as error:
        validate_anywidget_payload(_payload(document))
    message = str(error.value)
    assert expected in message
    assert len(message) <= 1024
    return message


def _peak_auxiliary_bytes(operation: Callable[[], object]) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def _external_esm_url(byte_length: int, *, suffix: str = "") -> str:
    prefix = "https://example.test/"
    padding = byte_length - len(prefix.encode()) - len(suffix.encode())
    return prefix + "x" * padding + suffix


def test_validated_payload_reports_the_model_graph() -> None:
    validation = validate_anywidget_payload(_payload(_document()))

    assert validation.root_model_id == "model-0"
    assert validation.model_count == 2


def test_payload_rejects_duplicate_json_keys() -> None:
    payload = _payload(_document()).replace(
        b'"model_id":"model-0"',
        b'"model_id":"model-0","model_id":"model-1"',
        1,
    )

    with pytest.raises(ValueError, match="contains duplicate key 'model_id'"):
        validate_anywidget_payload(payload)


def test_payload_rejects_malformed_model_notification() -> None:
    document = _document()
    document["modelNotifications"] = [{}]

    with pytest.raises(TypeError, match=r"modelNotifications\[0\] fields are invalid"):
        validate_anywidget_payload(_payload(document))


def test_payload_requires_root_esm() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"] = None

    with pytest.raises(ValueError, match="root model 'model-0' has no ESM spec"):
        validate_anywidget_payload(_payload(document))


def test_payload_requires_canonical_model_order() -> None:
    document = _document()
    document["modelNotifications"][1]["model_id"] = "model-3"

    with pytest.raises(ValueError, match=r"modelNotifications\[1\].model_id must be 'model-1'"):
        validate_anywidget_payload(_payload(document))


def test_payload_requires_referenced_models() -> None:
    document = _document()
    document["modelNotifications"] = document["modelNotifications"][:1]

    with pytest.raises(ValueError, match="model reference 'model-1' is unresolved"):
        validate_anywidget_payload(_payload(document))


def test_payload_rejects_models_outside_root_closure() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["state"] = {"binary": {}}

    with pytest.raises(ValueError, match="models outside the root closure: 'model-1'"):
        validate_anywidget_payload(_payload(document))


def test_payload_validates_buffer_path_parents() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["state"] = {"child": "anywidget:model-1"}

    with pytest.raises(ValueError, match="does not target existing state"):
        validate_anywidget_payload(_payload(document))


def test_payload_requires_ascii_base64() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["buffers"] = ["AAA\u0661"]

    with pytest.raises(ValueError, match="not canonical base64"):
        validate_anywidget_payload(_payload(document))


@pytest.mark.parametrize("encoded", ["AB==", "AAB="])
def test_payload_requires_zero_base64_padding_bits(encoded: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["buffers"] = [encoded]

    with pytest.raises(ValueError, match="not canonical base64"):
        validate_anywidget_payload(_payload(document))


def test_payload_base64_validation_has_bounded_auxiliary_allocation() -> None:
    encoded = "A" * (32 * 1024 * 1024)

    peak = _peak_auxiliary_bytes(lambda: payload_validation._parse_buffers([encoded], "model-0"))

    assert peak < 1024 * 1024


def test_buffer_replacement_removes_a_model_reference_from_the_closure() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {"child": "anywidget:model-1"}
    root["message"]["buffer_paths"] = [["child"]]
    document["modelNotifications"] = [root]

    validation = validate_anywidget_payload(_payload(document))

    assert validation.root_model_id == "model-0"
    assert validation.model_count == 1


def test_payload_rejects_malformed_embedded_file() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "data:text/javascript;base64,%%%"

    with pytest.raises(ValueError, match="malformed base64 data"):
        validate_anywidget_payload(_payload(document))


def test_payload_treats_base64_media_type_as_percent_encoded_data() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "data:base64,export%20default%20%7B%7D"

    validation = validate_anywidget_payload(_payload(document))

    assert validation.root_model_id == "model-0"


def test_payload_recognizes_base64_parameter_case_insensitively() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "data:text/javascript;BASE64,%%%"

    with pytest.raises(ValueError, match="malformed base64 data"):
        validate_anywidget_payload(_payload(document))


def test_payload_data_url_metadata_validation_has_bounded_auxiliary_allocation() -> None:
    semicolon_header = ";x" * ((9 * 1024 * 1024) // 2)
    data_url = f"data:{semicolon_header},x"

    peak = _peak_auxiliary_bytes(
        lambda: payload_validation._validate_data_url(data_url, "AnyWidget file 'large.js'")
    )

    assert peak < 1024 * 1024


@pytest.mark.parametrize("media_type", ["a" * 1024, "a" * 1022 + "é"])
def test_payload_accepts_data_url_media_type_at_byte_limit(media_type: str) -> None:
    document = _document()
    document["files"]["./@file/root.js"] = f"data:{media_type},x"

    validation = validate_anywidget_payload(_payload(document))

    assert len(media_type.encode()) == 1024
    assert validation.root_model_id == "model-0"


@pytest.mark.parametrize("media_type", ["a" * 1025, "a" * 1023 + "é"])
def test_payload_rejects_data_url_media_type_beyond_byte_limit(media_type: str) -> None:
    document = _document()
    document["files"]["./@file/root.js"] = f"data:{media_type},x"

    _bounded_error(document, "data URL media type exceeds 1024 UTF-8 bytes")

    assert len(media_type.encode()) == 1025


def test_payload_percent_data_validation_has_bounded_auxiliary_allocation() -> None:
    data_url = "data:," + "%41" * (256 * 1024)

    peak = _peak_auxiliary_bytes(
        lambda: payload_validation._validate_data_url(data_url, "AnyWidget file 'large.js'")
    )

    assert peak < 64 * 1024


@pytest.mark.parametrize(
    "body",
    [
        "%C3",
        "%C3x%A9",
        "%C3é",
        "%ED%A0%80",
        "%F4%90%80%80",
    ],
)
def test_payload_rejects_percent_data_that_is_not_utf8(body: str) -> None:
    document = _document()
    document["files"]["./@file/root.js"] = f"data:,{body}"

    with pytest.raises(ValueError, match="malformed percent-encoded data"):
        validate_anywidget_payload(_payload(document))


def test_payload_accepts_percent_data_across_literal_unicode_boundaries() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "data:,é%C3%A9"

    validation = validate_anywidget_payload(_payload(document))

    assert validation.root_model_id == "model-0"


def test_payload_rejects_missing_virtual_esm_file() -> None:
    document = _document()
    document["files"] = {}

    with pytest.raises(ValueError, match="references missing virtual file"):
        validate_anywidget_payload(_payload(document))


@pytest.mark.parametrize("url", [_external_esm_url(8192), _external_esm_url(8192, suffix="é")])
def test_payload_accepts_external_esm_url_at_byte_limit(url: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    validation = validate_anywidget_payload(_payload(document))

    assert len(url.encode()) == 8192
    assert validation.root_model_id == "model-0"


@pytest.mark.parametrize("url", [_external_esm_url(8193), _external_esm_url(8193, suffix="é")])
def test_payload_rejects_external_esm_url_beyond_byte_limit(url: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    message = _bounded_error(document, "contains an invalid ESM URL")

    assert len(url.encode()) == 8193
    assert "..." in message


@pytest.mark.parametrize(
    "url",
    [
        "https://invalid host.example/widget.js",
        "http://xn--/widget.js",
    ],
)
def test_payload_rejects_invalid_http_esm_url(url: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    with pytest.raises(ValueError, match="contains an invalid ESM URL"):
        validate_anywidget_payload(_payload(document))


def test_payload_bounds_file_path_diagnostics() -> None:
    document = _document()
    document["files"] = {_LARGE_DIAGNOSTIC_VALUE: "https://example.test/widget.js"}

    message = _bounded_error(document, "must contain a data URL")

    assert "..." in message


def test_payload_bounds_model_reference_diagnostics() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {"child": f"anywidget:{_LARGE_DIAGNOSTIC_VALUE}"}
    root["message"]["buffer_paths"] = []
    root["message"]["buffers"] = []
    document["modelNotifications"] = [root]

    message = _bounded_error(document, "model reference")

    assert "..." in message


def test_payload_bounds_url_diagnostics() -> None:
    document = _document()
    url = f"https://example.test/{_LARGE_DIAGNOSTIC_VALUE} "
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    message = _bounded_error(document, "contains an invalid ESM URL")

    assert "..." in message


def test_payload_bounds_buffer_path_diagnostics() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {}
    root["message"]["buffer_paths"] = [[_LARGE_DIAGNOSTIC_VALUE, "missing"]]

    message = _bounded_error(document, "does not target existing state")

    assert "..." in message


def test_payload_bounds_unrelated_model_list_diagnostics() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {}
    root["message"]["buffer_paths"] = []
    root["message"]["buffers"] = []
    document["modelNotifications"] = [root]
    document["modelNotifications"].extend(
        {
            "op": "model-lifecycle",
            "model_id": f"model-{index}",
            "message": {
                "method": "open",
                "state": {},
                "buffer_paths": [],
                "buffers": [],
                "esm_spec": None,
            },
        }
        for index in range(1, 2000)
    )

    message = _bounded_error(document, "models outside the root closure")

    assert "'model-1'" in message
    assert "'model-1999'" not in message
    assert "..." in message


def test_payload_bounds_unexpected_field_diagnostics() -> None:
    document = _document()
    field = f"unexpected-{_LARGE_DIAGNOSTIC_VALUE}"
    document[field] = None

    message = _bounded_error(document, "Unexpected:")

    assert "..." in message
