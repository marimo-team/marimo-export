from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from marimo_export.exporters._anywidget_payload import validate_anywidget_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "anywidget-v1.json"
_HTTP_MODULE_URL_CASES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "export"
        / "http-module-urls.json"
    ).read_text()
)


def _document() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


def _payload(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def test_validated_payload_reports_the_model_graph() -> None:
    validation = validate_anywidget_payload(_payload(_document()))

    assert validation.root_model_id == "model-0"
    assert validation.model_count == 2


def test_payload_rejects_duplicate_keys_and_malformed_notifications() -> None:
    duplicate = _payload(_document()).replace(
        b'"model_id":"model-0"',
        b'"model_id":"model-0","model_id":"model-1"',
        1,
    )
    with pytest.raises(ValueError, match="contains duplicate key 'model_id'"):
        validate_anywidget_payload(duplicate)

    malformed = _document()
    malformed["modelNotifications"] = [{}]
    with pytest.raises(TypeError, match=r"modelNotifications\[0\] fields are invalid"):
        validate_anywidget_payload(_payload(malformed))


def test_payload_requires_a_canonical_closed_model_graph() -> None:
    missing_esm = _document()
    missing_esm["modelNotifications"][0]["message"]["esm_spec"] = None
    with pytest.raises(ValueError, match="root model 'model-0' has no ESM spec"):
        validate_anywidget_payload(_payload(missing_esm))

    wrong_order = _document()
    wrong_order["modelNotifications"][1]["model_id"] = "model-3"
    with pytest.raises(ValueError, match=r"modelNotifications\[1\].model_id must be 'model-1'"):
        validate_anywidget_payload(_payload(wrong_order))

    unresolved = _document()
    unresolved["modelNotifications"] = unresolved["modelNotifications"][:1]
    with pytest.raises(ValueError, match="model reference 'model-1' is unresolved"):
        validate_anywidget_payload(_payload(unresolved))

    unrelated = _document()
    unrelated["modelNotifications"][0]["message"]["state"] = {"binary": {}}
    with pytest.raises(ValueError, match="models outside the root closure: 'model-1'"):
        validate_anywidget_payload(_payload(unrelated))


def test_payload_validates_buffer_paths_and_base64() -> None:
    missing_parent = _document()
    missing_parent["modelNotifications"][0]["message"]["state"] = {"child": "anywidget:model-1"}
    with pytest.raises(ValueError, match="does not target existing state"):
        validate_anywidget_payload(_payload(missing_parent))

    malformed = _document()
    malformed["modelNotifications"][0]["message"]["buffers"] = ["AB=="]
    with pytest.raises(ValueError, match="not canonical base64"):
        validate_anywidget_payload(_payload(malformed))


def test_buffer_replacement_updates_the_model_closure() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {"child": "anywidget:model-1"}
    root["message"]["buffer_paths"] = [["child"]]
    document["modelNotifications"] = [root]

    validation = validate_anywidget_payload(_payload(document))

    assert validation.model_count == 1


def test_payload_accepts_self_contained_and_remote_modules() -> None:
    for url in (
        "data:text/javascript,export%20default%20%7B%7D",
        "https://cdn.example.test/widget.js",
    ):
        document = _document()
        document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url
        assert validate_anywidget_payload(_payload(document)).root_model_id == "model-0"


@pytest.mark.parametrize(
    "case",
    _HTTP_MODULE_URL_CASES,
    ids=[case["name"] for case in _HTTP_MODULE_URL_CASES],
)
def test_http_module_url_fixtures_match_anywidget_payload(case: dict[str, Any]) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = case["url"]
    if case["valid"]:
        assert validate_anywidget_payload(_payload(document)).root_model_id == "model-0"
    else:
        with pytest.raises(ValueError, match="invalid ESM URL"):
            validate_anywidget_payload(_payload(document))


def test_payload_rejects_missing_or_local_file_modules() -> None:
    missing = _document()
    missing["files"] = {}
    with pytest.raises(ValueError, match="references missing virtual file"):
        validate_anywidget_payload(_payload(missing))

    local_file = _document()
    local_file["modelNotifications"][0]["message"]["esm_spec"]["url"] = "file:///tmp/widget.js"
    with pytest.raises(ValueError, match="incompatible ESM URL protocol"):
        validate_anywidget_payload(_payload(local_file))

    malformed = _document()
    malformed["files"]["/@file/root.js"] = "data:text/javascript;base64,%%%"
    with pytest.raises(ValueError, match="malformed base64 data"):
        validate_anywidget_payload(_payload(malformed))


def test_payload_bounds_external_urls_and_diagnostics() -> None:
    oversized_url = _document()
    oversized_url["modelNotifications"][0]["message"]["esm_spec"]["url"] = (
        "https://example.test/" + "x" * 8192
    )
    with pytest.raises(ValueError, match="contains an invalid ESM URL") as url_error:
        validate_anywidget_payload(_payload(oversized_url))
    assert len(str(url_error.value)) < 1024

    field = "\x1b" + "x" * (1024 * 1024)
    unexpected = _document()
    unexpected[field] = None
    with pytest.raises(TypeError, match="Unexpected:") as field_error:
        validate_anywidget_payload(_payload(unexpected))
    message = str(field_error.value)
    assert r"\x1b" in message
    assert "\x1b" not in message
    assert len(message) < 1024
