"""Resolve notebook producer identity against one stable source revision."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._notebook import _read_stable_source, _source_revision
from marimo_export._notebook import _SourceRevision as SourceRevision
from marimo_export._services.identity import producer_sha256 as identify_producer

if TYPE_CHECKING:
    from marimo_export._marimo.capabilities import KernelCell, NotebookCell

LOGGER = logging.getLogger(__name__)


class SourceProducer:
    """Cache producer identity while the complete source revision is unchanged."""

    def __init__(self, source: Path) -> None:
        self._source = source
        self._lock = threading.Lock()
        self._revision: SourceRevision | None = None
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


@dataclass(frozen=True, slots=True)
class _SavedProducer:
    revision: SourceRevision
    producer_sha256: str
    signature: str


class SavedProducerMatcher:
    """Match live cells to the saved source, caching one parse per revision."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self._saved: _SavedProducer | None = None

    def defer(self, live_signature: str) -> tuple[str, Callable[[], str | None]]:
        """Return a queue scope now and a producer resolver for the worker.

        The resolver returns `None` when the saved source no longer matches the
        live cells or cannot be read at the revision observed here.
        """

        revision = _source_revision(self.source)
        scope = sha256_bytes(
            canonical_bytes(
                {
                    "revision": list(revision),
                    "signature": live_signature,
                }
            )
        )

        def resolve() -> str | None:
            try:
                return self._match(revision, live_signature)
            except Exception as error:
                LOGGER.warning(
                    "Could not validate observed notebook state: %s",
                    error.__cause__ or error,
                )
                return None

        return scope, resolve

    def _match(self, revision: SourceRevision, live_signature: str) -> str | None:
        saved = self._saved
        if saved is None or saved.revision != revision:
            saved = _read_saved_producer(self.source, revision)
            if saved is None:
                return None
            self._saved = saved
        if live_signature != saved.signature:
            return None
        return saved.producer_sha256


def cell_signature(cells: Iterable[KernelCell | NotebookCell]) -> str:
    """Digest the cell facts that a live kernel and its saved source share."""

    value = [
        {
            "id": cell.id,
            "code": cell.code.rstrip(),
            "column": cell.config["column"],
            "disabled": cell.config["disabled"],
            "hide_code": cell.config["hide_code"],
        }
        for cell in cells
    ]
    return sha256_bytes(canonical_bytes(value))


def _read_saved_producer(
    source: Path,
    expected_revision: SourceRevision,
) -> _SavedProducer | None:
    from marimo_export._marimo.composition import saved_cells

    payload, _source_sha256, revision = _read_stable_source(source)
    if revision != expected_revision:
        return None
    signature = cell_signature(saved_cells(source, payload))
    producer = identify_producer(source)
    if _source_revision(source) != revision:
        return None
    return _SavedProducer(
        revision=revision,
        producer_sha256=producer,
        signature=signature,
    )


__all__ = ["SavedProducerMatcher", "SourceProducer", "cell_signature"]
