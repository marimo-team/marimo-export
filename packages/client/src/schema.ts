import type {
  ExportKey,
  ExportRef,
  JsonObject,
  JsonValue,
  PayloadKey,
  PayloadRef,
} from "./types.js";
import { MarimoExportError } from "./types.js";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const PAYLOAD_PREFIX = "marimo-export/payloads/sha256/";

export interface ManifestProjection {
  readonly format_id: string;
  readonly media_type: string;
  readonly metadata: JsonObject;
  readonly payload: PayloadRef;
}

export type ManifestFormats = Readonly<Record<string, ManifestProjection>>;
export type ManifestOutputs = Readonly<Record<string, ManifestFormats>>;

export interface ManifestScenario {
  readonly id: string;
  readonly inputs: JsonObject;
  readonly outputs: ManifestOutputs;
}

export interface ExportManifest {
  readonly schema: "marimo-export.index.v1";
  readonly notebook: {
    readonly name: string;
    readonly source_sha256: string;
  };
  readonly plan_sha256: string;
  readonly producer: {
    readonly marimo_version: string;
    readonly marimo_export_version: string;
  };
  readonly scenarios: readonly ManifestScenario[];
}

export function parseExportManifest(input: unknown): ExportManifest {
  const root = record(input, "index", [
    "schema",
    "notebook",
    "plan_sha256",
    "producer",
    "scenarios",
  ]);
  const schema = literal(root.schema, "marimo-export.index.v1", "index.schema");
  const notebook = record(root.notebook, "index.notebook", ["name", "source_sha256"]);
  const producer = record(root.producer, "index.producer", [
    "marimo_version",
    "marimo_export_version",
  ]);
  const scenarios = array(root.scenarios, "index.scenarios").map(parseScenario);
  if (scenarios.length === 0) fail("index.scenarios must contain at least one scenario.");

  unique(
    scenarios.map((scenario) => scenario.id),
    "scenario id",
  );
  unique(
    scenarios.map((scenario) => canonicalJson(scenario.inputs)),
    "scenario input vector",
  );
  validatePayloadReferences(scenarios);

  return Object.freeze({
    schema,
    notebook: Object.freeze({
      name: nonEmptyString(notebook.name, "index.notebook.name"),
      source_sha256: sha256(notebook.source_sha256, "index.notebook.source_sha256"),
    }),
    plan_sha256: sha256(root.plan_sha256, "index.plan_sha256"),
    producer: Object.freeze({
      marimo_version: nonEmptyString(producer.marimo_version, "index.producer.marimo_version"),
      marimo_export_version: nonEmptyString(
        producer.marimo_export_version,
        "index.producer.marimo_export_version",
      ),
    }),
    scenarios: Object.freeze(scenarios),
  });
}

export function parseExportRef(input: unknown, path = "ref"): ExportRef {
  try {
    return parseRefValue(input, path);
  } catch (error) {
    if (error instanceof MarimoExportError) {
      throw new MarimoExportError("invalid_ref", error.message, { cause: error });
    }
    throw error;
  }
}

function parseRefValue(input: unknown, path: string): ExportRef {
  const value = record(input, path, ["key", "sha256", "size"]);
  const digest = sha256(value.sha256, `${path}.sha256`);
  const key = assertPortablePath(nonEmptyString(value.key, `${path}.key`), `${path}.key`);
  const expectedKey = `marimo-export/indexes/${digest}.json`;
  if (key !== expectedKey) fail(`${path}.key must be ${JSON.stringify(expectedKey)}.`);
  return Object.freeze({
    key: key as ExportKey,
    sha256: digest,
    size: positiveInteger(value.size, `${path}.size`),
  });
}

export function parseJsonObject(input: unknown, path = "value"): JsonObject {
  return jsonObject(input, path);
}

export function assertPortablePath(input: string, path = "path"): string {
  if (
    input.startsWith("/") ||
    input.includes("\\") ||
    input.includes("\0") ||
    input.split("/").some((segment) => segment === "" || segment === "." || segment === "..")
  ) {
    fail(`${path} must be a portable relative path.`);
  }
  return input;
}

export function canonicalJson(value: JsonValue): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    validateJsonNumber(value, "Canonical JSON value");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const object = value as JsonObject;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key]!)}`)
    .join(",")}}`;
}

function parseScenario(input: unknown, index: number): ManifestScenario {
  const path = `index.scenarios[${index}]`;
  const value = record(input, path, ["id", "inputs", "outputs"]);
  const outputs = parseOutputs(value.outputs, `${path}.outputs`);
  if (Object.keys(outputs).length === 0) fail(`${path}.outputs must contain at least one output.`);
  return Object.freeze({
    id: nonEmptyString(value.id, `${path}.id`),
    inputs: jsonObject(value.inputs, `${path}.inputs`),
    outputs,
  });
}

function parseOutputs(input: unknown, path: string): ManifestOutputs {
  const value = openRecord(input, path);
  const outputs = Object.fromEntries(
    Object.entries(value).map(([name, formats]) => {
      const outputName = nonEmptyKey(name, `${path} key`);
      const parsed = parseFormats(formats, `${path}.${outputName}`);
      if (Object.keys(parsed).length === 0) {
        fail(`${path}.${outputName} must contain at least one format.`);
      }
      return [outputName, parsed];
    }),
  );
  return Object.freeze(outputs);
}

function parseFormats(input: unknown, path: string): ManifestFormats {
  const value = openRecord(input, path);
  const formats = Object.fromEntries(
    Object.entries(value).map(([format, projection]) => {
      const formatName = nonEmptyKey(format, `${path} key`);
      return [formatName, parseProjection(projection, `${path}.${formatName}`)];
    }),
  );
  return Object.freeze(formats);
}

function parseProjection(input: unknown, path: string): ManifestProjection {
  const value = record(input, path, ["format_id", "media_type", "metadata", "payload"]);
  const payload = record(value.payload, `${path}.payload`, ["key", "sha256", "size"]);
  const digest = sha256(payload.sha256, `${path}.payload.sha256`);
  const key = assertPortablePath(
    nonEmptyString(payload.key, `${path}.payload.key`),
    `${path}.payload.key`,
  );
  const expectedKey = `${PAYLOAD_PREFIX}${digest}`;
  if (key !== expectedKey) {
    fail(`${path}.payload.key must be ${JSON.stringify(expectedKey)}.`);
  }
  return Object.freeze({
    format_id: nonEmptyString(value.format_id, `${path}.format_id`),
    media_type: nonEmptyString(value.media_type, `${path}.media_type`),
    metadata: jsonObject(value.metadata, `${path}.metadata`),
    payload: Object.freeze({
      key: key as PayloadKey,
      sha256: digest,
      size: nonNegativeInteger(payload.size, `${path}.payload.size`),
    }),
  });
}

function validatePayloadReferences(scenarios: readonly ManifestScenario[]): void {
  const sizes = new Map<string, number>();
  for (const scenario of scenarios) {
    for (const formats of Object.values(scenario.outputs)) {
      for (const projection of Object.values(formats)) {
        const existing = sizes.get(projection.payload.key);
        if (existing !== undefined && existing !== projection.payload.size) {
          fail(`Payload ${JSON.stringify(projection.payload.key)} has conflicting declared sizes.`);
        }
        sizes.set(projection.payload.key, projection.payload.size);
      }
    }
  }
}

function record(input: unknown, path: string, fields: readonly string[]): Record<string, unknown> {
  const value = openRecord(input, path);
  const extras = Object.keys(value).filter((key) => !fields.includes(key));
  if (extras.length > 0) fail(`${path} contains unexpected fields: ${extras.join(", ")}.`);
  for (const field of fields) {
    if (!(field in value)) fail(`${path}.${field} is required.`);
  }
  return value;
}

function openRecord(input: unknown, path: string): Record<string, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    fail(`${path} must be an object.`);
  }
  return input as Record<string, unknown>;
}

function jsonObject(input: unknown, path: string): JsonObject {
  const value = openRecord(input, path);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, jsonValue(item, `${path}.${key}`)]),
    ),
  );
}

function jsonValue(input: unknown, path: string): JsonValue {
  if (input === null || typeof input === "string" || typeof input === "boolean") return input;
  if (typeof input === "number") {
    validateJsonNumber(input, path);
    return input;
  }
  if (Array.isArray(input)) {
    return Object.freeze(input.map((item, index) => jsonValue(item, `${path}[${index}]`)));
  }
  return jsonObject(input, path);
}

function array(input: unknown, path: string): unknown[] {
  if (!Array.isArray(input)) fail(`${path} must be an array.`);
  return input;
}

function nonEmptyString(input: unknown, path: string): string {
  if (typeof input !== "string" || input.length === 0) fail(`${path} must be a non-empty string.`);
  return input;
}

function nonEmptyKey(input: string, path: string): string {
  if (input.length === 0) fail(`${path} must be non-empty.`);
  return input;
}

function sha256(input: unknown, path: string): string {
  const value = nonEmptyString(input, path);
  if (!SHA256_PATTERN.test(value)) fail(`${path} must be a lowercase SHA-256 digest.`);
  return value;
}

function nonNegativeInteger(input: unknown, path: string): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 0) {
    fail(`${path} must be a non-negative safe integer.`);
  }
  return input;
}

function validateJsonNumber(value: number, path: string): void {
  if (!Number.isFinite(value)) fail(`${path} must be a finite JSON number.`);
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    fail(`${path} must be a safe JSON integer.`);
  }
}

function positiveInteger(input: unknown, path: string): number {
  const value = nonNegativeInteger(input, path);
  if (value === 0) fail(`${path} must be positive.`);
  return value;
}

function literal<T extends string>(input: unknown, expected: T, path: string): T {
  if (input !== expected) fail(`${path} must be ${JSON.stringify(expected)}.`);
  return expected;
}

function unique(values: readonly string[], label: string): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) fail(`index contains duplicate ${label} ${JSON.stringify(value)}.`);
    seen.add(value);
  }
}

function fail(message: string): never {
  throw new MarimoExportError("invalid_index", message);
}
