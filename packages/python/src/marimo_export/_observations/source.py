"""Resolve notebook producer identity against one stable source revision."""

from __future__ import annotations

import threading
from pathlib import Path

from marimo_export._notebook import _read_stable_source
from marimo_export._services.identity import producer_sha256 as identify_producer


class SourceProducer:
    """Cache producer identity while the complete source revision is unchanged."""

    def __init__(self, source: Path) -> None:
        self._source = source
        self._lock = threading.Lock()
        self._revision: tuple[int, int, int, int, int] | None = None
        self._source_sha256: str | None = None
        self._producer_sha256: str | None = None

    def resolve(self) -> str:
        _, before_sha256, before_revision = _read_stable_source(self._source)
        with self._lock:
            if (
                before_revision == self._revision
                and before_sha256 == self._source_sha256
                and self._producer_sha256 is not None
            ):
                return self._producer_sha256
            producer = identify_producer(self._source)
            _, after_sha256, after_revision = _read_stable_source(self._source)
            if before_sha256 != after_sha256 or before_revision != after_revision:
                producer = identify_producer(self._source)
                _, stable_sha256, stable_revision = _read_stable_source(self._source)
                if after_sha256 != stable_sha256 or after_revision != stable_revision:
                    raise OSError("The notebook changed while its producer identity was read")
                after_sha256 = stable_sha256
                after_revision = stable_revision
            self._revision = after_revision
            self._source_sha256 = after_sha256
            self._producer_sha256 = producer
            return producer


__all__ = ["SourceProducer"]
