"""Typed export source records and projection helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias, cast

from marimo._runtime.context.types import RuntimeContext
from marimo._types.ids import CellId_t
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from moexport.runtime import NotebookRuntime

SourceKey: TypeAlias = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
SourceRecord: TypeAlias = dict[str, Any]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CellSelector(SourceModel):
    """Select one notebook cell by stable id, public name, or order index."""

    id: str | None = Field(
        default=None,
        description="Runtime cell id to select.",
    )
    name: str | None = Field(
        default=None,
        description="Public cell function name to select.",
    )
    index: int | None = Field(
        default=None,
        description="Zero-based notebook cell position to select.",
    )

    @field_validator("id", "name")
    @classmethod
    def _selector_text_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("cell selector text must not be empty")
        return value

    @field_validator("index")
    @classmethod
    def _index_must_be_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("cell selector index must be non-negative")
        return value

    @classmethod
    def from_value(cls, value: object) -> CellSelector:
        if isinstance(value, CellSelector):
            return value
        if isinstance(value, int):
            return cls(index=value)
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, Mapping):
            return cls.model_validate(value)
        raise ValueError("cell selector must be an index, name, or selector object")

    def model_post_init(self, __context: object) -> None:
        selectors = [self.id, self.name, self.index]
        if sum(value is not None for value in selectors) != 1:
            raise ValueError("cell selector must set exactly one of id, name, or index")

    def expression_args(self) -> str:
        if self.id is not None:
            return f"id={json.dumps(self.id)}"
        if self.name is not None:
            return json.dumps(self.name)
        return f"index={self.index}"

    def select(self, notebook: NotebookRuntime) -> Any:
        if self.id is not None:
            return notebook.cell(id=self.id)
        if self.name is not None:
            return notebook.cell(name=self.name)
        if self.index is None:
            raise ValueError("cell selector must set id, name, or index")
        return notebook.cell(index=self.index)


class DefinitionSource(SourceModel):
    """A notebook definition exported by name."""

    type: Literal["definition"] = Field(
        default="definition",
        description="Discriminator for a notebook definition source.",
    )
    name: SourceKey = Field(
        description="Definition name to read from the notebook graph.",
    )


class ExpressionSource(SourceModel):
    """A Python expression evaluated against the active scenario."""

    type: Literal["expression"] = Field(
        default="expression",
        description="Discriminator for a Python expression source.",
    )
    expression: str = Field(
        description="Python expression evaluated against the active scenario.",
    )

    @field_validator("expression")
    @classmethod
    def _expression_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source expression must not be empty")
        return value


class CellOutputSource(SourceModel):
    """A selected cell output materialized for the active scenario."""

    type: Literal["cell_output"] = Field(
        default="cell_output",
        description="Discriminator for a selected cell output source.",
    )
    cell: CellSelector = Field(
        description="Cell whose visible output should be materialized.",
    )
    on_error: Literal["raise", "record"] = Field(
        default="raise",
        description="Whether output materialization errors should raise or be recorded.",
    )

    @field_validator("cell", mode="before")
    @classmethod
    def _cell_selector(cls, value: object) -> object:
        return CellSelector.from_value(value)


class NotebookSnapshotSource(SourceModel):
    """All selected notebook cells as ordered display-output records."""

    type: Literal["notebook_snapshot"] = Field(
        default="notebook_snapshot",
        description="Discriminator for a whole-notebook snapshot source.",
    )
    include_source: bool = Field(
        default=True,
        description="Include authored Python source in each cell record.",
    )
    include_empty_outputs: bool = Field(
        default=True,
        description="Include cells that have no display output.",
    )
    include_internal_cells: bool = Field(
        default=False,
        description="Include exporter-generated internal cells.",
    )
    on_error: Literal["raise", "record"] = Field(
        default="raise",
        description="Whether output materialization errors should raise or be recorded.",
    )


class ReportCell(SourceModel):
    """One cell included in a report source."""

    cell: CellSelector = Field(
        description="Cell to include in the report.",
    )
    label: str | None = Field(
        default=None,
        description="Optional report label for this cell.",
    )
    order: int | None = Field(
        default=None,
        description="Optional sort key for report ordering.",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_selector_shorthand(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if "cell" in value:
            return value
        selector = {
            key: item for key, item in value.items() if key in {"id", "name", "index"}
        }
        if not selector:
            return value
        return {
            "cell": selector,
            **{
                key: item
                for key, item in value.items()
                if key not in {"id", "name", "index"}
            },
        }

    @field_validator("cell", mode="before")
    @classmethod
    def _cell_selector(cls, value: object) -> object:
        return CellSelector.from_value(value)


class ReportSource(SourceModel):
    """A finite report built from selected cell outputs."""

    type: Literal["report"] = Field(
        default="report",
        description="Discriminator for a selected-cell report source.",
    )
    cells: list[ReportCell] = Field(
        min_length=1,
        description="Ordered cell outputs to include in the report.",
    )
    include_source: bool = Field(
        default=True,
        description="Include authored Python source in each report cell.",
    )
    on_error: Literal["raise", "record"] = Field(
        default="record",
        description="Whether output materialization errors should raise or be recorded.",
    )


SourceSpec: TypeAlias = Annotated[
    DefinitionSource
    | ExpressionSource
    | CellOutputSource
    | NotebookSnapshotSource
    | ReportSource,
    Field(discriminator="type"),
]

SOURCE_SPEC_ADAPTER = TypeAdapter(SourceSpec)


def normalize_source(value: object) -> SourceSpec:
    """Parse public source shorthand into a typed source record."""

    if isinstance(value, str):
        raise ValueError(
            "value source must be typed, for example {expr: 'df'} or {def: 'df'}"
        )
    if not isinstance(value, Mapping):
        return SOURCE_SPEC_ADAPTER.validate_python(value)

    mapping = cast(Mapping[object, object], value)
    if "type" in mapping:
        return SOURCE_SPEC_ADAPTER.validate_python(dict(mapping))
    if "def" in mapping:
        return DefinitionSource(name=_required_string(mapping["def"], "source.def"))
    if "expr" in mapping:
        return ExpressionSource(
            expression=_required_string(mapping["expr"], "source.expr")
        )
    if "cell" in mapping:
        return CellOutputSource(
            cell=CellSelector.from_value(mapping["cell"]),
            on_error=_on_error(mapping.get("on_error", "raise")),
        )
    if "snapshot" in mapping or "notebook" in mapping:
        return NotebookSnapshotSource.model_validate(
            {
                "type": "notebook_snapshot",
                **{
                    str(key): item
                    for key, item in mapping.items()
                    if key not in {"snapshot", "notebook"}
                },
            }
        )
    if "report" in mapping:
        report = mapping["report"]
        if not isinstance(report, Mapping):
            raise ValueError("source.report must be an object")
        return ReportSource.model_validate({"type": "report", **dict(report)})
    return SOURCE_SPEC_ADAPTER.validate_python(dict(mapping))


def source_expression(source: SourceSpec) -> str:
    """Return the private evaluator expression for a typed source."""

    if isinstance(source, DefinitionSource):
        return source.name
    if isinstance(source, ExpressionSource):
        return source.expression
    if isinstance(source, CellOutputSource):
        return (
            f"mox.runtime().cell({source.cell.expression_args()})"
            f".scenario_output(on_error={source.on_error!r})"
        )
    if isinstance(source, NotebookSnapshotSource):
        return (
            "mox.runtime().snapshot("
            f"include_source={source.include_source!r}, "
            f"include_empty_outputs={source.include_empty_outputs!r}, "
            f"include_internal_cells={source.include_internal_cells!r}, "
            f"on_error={source.on_error!r})"
        )
    if isinstance(source, ReportSource):
        cells = [
            {
                **cell.cell.model_dump(mode="json", exclude_none=True),
                **({"label": cell.label} if cell.label is not None else {}),
                **({"order": cell.order} if cell.order is not None else {}),
            }
            for cell in source.cells
        ]
        return (
            "mox.runtime().report("
            f"{json.dumps(cells)}, "
            f"include_source={source.include_source!r}, "
            f"on_error={source.on_error!r})"
        )
    raise TypeError(f"unknown source type {type(source).__name__}")


def source_record(source: SourceSpec) -> SourceRecord:
    """Return the manifest form of a source."""

    return source.model_dump(mode="json", exclude_none=True)


def selected_output_cell_ids(
    sources: Mapping[str, SourceSpec],
    runtime: RuntimeContext,
) -> set[CellId_t]:
    """Return output cells needed to evaluate typed sources."""

    notebook = NotebookRuntime(runtime)
    cell_ids: set[CellId_t] = set()
    for source in sources.values():
        if isinstance(source, CellOutputSource):
            cell_ids.add(source.cell.select(notebook)._cell_id)
        elif isinstance(source, NotebookSnapshotSource):
            cell_ids.update(cell._cell_id for cell in notebook.cells())
        elif isinstance(source, ReportSource):
            cell_ids.update(
                cell.cell.select(notebook)._cell_id for cell in source.cells
            )
    return cell_ids


def output_error_policy(
    sources: Mapping[str, SourceSpec],
) -> Literal["raise", "record"]:
    """Return the display-output error policy needed by typed sources."""

    for source in sources.values():
        if isinstance(source, CellOutputSource):
            if source.on_error == "record":
                return "record"
        elif (
            isinstance(
                source,
                (NotebookSnapshotSource, ReportSource),
            )
            and source.on_error == "record"
        ):
            return "record"
    return "raise"


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _on_error(value: object) -> Literal["raise", "record"]:
    if value not in {"raise", "record"}:
        raise ValueError("source.on_error must be 'raise' or 'record'")
    return cast(Literal["raise", "record"], value)


__all__ = [
    "CellOutputSource",
    "CellSelector",
    "DefinitionSource",
    "ExpressionSource",
    "NotebookSnapshotSource",
    "ReportCell",
    "ReportSource",
    "SOURCE_SPEC_ADAPTER",
    "SourceRecord",
    "SourceSpec",
    "normalize_source",
    "output_error_policy",
    "selected_output_cell_ids",
    "source_expression",
    "source_record",
]
