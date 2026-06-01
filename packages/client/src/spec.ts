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
  | { cell: string | number | JsonObject; on_error?: "raise" | "record" }
  | { snapshot?: JsonObject; notebook?: JsonObject }
  | { report: JsonObject }
  | ({ type: string } & JsonObject);

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

export type FormatInput =
  | BuiltinFormatName
  | string
  | { format: string; options?: JsonObject }
  | Record<string, JsonObject | ExplicitFormat | null>;

export interface ValueSpec {
  source: SourceSpec;
  formats: Record<string, ExplicitFormat | JsonObject | null> | FormatInput[];
}
