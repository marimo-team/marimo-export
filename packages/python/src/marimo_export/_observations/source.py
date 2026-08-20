"""Resolve notebook producer identity against one stable source revision."""

from __future__ import annotations

import threading
from pathlib import Path

from marimo_export._notebook import _source_revision
from marimo_export._services.identity import producer_sha256 as identify_producer


class SourceProducer:
    """Cache producer identity while the complete source revision is unchanged."""

    def __init__(self, source: Path) -> None:
        self._source = source
        self._lock = threading.Lock()
        self._revision: tuple[int, int, int, int, int] | None = None
        self._producer_sha256: str | None = None

    def resolve(self) -> str:
        before = _source_revision(self._source)
        with self._lock:
            if before == self._revision and self._producer_sha256 is not None:
                return self._producer_sha256
            producer = identify_producer(self._source)
            after = _source_revision(self._source)
            if before != after:
                producer = identify_producer(self._source)
                stable = _source_revision(self._source)
                if after != stable:
                    raise OSError("The notebook changed while its producer identity was read")
                after = stable
            self._revision = after
            self._producer_sha256 = producer
            return producer


__all__ = ["SourceProducer"]
