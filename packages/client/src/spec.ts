import type { JsonObject, JsonValue } from "./types";

export type SpecName = string;
export type BuiltinFormatName =
  | "json"
  | "text"
  | "html"
  | "arrow"
  | "parquet"
  | "vegalite"
  | "png"
  | "anywidget"
  | "display"
  | "display_json"
  | "markdown";

export interface ExportSpec {
  scenarios?: ScenarioSpec[];
  provenance?: ProvenanceSpec;
  values: Record<SpecName, ValueSpec>;
}

export interface ScenarioSpec {
  id?: SpecName;
  state?: Record<string, ScenarioValue>;
}

export type ScenarioValue = JsonValue | { code: string };

export interface ProvenanceSpec {
  source?: "none" | "hash" | "source";
  spec?: "none" | "hash" | "embed";
}

export type SourceSpec =
  | string
  | { def: string }
  | { expr: string }
  | { cell: string | number | CellSelector; on_error?: "raise" | "record" }
  | NotebookSnapshotShorthand
  | { report: ReportSourceInput }
  | DefinitionSource
  | ExpressionSource
  | CellOutputSource
  | NotebookSnapshotSource
  | ReportSource;

export type CellSelector =
  | { id: string; name?: never; index?: never }
  | { name: string; id?: never; index?: never }
  | { index: number; id?: never; name?: never };

export interface DefinitionSource {
  type: "definition";
  name: string;
}

export interface ExpressionSource {
  type: "expression";
  expression: string;
}

export interface CellOutputSource {
  type: "cell_output";
  cell: string | number | CellSelector;
  on_error?: "raise" | "record";
}

export interface NotebookSnapshotOptions {
  include_source?: boolean;
  include_empty_outputs?: boolean;
  include_internal_cells?: boolean;
  on_error?: "raise" | "record";
}

export type NotebookSnapshotShorthand = NotebookSnapshotOptions &
  ({ snapshot: JsonValue; notebook?: JsonValue } | { notebook: JsonValue; snapshot?: JsonValue });

export type NotebookSnapshotSource = NotebookSnapshotOptions & {
  type: "notebook_snapshot";
};

export type ReportCellInput = {
  label?: string | null;
  order?: number | null;
} & (
  | { cell: string | number | CellSelector; id?: never; name?: never; index?: never }
  | { id: string; cell?: never; name?: never; index?: never }
  | { name: string; cell?: never; id?: never; index?: never }
  | { index: number; cell?: never; id?: never; name?: never }
);

export interface ReportSourceInput {
  cells: readonly [ReportCellInput, ...ReportCellInput[]];
  include_source?: boolean;
  on_error?: "raise" | "record";
}

export type ReportSource = ReportSourceInput & {
  type: "report";
};

export interface RefExport {
  type: "ref";
  ref: string;
}

export interface CodeExport {
  type: "code";
  code: string;
}

export type ExportCallable = RefExport | CodeExport;

export interface ExplicitFormat {
  export: ExportCallable;
  options?: JsonObject;
}

export type BuiltinFormatConfig = ExplicitFormat | JsonObject | null;

export type BuiltinFormatMap = Partial<Record<BuiltinFormatName, BuiltinFormatConfig>>;

export type ExplicitFormatMap = Record<string, ExplicitFormat>;

export type NamedBuiltinFormat = {
  [Name in BuiltinFormatName]: Record<Name, BuiltinFormatConfig> &
    Partial<Record<Exclude<BuiltinFormatName, Name>, never>>;
}[BuiltinFormatName];

export type FormatInput =
  | BuiltinFormatName
  | { format: BuiltinFormatName; options?: JsonObject }
  | NamedBuiltinFormat;

export interface ValueSpec {
  source: SourceSpec;
  formats: BuiltinFormatMap | ExplicitFormatMap | FormatInput[];
}
