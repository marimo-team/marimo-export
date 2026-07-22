import { canonicalJson, parseJsonObject } from "../schema.js";
import type { JsonObject, JsonValue } from "../types.js";
import { MarimoExportError } from "../types.js";

export const EXPORT_PLAN_SCHEMA = "marimo-export.plan.v1" as const;

export type InputBinding =
  | { readonly definition: string; readonly ui?: never }
  | { readonly ui: string; readonly definition?: never };

export type ExportInput = InputBinding & {
  readonly default?: JsonValue;
};

export interface ExportScenarioPlan {
  readonly id: string;
  readonly inputs?: JsonObject;
}

export type ProjectionSource = string | { readonly expression: string };

const BUILTIN_EXPORTERS = [
  "json",
  "text",
  "html",
  "bytes",
  "arrow",
  "parquet",
  "vegalite",
  "png",
  "anywidget",
] as const;

const BUILTIN_EXPORTER_NAMES = new Set<string>(BUILTIN_EXPORTERS);

export type BuiltinExporter = (typeof BUILTIN_EXPORTERS)[number];

export type Exporter =
  | BuiltinExporter
  | { readonly ref: string; readonly version: string; readonly definition?: never }
  | { readonly definition: string; readonly version?: string; readonly ref?: never };

export interface ProjectionFormat {
  readonly exporter?: Exporter;
  readonly options?: JsonObject;
}

export interface ProjectionOutput {
  readonly source: ProjectionSource;
  readonly formats: Readonly<Record<string, ProjectionFormat>>;
}

export interface ExportPlan {
  readonly schema: typeof EXPORT_PLAN_SCHEMA;
  readonly inputs?: Readonly<Record<string, ExportInput>>;
  readonly scenarios?: readonly ExportScenarioPlan[];
  readonly outputs: Readonly<Record<string, ProjectionOutput>>;
}

interface ValidatedInput {
  readonly hasDefault: boolean;
  readonly defaultValue: JsonValue;
}

export function validateExportPlan(input: unknown): ExportPlan {
  const root = parsePlanObject(input);
  exactFields(root, "plan", ["schema", "inputs", "scenarios", "outputs"], ["schema", "outputs"]);
  if (root.schema !== EXPORT_PLAN_SCHEMA) {
    invalidPlan("plan.schema", `plan.schema must be ${JSON.stringify(EXPORT_PLAN_SCHEMA)}.`);
  }

  const inputs = validateInputs(root.inputs);
  validateScenarios(root.scenarios, inputs);
  validateOutputs(root.outputs);

  return root as unknown as ExportPlan;
}

function validateInputs(input: JsonValue | undefined): ReadonlyMap<string, ValidatedInput> {
  const value: JsonObject =
    input === undefined ? Object.freeze({}) : planObject(input, "plan.inputs");
  const inputs = new Map<string, ValidatedInput>();
  const targets = new Set<string>();

  for (const [rawName, rawInput] of Object.entries(value)) {
    const name = nonEmptyString(rawName, "plan.inputs key");
    const path = `plan.inputs.${name}`;
    const item = planObject(rawInput, path);
    exactFields(item, path, ["definition", "ui", "default"]);

    const bindingKeys = Object.keys(item).filter((key) => key !== "default");
    if (bindingKeys.length !== 1 || (bindingKeys[0] !== "definition" && bindingKeys[0] !== "ui")) {
      invalidPlan(path, `${path} must select exactly one of definition or ui.`);
    }
    const bindingKind = bindingKeys[0]!;
    const target = nonEmptyString(item[bindingKind], `${path}.${bindingKind}`);
    if (targets.has(target)) {
      invalidPlan("plan.inputs", "plan.inputs bindings must be unique.");
    }
    targets.add(target);
    inputs.set(name, {
      hasDefault: Object.hasOwn(item, "default"),
      defaultValue: item.default ?? null,
    });
  }

  return inputs;
}

function validateScenarios(
  input: JsonValue | undefined,
  inputs: ReadonlyMap<string, ValidatedInput>,
): void {
  const scenarios: readonly JsonValue[] =
    input === undefined ? [{ id: "default", inputs: {} }] : scenarioArray(input);
  if (scenarios.length === 0) {
    invalidPlan("plan.scenarios", "plan.scenarios must be a non-empty array.");
  }

  const ids = new Set<string>();
  const vectors = new Set<string>();
  for (const [index, rawScenario] of scenarios.entries()) {
    const path = `plan.scenarios[${index}]`;
    const scenario = planObject(rawScenario, path);
    exactFields(scenario, path, ["id", "inputs"], ["id"]);
    const id = nonEmptyString(scenario.id, `${path}.id`);
    if (ids.has(id)) {
      invalidPlan(`${path}.id`, "plan.scenarios ids must be unique.");
    }
    ids.add(id);

    const inputPath = `${path}.inputs`;
    const provided: JsonObject =
      scenario.inputs === undefined ? Object.freeze({}) : planObject(scenario.inputs, inputPath);
    const unknown = Object.keys(provided)
      .filter((name) => !inputs.has(name))
      .sort();
    if (unknown.length > 0) {
      invalidPlan(inputPath, `${inputPath} does not accept: ${unknown.join(", ")}.`);
    }

    const resolved: Record<string, JsonValue> = {};
    const missing: string[] = [];
    for (const [name, plan] of inputs) {
      if (Object.hasOwn(provided, name)) {
        resolved[name] = provided[name]!;
      } else if (plan.hasDefault) {
        resolved[name] = plan.defaultValue;
      } else {
        missing.push(name);
      }
    }
    if (missing.length > 0) {
      invalidPlan(inputPath, `${inputPath} is missing: ${missing.join(", ")}.`);
    }

    const identity = canonicalJson(resolved);
    if (vectors.has(identity)) {
      invalidPlan(inputPath, "plan.scenarios must resolve to unique input vectors.");
    }
    vectors.add(identity);
  }
}

function validateOutputs(input: JsonValue | undefined): void {
  const outputs = planObject(input, "plan.outputs");
  if (Object.keys(outputs).length === 0) {
    invalidPlan("plan.outputs", "plan.outputs must contain at least one output.");
  }

  for (const [rawName, rawOutput] of Object.entries(outputs)) {
    const name = nonEmptyString(rawName, "plan.outputs key");
    const path = `plan.outputs.${name}`;
    const output = planObject(rawOutput, path);
    exactFields(output, path, ["source", "formats"], ["source", "formats"]);
    validateSource(output.source, `${path}.source`);

    const formatsPath = `${path}.formats`;
    const formats = planObject(output.formats, formatsPath);
    if (Object.keys(formats).length === 0) {
      invalidPlan(formatsPath, `${formatsPath} must contain at least one format.`);
    }
    for (const [rawFormat, rawSpec] of Object.entries(formats)) {
      const format = nonEmptyString(rawFormat, `${formatsPath} key`);
      const formatPath = `${formatsPath}.${format}`;
      const spec = planObject(rawSpec, formatPath);
      exactFields(spec, formatPath, ["exporter", "options"]);
      if (Object.hasOwn(spec, "options")) {
        planObject(spec.options, `${formatPath}.options`);
      }
      validateExporter(spec.exporter, `${formatPath}.exporter`, format);
    }
  }
}

function validateSource(input: JsonValue | undefined, path: string): void {
  if (typeof input === "string") {
    nonEmptyString(input, path);
    return;
  }
  const source = planObject(input, path);
  const keys = Object.keys(source);
  if (keys.length !== 1 || keys[0] !== "expression") {
    invalidPlan(path, `${path} must be a definition string or an expression object.`);
  }
  nonEmptyString(source.expression, `${path}.expression`);
}

function validateExporter(input: JsonValue | undefined, path: string, fallbackName: string): void {
  if (input === undefined || typeof input === "string") {
    const name = input === undefined ? fallbackName : nonEmptyString(input, path);
    if (!BUILTIN_EXPORTER_NAMES.has(name)) {
      invalidPlan(path, `${path} must name a built-in exporter or an exporter object.`);
    }
    return;
  }

  const exporter = planObject(input, path);
  const keys = Object.keys(exporter).sort();
  const isRef = keys.length === 2 && keys[0] === "ref" && keys[1] === "version";
  const isDefinition =
    (keys.length === 1 && keys[0] === "definition") ||
    (keys.length === 2 && keys[0] === "definition" && keys[1] === "version");
  if (!isRef && !isDefinition) {
    invalidPlan(path, `${path} must contain ref plus version, or a notebook definition.`);
  }

  if (isRef) {
    const ref = nonEmptyString(exporter.ref, `${path}.ref`);
    nonEmptyString(exporter.version, `${path}.version`);
    const parts = ref.split(":");
    if (parts.length !== 2 || !parts[0]!.trim() || !parts[1]!.trim()) {
      invalidPlan(`${path}.ref`, `${path}.ref must use module:object syntax.`);
    }
    return;
  }

  nonEmptyString(exporter.definition, `${path}.definition`);
  if (Object.hasOwn(exporter, "version")) {
    nonEmptyString(exporter.version, `${path}.version`);
  }
}

function parsePlanObject(input: unknown): JsonObject {
  try {
    return parseJsonObject(input, "plan");
  } catch (error) {
    if (error instanceof MarimoExportError) {
      invalidPlan(errorPath(error.message), error.message, error);
    }
    invalidPlan("plan", "plan must contain JSON-compatible values.", error);
  }
}

function planObject(input: JsonValue | undefined, path: string): JsonObject {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    invalidPlan(path, `${path} must be an object.`);
  }
  return input as JsonObject;
}

function scenarioArray(input: JsonValue): readonly JsonValue[] {
  if (!Array.isArray(input)) {
    invalidPlan("plan.scenarios", "plan.scenarios must be a non-empty array.");
  }
  return input;
}

function exactFields(
  input: JsonObject,
  path: string,
  allowed: readonly string[],
  required: readonly string[] = [],
): void {
  const extras = Object.keys(input)
    .filter((field) => !allowed.includes(field))
    .sort();
  if (extras.length > 0) {
    invalidPlan(path, `${path} does not accept: ${extras.join(", ")}.`);
  }
  for (const field of required) {
    if (!Object.hasOwn(input, field)) {
      invalidPlan(`${path}.${field}`, `${path}.${field} is required.`);
    }
  }
}

function nonEmptyString(input: JsonValue | undefined, path: string): string {
  if (typeof input !== "string" || !input.trim()) {
    invalidPlan(path, `${path} must be a non-empty string.`);
  }
  return input;
}

function errorPath(message: string): string {
  const markers = [" must ", " contains ", " does not "];
  const positions = markers
    .map((marker) => message.indexOf(marker))
    .filter((position) => position > 0);
  const end = positions.length === 0 ? -1 : Math.min(...positions);
  const path = end === -1 ? "plan" : message.slice(0, end);
  return path.startsWith("plan") ? path : "plan";
}

function invalidPlan(path: string, message: string, cause?: unknown): never {
  throw new MarimoExportError("invalid_plan", message, {
    ...(cause === undefined ? {} : { cause }),
    details: { path },
  });
}
