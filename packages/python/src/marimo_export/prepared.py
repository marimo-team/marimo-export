"""Public handle for one immutable prepared notebook export."""

from __future__ import annotations

import weakref
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Protocol

from marimo_export._json import JsonObject, sha256_bytes
from marimo_export._prepared_integrity import (
    ClosureMember,
    verified_closure,
    verify_member,
)
from marimo_export.errors import ExportUnavailableError, IntegrityError
from marimo_export.planning import ExportPlan
from marimo_export.progress import CacheActivity, ProgressEvent
from marimo_export.reader import ExportState, NotebookExport, open_export
from marimo_export.repository import ExportRepository, RepositoryError
from marimo_export.spec import StrPath
from marimo_export.wire import portable_json

if TYPE_CHECKING:
    from marimo_export.result import ExportResult


class _Lease(Protocol):
    @property
    def alive(self) -> bool: ...

    def renew(self) -> bool: ...

    def close(self) -> None: ...


class _PreparedArtifact(Protocol):
    @property
    def alive(self) -> bool: ...

    @property
    def instance(self) -> str: ...

    @property
    def path(self) -> Path: ...

    def asset(self, relative: str) -> Path | None: ...

    def detach(self) -> _Lease: ...

    def close(self) -> None: ...


class PreparedAsset:
    """An independently leased regular file from a prepared export closure."""

    __slots__ = (
        "__weakref__",
        "_closed",
        "_finalizer",
        "_lease",
        "_lock",
        "_member",
        "_path",
        "_relative",
    )
    _closed: bool
    _finalizer: weakref.finalize
    _lease: _Lease
    _lock: RLock
    _member: ClosureMember
    _path: Path
    _relative: str

    def __init__(self) -> None:
        raise TypeError("PreparedAsset values are borrowed from PreparedExport.asset()")

    @classmethod
    def _create(
        cls,
        path: Path,
        lease: _Lease,
        relative: str,
        member: ClosureMember,
    ) -> PreparedAsset:
        try:
            verify_member(path, relative, member)
        except BaseException:
            lease.close()
            raise
        self = object.__new__(cls)
        self._path = path
        self._lease = lease
        self._lock = RLock()
        self._relative = relative
        self._member = member
        self._closed = False
        self._finalizer = weakref.finalize(self, lease.close)
        return self

    @property
    def path(self) -> Path:
        with self._lock:
            self._require_open()
            verify_member(self._path, self._relative, self._member)
            return self._path

    @property
    def size(self) -> int:
        _ = self.path
        return self._member.size

    def read_bytes(self) -> bytes:
        """Read the immutable asset while renewing its independent lease."""

        with self._lock:
            self._require_open()
            try:
                value = self._path.read_bytes()
            except OSError as error:
                raise ExportUnavailableError(
                    "The prepared export asset storage is unavailable.",
                    details={"path": self._relative},
                ) from error
            if len(value) != self._member.size or sha256_bytes(value) != self._member.sha256:
                raise IntegrityError(
                    "The prepared export asset changed after preparation.",
                    details={"path": self._relative},
                )
            return value

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._finalizer.alive:
                self._finalizer()

    def __enter__(self) -> PreparedAsset:
        with self._lock:
            self._require_open()
            return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed or not self._lease.renew():
            raise RepositoryError("The prepared export asset is closed.")


class PreparedExport:
    """A leased prepared export that can be opened, served, or written."""

    __slots__ = (
        "__weakref__",
        "_artifact",
        "_cache_activity",
        "_closed",
        "_closure",
        "_finalizer",
        "_identity",
        "_lock",
        "_owns_repository",
        "_plan",
        "_prepared_states",
        "_repository",
        "_reused",
        "_reused_states",
    )
    _artifact: _PreparedArtifact
    _cache_activity: CacheActivity
    _closed: bool
    _closure: Mapping[str, ClosureMember]
    _finalizer: weakref.finalize
    _identity: str
    _lock: RLock
    _owns_repository: bool
    _plan: ExportPlan
    _prepared_states: tuple[str, ...]
    _repository: ExportRepository
    _reused: bool
    _reused_states: tuple[str, ...]

    def __init__(self) -> None:
        raise TypeError("PreparedExport values are returned by plan preparation")

    @classmethod
    def _create(
        cls,
        *,
        artifact: _PreparedArtifact,
        repository: ExportRepository,
        owns_repository: bool,
        plan: ExportPlan,
        reused: bool,
        prepared_states: tuple[str, ...],
        reused_states: tuple[str, ...],
        cache_activity: CacheActivity,
    ) -> PreparedExport:
        self = object.__new__(cls)
        if not artifact.alive or not isinstance(artifact.path, Path):
            raise TypeError("artifact must be a live prepared export artifact")
        if not isinstance(repository, ExportRepository):
            raise TypeError("repository must be an ExportRepository")
        if not isinstance(owns_repository, bool):
            raise TypeError("owns_repository must be a boolean")
        if not isinstance(plan, ExportPlan):
            raise TypeError("plan must be an ExportPlan")
        if not isinstance(reused, bool) or reused != plan.exact_reuse:
            raise ValueError("reused must match plan.exact_reuse")
        prepared = _fingerprints(prepared_states, "prepared_states")
        reused_values = _fingerprints(reused_states, "reused_states")
        if set(prepared) & set(reused_values):
            raise ValueError("prepared and reused states must be disjoint")
        if set(prepared) | set(reused_values) != set(plan.state_fingerprints):
            raise ValueError("prepared and reused states must cover the plan")
        if reused and prepared:
            raise ValueError("an exactly reused export cannot prepare states")
        if not isinstance(cache_activity, CacheActivity):
            raise TypeError("cache_activity must be CacheActivity")
        opened, closure = verified_closure(artifact)
        if not plan.matches(opened):
            raise ValueError("artifact state relation does not match the plan")
        self._artifact = artifact
        self._closure = closure
        self._repository = repository
        self._owns_repository = owns_repository
        self._plan = plan
        self._reused = reused
        self._prepared_states = prepared
        self._reused_states = reused_values
        self._cache_activity = cache_activity
        self._identity = opened.identity
        self._lock = RLock()
        self._closed = False
        self._finalizer = weakref.finalize(
            self,
            _release,
            artifact,
            repository if owns_repository else None,
        )
        return self

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def plan(self) -> ExportPlan:
        return self._plan

    @property
    def path(self) -> Path:
        with self._lock:
            self._require_open()
            self._verify("index.json")
            return self._artifact.path

    @property
    def reused(self) -> bool:
        return self._reused

    @property
    def prepared_states(self) -> tuple[str, ...]:
        return self._prepared_states

    @property
    def reused_states(self) -> tuple[str, ...]:
        return self._reused_states

    @property
    def cache_activity(self) -> CacheActivity:
        return self._cache_activity

    def open(self) -> NotebookExport:
        """Open the immutable export while this lease remains alive."""

        with self._lock:
            self._require_open()
            self._verify("index.json")
            opened = open_export(self._artifact.path)
            if opened.identity != self.identity:
                raise IntegrityError("The prepared export index changed after preparation.")
            return opened

    def asset(self, relative: str) -> PreparedAsset:
        """Borrow one declared export file with an independent response lease."""

        if not isinstance(relative, str) or not relative:
            raise TypeError("relative must be a nonempty string")
        with self._lock:
            self._require_open()
            member = self._closure.get(relative)
            if member is None:
                raise RepositoryError("The requested prepared export asset is unavailable.")
            path = self._asset_path(relative)
            verify_member(path, relative, member)
            lease = self._artifact.detach()
            return PreparedAsset._create(path, lease, relative, member)

    def manifest(
        self,
        export_url: str,
        *,
        state: str | Mapping[str, object] | None = None,
        refresh_interval_ms: int | None = None,
    ) -> JsonObject:
        """Return the core browser manifest for one selected prepared state."""

        with self._lock:
            selected = _select_state(self.open(), state)
            return _prepared_manifest(
                instance=self.identity,
                export_url=export_url,
                inputs=selected.inputs,
                state_fingerprint=selected.fingerprint,
                refresh_interval_ms=refresh_interval_ms,
            )

    def to_dict(self) -> JsonObject:
        """Return stable public preparation facts for machine consumers."""

        with self._lock:
            self._require_open()
            return {
                "identity": self.identity,
                "path": str(self.path),
                "reused": self.reused,
                "plan": self.plan.to_dict(),
                "prepared_states": list(self.prepared_states),
                "reused_states": list(self.reused_states),
                "cache_activity": self.cache_activity.to_dict(),
            }

    def write(
        self,
        output: StrPath,
        *,
        replace: bool = False,
        progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ExportResult:
        """Write and verify this prepared export at one destination."""

        from marimo_export._services.write_export import write_prepared_export

        with self._lock:
            self._require_open()
            return write_prepared_export(self, output, replace=replace, progress=progress)

    def renew(self) -> None:
        """Renew the underlying repository lease."""

        with self._lock:
            self._require_open()
            self._verify("index.json")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._finalizer.alive:
                self._finalizer()

    def __enter__(self) -> PreparedExport:
        self._require_open()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed or not self._artifact.alive:
            raise RepositoryError("The prepared export is closed.")

    def _asset_path(self, relative: str) -> Path:
        path = self._artifact.asset(relative)
        if path is None:
            raise RepositoryError("The requested prepared export asset is unavailable.")
        return path

    def _verify(self, relative: str) -> None:
        member = self._closure[relative]
        verify_member(self._asset_path(relative), relative, member)


def _exact_prepared_export(
    *,
    artifact: _PreparedArtifact,
    repository: ExportRepository,
    owns_repository: bool,
    resolve_plan: Callable[[], ExportPlan],
    require_current: Callable[[], None] | None = None,
) -> PreparedExport:
    """Return one exactly reused export and close its artifact on failure."""

    try:
        if require_current is not None:
            require_current()
        plan = resolve_plan()
        exact_plan = replace(
            plan,
            reusable_states=tuple(sorted(plan.state_fingerprints)),
            missing_states=(),
            exact_reuse=True,
        )
        return PreparedExport._create(
            artifact=artifact,
            repository=repository,
            owns_repository=owns_repository,
            plan=exact_plan,
            reused=True,
            prepared_states=(),
            reused_states=exact_plan.state_fingerprints,
            cache_activity=CacheActivity(),
        )
    except BaseException:
        artifact.close()
        raise


def _prepared_manifest(
    *,
    instance: str,
    export_url: str,
    inputs: Mapping[str, object],
    state_fingerprint: str,
    refresh_interval_ms: int | None,
) -> JsonObject:
    if (
        not isinstance(export_url, str)
        or not export_url
        or len(export_url.encode("utf-8")) > 8 * 1024
    ):
        raise ValueError("export_url must be a nonempty string of at most 8192 UTF-8 bytes")
    if len(instance) != 64 or any(character not in "0123456789abcdef" for character in instance):
        raise ValueError("instance must be a lowercase SHA-256 digest")
    if len(state_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in state_fingerprint
    ):
        raise ValueError("state_fingerprint must be a lowercase SHA-256 digest")
    value: JsonObject = {
        "schema": "marimo-export.prepared.v1",
        "instance": instance,
        "export_url": export_url,
        "inputs": portable_json(inputs, "prepared manifest inputs"),
        "state_fingerprint": state_fingerprint,
    }
    if refresh_interval_ms is not None:
        if (
            not isinstance(refresh_interval_ms, int)
            or isinstance(refresh_interval_ms, bool)
            or (refresh_interval_ms != 0 and not 250 <= refresh_interval_ms <= 60_000)
        ):
            raise ValueError("refresh_interval_ms must be 0 or between 250 and 60000")
        value["refresh_interval_ms"] = refresh_interval_ms
    return value


def _select_state(
    notebook_export: NotebookExport,
    selected: str | Mapping[str, object] | None,
) -> ExportState:
    if selected is None:
        return notebook_export.default_state
    if isinstance(selected, str):
        return notebook_export.state(selected)
    if isinstance(selected, Mapping):
        inputs = portable_json(selected, "prepared state selection")
        if not isinstance(inputs, dict):
            raise AssertionError("prepared state selection is not an object")
        return notebook_export.resolve(inputs)
    raise TypeError("state must be an alias, input mapping, or None")


def _fingerprints(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple")
    parsed = tuple(sorted(values))
    if parsed != tuple(sorted(set(parsed))) or any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in parsed
    ):
        raise ValueError(f"{label} must contain sorted unique SHA-256 digests")
    return parsed


def _release(
    artifact: _PreparedArtifact,
    repository: ExportRepository | None,
) -> None:
    try:
        artifact.close()
    finally:
        if repository is not None:
            repository.close()


__all__ = ["PreparedAsset", "PreparedExport"]
