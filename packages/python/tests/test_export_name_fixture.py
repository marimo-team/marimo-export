from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from marimo_export import open_export
from marimo_export._json import JsonObject, canonical_bytes
from marimo_export.errors import NotebookExportError

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "export"


def test_export_name_fixture_matches_the_public_python_reader(tmp_path: Path) -> None:
    source = cast(
        JsonObject,
        json.loads((FIXTURES / "scalar-index.json").read_text(encoding="utf-8")),
    )
    cases = cast(
        list[JsonObject],
        json.loads((FIXTURES / "export-names.json").read_text(encoding="utf-8")),
    )

    for surface in ("alias", "output"):
        for position, case in enumerate(cases):
            value = cast(str, case["value"])
            wire = cast(JsonObject, copy.deepcopy(source))
            if surface == "alias":
                aliases = cast(dict[str, object], wire["aliases"])
                aliases[value] = aliases.pop("one")
            else:
                wire["outputs"] = [value]
                states = cast(dict[str, object], wire["states"])
                for state_value in states.values():
                    state = cast(dict[str, object], state_value)
                    outputs = cast(dict[str, object], state["outputs"])
                    outputs[value] = outputs.pop("answer")
            root = tmp_path / surface / str(position)
            root.mkdir(parents=True)
            (root / "index.json").write_bytes(canonical_bytes(wire))

            if case["valid"]:
                opened = open_export(root)
                if surface == "alias":
                    assert value in opened.state(value).aliases
                else:
                    assert opened.output_names == (value,)
            else:
                with pytest.raises(NotebookExportError) as raised:
                    open_export(root)
                assert raised.value.code == "export_invalid"
