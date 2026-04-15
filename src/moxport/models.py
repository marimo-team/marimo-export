from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

JsonValue: TypeAlias = Any
JsonObject: TypeAlias = dict[str, Any]
ResolutionType: TypeAlias = Literal["retained", "materialized", "recomputed"]


class MoxportModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessionInfo(MoxportModel):
    session_id: str
    filename: str | None = None
    path: str | None = None


class ScratchpadError(MoxportModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    msg: str | None = None


class ScratchpadResult(MoxportModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    output: JsonValue | None = None
    output_mimetype: str | None = None
    error: ScratchpadError | None = None


class CellInfo(MoxportModel):
    index: int
    id: str
    name: str | None = None
    code: str
    kind: str = "python"
    config: JsonObject = Field(default_factory=dict)
    defs: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


class LiveCellInfo(MoxportModel):
    index: int
    id: str
    name: str | None = None
    code: str


class MaterializedCell(MoxportModel):
    id: str | None = None
    cell_type: str | None = None
    source: str | list[str] | None = None
    outputs: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class MaterializedNotebook(MoxportModel):
    cells: list[MaterializedCell] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    nbformat: int | None = None
    nbformat_minor: int | None = None


class RefDescriptionBase(MoxportModel):
    type: Literal["dataframe", "array", "html", "widget", "object"]
    kind: Literal["expression", "cell"]
    selector: str
    resolution: ResolutionType | None = None
    python_type: str
    module: str
    preview: str


class DataFrameRefDescription(RefDescriptionBase):
    type: Literal["dataframe"]
    rows: int
    cols: int
    columns: list[str] = Field(default_factory=list)


class ArrayRefDescription(RefDescriptionBase):
    type: Literal["array"]
    shape: list[int] = Field(default_factory=list)
    ndim: int
    dtype: JsonValue | None = None


class HtmlRefDescription(RefDescriptionBase):
    type: Literal["html"]
    mime: str
    text: str


class WidgetRefDescription(RefDescriptionBase):
    type: Literal["widget"]


class ObjectRefDescription(RefDescriptionBase):
    type: Literal["object"]


RemoteRefDescription = Annotated[
    DataFrameRefDescription
    | ArrayRefDescription
    | HtmlRefDescription
    | WidgetRefDescription
    | ObjectRefDescription,
    Field(discriminator="type"),
]
REMOTE_REF_DESCRIPTION_ADAPTER = TypeAdapter(RemoteRefDescription)


class RuntimeVariable(MoxportModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    datatype: str | None = None
    preview: str | None = None
    ref: Any

    def query_json(self, expression: str = "value") -> JsonValue | Any:
        return self.ref.query_json(expression)


class NotebookSummary(MoxportModel):
    session: SessionInfo
    cell_count: int


class InstalledPackage(MoxportModel):
    name: str
    version: str


class PackageListResult(MoxportModel):
    packages: list[InstalledPackage] = Field(default_factory=list)


class PackageOperationResult(MoxportModel):
    success: bool
    error: str | None = None


class WorkspaceFile(MoxportModel):
    id: str
    path: str
    name: str
    is_directory: bool = Field(alias="isDirectory")
    is_marimo_file: bool = Field(alias="isMarimoFile")
    children: list[WorkspaceFile] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class WorkspaceFilesResult(MoxportModel):
    root: str
    files: list[WorkspaceFile] = Field(default_factory=list)
    has_more: bool = Field(default=False, alias="hasMore")
    file_count: int = Field(default=0, alias="fileCount")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


WorkspaceFile.model_rebuild()
