from __future__ import annotations

import json
from pathlib import Path

from marimo_export._limits import MAX_INDEX_VALUES

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "export"


def test_index_value_limit_matches_the_browser_reader_fixture() -> None:
    limits = json.loads((FIXTURES / "index-limits.json").read_text(encoding="utf-8"))

    assert limits["max_index_values"] == MAX_INDEX_VALUES
