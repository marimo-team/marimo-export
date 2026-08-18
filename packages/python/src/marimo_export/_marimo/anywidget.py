"""Focused port and composition root for live AnyWidget capture."""

from __future__ import annotations

from typing import Protocol


class AnyWidgetCapture(Protocol):
    """Capture one live AnyWidget representation."""

    def capture(self, value: object) -> bytes: ...


def create_anywidget_capture() -> AnyWidgetCapture:
    """Construct the adapter for one live AnyWidget graph."""

    from marimo_export._marimo.compat.anywidget import PrivateAnyWidgetCapture

    return PrivateAnyWidgetCapture()


__all__ = ["AnyWidgetCapture", "create_anywidget_capture"]
