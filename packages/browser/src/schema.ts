import { portableJsonObject, portableJsonValue } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import { parseMediaType } from "./media-type.js";
import type {
  ArrowDescriptor,
  AssetDescriptor,
  BlobAssetDescriptor,
  ControlBinding,
  JsonDescriptor,
  NotebookProvenance,
  NumpyDescriptor,
  MarimoCellDescriptor,
  MarimoOutputDescriptor,
  OutputDescriptor,
  ProducerProvenance,
  Provenance,
  ScalarDescriptor,
  ScalarValue,
} from "./types.js";
import { isNotebookExportError, NotebookExportError } from "./types.js";
import { isJsonBoolean, isJsonNumber, isJsonString, isRecordValue } from "./value-types.js";

const encoder = new TextEncoder();
const SHA256 = /^[0-9a-f]{64}$/u;
const BIGINT = /^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/u;
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const MAX_EXPORT_NAME_BYTES = 255;
const MAX_CONTROL_ID_BYTES = 1_024;
const MAX_CONTROL_PATH_STEPS = 256;
const MAX_PROVENANCE_BYTES = 2_048;
const MAX_ASSET_SIZE = 2_147_483_647;
const MAX_METADATA_BYTES = 256 * 1024;
const WINDOWS_RESERVED = /[<>:"/\\|?*]/u;
const WINDOWS_DEVICE = /^(?:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$/iu;
const EDGE_WHITESPACE: ReadonlySet<number> = new Set([
  0x0009, 0x000a, 0x000b, 0x000c, 0x000d, 0x001c, 0x001d, 0x001e, 0x001f, 0x0020, 0x0085, 0x00a0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
  0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff,
]);
export interface ParsedState {
  readonly inputs: JsonObject;
  readonly outputs: Readonly<Record<string, OutputDescriptor>>;
}

export interface ParsedExportIndex {
  readonly specSha256: string;
  readonly defaultState: string;
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly inputs: readonly string[];
  readonly controlBindings: Readonly<Record<string, ControlBinding>>;
  readonly outputs: readonly string[];
  readonly aliases: Readonly<Record<string, string>>;
  readonly states: Readonly<Record<string, ParsedState>>;
}

export function parseExportIndex<Input>(input: Input): ParsedExportIndex {
  try {
    const root = strictRecord(portableJsonObject(input, "export"), "export", [
      "aliases",
      "control_bindings",
      "default_state",
      "inputs",
      "notebook",
      "outputs",
      "producer",
      "schema",
      "spec_sha256",
      "states",
    ]);
    literal(root.schema, "marimo-export.export.v1", "export.schema");
    const specSha256 = digest(root.spec_sha256, "export.spec_sha256");
    const defaultState = digest(root.default_state, "export.default_state");
    const notebook = parseNotebook(root.notebook);
    const producer = parseProducer(root.producer);
    const inputs = opaqueNameArray(root.inputs, "export.inputs");
    const outputs = nameArray(root.outputs, "export.outputs", true);
    const inputSet = new Set(inputs);
    const controlBindingRecord = record(root.control_bindings, "export.control_bindings");
    const controlBindings = Object.freeze(
      Object.fromEntries(
        Object.entries(controlBindingRecord).map(([objectId, binding]) => {
          const controlId = boundedPrintable(
            objectId,
            "export.control_bindings key",
            MAX_CONTROL_ID_BYTES,
          );
          return [
            controlId,
            parseControlBinding(
              binding,
              `export.control_bindings[${JSON.stringify(controlId)}]`,
              inputSet,
            ),
          ];
        }),
      ),
    );
    const outputSet = new Set(outputs);
    const statesRecord = record(root.states, "export.states");
    if (Object.keys(statesRecord).length === 0) fail("export.states must not be empty");

    const states = Object.freeze(
      Object.fromEntries(
        Object.entries(statesRecord).map(([key, value]) => {
          const fingerprint = digest(key, "export.states key");
          const path = `export.states[${JSON.stringify(fingerprint)}]`;
          const state = strictRecord(value, path, ["inputs", "outputs"]);
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
            fingerprint,
            Object.freeze({
              inputs: stateInputs,
              outputs: parsedOutputs,
            }),
          ];
        }),
      ),
    );
    const aliasesRecord = record(root.aliases, "export.aliases");
    const aliases = Object.freeze(
      Object.fromEntries(
        Object.entries(aliasesRecord).map(([name, value]) => {
          const alias = exportName(name, "export.aliases key");
          const fingerprint = digest(value, `export.aliases[${JSON.stringify(alias)}]`);
          if (!Object.hasOwn(states, fingerprint)) {
            fail(
              `export.aliases[${JSON.stringify(alias)}] references an unknown state fingerprint`,
            );
          }
          return [alias, fingerprint];
        }),
      ),
    );
    if (!Object.hasOwn(states, defaultState)) {
      fail("export.default_state must reference a declared state fingerprint");
    }

    validateRepresentations(states, outputs);
    validateAssets(states);
    return Object.freeze({
      specSha256,
      defaultState,
      notebook,
      producer,
      inputs,
      controlBindings,
      outputs,
      aliases,
      states,
    });
  } catch (error) {
    if (isNotebookExportError(error)) throw error;
    throw new NotebookExportError("export_invalid", boundedMessage(error), { cause: error });
  }
}

function parseNotebook(input: JsonValue | undefined): NotebookProvenance {
  const value = strictRecord(input, "export.notebook", ["document_sha256", "filename"]);
  const filename =
    value.filename === null ? null : portableBasename(value.filename, "export.notebook.filename");
  return Object.freeze({
    filename,
    documentSha256: digest(value.document_sha256, "export.notebook.document_sha256"),
  });
}

function parseProducer(input: JsonValue | undefined): ProducerProvenance {
  const value = strictRecord(input, "export.producer", [
    "implementation_sha256",
    "marimo",
    "marimo_export",
  ]);
  return Object.freeze({
    implementationSha256: digest(
      value.implementation_sha256,
      "export.producer.implementation_sha256",
    ),
    marimo: boundedPrintable(value.marimo, "export.producer.marimo", MAX_EXPORT_NAME_BYTES),
    marimoExport: boundedPrintable(
      value.marimo_export,
      "export.producer.marimo_export",
      MAX_EXPORT_NAME_BYTES,
    ),
  });
}

function parseControlBinding(
  input: JsonValue | undefined,
  path: string,
  inputNames: ReadonlySet<string>,
): ControlBinding {
  const value = strictRecord(input, path, ["input", "path"]);
  const inputName = opaqueInputName(value.input, `${path}.input`);
  if (!inputNames.has(inputName)) fail(`${path}.input must name a declared input`);
  if (!Array.isArray(value.path) || value.path.length > MAX_CONTROL_PATH_STEPS) {
    fail(`${path}.path must be an array of at most ${MAX_CONTROL_PATH_STEPS} steps`);
  }
  return Object.freeze({
    input: inputName,
    path: Object.freeze(
      value.path.map((step, index) => parseControlPathStep(step, `${path}.path[${index}]`)),
    ),
  });
}

function parseControlPathStep(
  input: JsonValue | undefined,
  path: string,
): ControlBinding["path"][number] {
  const value = record(input, path);
  if (value.kind === "element") {
    exactFields(value, ["kind"], path);
    return Object.freeze({ kind: "element" });
  }
  if (value.kind === "index") {
    exactFields(value, ["kind", "value"], path);
    if (!isJsonNumber(value.value) || !Number.isSafeInteger(value.value) || value.value < 0) {
      fail(`${path}.value must be a nonnegative safe integer`);
    }
    return Object.freeze({ kind: "index", value: value.value });
  }
  if (value.kind === "key") {
    exactFields(value, ["kind", "value"], path);
    if (!isJsonString(value.value)) fail(`${path}.value must be a string`);
    unicodeScalar(value.value, `${path}.value`);
    if (encoder.encode(value.value).byteLength > MAX_CONTROL_ID_BYTES) {
      fail(`${path}.value exceeds ${MAX_CONTROL_ID_BYTES} UTF-8 bytes`);
    }
    return Object.freeze({ kind: "key", value: value.value });
  }
  fail(`${path}.kind must be index, key, or element`);
}

function parseDescriptor(input: JsonValue | undefined, outputName: string): OutputDescriptor {
  const value = record(input, `output ${JSON.stringify(outputName)}`);
  const codec = value.codec;
  if (codec === "marimo.scalar.v1") return parseScalar(value, outputName);
  if (codec === "marimo.json.v1") return parseJson(value, outputName);
  if (codec === "marimo.output.v1") return parseMarimoOutput(value, outputName);
  if (codec === "marimo.cell.v1") return parseMarimoCell(value, outputName);
  if (codec === "numpy.npy.v1") return parseNumpy(value, outputName);
  if (codec === "apache.arrow.file.v1") return parseArrow(value, outputName);
  if (codec === "marimo.blob-asset.msgpack.v1") return parseBlobAsset(value, outputName);
  fail(`output ${JSON.stringify(outputName)} has an unknown codec`);
}

function parseJson(value: JsonObject, outputName: string): JsonDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["codec", "media_type", "provenance", "value"], path);
  literal(value.media_type, "application/vnd.marimo.json.v1+json", `${path}.media_type`);
  return Object.freeze({
    codec: "marimo.json.v1",
    mediaType: "application/vnd.marimo.json.v1+json",
    provenance: parseProvenance(value.provenance, path),
    value: portableJsonValue(value.value, `${path}.value`),
  });
}

function parseMarimoOutput(value: JsonObject, outputName: string): MarimoOutputDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/vnd.marimo.output.v1+json", `${path}.media_type`);
  return Object.freeze({
    codec: "marimo.output.v1",
    mediaType: "application/vnd.marimo.output.v1+json",
    provenance: parseProvenance(value.provenance, path),
    asset: parseAsset(value.asset, path),
  });
}

function parseMarimoCell(value: JsonObject, outputName: string): MarimoCellDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/vnd.marimo.cell.v1+json", `${path}.media_type`);
  return Object.freeze({
    codec: "marimo.cell.v1",
    mediaType: "application/vnd.marimo.cell.v1+json",
    provenance: parseProvenance(value.provenance, path),
    asset: parseAsset(value.asset, path),
  });
}

function parseScalar(value: JsonObject, outputName: string): ScalarDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["codec", "media_type", "provenance", "value"], path);
  literal(value.media_type, "application/vnd.marimo.scalar.v1+json", `${path}.media_type`);
  const provenance = parseProvenance(value.provenance, path);
  return Object.freeze({
    codec: "marimo.scalar.v1",
    mediaType: "application/vnd.marimo.scalar.v1+json",
    provenance,
    value: parseScalarValue(value.value),
  });
}

function parseNumpy(value: JsonObject, outputName: string): NumpyDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/x-npy", `${path}.media_type`);
  return Object.freeze({
    codec: "numpy.npy.v1",
    mediaType: "application/x-npy",
    provenance: parseProvenance(value.provenance, path),
    asset: parseAsset(value.asset, path),
  });
}

function parseArrow(value: JsonObject, outputName: string): ArrowDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "media_type", "provenance"], path);
  literal(value.media_type, "application/vnd.apache.arrow.file", `${path}.media_type`);
  return Object.freeze({
    codec: "apache.arrow.file.v1",
    mediaType: "application/vnd.apache.arrow.file",
    provenance: parseProvenance(value.provenance, path),
    asset: parseAsset(value.asset, path),
  });
}

function parseBlobAsset(value: JsonObject, outputName: string): BlobAssetDescriptor {
  const path = `output ${JSON.stringify(outputName)}`;
  exactFields(value, ["asset", "codec", "filename", "media_type", "metadata", "provenance"], path);
  if (!isJsonString(value.media_type)) fail(`${path}.media_type must be a string`);
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
    provenance: parseProvenance(value.provenance, path),
    asset: parseAsset(value.asset, path),
  });
}

function parseProvenance(input: JsonValue | undefined, parent: string): Provenance {
  const path = `${parent}.provenance`;
  const value = strictRecord(input, path, ["python_type"]);
  return Object.freeze({
    pythonType: boundedPrintable(value.python_type, `${path}.python_type`, MAX_PROVENANCE_BYTES),
  });
}

function parseAsset(input: JsonValue | undefined, parent: string): AssetDescriptor {
  const path = `${parent}.asset`;
  const value = strictRecord(input, path, ["sha256", "size"]);
  if (
    !isJsonNumber(value.size) ||
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

function parseScalarValue(input: JsonValue | undefined): ScalarValue {
  if (input === null) return null;
  if (isJsonBoolean(input) || isJsonString(input)) return input;
  if (isJsonNumber(input)) {
    if (Number.isInteger(input) && !Number.isSafeInteger(input)) {
      fail("untagged scalar integer exceeds the safe integer range");
    }
    return input;
  }
  const value = strictRecord(input, "scalar.value", ["type", "value"]);
  if (value.type === "bigint") {
    if (!isJsonString(value.value) || !BIGINT.test(value.value)) {
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
      if (descriptor.codec === "marimo.scalar.v1" || descriptor.codec === "marimo.json.v1") {
        continue;
      }
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

export function canonicalJson(value: JsonValue): string {
  if (value === null || isJsonBoolean(value)) return JSON.stringify(value);
  if (isJsonString(value)) {
    unicodeScalar(value, "canonical JSON string");
    return JSON.stringify(value);
  }
  if (isJsonNumber(value)) {
    if (!Number.isFinite(value)) fail("canonical JSON number must be finite");
    return JSON.stringify(Object.is(value, -0) ? 0 : value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!isJsonObject(value)) fail("canonical JSON object must use string keys");
  const keys = Object.keys(value);
  keys.forEach((key) => unicodeScalar(key, "canonical JSON key"));
  return `{${keys
    .sort(compareUnicodeScalarStrings)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key]!)}`)
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
  input: JsonValue | undefined,
  path: string,
  fields: readonly string[],
): JsonObject {
  const value = record(input, path);
  exactFields(value, fields, path);
  return value;
}

function record(input: JsonValue | undefined, path: string): JsonObject {
  if (!isJsonObject(input)) {
    fail(`${path} must be an object`);
  }
  return input;
}

function exactFields(value: JsonObject, fields: readonly string[], path: string): void {
  const expected = new Set(fields);
  const actual = Object.keys(value);
  const missing = fields.filter((field) => !Object.hasOwn(value, field));
  const extra = actual.filter((field) => !expected.has(field));
  if (missing.length > 0) fail(`${path} is missing fields: ${missing.slice(0, 8).join(", ")}`);
  if (extra.length > 0) fail(`${path} has unknown fields: ${extra.slice(0, 8).join(", ")}`);
}

function requireExactKeys(value: JsonObject, expected: ReadonlySet<string>, path: string): void {
  const keys = Object.keys(value);
  const missing = [...expected].filter((key) => !Object.hasOwn(value, key));
  const extra = keys.filter((key) => !expected.has(key));
  if (missing.length > 0 || extra.length > 0) fail(`${path} must match the root name set`);
}

function nameArray(
  input: JsonValue | undefined,
  path: string,
  nonempty: boolean,
): readonly string[] {
  if (!Array.isArray(input)) fail(`${path} must be an array`);
  const values = input.map((name, index) => exportName(name, `${path}[${index}]`));
  if (nonempty && values.length === 0) fail(`${path} must not be empty`);
  if (new Set(values).size !== values.length) fail(`${path} must contain unique names`);
  return Object.freeze(values);
}

function opaqueNameArray(input: JsonValue | undefined, path: string): readonly string[] {
  if (!Array.isArray(input)) fail(`${path} must be an array`);
  const values = input.map((name, index) => opaqueInputName(name, `${path}[${index}]`));
  if (new Set(values).size !== values.length) fail(`${path} must contain unique names`);
  return Object.freeze(values);
}

export function opaqueInputName(input: JsonValue | undefined, path: string): string {
  if (!isJsonString(input)) fail(`${path} must be a string`);
  unicodeScalar(input, path);
  if (input.length === 0 || encoder.encode(input).byteLength > MAX_EXPORT_NAME_BYTES) {
    fail(`${path} must be a bounded nonempty UTF-8 string`);
  }
  return input;
}

function exportName(input: JsonValue | undefined, path: string): string {
  return boundedPrintable(input, path, MAX_EXPORT_NAME_BYTES);
}

function boundedPrintable(input: JsonValue | undefined, path: string, maxBytes: number): string {
  if (!isJsonString(input)) fail(`${path} must be a string`);
  unicodeScalar(input, path);
  if (
    input.length === 0 ||
    hasEdgeWhitespace(input) ||
    hasControl(input) ||
    encoder.encode(input).byteLength > maxBytes
  ) {
    fail(`${path} must be a bounded printable string`);
  }
  return input;
}

function hasEdgeWhitespace(value: string): boolean {
  return (
    EDGE_WHITESPACE.has(value.charCodeAt(0)) ||
    EDGE_WHITESPACE.has(value.charCodeAt(value.length - 1))
  );
}

function portableBasename(input: JsonValue | undefined, path: string): string {
  if (!isJsonString(input)) fail(`${path} must be a string`);
  unicodeScalar(input, path);
  const stem = input.split(".", 1)[0]!.replace(/[ .]+$/u, "");
  if (
    input.length === 0 ||
    input === "." ||
    input === ".." ||
    hasEdgeWhitespace(input) ||
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

function digest(input: JsonValue | undefined, path: string): string {
  if (!isJsonString(input) || !SHA256.test(input)) {
    fail(`${path} must be a lowercase SHA-256 digest`);
  }
  return input;
}

function literal<T extends string>(input: JsonValue | undefined, expected: T, path: string): T {
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

function boundedMessage(cause: unknown): string {
  const source = cause instanceof Error ? cause.message : "Export index validation failed.";
  const message = source.length > 2_048 ? `${source.slice(0, 2_045)}...` : source;
  return `Invalid export index: ${message}`;
}

function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return isRecordValue(value);
}

function fail(message: string): never {
  throw new NotebookExportError(
    "export_invalid",
    message.length > 2_048 ? `${message.slice(0, 2_045)}...` : message,
  );
}
