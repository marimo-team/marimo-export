"""Select one canonical owner for each inferred Marimo control root."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from marimo_export._format import identifier_name

_MAX_CONTROL_ID_BYTES = 1_024


@dataclass(frozen=True, slots=True)
class ControlRootCandidate:
    """One UI definition considered for canonical root selection."""

    name: str
    control_ids: tuple[str, ...]
    input_dependencies: tuple[str, ...]
    eligible: bool = field(kw_only=True)

    def __post_init__(self) -> None:
        try:
            identifier_name(self.name, "control root name")
        except (TypeError, ValueError) as error:
            raise ValueError("control root name must be a valid input identifier") from error
        if (
            not isinstance(self.control_ids, tuple)
            or self.control_ids != tuple(sorted(set(self.control_ids)))
            or any(
                not isinstance(control_id, str)
                or not control_id
                or len(control_id.encode("utf-8")) > _MAX_CONTROL_ID_BYTES
                for control_id in self.control_ids
            )
        ):
            raise ValueError("control root IDs must be sorted bounded strings")
        if (
            not isinstance(self.input_dependencies, tuple)
            or self.input_dependencies != tuple(sorted(set(self.input_dependencies)))
            or any(not _valid_input_name(dependency) for dependency in self.input_dependencies)
        ):
            raise ValueError("control root dependencies must be sorted input identifiers")
        if not isinstance(self.eligible, bool):
            raise TypeError("control root eligibility must be a boolean")


def select_control_roots(
    candidates: Iterable[ControlRootCandidate],
    *,
    relevant: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return globally owned control roots relevant to selected definitions."""

    try:
        values = tuple(candidates)
    except TypeError as error:
        raise TypeError("candidates must be iterable") from error
    by_name: dict[str, ControlRootCandidate] = {}
    for candidate in values:
        if not isinstance(candidate, ControlRootCandidate):
            raise TypeError("candidates must contain ControlRootCandidate values")
        if candidate.name in by_name:
            raise ValueError(f"control root candidate {candidate.name!r} is duplicated")
        by_name[candidate.name] = candidate
    dependencies = {
        name: set(candidate.input_dependencies) & set(by_name)
        for name, candidate in by_name.items()
    }
    _validate_acyclic(dependencies)
    ineligible_sets = [
        frozenset(candidate.control_ids)
        for candidate in by_name.values()
        if not candidate.eligible and candidate.control_ids
    ]
    eligible = {
        name: candidate
        for name, candidate in by_name.items()
        if candidate.eligible
        and candidate.control_ids
        and not any(set(candidate.control_ids) & controls for controls in ineligible_sets)
    }
    aliases = _control_set_aliases(eligible)
    if relevant is None:
        selected = set(by_name)
    else:
        try:
            selected = set(relevant)
        except TypeError as error:
            raise TypeError("relevant must be iterable") from error
        unknown = selected - set(by_name)
        if unknown:
            raise ValueError(f"relevant control candidates are unavailable: {sorted(unknown)!r}")
        selected = _dependency_closure(selected, dependencies)
    return tuple(sorted({aliases[name] for name in selected if name in aliases}))


def _control_set_aliases(
    candidates: dict[str, ControlRootCandidate],
) -> dict[str, str]:
    equal_sets: dict[frozenset[str], list[ControlRootCandidate]] = {}
    for candidate in candidates.values():
        equal_sets.setdefault(frozenset(candidate.control_ids), []).append(candidate)
    control_sets = sorted(equal_sets, key=lambda controls: (len(controls), sorted(controls)))
    for position, controls in enumerate(control_sets):
        for other in control_sets[position + 1 :]:
            if controls & other and not (controls < other or other < controls):
                raise ValueError(
                    f"control root candidates "
                    f"{min(candidate.name for candidate in equal_sets[controls])!r} and "
                    f"{min(candidate.name for candidate in equal_sets[other])!r} "
                    "overlap without a unique owner"
                )
    owner_by_set: dict[frozenset[str], str] = {}
    for controls in equal_sets:
        supersets = [other for other in control_sets if controls < other]
        owner_set = max(supersets, key=len) if supersets else controls
        owner_by_set[controls] = min(candidate.name for candidate in equal_sets[owner_set])
    return {
        candidate.name: owner_by_set[frozenset(candidate.control_ids)]
        for candidate in candidates.values()
    }


def _dependency_closure(
    selected: set[str],
    dependencies: dict[str, set[str]],
) -> set[str]:
    result = set(selected)
    pending = list(selected)
    while pending:
        name = pending.pop()
        for dependency in dependencies[name] - result:
            result.add(dependency)
            pending.append(dependency)
    return result


def _valid_input_name(value: object) -> bool:
    try:
        identifier_name(value, "control root dependency")
    except (TypeError, ValueError):
        return False
    return True


def _validate_acyclic(dependencies: dict[str, set[str]]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            raise ValueError("control root dependencies contain a cycle")
        if name in visited:
            return
        active.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for name in dependencies:
        visit(name)


__all__ = ["ControlRootCandidate", "select_control_roots"]
