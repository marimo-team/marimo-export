from __future__ import annotations

from typing import cast

import pytest
from marimo_export.progress import CacheActivity, ProgressEvent, ProgressKind


def test_progress_event_serializes_every_field() -> None:
    event = ProgressEvent(
        kind="state_finished",
        completed=2,
        total=3,
        state="circles-k03",
        cache=CacheActivity(
            authored_hits=4,
            authored_misses=1,
            projection_hits=2,
            projection_misses=0,
        ),
        elapsed_seconds=1.25,
        message="Prepared circles-k03",
    )

    assert event.to_dict() == {
        "kind": "state_finished",
        "completed": 2,
        "total": 3,
        "state": "circles-k03",
        "cache": {
            "authored_hits": 4,
            "authored_misses": 1,
            "projection_hits": 2,
            "projection_misses": 0,
        },
        "elapsed_seconds": 1.25,
        "message": "Prepared circles-k03",
    }


def test_progress_event_rejects_inconsistent_counts() -> None:
    with pytest.raises(ValueError, match="completed cannot exceed total"):
        ProgressEvent(kind="state_started", completed=2, total=1)


@pytest.mark.parametrize(
    "kind",
    ("delivery_verification_started", "delivery_commit_started"),
)
def test_progress_event_accepts_delivery_kinds(kind: str) -> None:
    assert ProgressEvent(kind=cast(ProgressKind, kind)).kind == kind


def test_cache_activity_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        CacheActivity(authored_hits=True)  # type: ignore[arg-type]
