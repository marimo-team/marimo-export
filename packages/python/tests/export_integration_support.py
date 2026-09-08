from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from marimo_export import ExportSpec, capture
from marimo_export import build as build_notebook_export
from marimo_export._remote.managed import ManagedServer, _SessionStream
from marimo_export.progress import CacheActivity
from marimo_export.repository import ExportRepository
from marimo_export.result import ExportResult
from marimo_export.sessions import Client, Session


@contextmanager
def native_session(notebook: Path, *, auto_run: bool = True) -> Iterator[Session]:
    server = ManagedServer(notebook, timeout=30)
    try:
        # Native instantiation renders literal Markdown outside the execution graph.
        # The managed producer's explicit full-cell run would mask that boundary.
        stream = _SessionStream(server, timeout=30)
        server._stream = stream
        stream.wait_for_kernel()
        completed = stream.completed_runs
        server._post_json(
            "/api/kernel/instantiate",
            {"objectIds": [], "values": [], "autoRun": auto_run},
        )
        stream.wait_for_completed_run(completed)
        with Client(server.base_url, access_token=server.access_token, timeout=30) as client:
            yield client.session(server.session_id)
    finally:
        server.stop()


def build(
    notebook: Path,
    *,
    spec: ExportSpec,
    output: Path,
    timeout: float = 30,
) -> ExportResult:
    with ExportRepository.open(output.parent / f".{output.name}-repository") as repository:
        return build_notebook_export(
            notebook,
            spec=spec,
            output=output,
            repository=repository,
            timeout=timeout,
        )


def capture_export(
    notebook: Path,
    spec: ExportSpec,
    output: Path,
) -> CacheActivity:
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        return capture_live(server, spec, output)
    finally:
        server.stop()


def capture_live(
    server: ManagedServer,
    spec: ExportSpec,
    output: Path,
) -> CacheActivity:
    with (
        ExportRepository.open(output.parent / f".{output.name}-repository") as repository,
        capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            repository=repository,
            timeout=30,
        ) as prepared,
    ):
        activity = prepared.cache_activity
        prepared.write(output)
        return activity


def capture_session(
    session: Session,
    spec: ExportSpec,
    output: Path,
) -> CacheActivity:
    with (
        ExportRepository.open(output.parent / f".{output.name}-repository") as repository,
        session.capture(spec=spec, repository=repository) as prepared,
    ):
        activity = prepared.cache_activity
        prepared.write(output)
        return activity


__all__ = ["build", "capture_export", "capture_live", "capture_session", "native_session"]
