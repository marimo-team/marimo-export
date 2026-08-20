from __future__ import annotations

import weakref
from contextlib import contextmanager
from typing import Any

import marimo_export._marimo.compat.cache.barrier as cache_barrier
import pytest
from marimo_export._diagnostics import cleanup_failures
from marimo_export._marimo.compat.child_run import (
    cleanup_state_child,
    own_state_child,
    release_state_child,
)


def test_state_child_cleanup_releases_after_teardown_cancellation() -> None:
    events: list[str] = []

    class Runner:
        pass

    class Parent:
        def __init__(self, child_context: object) -> None:
            self.children = [child_context]

        def remove_child(self, child_context: object) -> None:
            self.children.remove(child_context)

    runner = Runner()
    child_context = object()
    parent = Parent(child_context)
    finalizer = weakref.finalize(runner, parent.remove_child, child_context)
    finalizer.atexit = False

    def teardown() -> None:
        events.append("teardown")
        raise KeyboardInterrupt("cancelled")

    def release() -> None:
        events.append("release")
        release_state_child(
            child=runner,
            parent_context=parent,
            child_context=child_context,
        )

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        cleanup_state_child(
            close_recording=lambda: events.append("recording"),
            teardown=teardown,
            release=release,
            primary=None,
            state_name="baseline",
        )

    assert events == ["recording", "teardown", "release"]
    assert parent.children == []
    assert not finalizer.alive


def test_state_child_cleanup_preserves_the_execution_error() -> None:
    primary = ValueError("execution failed")

    def teardown() -> None:
        raise KeyboardInterrupt("cancelled")

    def release() -> None:
        raise RuntimeError("release failed")

    cleanup_state_child(
        close_recording=lambda: None,
        teardown=teardown,
        release=release,
        primary=primary,
        state_name="baseline",
    )

    assert cleanup_failures(primary) == (
        "state child cleanup also failed: KeyboardInterrupt",
        "state child cleanup also failed: RuntimeError",
    )


def test_state_child_ownership_releases_after_recording_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class ChildContext:
        @contextmanager
        def install(self) -> Any:
            yield

    class Runner:
        def __init__(self, context: ChildContext) -> None:
            self._runtime_context = context

    class Parent:
        def __init__(self, context: ChildContext) -> None:
            self.children = [context]

        def remove_child(self, context: ChildContext) -> None:
            self.children.remove(context)

    context = ChildContext()
    parent = Parent(context)
    runner = Runner(context)
    finalizer = weakref.finalize(runner, parent.remove_child, context)
    finalizer.atexit = False
    monkeypatch.setattr(cache_barrier, "flush_native_caches", lambda: events.append("flush"))

    @contextmanager
    def fail_recording() -> Any:
        raise RuntimeError("recording failed")
        yield

    with (
        pytest.raises(RuntimeError, match="recording failed"),
        own_state_child(
            child=runner,
            parent_context=parent,
            state_name="baseline",
        ) as ownership,
    ):
        ownership.recordings.enter_context(fail_recording())

    assert events == ["flush"]
    assert parent.children == []
    assert not finalizer.alive
