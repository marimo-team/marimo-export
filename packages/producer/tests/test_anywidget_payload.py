from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from marimo_export.projection.exporters.anywidget import _from_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "anywidget-v1.json"


def _document() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


def _payload(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def test_validated_payload_constructs_projection_metadata() -> None:
    projection = _from_payload(_payload(_document()))

    assert projection.metadata == {"models": 2, "root_model_id": "model-0"}


def test_payload_rejects_malformed_model_notification() -> None:
    document = _document()
    document["modelNotifications"] = [{}]

    with pytest.raises(TypeError, match=r"modelNotifications\[0\] fields are invalid"):
        _from_payload(_payload(document))


def test_payload_requires_root_esm() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"] = None

    with pytest.raises(ValueError, match="root model 'model-0' has no ESM spec"):
        _from_payload(_payload(document))


def test_payload_requires_canonical_model_order() -> None:
    document = _document()
    document["modelNotifications"][1]["model_id"] = "model-3"

    with pytest.raises(ValueError, match=r"modelNotifications\[1\].model_id must be 'model-1'"):
        _from_payload(_payload(document))


def test_payload_requires_referenced_models() -> None:
    document = _document()
    document["modelNotifications"] = document["modelNotifications"][:1]

    with pytest.raises(ValueError, match="model reference 'model-1' is unresolved"):
        _from_payload(_payload(document))


def test_payload_rejects_models_outside_root_closure() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["state"] = {"binary": {}}

    with pytest.raises(ValueError, match="models outside the root closure: 'model-1'"):
        _from_payload(_payload(document))


def test_payload_validates_buffer_path_parents() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["state"] = {"child": "anywidget:model-1"}

    with pytest.raises(ValueError, match="does not target existing state"):
        _from_payload(_payload(document))


def test_payload_requires_ascii_base64() -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["buffers"] = ["AAA\u0661"]

    with pytest.raises(ValueError, match="not canonical base64"):
        _from_payload(_payload(document))


def test_buffer_replacement_removes_a_model_reference_from_the_closure() -> None:
    document = _document()
    root = document["modelNotifications"][0]
    root["message"]["state"] = {"child": "anywidget:model-1"}
    root["message"]["buffer_paths"] = [["child"]]
    document["modelNotifications"] = [root]

    projection = _from_payload(_payload(document))

    assert projection.metadata == {"models": 1, "root_model_id": "model-0"}


def test_payload_rejects_malformed_embedded_file() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "data:text/javascript;base64,%%%"

    with pytest.raises(ValueError, match="malformed base64 data"):
        _from_payload(_payload(document))


def test_payload_rejects_missing_virtual_esm_file() -> None:
    document = _document()
    document["files"] = {}

    with pytest.raises(ValueError, match="references missing virtual file"):
        _from_payload(_payload(document))


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
        _from_payload(_payload(document))
