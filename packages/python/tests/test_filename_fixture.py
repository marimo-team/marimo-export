from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from marimo_export import open_export
from marimo_export._json import JsonObject, canonical_bytes
from marimo_export.descriptors import BlobAssetDescriptor
from marimo_export.errors import NotebookExportError

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "export"


def test_filename_fixture_matches_the_public_python_reader(tmp_path: Path) -> None:
    source = cast(
        JsonObject,
        json.loads((FIXTURES / "scalar-index.json").read_text(encoding="utf-8")),
    )
    fixture = cast(
        JsonObject,
        json.loads((FIXTURES / "portable-filenames.json").read_text(encoding="utf-8")),
    )
    surfaces = cast(list[str], fixture["surfaces"])
    cases = cast(list[JsonObject], fixture["cases"])

    for surface in surfaces:
        for position, case in enumerate(cases):
            value = cast(str, case["value"])
            wire = cast(JsonObject, copy.deepcopy(source))
            if surface == "notebook":
                notebook = cast(dict[str, object], wire["notebook"])
                notebook["filename"] = value
            elif surface == "blob":
                wire["outputs"] = ["view"]
                descriptor = {
                    "asset": {"sha256": "e" * 64, "size": 1},
                    "codec": "marimo.blob-asset.msgpack.v1",
                    "filename": value,
                    "media_type": "application/octet-stream",
                    "metadata": {},
                    "provenance": {"python_type": "fixture.Value"},
                }
                states = cast(dict[str, object], wire["states"])
                for state_value in states.values():
                    state = cast(dict[str, object], state_value)
                    state["outputs"] = {"view": descriptor}
            else:
                raise AssertionError(f"unknown filename fixture surface {surface!r}")
            root = tmp_path / surface / str(position)
            root.mkdir(parents=True)
            (root / "index.json").write_bytes(canonical_bytes(wire))

            if case["valid"]:
                opened = open_export(root)
                if surface == "notebook":
                    assert opened.notebook.filename == value
                else:
                    output = opened.state("one").output("view")
                    assert cast(BlobAssetDescriptor, output.descriptor).filename == value
            else:
                with pytest.raises(NotebookExportError) as raised:
                    open_export(root)
                assert raised.value.code == "export_invalid"
