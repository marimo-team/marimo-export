from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from marimo_export._json import JsonObject, canonical_bytes

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def test_canonical_json_fixtures_match_python_producer() -> None:
    cases = cast(
        list[JsonObject],
        json.loads((FIXTURES / "canonical-json" / "cases.json").read_text()),
    )

    for case in cases:
        assert canonical_bytes(case["value"]).decode() == case["canonical"]
