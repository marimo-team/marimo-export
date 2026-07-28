from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, TypeVar

SourceT = TypeVar("SourceT")
NativeT = TypeVar("NativeT")
Exporter: TypeAlias = Callable[[SourceT], NativeT]

__all__ = ["Exporter"]
