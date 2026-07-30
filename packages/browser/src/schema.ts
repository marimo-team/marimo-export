import { parseMediaType } from "./media-type.js";
import type {
  ArrowDescriptor,
  AssetDescriptor,
  BlobAssetDescriptor,
  JsonObject,
  JsonValue,
  NotebookProvenance,
  NumpyDescriptor,
  OutputDescriptor,
  ProducerProvenance,
  Provenance,
  ScalarDescriptor,
  ScalarValue,
} from "./types.js";
import { NotebookExportError, freezeJsonObject, freezeJsonValue } from "./types.js";

const encoder = new TextEncoder();
const SHA256 = /^[0-9a-f]{64}$/u;
const BIGINT = /^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/u;
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const MAX_EXPORT_NAME_BYTES = 255;
const MAX_PROVENANCE_BYTES = 2_048;
const MAX_ASSET_SIZE = 2_147_483_647;
const MAX_METADATA_BYTES = 256 * 1024;
const MAX_JSON_DEPTH = 256;
const WINDOWS_RESERVED = /[<>:"/\\|?*]/u;
const WINDOWS_DEVICE = /^(?:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$/iu;

export interface ParsedState {
  readonly fingerprint: string;
  readonly inputs: JsonObject;
  readonly outputs: Readonly<Record<string, OutputDescriptor>>;
}

export interface ParsedExportIndex {
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
  readonly states: Readonly<Record<string, ParsedState>>;
}

export function parseExportIndex(input: unknown): ParsedExportIndex {
  try {
    const root = strictRecord(input, "export", [
      "inputs",
      "notebook",
      "outputs",
      "producer",
      "schema",
      "states",
    ]);
    literal(root.schema, "marimo-export.export.v1", "export.schema");
    const notebook = parseNotebook(root.notebook);
    const producer = parseProducer(root.producer);
    const inputs = nameArray(root.inputs, "export.inputs", false);
    const outputs = nameArray(root.outputs, "export.outputs", true);
    const inputSet = new Set(inputs);
    const outputSet = new Set(outputs);
    const statesRecord = record(root.states, "export.states");
    if (Object.keys(statesRecord).length === 0) fail("export.states must not be empty");

    const states = Object.freeze(
      Object.fromEntries(
        Object.entries(statesRecord).map(([name, value]) => {
          const stateName = exportName(name, "export.states key");
          const path = `export.states[${JSON.stringify(stateName)}]`;
          const state = strictRecord(value, path, ["fingerprint", "inputs", "outputs"]);
          const stateInputs = portableJsonObject(state.inputs, `${path}.inputs`);
          requireExactKeys(stateInputs, inputSet, `${path}.inputs`);
          const outputRecord = record(state.outputs, `${path}.outputs`);
          requireExactKeys(outputRecord, outputSet, `${path}.outputs`);
          const parsedOutputs = Object.freeze(
            Object.fromEntries(
              Object.entries(outputRecord).map(([outputName, descriptor]) => [
                exportName(outputName, `${path}.outputs key`),
                parseDescriptor(descriptor, outputName),
              ]),
            ),
          );
          return [
            stateName,
            Object.freeze({
              fingerprint: digest(state.fingerprint, `${path}.fingerprint`),
              inputs: stateInputs,
              outputs: parsedOutputs,
            }),
          ];
        }),
      ),
    );

    validateStateVectors(states);
    validateRepresentations(states, outputs);
    validateAssets(states);
    return Object.freeze({
      notebook,
      producer,
      inputs,
      outputs,
      states,
    });
  } catch (error) {
    if (error instanceof NotebookExportError) throw error;
    throw new NotebookExportError("export_invalid", boundedMessage(error), { cause: error });
  }
}

function parseNotebook(input: unknown): NotebookProvenance {
  const value = strictRecord(input, "export.notebook", ["document_sha256", "filename"]);
  const filename =
    value.filename === null ? null : portableBasename(value.filename, "export.notebook.filename");
  return Object.freeze({
    filename,
    documentSha256: digest(value.document_sha256, "export.notebook.document_sha256"),
  });
}

function parseProducer(input: unknown): ProducerProvenance {
  const value = strictRecord(input, "export.producer", ["marimo", "marimo_export"]);
  return Object.freeze({
    marimo: boundedPrintable(value.marimo, "export.producer.marimo", MAX_EXPORT_NAME_BYTES),
    marimoExport: boundedPrintable(
      value.marimo_export,
      "export.producer.marimo_export",
      MAX_EXPORT_NAME_BYTES,
    ),
  });
}

function parseDescriptor(input: unknown, outputName: string): OutputDescriptor {
  const value = record(input, `output ${JSON.stringify(outputName)}`);
  const codec = value.codec;
  if (codec === "marimo.scalar.v1") return parseScalar(value, outputName);
  if (codec === "numpy.npy.v1") return parseNumpy(value, outputName);
  if (codec === "apache.arrow.file.v1") return parseArrow(value, outputName);
  if (codec === "marimo.blob-asset.msgpack.v1") return parseBlobAsset(value, outputName);
  fail(`output ${JSON.stringify(outputName)} has an unknown codec`);
}

function parseScalar(value: Record<string, unknown>, outputName: string): ScalarDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["codec", "media_type", "provenance", "value"], path);
  literal(value.media_type, "application/vnd.marimo.scalar.v1+json", `${path}.media_type`);
  const provenance = parseProvenance(value.provenance, path, false);
  return Object.freeze({
    codec: "marimo.scalar.v1",
    mediaType: "application/vnd.marimo.scalar.v1+json",
    provenance,
    value: parseScalarValue(value.value),
  });
}

function parseNumpy(value: Record<string, unknown>, outputName: string): NumpyDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/x-npy", `${path}.media_type`);
  return Object.freeze({
    codec: "numpy.npy.v1",
    mediaType: "application/x-npy",
    provenance: parseProvenance(value.provenance, path, true),
    asset: parseAsset(value.asset, path),
  });
}

function parseArrow(value: Record<string, unknown>, outputName: string): ArrowDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/vnd.apache.arrow.file", `${path}.media_type`);
  return Object.freeze({
    codec: "apache.arrow.file.v1",
    mediaType: "application/vnd.apache.arrow.file",
    provenance: parseProvenance(value.provenance, path, true),
    asset: parseAsset(value.asset, path),
  });
}

function parseBlobAsset(value: Record<string, unknown>, outputName: string): BlobAssetDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "filename", "media_type", "metadata", "provenance"], path);
  if (typeof value.media_type !== "string") fail(`${path}.media_type must be a string`);
  parseMediaType(value.media_type);
  const filename =
    value.filename === null ? null : portableBasename(value.filename, `${path}.filename`);
  const metadata = portableJsonObject(value.metadata, `${path}.metadata`);
  if (encoder.encode(canonicalJson(metadata)).byteLength > MAX_METADATA_BYTES) {
    fail(`${path}.metadata exceeds ${MAX_METADATA_BYTES} canonical JSON bytes`);
  }
  return Object.freeze({
    codec: "marimo.blob-asset.msgpack.v1",
    mediaType: value.media_type,
    filename,
    metadata,
    provenance: parseProvenance(value.provenance, path, true),
    asset: parseAsset(value.asset, path),
  });
}

function parseProvenance(input: unknown, parent: string, asset: boolean): Provenance {
  const path = `${parent}.provenance`;
  const value = strictRecord(input, path, ["cache_key", "python_type", "return_reference"]);
  const reference =
    value.return_reference === null
      ? null
      : opaqueReference(value.return_reference, `${path}.return_reference`);
  if (asset === (reference === null)) {
    fail(`${path}.return_reference ${asset ? "must be present" : "must be null"}`);
  }
  return Object.freeze({
    cacheKey: opaqueReference(value.cache_key, `${path}.cache_key`),
    returnReference: reference,
    pythonType: boundedPrintable(value.python_type, `${path}.python_type`, MAX_PROVENANCE_BYTES),
  });
}

function parseAsset(input: unknown, parent: string): AssetDescriptor {
  const path = `${parent}.asset`;
  const value = strictRecord(input, path, ["sha256", "size"]);
  if (
    typeof value.size !== "number" ||
    !Number.isInteger(value.size) ||
    value.size < 1 ||
    value.size > MAX_ASSET_SIZE
  ) {
    fail(`${path}.size must be an integer from 1 through ${MAX_ASSET_SIZE}`);
  }
  return Object.freeze({
    sha256: digest(value.sha256, `${path}.sha256`),
    size: value.size,
  });
}

function parseScalarValue(input: unknown): ScalarValue {
  if (input === null || typeof input === "boolean" || typeof input === "string") return input;
  if (typeof input === "number") {
    if (Number.isInteger(input) && !Number.isSafeInteger(input)) {
      fail("untagged scalar integer exceeds the safe integer range");
    }
    return input;
  }
  const value = strictRecord(input, "scalar.value", ["type", "value"]);
  if (value.type === "bigint") {
    if (typeof value.value !== "string" || !BIGINT.test(value.value)) {
      fail("tagged bigint has an invalid decimal value");
    }
    const result = BigInt(value.value);
    if (result >= -BigInt(MAX_SAFE_INTEGER) && result <= BigInt(MAX_SAFE_INTEGER)) {
      fail("tagged bigint must lie outside the safe integer range");
    }
    return result;
  }
  if (value.type === "float") {
    if (value.value === "nan") return Number.NaN;
    if (value.value === "infinity") return Number.POSITIVE_INFINITY;
    if (value.value === "-infinity") return Number.NEGATIVE_INFINITY;
    if (value.value === "negative-zero") return -0;
    fail("tagged float has an invalid value");
  }
  fail("scalar tag type must be bigint or float");
}

function validateStateVectors(states: Readonly<Record<string, ParsedState>>): void {
  const vectors = new Map<string, string>();
  for (const [name, state] of Object.entries(states)) {
    const key = canonicalJson(state.inputs);
    const other = vectors.get(key);
    if (other !== undefined) {
      fail(`export states ${JSON.stringify(other)} and ${JSON.stringify(name)} have equal inputs`);
    }
    vectors.set(key, name);
  }
}

function validateRepresentations(
  states: Readonly<Record<string, ParsedState>>,
  outputNames: readonly string[],
): void {
  const representations = new Map<string, string>();
  for (const state of Object.values(states)) {
    for (const name of outputNames) {
      const descriptor = state.outputs[name]!;
      const representation = `${descriptor.codec}\0${descriptor.mediaType}`;
      const previous = representations.get(name);
      if (previous !== undefined && previous !== representation) {
        throw new NotebookExportError(
          "output_representation_changed",
          `Output ${JSON.stringify(name)} changes codec or media type across states.`,
          { details: { output: name } },
        );
      }
      representations.set(name, representation);
    }
  }
}

function validateAssets(states: Readonly<Record<string, ParsedState>>): void {
  const assets = new Map<string, string>();
  let total = 0;
  for (const state of Object.values(states)) {
    for (const descriptor of Object.values(state.outputs)) {
      if (descriptor.codec === "marimo.scalar.v1") continue;
      const identity = `${descriptor.codec}\0${descriptor.asset.sha256}`;
      const facts =
        descriptor.codec === "marimo.blob-asset.msgpack.v1"
          ? `${descriptor.asset.size}\0${descriptor.mediaType}\0${descriptor.filename ?? ""}\0${canonicalJson(descriptor.metadata)}`
          : `${descriptor.asset.size}\0${descriptor.mediaType}`;
      const previous = assets.get(identity);
      if (previous !== undefined && previous !== facts) {
        fail(`asset ${descriptor.asset.sha256} has conflicting descriptor facts`);
      }
      if (previous === undefined) {
        assets.set(identity, facts);
        total += descriptor.asset.size;
        if (!Number.isSafeInteger(total))
          fail("aggregate unique asset size exceeds the safe range");
      }
    }
  }
}

export function portableJsonObject(input: unknown, path: string): JsonObject {
  const value = portableJsonValue(input, path, 0);
  if (Array.isArray(value) || value === null || typeof value !== "object") {
    fail(`${path} must be an object`);
  }
  return value as JsonObject;
}

function portableJsonValue(input: unknown, path: string, depth: number): JsonValue {
  if (depth > MAX_JSON_DEPTH) fail(`${path} exceeds the maximum JSON depth`);
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "string") {
    unicodeScalar(input, path);
    return input;
  }
  if (typeof input === "number") {
    if (!Number.isFinite(input)) fail(`${path} must be finite`);
    if (Number.isInteger(input) && !Number.isSafeInteger(input)) {
      fail(`${path} integer exceeds the safe range`);
    }
    return Object.is(input, -0) ? 0 : input;
  }
  if (Array.isArray(input)) {
    return Object.freeze(
      input.map((item, index) => portableJsonValue(item, `${path}[${index}]`, depth + 1)),
    );
  }
  const value = record(input, path);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(value).map(([key, item]) => {
        unicodeScalar(key, `${path} key`);
        return [key, portableJsonValue(item, `${path}.${key}`, depth + 1)];
      }),
    ),
  );
}

export function canonicalJson(value: JsonValue): string {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") {
    unicodeScalar(value, "canonical JSON string");
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("canonical JSON number must be finite");
    return JSON.stringify(Object.is(value, -0) ? 0 : value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as JsonObject;
  const keys = Object.keys(object);
  keys.forEach((key) => unicodeScalar(key, "canonical JSON key"));
  return `{${keys
    .sort(compareUnicodeScalarStrings)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key]!)}`)
    .join(",")}}`;
}

export function compareUnicodeScalarStrings(left: string, right: string): number {
  const leftPoints = left[Symbol.iterator]();
  const rightPoints = right[Symbol.iterator]();
  while (true) {
    const a = leftPoints.next();
    const b = rightPoints.next();
    if (a.done || b.done) {
      if (a.done && b.done) return 0;
      return a.done ? -1 : 1;
    }
    const difference = a.value.codePointAt(0)! - b.value.codePointAt(0)!;
    if (difference !== 0) return difference;
  }
}

function strictRecord(
  input: unknown,
  path: string,
  fields: readonly string[],
): Record<string, unknown> {
  const value = record(input, path);
  exactFields(value, fields, path);
  return value;
}

function record(input: unknown, path: string): Record<string, unknown> {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    fail(`${path} must be an object`);
  }
  return input as Record<string, unknown>;
}

function exactFields(
  value: Record<string, unknown>,
  fields: readonly string[],
  path: string,
): void {
  const expected = new Set(fields);
  const actual = Object.keys(value);
  const missing = fields.filter((field) => !Object.hasOwn(value, field));
  const extra = actual.filter((field) => !expected.has(field));
  if (missing.length > 0) fail(`${path} is missing fields: ${missing.slice(0, 8).join(", ")}`);
  if (extra.length > 0) fail(`${path} has unknown fields: ${extra.slice(0, 8).join(", ")}`);
}

function requireExactKeys(
  value: Record<string, unknown> | JsonObject,
  expected: ReadonlySet<string>,
  path: string,
): void {
  const keys = Object.keys(value);
  const missing = [...expected].filter((key) => !Object.hasOwn(value, key));
  const extra = keys.filter((key) => !expected.has(key));
  if (missing.length > 0 || extra.length > 0) fail(`${path} must match the root name set`);
}

function nameArray(input: unknown, path: string, nonempty: boolean): readonly string[] {
  if (!Array.isArray(input)) fail(`${path} must be an array`);
  const values = input.map((name, index) => exportName(name, `${path}[${index}]`));
  if (nonempty && values.length === 0) fail(`${path} must not be empty`);
  if (new Set(values).size !== values.length) fail(`${path} must contain unique names`);
  return Object.freeze(values);
}

function exportName(input: unknown, path: string): string {
  return boundedPrintable(input, path, MAX_EXPORT_NAME_BYTES);
}

function boundedPrintable(input: unknown, path: string, maxBytes: number): string {
  if (typeof input !== "string") fail(`${path} must be a string`);
  unicodeScalar(input, path);
  if (
    input.length === 0 ||
    input !== input.trim() ||
    hasControl(input) ||
    encoder.encode(input).byteLength > maxBytes
  ) {
    fail(`${path} must be a bounded printable string`);
  }
  return input;
}

function opaqueReference(input: unknown, path: string): string {
  const value = boundedPrintable(input, path, MAX_PROVENANCE_BYTES);
  if (value.startsWith("/") || /^[A-Za-z]:[\\/]/u.test(value) || value.startsWith("\\\\")) {
    fail(`${path} must be a store-relative opaque identifier`);
  }
  return value;
}

function portableBasename(input: unknown, path: string): string {
  if (typeof input !== "string") fail(`${path} must be a string`);
  unicodeScalar(input, path);
  const stem = input.split(".", 1)[0]!.replace(/[ .]+$/u, "");
  if (
    input.length === 0 ||
    input === "." ||
    input === ".." ||
    input !== input.trim() ||
    input.endsWith(".") ||
    hasControl(input) ||
    WINDOWS_RESERVED.test(input) ||
    WINDOWS_DEVICE.test(stem) ||
    encoder.encode(input).byteLength > MAX_EXPORT_NAME_BYTES
  ) {
    fail(`${path} must be a portable basename`);
  }
  return input;
}

function digest(input: unknown, path: string): string {
  if (typeof input !== "string" || !SHA256.test(input)) {
    fail(`${path} must be a lowercase SHA-256 digest`);
  }
  return input;
}

function literal<T extends string>(input: unknown, expected: T, path: string): T {
  if (input !== expected) fail(`${path} must be ${JSON.stringify(expected)}`);
  return expected;
}

function unicodeScalar(value: string, path: string): void {
  if (/[\ud800-\udfff]/u.test(value)) fail(`${path} must contain Unicode scalar values`);
}

function hasControl(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit < 32 || codeUnit === 127) return true;
  }
  return false;
}

function boundedMessage(error: unknown): string {
  const source = error instanceof Error ? error.message : "Export index validation failed.";
  const message = source.length > 2_048 ? `${source.slice(0, 2_045)}...` : source;
  return `Invalid export index: ${message}`;
}

function fail(message: string): never {
  throw new NotebookExportError(
    "export_invalid",
    message.length > 2_048 ? `${message.slice(0, 2_045)}...` : message,
  );
}

export function cloneFrozenJsonObject(value: JsonObject): JsonObject {
  return freezeJsonObject(value);
}

export function cloneFrozenJsonValue(value: JsonValue): JsonValue {
  return freezeJsonValue(value);
}
