from __future__ import annotations

import math
import threading
import time
import weakref
from pathlib import Path
from time import monotonic as _monotonic
from uuid import uuid4

from marimo_export._repository.capabilities import (
    ArtifactKey,
    LeaseCatalog,
    LostLifecycle,
)
from marimo_export._repository.models import (
    MAX_SQLITE_INTEGER,
    RepositoryBusyError,
    RepositoryIdentity,
)

_BASE = "base"
_RETAINED = "retained"
_DETACHED = "detached"


class ArtifactLease:
    """Keep one immutable repository artifact live until closed."""

    def __init__(self, manager: LeaseManager, key: ArtifactKey, kind: str) -> None:
        self._manager = manager
        self._key = key
        self._kind = kind
        self._finalizer = weakref.finalize(
            self,
            _release,
            weakref.ref(manager),
            key,
            kind,
        )

    def renew(self) -> bool:
        return self._finalizer.alive and self._manager.touch(self._key, self._kind)

    @property
    def alive(self) -> bool:
        return self._finalizer.alive and self._manager.owns(self._key, self._kind)

    def retain(self) -> ArtifactLease:
        if not self._finalizer.alive:
            raise RuntimeError("The repository artifact lease is closed")
        return self._manager.retain(self._key, detached=False)

    def detach(self) -> ArtifactLease:
        if not self._finalizer.alive:
            raise RuntimeError("The repository artifact lease is closed")
        return self._manager.retain(self._key, detached=True)

    def close(self) -> None:
        self._finalizer()

    def __enter__(self) -> ArtifactLease:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class LeaseManager:
    """Own renewable cross-process leases for one repository instance."""

    def __init__(
        self,
        catalog: LeaseCatalog,
        *,
        ttl_seconds: float,
        heartbeat_seconds: float,
    ) -> None:
        for value, name in (
            (ttl_seconds, "lease TTL"),
            (heartbeat_seconds, "lease heartbeat"),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
                or value > threading.TIMEOUT_MAX
            ):
                raise ValueError(f"{name} must be a finite positive timeout")
        if heartbeat_seconds >= ttl_seconds:
            raise ValueError("lease heartbeat must be shorter than its TTL")
        self.catalog = catalog
        self.owner = uuid4().hex
        self._ttl_us = int(ttl_seconds * 1_000_000)
        if not 1 <= self._ttl_us <= MAX_SQLITE_INTEGER:
            raise ValueError("lease TTL exceeds SQLite's integer range")
        self._heartbeat_seconds = heartbeat_seconds
        self._base: dict[ArtifactKey, int] = {}
        self._retained: dict[ArtifactKey, int] = {}
        self._detached: dict[ArtifactKey, int] = {}
        self._confirmed_artifact_expires_at_us: dict[ArtifactKey, int] = {}
        self._confirmed_artifact_deadline: dict[ArtifactKey, float] = {}
        self._pending_artifact_releases: dict[ArtifactKey, int] = {}
        self._staging: dict[str, Path] = {}
        self._confirmed_staging_deadline: dict[str, float] = {}
        self._pending_staging_releases: set[str] = set()
        self._reservations: dict[str, tuple[int, int]] = {}
        self._confirmed_reservation_deadline: dict[str, float] = {}
        self._lost_reservations: set[str] = set()
        self._pending_reservation_releases: set[str] = set()
        self._condition = threading.Condition()
        self._wake = threading.Event()
        self._closed = False
        self._maintaining = False
        self._stopped = False
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None

    def expiry(self) -> int:
        with self._condition:
            self._raise_failure()
        now_us = _now_us()
        if self._ttl_us > MAX_SQLITE_INTEGER - now_us:
            raise ValueError("lease expiry exceeds SQLite's integer range")
        return now_us + self._ttl_us

    def acquire(self, key: ArtifactKey, *, renewed_until_us: int) -> ArtifactLease:
        with self._condition:
            self._raise_failure()
            if self._closed:
                raise RuntimeError("The export repository is closed")
            if renewed_until_us <= _now_us():
                raise RepositoryBusyError(
                    "The durable repository artifact lease expired before it was acquired."
                )
            self._base[key] = self._base.get(key, 0) + 1
            self._confirmed_artifact_expires_at_us[key] = max(
                renewed_until_us,
                self._confirmed_artifact_expires_at_us.get(key, 0),
            )
            deadline = _confirmed_deadline(renewed_until_us)
            self._confirmed_artifact_deadline[key] = max(
                deadline,
                self._confirmed_artifact_deadline.get(key, 0),
            )
            self._pending_artifact_releases.pop(key, None)
            self._start()
        del renewed_until_us
        return ArtifactLease(self, key, _BASE)

    def retain(self, key: ArtifactKey, *, detached: bool) -> ArtifactLease:
        with self._condition:
            self._raise_failure()
            if self._closed or not self._live(key):
                raise RuntimeError("The export repository is closed")
            counts = self._detached if detached else self._retained
            counts[key] = counts.get(key, 0) + 1
            self._pending_artifact_releases.pop(key, None)
        return ArtifactLease(self, key, _DETACHED if detached else _RETAINED)

    def reserve_staging(
        self,
        relative: str,
        path: Path,
        *,
        timeout_seconds: float,
    ) -> None:
        with self._condition:
            self._require_open()
            self._staging[relative] = path
            self._pending_staging_releases.discard(relative)
        expires_at_us = self.expiry()
        try:
            self.catalog.acquire_staging(
                self.owner,
                relative,
                expires_at_us,
                timeout_seconds,
            )
        except BaseException:
            with self._condition:
                self._staging.pop(relative, None)
                self._confirmed_staging_deadline.pop(relative, None)
            raise
        with self._condition:
            if relative in self._staging:
                if expires_at_us <= _now_us():
                    self._staging.pop(relative, None)
                    raise RepositoryBusyError(
                        "The durable repository staging lease expired before acquisition."
                    )
                deadline = _confirmed_deadline(expires_at_us)
                self._confirmed_staging_deadline[relative] = max(
                    deadline,
                    self._confirmed_staging_deadline.get(relative, 0),
                )
                self._start()

    def release_staging(self, relative: str) -> None:
        with self._condition:
            self._confirmed_staging_deadline.pop(relative, None)
            if self._staging.pop(relative, None) is not None:
                self._pending_staging_releases.add(relative)
        self._wake.set()
        self.flush_releases()

    def claim_reservation(
        self,
        identity: RepositoryIdentity,
        *,
        timeout_seconds: float,
    ) -> int | None:
        with self._condition:
            self._require_open()
        expires_at_us = self.expiry()
        fence = self.catalog.claim_reservation(
            self.owner,
            identity.key,
            identity.producer_sha256,
            identity.output_plan_sha256,
            identity.spec_sha256,
            expires_at_us,
            _now_us(),
            timeout_seconds,
        )
        if fence is None:
            return None
        with self._condition:
            self._require_open()
            if expires_at_us <= _now_us():
                self._lost_reservations.add(identity.key)
                raise RepositoryBusyError(
                    "The durable preparation reservation expired before acquisition."
                )
            current = self._reservations.get(identity.key)
            if current is not None and current[0] != fence:
                raise RuntimeError("The preparation reservation fence changed unexpectedly")
            count = 0 if current is None else current[1]
            self._reservations[identity.key] = (fence, count + 1)
            deadline = _confirmed_deadline(expires_at_us)
            self._confirmed_reservation_deadline[identity.key] = max(
                deadline,
                self._confirmed_reservation_deadline.get(identity.key, 0),
            )
            self._lost_reservations.discard(identity.key)
            self._pending_reservation_releases.discard(identity.key)
            self._start()
        return fence

    def release_reservation(self, identity_key: str) -> None:
        with self._condition:
            current = self._reservations.get(identity_key)
            if current is not None and current[1] > 1:
                self._reservations[identity_key] = (current[0], current[1] - 1)
            elif current is not None:
                self._reservations.pop(identity_key)
                self._confirmed_reservation_deadline.pop(identity_key, None)
                self._pending_reservation_releases.add(identity_key)
            else:
                self._confirmed_reservation_deadline.pop(identity_key, None)
        self._wake.set()
        self.flush_releases()

    def reservation_alive(self, identity_key: str, fence: int) -> bool:
        with self._condition:
            self._raise_failure()
            return (
                not self._closed
                and identity_key not in self._lost_reservations
                and (current := self._reservations.get(identity_key)) is not None
                and current[0] == fence
            )

    def touch(self, key: ArtifactKey, kind: str) -> bool:
        with self._condition:
            self._raise_failure()
            return self._counts(kind).get(key, 0) > 0

    def owns(self, key: ArtifactKey, kind: str) -> bool:
        with self._condition:
            self._expire_unconfirmed_artifacts()
            return self._failure is None and self._counts(kind).get(key, 0) > 0

    def release(self, key: ArtifactKey, kind: str) -> None:
        with self._condition:
            counts = self._counts(kind)
            count = counts.get(key)
            if count is None:
                return
            if count > 1:
                counts[key] = count - 1
            else:
                counts.pop(key, None)
            if not self._live(key):
                confirmed = self._confirmed_artifact_expires_at_us.pop(key, 0)
                self._confirmed_artifact_deadline.pop(key, None)
                self._pending_artifact_releases[key] = confirmed
            self._condition.notify_all()
        self._wake.set()

    def flush_releases(self) -> None:
        with self._condition:
            self._raise_failure()
            if not self._pending() and not self._maintaining:
                return
            self._wake.set()
            finished = self._condition.wait_for(
                lambda: (
                    self._failure is not None or (not self._pending() and not self._maintaining)
                ),
                timeout=10,
            )
            if not finished:
                raise RuntimeError("Repository lease releases did not finish")
            self._raise_failure()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for key in (*self._base, *self._retained):
                if key not in self._detached:
                    self._pending_artifact_releases[key] = (
                        self._confirmed_artifact_expires_at_us.get(key, 0)
                    )
            self._base.clear()
            self._retained.clear()
            for key in tuple(self._confirmed_artifact_deadline):
                if key not in self._detached:
                    self._confirmed_artifact_expires_at_us.pop(key, None)
                    self._confirmed_artifact_deadline.pop(key, None)
            self._pending_staging_releases.update(self._staging)
            self._staging.clear()
            self._confirmed_staging_deadline.clear()
            self._pending_reservation_releases.update(self._reservations)
            self._reservations.clear()
            self._confirmed_reservation_deadline.clear()
            if self._thread is None:
                self._stopped = True
                return
            wait = not self._detached
            self._condition.notify_all()
        self._wake.set()
        if wait and self._thread is not threading.current_thread():
            with self._condition:
                self._condition.wait_for(lambda: self._stopped, timeout=1)
        if self._failure is not None:
            raise RuntimeError("Repository lease heartbeat failed") from self._failure

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def _counts(self, kind: str) -> dict[ArtifactKey, int]:
        if kind == _BASE:
            return self._base
        if kind == _RETAINED:
            return self._retained
        return self._detached

    def _live(self, key: ArtifactKey) -> bool:
        return key in self._base or key in self._retained or key in self._detached

    def _pending(self) -> bool:
        return bool(
            self._pending_artifact_releases
            or self._pending_staging_releases
            or self._pending_reservation_releases
        )

    def _require_open(self) -> None:
        self._raise_failure()
        if self._closed:
            raise RuntimeError("The export repository is closed")

    def _raise_failure(self) -> None:
        self._expire_unconfirmed_lifecycle()
        self._expire_unconfirmed_artifacts()
        if self._failure is not None:
            raise RuntimeError("Repository lease heartbeat failed") from self._failure

    def _expire_unconfirmed_artifacts(self) -> None:
        if self._failure is not None:
            return
        now = _monotonic()
        if any(
            self._live(key) and deadline <= now
            for key, deadline in self._confirmed_artifact_deadline.items()
        ):
            self._failure = RepositoryBusyError(
                "A durable repository artifact lease expired before renewal was confirmed."
            )

    def _expire_unconfirmed_lifecycle(self) -> None:
        now = _monotonic()
        expired_staging = tuple(
            relative
            for relative, deadline in self._confirmed_staging_deadline.items()
            if relative in self._staging and deadline <= now
        )
        for relative in expired_staging:
            self._staging.pop(relative, None)
            self._confirmed_staging_deadline.pop(relative, None)
        expired_reservations = tuple(
            identity
            for identity, deadline in self._confirmed_reservation_deadline.items()
            if identity in self._reservations and deadline <= now
        )
        for identity in expired_reservations:
            self._reservations.pop(identity, None)
            self._confirmed_reservation_deadline.pop(identity, None)
            self._lost_reservations.add(identity)
        if expired_staging or expired_reservations:
            self._condition.notify_all()

    def _start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=_heartbeat,
            args=(weakref.ref(self), self._wake, self._heartbeat_seconds),
            name="marimo-export-repository-leases",
            daemon=True,
        )
        self._thread.start()

    def _maintain(self) -> bool:
        with self._condition:
            self._expire_unconfirmed_lifecycle()
            self._maintaining = True
            live = tuple({*self._base, *self._retained, *self._detached})
            artifacts = tuple(
                (key, expires_at_us)
                for key, expires_at_us in self._pending_artifact_releases.items()
                if key not in live
            )
            for key, _expires_at_us in artifacts:
                self._pending_artifact_releases.pop(key, None)
            missing_staging = tuple(
                relative
                for relative, path in self._staging.items()
                if path.is_symlink() or not path.is_dir()
            )
            for relative in missing_staging:
                self._staging.pop(relative, None)
                self._confirmed_staging_deadline.pop(relative, None)
                self._pending_staging_releases.add(relative)
            staging_releases = tuple(self._pending_staging_releases)
            reservation_releases = tuple(self._pending_reservation_releases)
            self._pending_staging_releases.clear()
            self._pending_reservation_releases.clear()
            staging = tuple(self._staging)
            reservations = tuple(self._reservations)
            reservation_fences = {
                identity: self._reservations[identity][0] for identity in reservations
            }
            closed = self._closed
        try:
            if artifacts or staging_releases or reservation_releases:
                self.catalog.release_lifecycle(
                    owner=self.owner,
                    artifacts=artifacts or (),
                    staging=staging_releases or (),
                    reservations=reservation_releases or (),
                )
            if live or staging or reservations:
                renewed_until_us = self.expiry()
                renewal_deadline = _confirmed_deadline(renewed_until_us)
                with self._condition:
                    live = tuple(key for key in live if self._live(key))
                    staging = tuple(relative for relative in staging if relative in self._staging)
                    reservations = tuple(
                        identity for identity in reservations if identity in self._reservations
                    )
                lost = (
                    self.catalog.renew_lifecycle(
                        owner=self.owner,
                        artifacts=live,
                        staging=staging,
                        reservations=reservations,
                        expires_at_us=renewed_until_us,
                    )
                    if live or staging or reservations
                    else LostLifecycle(frozenset(), frozenset(), frozenset())
                )
                with self._condition:
                    self._expire_unconfirmed_lifecycle()
                    self._expire_unconfirmed_artifacts()
                    now = _monotonic()
                    for key in live:
                        if (
                            key not in lost.artifacts
                            and not self._live(key)
                            and key in self._pending_artifact_releases
                        ):
                            self._pending_artifact_releases[key] = max(
                                renewed_until_us,
                                self._pending_artifact_releases[key],
                            )
                    if self._failure is None and renewal_deadline > now:
                        for key in live:
                            if key not in lost.artifacts and self._live(key):
                                self._confirmed_artifact_expires_at_us[key] = max(
                                    renewed_until_us,
                                    self._confirmed_artifact_expires_at_us.get(key, 0),
                                )
                                self._confirmed_artifact_deadline[key] = max(
                                    renewal_deadline,
                                    self._confirmed_artifact_deadline.get(key, 0),
                                )
                    if renewal_deadline > now:
                        for relative in staging:
                            if relative not in lost.staging and relative in self._staging:
                                self._confirmed_staging_deadline[relative] = max(
                                    renewal_deadline,
                                    self._confirmed_staging_deadline.get(relative, 0),
                                )
                        for identity in reservations:
                            current = self._reservations.get(identity)
                            if (
                                identity not in lost.reservations
                                and current is not None
                                and current[0] == reservation_fences[identity]
                            ):
                                self._confirmed_reservation_deadline[identity] = max(
                                    renewal_deadline,
                                    self._confirmed_reservation_deadline.get(identity, 0),
                                )
                    if lost.artifacts and self._failure is None:
                        self._failure = RepositoryBusyError(
                            "A durable repository artifact lease was lost during renewal."
                        )
                    for relative in lost.staging:
                        if relative in staging:
                            self._staging.pop(relative, None)
                            self._confirmed_staging_deadline.pop(relative, None)
                    if lost.reservations:
                        for identity in lost.reservations:
                            current = self._reservations.get(identity)
                            if current is not None and current[0] == reservation_fences[identity]:
                                self._reservations.pop(identity, None)
                                self._confirmed_reservation_deadline.pop(identity, None)
                                self._lost_reservations.add(identity)
        except Exception as error:
            with self._condition:
                self._pending_artifact_releases.update(artifacts)
                self._pending_staging_releases.update(staging_releases)
                self._pending_reservation_releases.update(reservation_releases)
                if closed and not self._detached:
                    self._pending_artifact_releases.clear()
                    self._pending_staging_releases.clear()
                    self._pending_reservation_releases.clear()
                    self._stopped = True
                    self._condition.notify_all()
                    return True
            if isinstance(error, RepositoryBusyError):
                return False
            raise
        finally:
            with self._condition:
                self._maintaining = False
                self._condition.notify_all()
                if self._pending():
                    self._wake.set()
        with self._condition:
            if closed and not self._detached and not self._pending():
                self._stopped = True
                self._condition.notify_all()
                return True
        return False


def _heartbeat(
    manager_ref: weakref.ReferenceType[LeaseManager],
    wake: threading.Event,
    interval: float,
) -> None:
    try:
        while True:
            wake.wait(interval)
            wake.clear()
            manager = manager_ref()
            if manager is None or manager._maintain():
                return
            del manager
    except BaseException as error:
        manager = manager_ref()
        if manager is not None:
            with manager._condition:
                manager._failure = error
    finally:
        manager = manager_ref()
        if manager is not None:
            with manager._condition:
                manager._maintaining = False
                manager._stopped = True
                manager._condition.notify_all()


def _release(
    manager_ref: weakref.ReferenceType[LeaseManager],
    key: ArtifactKey,
    kind: str,
) -> None:
    manager = manager_ref()
    if manager is not None:
        manager.release(key, kind)


def _now_us() -> int:
    return time.time_ns() // 1000


def _confirmed_deadline(expires_at_us: int) -> float:
    remaining_seconds = max(0.0, (expires_at_us - _now_us()) / 1_000_000)
    return _monotonic() + remaining_seconds


__all__ = ["ArtifactLease", "LeaseManager"]
