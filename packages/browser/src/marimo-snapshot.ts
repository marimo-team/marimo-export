import { parseStrictJson, portableJsonObject, portableJsonValue } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import { canonicalJson } from "./schema.js";
import { isJsonNumber, isJsonString, isRecordValue } from "./value-types.js";

const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const encoder = new TextEncoder();
const SHA256 = /^[0-9a-f]{64}$/u;
const MAX_SNAPSHOT_VALUES = 2_000_000;
const CELL_CHANNELS: ReadonlySet<string> = new Set([
  "stdout",
  "stderr",
  "stdin",
  "pdb",
  "output",
  "marimo-error",
  "media",
] satisfies readonly MarimoCellChannel[]);

export type MarimoCellChannel =
  | "stdout"
  | "stderr"
  | "stdin"
  | "pdb"
  | "output"
  | "marimo-error"
  | "media";

export type MarimoBufferPathToken = string | number;
export type MarimoBufferPath = readonly [MarimoBufferPathToken, ...MarimoBufferPathToken[]];

export interface MarimoEsmSpec {
  readonly hash: string;
  readonly url: string;
}

export interface MarimoModelOpenMessage {
  readonly method: "open";
  readonly state: JsonObject;
  readonly buffer_paths: readonly MarimoBufferPath[];
  readonly buffers: readonly string[];
  readonly esm_spec: MarimoEsmSpec | null;
}

export interface MarimoModelUpdateMessage {
  readonly method: "update";
  readonly state: JsonObject;
  readonly buffer_paths: readonly MarimoBufferPath[];
  readonly buffers: readonly string[];
  readonly esm_spec: MarimoEsmSpec | null;
}

export interface MarimoModelCustomMessage {
  readonly method: "custom";
  readonly content: JsonValue;
  readonly buffers: readonly string[];
}

export interface MarimoModelCloseMessage {
  readonly method: "close";
}

export type MarimoModelLifecycleMessage =
  | MarimoModelOpenMessage
  | MarimoModelUpdateMessage
  | MarimoModelCustomMessage
  | MarimoModelCloseMessage;

export interface MarimoModelLifecycleNotification {
  readonly op: "model-lifecycle";
  readonly model_id: string;
  readonly message: MarimoModelLifecycleMessage;
}

export interface MarimoCellOutput {
  readonly channel: MarimoCellChannel;
  readonly mimetype: string;
  readonly data: JsonValue;
}

export interface MarimoReplayResources {
  readonly files: Readonly<Record<string, string>>;
  readonly modelNotifications: readonly MarimoModelLifecycleNotification[];
  readonly functions: Readonly<Record<string, readonly string[]>>;
  readonly uiValues: Readonly<Record<string, JsonValue>>;
}

export interface MarimoOutputSnapshot {
  readonly schema: "marimo.output.v1";
  readonly projectionSha256: string;
  readonly ownerCellId: string;
  readonly output: MarimoCellOutput | null;
  readonly resources: MarimoReplayResources;
}

export interface MarimoCellIdentity {
  readonly id: string;
  readonly name: string | null;
  readonly codeSha256: string;
  readonly config: JsonObject;
}

export interface MarimoCellSnapshot {
  readonly schema: "marimo.cell.v1";
  readonly projectionSha256: string;
  readonly cell: MarimoCellIdentity;
  readonly outcome: "completed";
  readonly output: MarimoCellOutput | null;
  readonly console: readonly MarimoCellOutput[];
  readonly resources: MarimoReplayResources;
}

export function parseMarimoOutputSnapshot(bytes: Uint8Array): MarimoOutputSnapshot {
  const root = snapshotRoot(bytes, "marimo.output.v1", [
    "output",
    "ownerCellId",
    "projectionSha256",
    "resources",
    "schema",
  ]);
  const ownerCellId = nonEmptyString(root.ownerCellId, "ownerCellId");
  const projectionSha256 = digest(root.projectionSha256, "projectionSha256");
  return Object.freeze({
    schema: "marimo.output.v1",
    projectionSha256,
    ownerCellId,
    output: parseCellOutput(root.output, "output"),
    resources: parseResources(root.resources, ownerCellId, projectionSha256),
  });
}

export function parseMarimoCellSnapshot(bytes: Uint8Array): MarimoCellSnapshot {
  const root = snapshotRoot(bytes, "marimo.cell.v1", [
    "cell",
    "console",
    "outcome",
    "output",
    "projectionSha256",
    "resources",
    "schema",
  ]);
  if (root.outcome !== "completed") {
    throw new TypeError('Marimo cell snapshot outcome must be "completed".');
  }
  if (!Array.isArray(root.console)) {
    throw new TypeError("Marimo cell snapshot console must be an array.");
  }
  const cell = parseCellIdentity(root.cell);
  const projectionSha256 = digest(root.projectionSha256, "projectionSha256");
  return Object.freeze({
    schema: "marimo.cell.v1",
    projectionSha256,
    cell,
    outcome: "completed",
    output: parseCellOutput(root.output, "output"),
    console: Object.freeze(
      root.console.map((output, index) => {
        const parsed = parseCellOutput(output, `console[${index}]`);
        if (parsed === null) throw new TypeError(`console[${index}] must contain an output.`);
        return parsed;
      }),
    ),
    resources: parseResources(root.resources, cell.id, projectionSha256),
  });
}

function snapshotRoot(
  bytes: Uint8Array,
  schema: "marimo.output.v1" | "marimo.cell.v1",
  fields: readonly string[],
): JsonObject {
  const text = decoder.decode(bytes);
  const parsed = parseStrictJson(text, MAX_SNAPSHOT_VALUES);
  const root = strictRecord(parsed, `${schema} snapshot`, fields);
  if (root.schema !== schema)
    throw new TypeError(`Snapshot schema must be ${JSON.stringify(schema)}.`);
  const canonical = encoder.encode(canonicalJson(parsed));
  if (!equalBytes(bytes, canonical))
    throw new TypeError(`${schema} snapshot must be canonical JSON.`);
  return root;
}

function parseCellIdentity(value: JsonValue | undefined): MarimoCellIdentity {
  const cell = strictRecord(value, "cell", ["codeSha256", "config", "id", "name"]);
  const id = nonEmptyString(cell.id, "cell.id");
  const name = cell.name === null ? null : nonEmptyString(cell.name, "cell.name");
  if (!isJsonString(cell.codeSha256) || !SHA256.test(cell.codeSha256)) {
    throw new TypeError("cell.codeSha256 must be a lowercase SHA-256 digest.");
  }
  return Object.freeze({
    id,
    name,
    codeSha256: cell.codeSha256,
    config: portableJsonObject(cell.config, "cell.config"),
  });
}

function parseCellOutput(value: JsonValue | undefined, path: string): MarimoCellOutput | null {
  if (value === null) return null;
  const output = strictRecord(value, path, ["channel", "data", "mimetype"]);
  return Object.freeze({
    channel: cellChannel(output.channel, `${path}.channel`),
    mimetype: nonEmptyString(output.mimetype, `${path}.mimetype`),
    data: portableJsonValue(output.data, `${path}.data`),
  });
}

function parseResources(
  value: JsonValue | undefined,
  ownerCellId: string,
  projectionSha256: string,
): MarimoReplayResources {
  const resources = strictRecord(value, "resources", [
    "files",
    "functions",
    "modelNotifications",
    "uiValues",
  ]);
  const files = record(resources.files, "resources.files");
  const parsedFiles = Object.freeze(
    Object.fromEntries(
      Object.entries(files).map(([path, dataUrl]) => [
        nonEmptyString(path, "resources.files key"),
        dataUrlString(dataUrl, `resources.files[${JSON.stringify(path)}]`),
      ]),
    ),
  );
  if (!Array.isArray(resources.modelNotifications)) {
    throw new TypeError("resources.modelNotifications must be an array.");
  }
  const modelNotifications = Object.freeze(
    resources.modelNotifications.map((notification, index) =>
      parseModelLifecycle(notification, index, parsedFiles, projectionSha256),
    ),
  );
  const functions = record(resources.functions, "resources.functions");
  const parsedFunctions = Object.freeze(
    Object.fromEntries(
      Object.entries(functions).map(([namespace, names]) => {
        if (!Array.isArray(names) || names.some((name) => !isJsonString(name) || !name)) {
          throw new TypeError(
            `resources.functions[${JSON.stringify(namespace)}] must be an array of names.`,
          );
        }
        if (names.length > 0) {
          throw new TypeError(
            `resources.functions[${JSON.stringify(namespace)}] must be empty for static replay.`,
          );
        }
        return [
          projectionUiObjectId(namespace, "resources.functions key", ownerCellId, projectionSha256),
          Object.freeze([...names]),
        ];
      }),
    ),
  );
  const uiValues = record(resources.uiValues, "resources.uiValues");
  const parsedUiValues = Object.freeze(
    Object.fromEntries(
      Object.entries(uiValues).map(([objectId, item]) => [
        projectionUiObjectId(objectId, "resources.uiValues key", ownerCellId, projectionSha256),
        portableJsonValue(item, `resources.uiValues[${JSON.stringify(objectId)}]`),
      ]),
    ),
  );
  for (const objectId of Object.keys(parsedUiValues)) {
    if (!Object.hasOwn(parsedFunctions, objectId)) {
      throw new TypeError(
        `resources.uiValues key ${JSON.stringify(objectId)} has no function namespace.`,
      );
    }
  }
  for (const objectId of Object.keys(parsedFunctions)) {
    if (!Object.hasOwn(parsedUiValues, objectId)) {
      throw new TypeError(
        `resources.functions key ${JSON.stringify(objectId)} has no replay UI value.`,
      );
    }
  }
  return Object.freeze({
    files: parsedFiles,
    modelNotifications,
    functions: parsedFunctions,
    uiValues: parsedUiValues,
  });
}

function projectionUiObjectId(
  value: JsonValue | undefined,
  path: string,
  ownerCellId: string,
  projectionSha256: string,
): string {
  const objectId = nonEmptyString(value, path);
  const ownerPrefix = `${ownerCellId}-projection-${projectionSha256}-ui-`;
  if (!objectId.startsWith(ownerPrefix) || objectId.length === ownerPrefix.length) {
    throw new TypeError(
      `${path} must be a projection-scoped UI object owned by ${JSON.stringify(ownerCellId)}.`,
    );
  }
  return objectId;
}

function parseModelLifecycle(
  value: JsonValue | undefined,
  index: number,
  files: Readonly<Record<string, string>>,
  projectionSha256: string,
): MarimoModelLifecycleNotification {
  const path = `resources.modelNotifications[${index}]`;
  const notification = strictRecord(value, path, ["message", "model_id", "op"]);
  if (notification.op !== "model-lifecycle") {
    throw new TypeError(`${path}.op must be "model-lifecycle".`);
  }
  const modelId = nonEmptyString(notification.model_id, `${path}.model_id`);
  const modelPrefix = `projection-${projectionSha256}-model-`;
  const modelIndex = modelId.slice(modelPrefix.length);
  if (!modelId.startsWith(modelPrefix) || !/^(?:0|[1-9][0-9]*)$/u.test(modelIndex)) {
    throw new TypeError(`${path}.model_id must belong to the snapshot projection.`);
  }
  const message = record(notification.message, `${path}.message`);
  const method = message.method;
  if (method === "open" || method === "update") {
    exactFields(
      message,
      ["buffer_paths", "buffers", "esm_spec", "method", "state"],
      `${path}.message`,
    );
    const state = portableJsonObject(message.state, `${path}.message.state`);
    const paths = bufferPaths(message.buffer_paths, `${path}.message.buffer_paths`);
    const buffers = stringArray(message.buffers, `${path}.message.buffers`);
    if (paths.length !== buffers.length) {
      throw new TypeError(`${path}.message buffer paths and buffers must have equal length.`);
    }
    const esmSpec = parseEsmSpec(message.esm_spec, `${path}.message.esm_spec`, files);
    return Object.freeze({
      op: "model-lifecycle",
      model_id: modelId,
      message: Object.freeze({
        method,
        state,
        buffer_paths: paths,
        buffers,
        esm_spec: esmSpec,
      }),
    });
  } else if (method === "custom") {
    exactFields(message, ["buffers", "content", "method"], `${path}.message`);
    return Object.freeze({
      op: "model-lifecycle",
      model_id: modelId,
      message: Object.freeze({
        method: "custom",
        content: portableJsonValue(message.content, `${path}.message.content`),
        buffers: stringArray(message.buffers, `${path}.message.buffers`),
      }),
    });
  } else if (method === "close") {
    exactFields(message, ["method"], `${path}.message`);
    return Object.freeze({
      op: "model-lifecycle",
      model_id: modelId,
      message: Object.freeze({ method: "close" }),
    });
  } else {
    throw new TypeError(`${path}.message.method is invalid.`);
  }
}

function bufferPaths(value: JsonValue | undefined, path: string): readonly MarimoBufferPath[] {
  if (!Array.isArray(value)) throw new TypeError(`${path} must be an array.`);
  return Object.freeze(
    value.map((item, index) => {
      if (!Array.isArray(item) || item.length === 0) {
        throw new TypeError(`${path}[${index}] must be a non-empty array.`);
      }
      const parsed = item.map((token) => {
        if (isJsonString(token)) return token;
        if (isJsonNumber(token) && Number.isSafeInteger(token) && token >= 0) return token;
        throw new TypeError(`${path}[${index}] contains an invalid token.`);
      });
      // SAFETY: The nonempty array check above establishes MarimoBufferPath's tuple head.
      return Object.freeze(parsed) as MarimoBufferPath;
    }),
  );
}

function parseEsmSpec(
  value: JsonValue | undefined,
  path: string,
  files: Readonly<Record<string, string>>,
): MarimoEsmSpec | null {
  if (value === null) return null;
  const spec = strictRecord(value, path, ["hash", "url"]);
  const hash = nonEmptyString(spec.hash, `${path}.hash`);
  const url = nonEmptyString(spec.url, `${path}.url`);
  const embeddedKey = url.startsWith("./@file/") ? url.slice(1) : url;
  if (Object.hasOwn(files, embeddedKey) || url.startsWith("data:")) {
    return Object.freeze({ hash, url });
  }
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch (error) {
    throw new TypeError(`${path}.url references an unavailable resource.`, { cause: error });
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new TypeError(`${path}.url uses an incompatible protocol.`);
  }
  return Object.freeze({ hash, url });
}

function stringArray(value: JsonValue | undefined, path: string): readonly string[] {
  if (!Array.isArray(value) || value.some((item) => !isJsonString(item))) {
    throw new TypeError(`${path} must be an array of strings.`);
  }
  return Object.freeze([...value]);
}

function strictRecord(
  value: JsonValue | undefined,
  path: string,
  fields: readonly string[],
): JsonObject {
  const parsed = record(value, path);
  exactFields(parsed, fields, path);
  return parsed;
}

function exactFields(value: JsonObject, fields: readonly string[], path: string): void {
  const expected = new Set(fields);
  const missing = fields.filter((field) => !Object.hasOwn(value, field));
  const extra = Object.keys(value).filter((field) => !expected.has(field));
  if (missing.length > 0) throw new TypeError(`${path} is missing fields: ${missing.join(", ")}.`);
  if (extra.length > 0) throw new TypeError(`${path} has unknown fields: ${extra.join(", ")}.`);
}

function record(value: JsonValue | undefined, path: string): JsonObject {
  if (!isJsonObject(value)) {
    throw new TypeError(`${path} must be an object.`);
  }
  return value;
}

function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return isRecordValue(value);
}

function nonEmptyString(value: JsonValue | undefined, path: string): string {
  if (!isJsonString(value) || value.length === 0) {
    throw new TypeError(`${path} must be a non-empty string.`);
  }
  return value;
}

function digest(value: JsonValue | undefined, path: string): string {
  const parsed = nonEmptyString(value, path);
  if (!SHA256.test(parsed)) throw new TypeError(`${path} must be a lowercase SHA-256 digest.`);
  return parsed;
}

function dataUrlString(value: JsonValue | undefined, path: string): string {
  const dataUrl = nonEmptyString(value, path);
  if (!dataUrl.startsWith("data:")) throw new TypeError(`${path} must contain a data URL.`);
  return dataUrl;
}

function cellChannel(value: JsonValue | undefined, path: string): MarimoCellChannel {
  const channel = nonEmptyString(value, path);
  if (!CELL_CHANNELS.has(channel)) throw new TypeError(`${path} is not a Marimo cell channel.`);
  // SAFETY: CELL_CHANNELS contains every MarimoCellChannel literal and membership passed.
  return channel as MarimoCellChannel;
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((byte, index) => byte === right[index]);
}
