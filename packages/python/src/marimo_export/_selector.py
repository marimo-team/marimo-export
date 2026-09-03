from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

from marimo_export.errors import SpecError

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)")
_JSON_DECODER = json.JSONDecoder()
_MAX_SELECTOR_BYTES = 2_048


@dataclass(frozen=True, slots=True)
class AttributeStep:
    kind: Literal["attribute"]
    key: str


@dataclass(frozen=True, slots=True)
class ItemStep:
    kind: Literal["item"]
    key: str | int


SelectorStep: TypeAlias = AttributeStep | ItemStep


@dataclass(frozen=True, slots=True)
class ValueSelector:
    """An ASCII Python name followed by attribute or item selection."""

    source: str
    root: str
    path: tuple[SelectorStep, ...]

    @classmethod
    def parse(cls, source: str) -> ValueSelector:
        if not isinstance(source, str):
            raise TypeError("selector must be a string")
        if not source or source != source.strip():
            raise _selector_error(source, "must not be empty or contain surrounding whitespace")
        if len(source.encode("utf-8")) > _MAX_SELECTOR_BYTES:
            raise _selector_error(source, f"must contain at most {_MAX_SELECTOR_BYTES} UTF-8 bytes")
        root = _IDENTIFIER.match(source)
        if root is None:
            raise _selector_error(source, "must start with an ASCII Python identifier")
        path: list[SelectorStep] = []
        position = root.end()
        while position < len(source):
            token = source[position]
            if token == ".":
                step, position = _parse_attribute(source, position)
            elif token == "[":
                step, position = _parse_item(source, position)
            else:
                raise _selector_error(
                    source,
                    "may contain dot selection and bracket indexing",
                )
            path.append(step)
        return cls(source=source, root=root.group(), path=tuple(path))


def _parse_attribute(source: str, position: int) -> tuple[AttributeStep, int]:
    selected = _IDENTIFIER.match(source, position + 1)
    if selected is None:
        raise _selector_error(source, "dot selection requires an ASCII object key")
    return AttributeStep(kind="attribute", key=selected.group()), selected.end()


def _parse_item(source: str, position: int) -> tuple[ItemStep, int]:
    position += 1
    selected_index = _INDEX.match(source, position)
    if selected_index is not None:
        step = ItemStep(kind="item", key=int(selected_index.group()))
        position = selected_index.end()
    elif position < len(source) and source[position] == '"':
        try:
            selected_key, consumed = _JSON_DECODER.raw_decode(source[position:])
        except json.JSONDecodeError as error:
            raise _selector_error(source, "bracket object keys must be JSON strings") from error
        if not isinstance(selected_key, str):
            raise _selector_error(source, "bracket object keys must be JSON strings")
        step = ItemStep(kind="item", key=selected_key)
        position += consumed
    else:
        raise _selector_error(
            source,
            "brackets require a non-negative integer or JSON string",
        )
    if position >= len(source) or source[position] != "]":
        raise _selector_error(source, "bracket selection requires a closing ]")
    return step, position + 1


def _selector_error(source: object, detail: str) -> SpecError:
    return SpecError(
        f"invalid value selector {source!r}: {detail}",
        code="spec_output_invalid",
    )


__all__ = ["AttributeStep", "ItemStep", "SelectorStep", "ValueSelector"]
