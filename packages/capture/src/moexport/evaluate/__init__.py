"""Public live-runtime evaluation API.

This package exposes the small kernel-side surface used by export capture:
``evaluate(...)``. The implementation is split by responsibility below this
module, but callers should treat this file as the boundary and import from
``moexport.evaluate`` or ``moexport``.
"""

from __future__ import annotations

from moexport.evaluate._batch import evaluate
from moexport.evaluate._types import EvaluateResult, TargetRunResult

__all__ = [
    "EvaluateResult",
    "TargetRunResult",
    "evaluate",
]
