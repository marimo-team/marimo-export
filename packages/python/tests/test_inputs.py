from __future__ import annotations

import pytest
from marimo_export._cell_ids import canonical_cell_id
from marimo_export._control_roots import ControlRootCandidate, select_control_roots


def _candidate(
    name: str,
    *,
    control_ids: tuple[str, ...] | None = None,
    dependencies: tuple[str, ...] = (),
    eligible: bool = True,
) -> ControlRootCandidate:
    return ControlRootCandidate(
        name=name,
        control_ids=(f"{name}-control",) if control_ids is None else control_ids,
        input_dependencies=dependencies,
        eligible=eligible,
    )


def test_select_control_roots_keeps_disjoint_composite_dependencies() -> None:
    roots = select_control_roots(
        (
            _candidate("child"),
            _candidate(
                "parent",
                control_ids=("parent-child", "parent-root"),
                dependencies=("child",),
            ),
            _candidate("unrelated"),
        )
    )

    assert roots == ("child", "parent", "unrelated")


def test_select_control_roots_collapses_one_id_aliases() -> None:
    roots = select_control_roots(
        (
            _candidate("child", control_ids=("shared",)),
            _candidate("alias", control_ids=("shared",), dependencies=("child",)),
        )
    )

    assert roots == ("alias",)


def test_select_control_roots_assigns_strict_subsets_to_their_owner() -> None:
    roots = select_control_roots(
        (
            _candidate("child", control_ids=("shared",)),
            _candidate("parent", control_ids=("parent", "shared")),
        )
    )

    assert roots == ("parent",)


def test_select_control_roots_rejects_ambiguous_partial_overlap() -> None:
    with pytest.raises(ValueError, match="overlap without a unique owner"):
        select_control_roots(
            (
                _candidate("first", control_ids=("first", "shared")),
                _candidate("second", control_ids=("second", "shared")),
            )
        )


def test_select_control_roots_keeps_distinct_derived_single_controls() -> None:
    roots = select_control_roots(
        (
            _candidate("child"),
            _candidate("derived", dependencies=("child",)),
        )
    )

    assert roots == ("child", "derived")


def test_select_control_roots_retains_multiple_parents() -> None:
    roots = select_control_roots(
        (
            _candidate("child"),
            _candidate(
                "first_parent",
                control_ids=("first-child", "first-root"),
                dependencies=("child",),
            ),
            _candidate(
                "second_parent",
                control_ids=("second-child", "second-root"),
                dependencies=("child",),
            ),
        )
    )

    assert roots == ("child", "first_parent", "second_parent")


def test_select_control_roots_ignores_candidates_without_controls() -> None:
    assert select_control_roots((_candidate("empty", control_ids=()),)) == ()


def test_select_control_roots_excludes_controls_owned_by_ineligible_tree() -> None:
    roots = select_control_roots(
        (
            _candidate(
                "secret_parent",
                control_ids=("child", "secret-root"),
                eligible=False,
            ),
            _candidate("portable_child", control_ids=("child",)),
            _candidate("unrelated"),
        )
    )

    assert roots == ("unrelated",)


def test_select_control_roots_rejects_dependency_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        select_control_roots(
            (
                _candidate("first", control_ids=("shared",), dependencies=("second",)),
                _candidate("second", control_ids=("shared",), dependencies=("first",)),
            )
        )


def test_control_root_candidate_rejects_keywords_and_invalid_dependencies() -> None:
    with pytest.raises(ValueError, match="valid input identifier"):
        _candidate("class")
    with pytest.raises(ValueError, match="dependencies"):
        _candidate("valid", dependencies=("for",))


def test_select_control_roots_rejects_partial_dependency_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        select_control_roots(
            (
                _candidate("first", control_ids=("shared",), dependencies=("second",)),
                _candidate("second", control_ids=("shared",), dependencies=("first",)),
                _candidate("third"),
            )
        )


def test_canonical_cell_id_removes_only_external_uuidv4_scope() -> None:
    prefix = "5c0ee8ec-d28d-4cb7-a4dc-4a77a54326a7"

    assert canonical_cell_id(f"{prefix}Hbol") == "Hbol"
    assert canonical_cell_id("Hbol") == "Hbol"
    assert canonical_cell_id("5c0ee8ec-d28d-1cb7-a4dc-4a77a54326a7Hbol") != "Hbol"

    with pytest.raises(ValueError, match="bounded"):
        canonical_cell_id("")
