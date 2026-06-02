import * as v from "valibot";

import type { JsonObject, JsonValue } from "./types.js";

const SPEC_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_-]*$/;
const SOURCE_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const STATE_KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/;
declare const EXPORT_SPEC_BRAND: unique symbol;
declare const FORMAT_MAP_BRAND: unique symbol;

export const builtinFormatNames = [
  "json",
  "text",
  "html",
  "arrow",
  "parquet",
  "vegalite",
  "png",
  "anywidget",
  "display",
  "markdown",
] as const;

export type BuiltinFormatName = (typeof builtinFormatNames)[number];
export type SpecName = string;

type JsonValueSchema = v.GenericSchema<JsonValue>;

const jsonValueSchema: JsonValueSchema = v.lazy(() =>
  v.union([
    v.string(),
    v.pipe(
      v.number(),
      v.check((value: number) => Number.isFinite(value), "JSON numbers must be finite."),
    ),
    v.boolean(),
    v.null_(),
    v.array(jsonValueSchema),
    v.record(v.string(), jsonValueSchema),
  ]),
);

const jsonObjectSchema = v.record(v.string(), jsonValueSchema);
const builtinFormatOptionsSchema = v.pipe(
  jsonObjectSchema,
  v.check(
    (value) => !("export" in value) && !("options" in value),
    "Built-in format options cannot use reserved export or options keys.",
  ),
);

const specNameSchema = v.pipe(
  v.string(),
  v.regex(SPEC_NAME_PATTERN, "Spec names must start with a letter or underscore."),
);

const sourceNameSchema = v.pipe(
  v.string(),
  v.regex(SOURCE_NAME_PATTERN, "Source names must be valid Python identifiers."),
);

const stateKeySchema = v.pipe(
  v.string(),
  v.regex(STATE_KEY_PATTERN, "State keys must be Python names or dotted Python paths."),
);

const codeStateValueSchema = v.strictObject({
  code: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "State code cannot be empty."),
  ),
});

const jsonScenarioValueSchema = v.pipe(
  jsonValueSchema,
  v.check(
    (value) => !isPlainRecord(value) || !("code" in value),
    'Scenario code values use { code: "..." }.',
  ),
  v.check((value) => !isCodeStateMarkerObject(value), 'Scenario code values use { code: "..." }.'),
);

const scenarioValueSchema = v.union([codeStateValueSchema, jsonScenarioValueSchema]);

export const scenarioSpecSchema = v.strictObject({
  id: v.optional(specNameSchema),
  state: v.optional(v.record(stateKeySchema, scenarioValueSchema)),
});

export const provenanceSpecSchema = v.strictObject({
  source: v.optional(v.picklist(["none", "hash", "source"])),
  spec: v.optional(v.picklist(["none", "hash", "embed"])),
});

const selectorByIdSchema = v.strictObject({
  id: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Cell id cannot be empty."),
  ),
});

const selectorByNameSchema = v.strictObject({
  name: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Cell name cannot be empty."),
  ),
});

const selectorByIndexSchema = v.strictObject({
  index: v.pipe(
    v.number(),
    v.check(
      (value) => Number.isInteger(value) && value >= 0,
      "Cell index must be a non-negative integer.",
    ),
  ),
});

export const cellSelectorSchema = v.union([
  selectorByIdSchema,
  selectorByNameSchema,
  selectorByIndexSchema,
]);

const cellReferenceSchema = v.union([
  v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Cell name cannot be empty."),
  ),
  v.pipe(
    v.number(),
    v.check(
      (value) => Number.isInteger(value) && value >= 0,
      "Cell index must be a non-negative integer.",
    ),
  ),
  cellSelectorSchema,
]);

export const definitionSourceSchema = v.strictObject({
  type: v.literal("definition"),
  name: sourceNameSchema,
});

export const expressionSourceSchema = v.strictObject({
  type: v.literal("expression"),
  expression: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Source expression cannot be empty."),
  ),
});

export const cellOutputSourceSchema = v.strictObject({
  type: v.literal("cell_output"),
  cell: cellReferenceSchema,
  on_error: v.optional(v.picklist(["raise", "record"])),
});

const notebookSnapshotOptionsSchema = {
  include_source: v.optional(v.boolean()),
  include_empty_outputs: v.optional(v.boolean()),
  include_internal_cells: v.optional(v.boolean()),
  on_error: v.optional(v.picklist(["raise", "record"])),
};

export const notebookSnapshotSourceSchema = v.strictObject({
  type: v.literal("notebook_snapshot"),
  ...notebookSnapshotOptionsSchema,
});

const notebookSnapshotShorthandSchema = v.union([
  v.strictObject({
    snapshot: v.literal(true),
    ...notebookSnapshotOptionsSchema,
  }),
  v.strictObject({
    notebook: v.literal(true),
    ...notebookSnapshotOptionsSchema,
  }),
]);

const reportCellByCellSchema = v.strictObject({
  cell: cellReferenceSchema,
  label: v.optional(v.nullable(v.string())),
  order: v.optional(
    v.nullable(
      v.pipe(
        v.number(),
        v.check((value: number) => Number.isInteger(value), "Report order must be an integer."),
      ),
    ),
  ),
});

const reportCellByIdSchema = v.strictObject({
  id: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Report cell id cannot be empty."),
  ),
  label: v.optional(v.nullable(v.string())),
  order: v.optional(
    v.nullable(
      v.pipe(
        v.number(),
        v.check((value: number) => Number.isInteger(value), "Report order must be an integer."),
      ),
    ),
  ),
});

const reportCellByNameSchema = v.strictObject({
  name: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Report cell name cannot be empty."),
  ),
  label: v.optional(v.nullable(v.string())),
  order: v.optional(
    v.nullable(
      v.pipe(
        v.number(),
        v.check((value: number) => Number.isInteger(value), "Report order must be an integer."),
      ),
    ),
  ),
});

const reportCellByIndexSchema = v.strictObject({
  index: v.pipe(
    v.number(),
    v.check(
      (value) => Number.isInteger(value) && value >= 0,
      "Report cell index must be a non-negative integer.",
    ),
  ),
  label: v.optional(v.nullable(v.string())),
  order: v.optional(
    v.nullable(
      v.pipe(
        v.number(),
        v.check((value: number) => Number.isInteger(value), "Report order must be an integer."),
      ),
    ),
  ),
});

export const reportCellInputSchema = v.union([
  reportCellByCellSchema,
  reportCellByIdSchema,
  reportCellByNameSchema,
  reportCellByIndexSchema,
]);

const reportCellsSchema = v.tupleWithRest([reportCellInputSchema], reportCellInputSchema);

const reportSourceInputSchema = v.strictObject({
  cells: reportCellsSchema,
  include_source: v.optional(v.boolean()),
  on_error: v.optional(v.picklist(["raise", "record"])),
});

export const reportSourceSchema = v.strictObject({
  type: v.literal("report"),
  cells: reportCellsSchema,
  include_source: v.optional(v.boolean()),
  on_error: v.optional(v.picklist(["raise", "record"])),
});

const definitionShorthandSchema = v.strictObject({
  def: sourceNameSchema,
});

const expressionShorthandSchema = v.strictObject({
  expr: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Source expression cannot be empty."),
  ),
});

const cellOutputShorthandSchema = v.strictObject({
  cell: cellReferenceSchema,
  on_error: v.optional(v.picklist(["raise", "record"])),
});

const reportShorthandSchema = v.strictObject({
  report: reportSourceInputSchema,
});

export const sourceSpecSchema = v.union([
  v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Source expression cannot be empty."),
  ),
  definitionShorthandSchema,
  expressionShorthandSchema,
  cellOutputShorthandSchema,
  notebookSnapshotShorthandSchema,
  reportShorthandSchema,
  definitionSourceSchema,
  expressionSourceSchema,
  cellOutputSourceSchema,
  notebookSnapshotSourceSchema,
  reportSourceSchema,
]);

export const refExportSchema = v.strictObject({
  type: v.literal("ref"),
  ref: v.pipe(
    v.string(),
    v.check((value) => /^[^:]+:[^:]+$/.test(value), "Export ref must use module:object syntax."),
  ),
});

export const codeExportSchema = v.strictObject({
  type: v.literal("code"),
  code: v.pipe(
    v.string(),
    v.check((value) => value.trim().length > 0, "Export code cannot be empty."),
  ),
});

export const exportCallableSchema = v.union([refExportSchema, codeExportSchema]);

export const explicitFormatSchema = v.strictObject({
  export: exportCallableSchema,
  options: v.optional(jsonObjectSchema),
});

const explicitNamedFormatSchema = v.strictObject({
  format: specNameSchema,
  export: exportCallableSchema,
  options: v.optional(jsonObjectSchema),
});

const builtinFormatConfigSchema = v.union([
  explicitFormatSchema,
  builtinFormatOptionsSchema,
  v.null_(),
]);

const jsonFormatShorthandSchema = v.strictObject({ json: builtinFormatConfigSchema });
const textFormatShorthandSchema = v.strictObject({ text: builtinFormatConfigSchema });
const htmlFormatShorthandSchema = v.strictObject({ html: builtinFormatConfigSchema });
const arrowFormatShorthandSchema = v.strictObject({ arrow: builtinFormatConfigSchema });
const parquetFormatShorthandSchema = v.strictObject({ parquet: builtinFormatConfigSchema });
const vegaliteFormatShorthandSchema = v.strictObject({ vegalite: builtinFormatConfigSchema });
const pngFormatShorthandSchema = v.strictObject({ png: builtinFormatConfigSchema });
const anywidgetFormatShorthandSchema = v.strictObject({ anywidget: builtinFormatConfigSchema });
const displayFormatShorthandSchema = v.strictObject({ display: builtinFormatConfigSchema });
const markdownFormatShorthandSchema = v.strictObject({ markdown: builtinFormatConfigSchema });

const namedBuiltinFormatSchema = v.union([
  jsonFormatShorthandSchema,
  textFormatShorthandSchema,
  htmlFormatShorthandSchema,
  arrowFormatShorthandSchema,
  parquetFormatShorthandSchema,
  vegaliteFormatShorthandSchema,
  pngFormatShorthandSchema,
  anywidgetFormatShorthandSchema,
  displayFormatShorthandSchema,
  markdownFormatShorthandSchema,
]);

type BuiltinFormatConfigOutput = v.InferOutput<typeof builtinFormatConfigSchema>;
type FormatMapOutput = Partial<Record<BuiltinFormatName, BuiltinFormatConfigOutput>>;

const formatMapSchema = v.custom<FormatMapOutput>(
  isFormatMap,
  "Format maps need built-in format names. Custom formats use {format, export, options}.",
);

export const formatInputSchema = v.union([
  v.picklist(builtinFormatNames),
  v.strictObject({
    format: v.picklist(builtinFormatNames),
    options: v.optional(jsonObjectSchema),
  }),
  explicitNamedFormatSchema,
  namedBuiltinFormatSchema,
]);

export const valueSpecSchema = v.strictObject({
  source: sourceSpecSchema,
  formats: v.pipe(
    v.union([formatMapSchema, v.tupleWithRest([formatInputSchema], formatInputSchema)]),
    v.check((value) => Object.keys(value).length > 0, "Values need at least one format."),
  ),
});

export const exportSpecSchema = v.strictObject({
  scenarios: v.optional(
    v.pipe(
      v.array(scenarioSpecSchema),
      v.check((value) => uniqueScenarioIds(value), "Scenario ids must be unique."),
    ),
  ),
  provenance: v.optional(provenanceSpecSchema),
  values: v.pipe(
    v.record(specNameSchema, valueSpecSchema),
    v.check((value) => Object.keys(value).length > 0, "Export specs need at least one value."),
  ),
});

export type ScenarioSpec = v.InferOutput<typeof scenarioSpecSchema>;
export type ScenarioValue = v.InferOutput<typeof scenarioValueSchema>;
export type ProvenanceSpec = v.InferOutput<typeof provenanceSpecSchema>;
export type CellSelector = v.InferOutput<typeof cellSelectorSchema>;
export type DefinitionSource = v.InferOutput<typeof definitionSourceSchema>;
export type ExpressionSource = v.InferOutput<typeof expressionSourceSchema>;
export type CellOutputSource = v.InferOutput<typeof cellOutputSourceSchema>;
export type NotebookSnapshotSource = v.InferOutput<typeof notebookSnapshotSourceSchema>;
export type NotebookSnapshotOptions = Omit<NotebookSnapshotSource, "type">;
export type NotebookSnapshotShorthand = v.InferOutput<typeof notebookSnapshotShorthandSchema>;
export type ReportCellInput = v.InferOutput<typeof reportCellInputSchema>;
export type ReportSourceInput = v.InferOutput<typeof reportSourceInputSchema>;
export type ReportSource = v.InferOutput<typeof reportSourceSchema>;
export type SourceSpec = v.InferOutput<typeof sourceSpecSchema>;
export type RefExport = v.InferOutput<typeof refExportSchema>;
export type CodeExport = v.InferOutput<typeof codeExportSchema>;
export type ExportCallable = v.InferOutput<typeof exportCallableSchema>;
export type ExplicitFormat = v.InferOutput<typeof explicitFormatSchema>;
export type ExplicitNamedFormat = v.InferOutput<typeof explicitNamedFormatSchema>;
export type BuiltinFormatConfig = v.InferOutput<typeof builtinFormatConfigSchema>;
export type BuiltinFormatMap = Partial<Record<BuiltinFormatName, BuiltinFormatConfig>>;
export type ExplicitFormatMap = Record<string, ExplicitFormat>;
export type FormatMap = FormatMapOutput & { readonly [FORMAT_MAP_BRAND]: true };
export type NamedBuiltinFormat = v.InferOutput<typeof namedBuiltinFormatSchema>;
export type FormatInput = v.InferOutput<typeof formatInputSchema>;
export type ValueSpec = v.InferOutput<typeof valueSpecSchema>;
export type ExportSpec = v.InferOutput<typeof exportSpecSchema> & {
  readonly [EXPORT_SPEC_BRAND]: true;
};

type NonEmptyReadonlyArray<T> = readonly [T, ...T[]];

export type CellSelectorInput =
  | string
  | number
  | { readonly id: string }
  | { readonly name: string }
  | { readonly index: number };
export type SourceErrorMode = "raise" | "record";
export type NotebookSnapshotInput = {
  readonly include_source?: boolean | undefined;
  readonly include_empty_outputs?: boolean | undefined;
  readonly include_internal_cells?: boolean | undefined;
  readonly on_error?: SourceErrorMode | undefined;
};
export type ReportCellInputSpec =
  | {
      readonly cell: CellSelectorInput;
      readonly label?: string | null | undefined;
      readonly order?: number | null | undefined;
    }
  | {
      readonly id: string;
      readonly label?: string | null | undefined;
      readonly order?: number | null | undefined;
    }
  | {
      readonly name: string;
      readonly label?: string | null | undefined;
      readonly order?: number | null | undefined;
    }
  | {
      readonly index: number;
      readonly label?: string | null | undefined;
      readonly order?: number | null | undefined;
    };
export type ReportSourceInputSpec = {
  readonly cells: NonEmptyReadonlyArray<ReportCellInputSpec>;
  readonly include_source?: boolean | undefined;
  readonly on_error?: SourceErrorMode | undefined;
};
export type SourceInput =
  | string
  | { readonly def: string }
  | { readonly expr: string }
  | { readonly cell: CellSelectorInput; readonly on_error?: SourceErrorMode | undefined }
  | ({ readonly snapshot: true } & NotebookSnapshotInput)
  | ({ readonly notebook: true } & NotebookSnapshotInput)
  | { readonly report: ReportSourceInputSpec }
  | { readonly type: "definition"; readonly name: string }
  | { readonly type: "expression"; readonly expression: string }
  | {
      readonly type: "cell_output";
      readonly cell: CellSelectorInput;
      readonly on_error?: SourceErrorMode | undefined;
    }
  | ({ readonly type: "notebook_snapshot" } & NotebookSnapshotInput)
  | ({ readonly type: "report" } & ReportSourceInputSpec);
export type ExportCallableInput =
  | { readonly type: "ref"; readonly ref: string }
  | { readonly type: "code"; readonly code: string };
export type ExplicitFormatInput = {
  readonly export: ExportCallableInput;
  readonly options?: JsonObject | undefined;
};
export type ExplicitNamedFormatInput = {
  readonly format: string;
  readonly export: ExportCallableInput;
  readonly options?: JsonObject | undefined;
};
export type BuiltinFormatOptionsInput = JsonObject & {
  readonly export?: never;
  readonly options?: never;
};
export type BuiltinFormatConfigInput = BuiltinFormatOptionsInput | ExplicitFormatInput | null;
export type BuiltinFormatObjectInput = {
  readonly [Name in BuiltinFormatName]: {
    readonly [Key in Name]: BuiltinFormatConfigInput;
  };
}[BuiltinFormatName];
export type FormatListItemInput =
  | BuiltinFormatName
  | { readonly format: BuiltinFormatName; readonly options?: JsonObject | undefined }
  | ExplicitNamedFormatInput
  | BuiltinFormatObjectInput;
export type FormatListInput = NonEmptyReadonlyArray<FormatListItemInput>;
export type FormatMapInput = Readonly<Partial<Record<BuiltinFormatName, BuiltinFormatConfigInput>>>;

export type ExportSpecInput = {
  readonly scenarios?:
    | readonly {
        readonly id?: string | undefined;
        readonly state?:
          | Readonly<Record<string, JsonValue | { readonly code: string }>>
          | undefined;
      }[]
    | undefined;
  readonly provenance?:
    | {
        readonly source?: "none" | "hash" | "source" | undefined;
        readonly spec?: "none" | "hash" | "embed" | undefined;
      }
    | undefined;
  readonly values: Readonly<
    Record<
      string,
      {
        readonly source: SourceInput;
        readonly formats: FormatListInput | FormatMapInput;
      }
    >
  >;
};
export interface ExportSpecIssue {
  path: string;
  message: string;
}

export type ExportSpecParseResult =
  | {
      success: true;
      spec: ExportSpec;
      issues?: never;
    }
  | {
      success: false;
      spec?: never;
      issues: ExportSpecIssue[];
    };

export function parseExportSpec(input: unknown): ExportSpec {
  return v.parse(exportSpecSchema, input) as ExportSpec;
}

export function safeParseExportSpec(input: unknown): ExportSpecParseResult {
  const result = v.safeParse(exportSpecSchema, input);
  return result.success
    ? { success: true, spec: result.output as ExportSpec }
    : { success: false, issues: result.issues.map(formatIssue) };
}

function formatIssue(issue: v.InferIssue<typeof exportSpecSchema>): ExportSpecIssue {
  return {
    path: formatIssuePath(issue.path),
    message: issue.message,
  };
}

function formatIssuePath(path: v.InferIssue<typeof exportSpecSchema>["path"]): string {
  if (!path) {
    return "";
  }
  return path.map((item) => String(item.key)).join(".");
}

function uniqueScenarioIds(scenarios: readonly ScenarioSpec[]): boolean {
  const ids = scenarios.map((scenario) => scenario.id ?? "default");
  return new Set(ids).size === ids.length;
}

function isCodeStateMarkerObject(value: unknown): boolean {
  return isPlainRecord(value) && value.type === "code" && !("code" in value);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFormatMap(value: unknown): value is FormatMapOutput {
  if (!isPlainRecord(value) || Object.keys(value).length === 0) {
    return false;
  }

  for (const [name, config] of Object.entries(value)) {
    if (!SPEC_NAME_PATTERN.test(name)) {
      return false;
    }

    if (!isBuiltinFormatName(name)) {
      return false;
    }

    if (!v.safeParse(builtinFormatConfigSchema, config).success) {
      return false;
    }
  }

  return true;
}

function isBuiltinFormatName(name: string): name is BuiltinFormatName {
  return (builtinFormatNames as readonly string[]).includes(name);
}
