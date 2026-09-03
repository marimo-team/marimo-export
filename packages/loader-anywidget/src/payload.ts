import type { ModelState, ModelValue } from "./runtime/model.js";
import {
  isBooleanValue,
  isNumberValue,
  isRecordValue,
  isStringValue,
} from "./runtime/value-types.js";

export const ANYWIDGET_SCHEMA = "marimo-export.anywidget.v1";

export type SnapshotState = ModelState;

export interface ParsedDataUrl {
  readonly body: string;
  readonly isBase64: boolean;
  readonly mediaType: string;
}

export interface EsmSpec {
  readonly url: string;
  readonly hash: string;
}

export interface ModelSnapshot {
  readonly id: string;
  readonly state: SnapshotState;
  readonly esmSpec: EsmSpec | undefined;
}

export interface AnyWidgetSnapshot {
  readonly rootModelId: string;
  readonly files: Readonly<Record<string, string>>;
  readonly models: ReadonlyMap<string, ModelSnapshot>;
}

export function embeddedFileKey(url: string): string {
  return url.startsWith("./@file/") ? url.slice(1) : url;
}

type PathToken = string | number;

const UNSAFE_PATH_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const MAX_DIAGNOSTIC_LENGTH = 2_048;
const MAX_UNEXPECTED_FIELDS = 8;
const MAX_DIAGNOSTIC_FIELD_LENGTH = 128;
const MAX_DATA_URL_MEDIA_TYPE_BYTES = 1_024;
const MAX_EXTERNAL_ESM_URL_BYTES = 8_192;

export function parseAnyWidgetPayload<Value>(value: Value): AnyWidgetSnapshot {
  const payload = record(parseSnapshotValue(value), "AnyWidget payload");
  exactKeys(payload, ["schema", "rootModelId", "files", "modelNotifications"], "payload");
  if (payload.schema !== ANYWIDGET_SCHEMA) {
    throw new TypeError(`AnyWidget payload schema must be ${JSON.stringify(ANYWIDGET_SCHEMA)}.`);
  }

  const rootModelId = nonEmptyString(payload.rootModelId, "rootModelId");
  if (rootModelId !== "model-0") {
    throw new TypeError('AnyWidget payload rootModelId must be "model-0".');
  }
  const files = parseFiles(payload.files);
  const notifications = array(payload.modelNotifications, "modelNotifications");
  const models = new Map<string, ModelSnapshot>();

  for (const [index, value] of notifications.entries()) {
    const notification = record(value, `modelNotifications[${index}]`);
    exactKeys(notification, ["op", "model_id", "message"], `modelNotifications[${index}]`);
    if (notification.op !== "model-lifecycle") {
      throw new TypeError(`modelNotifications[${index}].op must be "model-lifecycle".`);
    }
    const id = nonEmptyString(notification.model_id, `modelNotifications[${index}].model_id`);
    const expectedId = `model-${index}`;
    if (id !== expectedId) {
      throw new TypeError(
        `modelNotifications[${index}].model_id must be ${JSON.stringify(expectedId)}.`,
      );
    }
    const message = record(notification.message, `modelNotifications[${index}].message`);
    exactKeys(
      message,
      ["method", "state", "buffer_paths", "buffers", "esm_spec"],
      `modelNotifications[${index}].message`,
    );
    if (message.method !== "open") {
      throw new TypeError(`AnyWidget model ${JSON.stringify(id)} must contain an open message.`);
    }

    const state = cloneJsonObject(
      record(message.state, `AnyWidget model ${JSON.stringify(id)} state`),
    );
    const bufferPaths = parseBufferPaths(message.buffer_paths, id);
    const buffers = parseBuffers(message.buffers, id);
    if (bufferPaths.length !== buffers.length) {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(id)} has ${bufferPaths.length} buffer paths and ${buffers.length} buffers.`,
      );
    }
    for (const [bufferIndex, path] of bufferPaths.entries()) {
      setBuffer(state, path, buffers[bufferIndex]!, id);
    }

    const esmSpec = parseEsmSpec(message.esm_spec, files, id);
    models.set(id, Object.freeze({ id, state, esmSpec }));
  }

  const root = models.get(rootModelId);
  if (root === undefined) {
    throw new TypeError(`AnyWidget root model ${JSON.stringify(rootModelId)} is missing.`);
  }
  if (root.esmSpec === undefined) {
    throw new TypeError(`AnyWidget root model ${JSON.stringify(rootModelId)} has no ESM spec.`);
  }

  const reachable = collectReachableModels(rootModelId, models);
  if (reachable.size !== models.size) {
    const unrelated: string[] = [];
    let unrelatedCount = 0;
    for (const id of models.keys()) {
      if (reachable.has(id)) continue;
      unrelatedCount += 1;
      if (unrelated.length < MAX_UNEXPECTED_FIELDS) unrelated.push(id);
    }
    throw new TypeError(
      truncateDiagnostic(
        `AnyWidget payload contains models outside the root closure: ${renderUnexpectedFields(unrelated, unrelatedCount)}.`,
      ),
    );
  }

  return Object.freeze({
    rootModelId,
    files,
    models,
  });
}

export function cloneModelState<T extends ModelState>(state: T): T {
  return structuredClone(state);
}

export function readonlyModelState<T extends ModelState>(state: T): Readonly<T> {
  return deepFreeze(cloneModelState(state));
}

export function parseDataUrl(value: string, path: string): ParsedDataUrl {
  const comma = value.indexOf(",");
  if (comma === -1) throw new TypeError(`${path} is a malformed data URL.`);
  const mediaTypeEnd = dataUrlMediaTypeEnd(value, comma, path);
  return {
    body: value.slice(comma + 1),
    isBase64: hasBase64Parameter(value, mediaTypeEnd + 1, comma),
    mediaType: value.slice(5, mediaTypeEnd) || "text/plain",
  };
}

function parseFiles(value: ModelValue | undefined): Readonly<Record<string, string>> {
  const input = record(value, "files");
  const files: Record<string, string> = Object.create(null);
  for (const [path, dataUrl] of Object.entries(input)) {
    if (path.length === 0) {
      throw new TypeError("AnyWidget file paths must be non-empty strings.");
    }
    const fileLabel = `AnyWidget file ${quoteField(path)}`;
    if (!isStringValue(dataUrl) || !dataUrl.startsWith("data:")) {
      throw new TypeError(`${fileLabel} must contain a data URL.`);
    }
    validateDataUrl(dataUrl, fileLabel);
    files[path] = dataUrl;
  }
  return Object.freeze(files);
}

function parseEsmSpec(
  value: ModelValue | undefined,
  files: Readonly<Record<string, string>>,
  modelId: string,
): EsmSpec | undefined {
  if (value === null) return undefined;
  const spec = record(value, `AnyWidget model ${JSON.stringify(modelId)} ESM spec`);
  exactKeys(spec, ["url", "hash"], `AnyWidget model ${JSON.stringify(modelId)} ESM spec`);
  const url = nonEmptyString(spec.url, `AnyWidget model ${JSON.stringify(modelId)} ESM URL`);
  const hash = nonEmptyString(spec.hash, `AnyWidget model ${JSON.stringify(modelId)} ESM hash`);
  if (!Object.hasOwn(files, embeddedFileKey(url))) {
    if (hasDataUrlPrefix(url)) {
      validateDataUrl(url, `AnyWidget model ${JSON.stringify(modelId)} ESM URL`);
      return Object.freeze({ url, hash });
    }
    if (!hasUtf8ByteLengthAtMost(url, MAX_EXTERNAL_ESM_URL_BYTES)) {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(modelId)} contains an invalid ESM URL ${quoteField(url)}.`,
      );
    }
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch (error) {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(modelId)} references missing virtual file ${quoteField(url)}.`,
        { cause: error },
      );
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(modelId)} uses incompatible ESM URL protocol ${quoteField(parsed.protocol)}.`,
      );
    }
  }
  return Object.freeze({ url, hash });
}

function parseBufferPaths(
  value: ModelValue | undefined,
  modelId: string,
): readonly (readonly PathToken[])[] {
  const input = array(value, `AnyWidget model ${JSON.stringify(modelId)} buffer_paths`);
  const seen = new Set<string>();
  return Object.freeze(
    input.map((value, index) => {
      const path = array(value, `AnyWidget model ${JSON.stringify(modelId)} buffer path ${index}`);
      if (path.length === 0) {
        throw new TypeError(`AnyWidget model ${JSON.stringify(modelId)} has an empty buffer path.`);
      }
      const parsed = path.map((token) => {
        if (isNumberValue(token) && Number.isSafeInteger(token) && token >= 0) return token;
        if (isStringValue(token) && !UNSAFE_PATH_KEYS.has(token)) return token;
        throw new TypeError(
          `AnyWidget model ${JSON.stringify(modelId)} has an invalid buffer path token.`,
        );
      });
      const identity = JSON.stringify(parsed);
      if (seen.has(identity)) {
        throw new TypeError(
          `AnyWidget model ${JSON.stringify(modelId)} repeats buffer path ${quoteField(identity)}.`,
        );
      }
      seen.add(identity);
      return Object.freeze(parsed);
    }),
  );
}

function parseBuffers(value: ModelValue | undefined, modelId: string): readonly DataView[] {
  return Object.freeze(
    array(value, `AnyWidget model ${JSON.stringify(modelId)} buffers`).map((buffer, index) => {
      if (!isStringValue(buffer) || !isCanonicalBase64(buffer)) {
        throw new TypeError(
          `AnyWidget model ${JSON.stringify(modelId)} buffer ${index} is not canonical base64.`,
        );
      }
      return base64ToDataView(buffer);
    }),
  );
}

function base64ToDataView(value: string): DataView {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new DataView(bytes.buffer);
}

function validateDataUrl(value: string, path: string): void {
  const { body, isBase64 } = parseDataUrl(value, path);
  if (isBase64) {
    if (!isCanonicalBase64(body)) {
      throw new TypeError(`${path} contains malformed base64 data.`);
    }
    return;
  }
  if (!isValidPercentData(body)) {
    throw new TypeError(`${path} contains malformed percent-encoded data.`);
  }
}

function dataUrlMediaTypeEnd(value: string, comma: number, path: string): number {
  let byteLength = 0;
  for (let index = 5; index < comma; index += 1) {
    const codePoint = value.codePointAt(index)!;
    if (codePoint === 0x3b) return index;
    byteLength += utf8CodePointBytes(codePoint);
    if (byteLength > MAX_DATA_URL_MEDIA_TYPE_BYTES) {
      throw new TypeError(
        `${path} data URL media type exceeds ${MAX_DATA_URL_MEDIA_TYPE_BYTES} UTF-8 bytes.`,
      );
    }
    if (codePoint > 0xffff) index += 1;
  }
  return comma;
}

function hasBase64Parameter(value: string, start: number, end: number): boolean {
  let segmentStart = start;
  for (let index = start; index <= end; index += 1) {
    if (index < end && value.charCodeAt(index) !== 0x3b) continue;
    if (isBase64Parameter(value, segmentStart, index)) return true;
    segmentStart = index + 1;
  }
  return false;
}

function isBase64Parameter(value: string, start: number, end: number): boolean {
  return (
    end - start === 6 &&
    (value.charCodeAt(start) | 0x20) === 0x62 &&
    (value.charCodeAt(start + 1) | 0x20) === 0x61 &&
    (value.charCodeAt(start + 2) | 0x20) === 0x73 &&
    (value.charCodeAt(start + 3) | 0x20) === 0x65 &&
    value.charCodeAt(start + 4) === 0x36 &&
    value.charCodeAt(start + 5) === 0x34
  );
}

function isCanonicalBase64(value: string): boolean {
  if (value.length % 4 !== 0) return false;
  let contentEnd = value.length;
  if (contentEnd > 0 && value.charCodeAt(contentEnd - 1) === 0x3d) {
    contentEnd -= 1;
    if (contentEnd > 0 && value.charCodeAt(contentEnd - 1) === 0x3d) contentEnd -= 1;
  }
  for (let index = 0; index < contentEnd; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (
      (codeUnit < 0x41 || codeUnit > 0x5a) &&
      (codeUnit < 0x61 || codeUnit > 0x7a) &&
      (codeUnit < 0x30 || codeUnit > 0x39) &&
      codeUnit !== 0x2b &&
      codeUnit !== 0x2f
    ) {
      return false;
    }
  }
  const padding = value.length - contentEnd;
  if (padding > 0) {
    const finalValue = base64Value(value.charCodeAt(contentEnd - 1));
    if ((finalValue & (padding === 2 ? 0x0f : 0x03)) !== 0) return false;
  }
  return true;
}

function base64Value(codeUnit: number): number {
  if (codeUnit >= 0x41 && codeUnit <= 0x5a) return codeUnit - 0x41;
  if (codeUnit >= 0x61 && codeUnit <= 0x7a) return codeUnit - 0x61 + 26;
  if (codeUnit >= 0x30 && codeUnit <= 0x39) return codeUnit - 0x30 + 52;
  return codeUnit === 0x2b ? 62 : 63;
}

function isValidPercentData(value: string): boolean {
  if (value.indexOf("%") === -1) return true;
  let index = 0;
  let remaining = 0;
  let continuationMin = 0x80;
  let continuationMax = 0xbf;
  while (index < value.length) {
    const codePoint = value.codePointAt(index)!;
    if (codePoint > 0x7f) {
      if (remaining > 0) return false;
      index += codePoint > 0xffff ? 2 : 1;
      continue;
    }

    let byte: number;
    if (codePoint === 0x25) {
      if (index + 2 >= value.length) return false;
      const high = hexValue(value.charCodeAt(index + 1));
      const low = hexValue(value.charCodeAt(index + 2));
      if (high < 0 || low < 0) return false;
      byte = high * 16 + low;
      index += 3;
    } else {
      byte = codePoint;
      index += 1;
    }

    if (remaining > 0) {
      if (byte < continuationMin || byte > continuationMax) return false;
      remaining -= 1;
      continuationMin = 0x80;
      continuationMax = 0xbf;
      continue;
    }
    if (byte <= 0x7f) continue;
    if (byte >= 0xc2 && byte <= 0xdf) {
      remaining = 1;
    } else if (byte === 0xe0) {
      remaining = 2;
      continuationMin = 0xa0;
    } else if ((byte >= 0xe1 && byte <= 0xec) || (byte >= 0xee && byte <= 0xef)) {
      remaining = 2;
    } else if (byte === 0xed) {
      remaining = 2;
      continuationMax = 0x9f;
    } else if (byte === 0xf0) {
      remaining = 3;
      continuationMin = 0x90;
    } else if (byte >= 0xf1 && byte <= 0xf3) {
      remaining = 3;
    } else if (byte === 0xf4) {
      remaining = 3;
      continuationMax = 0x8f;
    } else {
      return false;
    }
  }
  return remaining === 0;
}

function hexValue(codeUnit: number): number {
  if (codeUnit >= 0x30 && codeUnit <= 0x39) return codeUnit - 0x30;
  if (codeUnit >= 0x41 && codeUnit <= 0x46) return codeUnit - 0x37;
  if (codeUnit >= 0x61 && codeUnit <= 0x66) return codeUnit - 0x57;
  return -1;
}

function hasDataUrlPrefix(value: string): boolean {
  return (
    value.length >= 5 &&
    (value.charCodeAt(0) | 0x20) === 0x64 &&
    (value.charCodeAt(1) | 0x20) === 0x61 &&
    (value.charCodeAt(2) | 0x20) === 0x74 &&
    (value.charCodeAt(3) | 0x20) === 0x61 &&
    value.charCodeAt(4) === 0x3a
  );
}

function hasUtf8ByteLengthAtMost(value: string, maximum: number): boolean {
  let byteLength = 0;
  for (let index = 0; index < value.length; index += 1) {
    const codePoint = value.codePointAt(index)!;
    byteLength += utf8CodePointBytes(codePoint);
    if (byteLength > maximum) return false;
    if (codePoint > 0xffff) index += 1;
  }
  return true;
}

function utf8CodePointBytes(codePoint: number): number {
  if (codePoint <= 0x7f) return 1;
  if (codePoint <= 0x7ff) return 2;
  if (codePoint <= 0xffff) return 3;
  return 4;
}

function setBuffer(
  state: SnapshotState,
  path: readonly PathToken[],
  buffer: DataView,
  modelId: string,
) {
  let target: ModelValue = state;
  for (const token of path.slice(0, -1)) {
    target = ownChild(target, token, modelId, path);
  }
  const finalToken = path.at(-1)!;
  if (isNumberValue(finalToken)) {
    if (
      !Array.isArray(target) ||
      finalToken >= target.length ||
      !Object.hasOwn(target, finalToken)
    ) {
      invalidBufferPath(modelId, path);
    }
    defineBuffer(target, finalToken, buffer);
    return;
  }
  if (!isModelState(target)) invalidBufferPath(modelId, path);
  defineBuffer(target, finalToken, buffer);
}

function defineBuffer<Target extends object>(
  target: Target,
  token: PathToken,
  buffer: DataView,
): void {
  Object.defineProperty(target, token, {
    value: buffer,
    configurable: true,
    enumerable: true,
    writable: true,
  });
}

function ownChild(
  target: ModelValue,
  token: PathToken,
  modelId: string,
  path: readonly PathToken[],
): ModelValue {
  if (isNumberValue(token)) {
    if (!Array.isArray(target) || token >= target.length || !Object.hasOwn(target, token)) {
      invalidBufferPath(modelId, path);
    }
    const child = target[token];
    if (child === undefined) invalidBufferPath(modelId, path);
    return child;
  }
  if (!isModelState(target) || !Object.hasOwn(target, token)) invalidBufferPath(modelId, path);
  const child = target[token];
  if (child === undefined) invalidBufferPath(modelId, path);
  return child;
}

function invalidBufferPath(modelId: string, _path: readonly PathToken[]): never {
  throw new TypeError(
    `AnyWidget model ${JSON.stringify(modelId)} has a buffer path that does not target existing state.`,
  );
}

function collectReachableModels(
  rootModelId: string,
  models: ReadonlyMap<string, ModelSnapshot>,
): ReadonlySet<string> {
  const reachable = new Set<string>();
  const pending = [rootModelId];
  while (pending.length > 0) {
    const id = pending.pop()!;
    if (reachable.has(id)) continue;
    const model = models.get(id);
    if (model === undefined) {
      throw new TypeError(`AnyWidget model reference ${quoteField(id)} is unresolved.`);
    }
    reachable.add(id);
    for (const reference of findWidgetReferences(model.state)) pending.push(reference);
  }
  return reachable;
}

function findWidgetReferences(value: ModelValue): readonly string[] {
  const references: string[] = [];
  const visit = (value: ModelValue): void => {
    if (isModelString(value)) {
      for (const prefix of ["anywidget:", "IPY_MODEL_"]) {
        if (!value.startsWith(prefix)) continue;
        const id = value.slice(prefix.length);
        if (id.length === 0) {
          throw new TypeError("AnyWidget state contains an empty model reference.");
        }
        references.push(id);
        return;
      }
      return;
    }
    if (Array.isArray(value)) {
      for (const child of value) visit(child);
      return;
    }
    if (isModelState(value) && !(value instanceof DataView)) {
      for (const child of Object.values(value)) visit(child);
    }
  };
  visit(value);
  return references;
}

function cloneJsonObject(value: ModelState): SnapshotState {
  return structuredClone(value);
}

function deepFreeze<T>(value: T): T {
  if (ArrayBuffer.isView(value)) return value;
  if (Array.isArray(value)) {
    for (const child of value) deepFreeze(child);
    return Object.freeze(value);
  }
  if (isModelState(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  }
  return value;
}

function exactKeys(value: ModelState, expected: readonly string[], path: string): void {
  const allowed = new Set(expected);
  const unexpected: string[] = [];
  let unexpectedCount = 0;
  for (const key of Object.keys(value)) {
    if (allowed.has(key)) continue;
    unexpectedCount += 1;
    if (unexpected.length < MAX_UNEXPECTED_FIELDS) unexpected.push(key);
  }
  const missing = expected.filter((key) => !Object.hasOwn(value, key));
  if (unexpectedCount > 0 || missing.length > 0) {
    const missingText = missing.length === 0 ? "none" : missing.map(quoteField).join(", ");
    const unexpectedText = renderUnexpectedFields(unexpected, unexpectedCount);
    throw new TypeError(
      truncateDiagnostic(
        `${path} fields are invalid. Missing: ${missingText}. Unexpected: ${unexpectedText}.`,
      ),
    );
  }
}

function renderUnexpectedFields(fields: readonly string[], total: number): string {
  if (total === 0) return "none";
  const rendered = fields.map(quoteField).join(", ");
  const omitted = total - fields.length;
  return omitted === 0 ? rendered : `${rendered}, ... (+${omitted} more)`;
}

function quoteField(value: string): string {
  let body = "";
  const bodyLimit = MAX_DIAGNOSTIC_FIELD_LENGTH - 5;
  let truncated = false;
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    let token: string;
    if (codeUnit === 0x22 || codeUnit === 0x5c) {
      token = `\\${value[index]}`;
    } else if (
      codeUnit <= 0x1f ||
      (codeUnit >= 0x7f && codeUnit <= 0x9f) ||
      (codeUnit >= 0xd800 && codeUnit <= 0xdfff)
    ) {
      if (
        codeUnit >= 0xd800 &&
        codeUnit <= 0xdbff &&
        index + 1 < value.length &&
        value.charCodeAt(index + 1) >= 0xdc00 &&
        value.charCodeAt(index + 1) <= 0xdfff
      ) {
        token = value.slice(index, index + 2);
        index += 1;
      } else {
        token = `\\u${codeUnit.toString(16).padStart(4, "0")}`;
      }
    } else {
      token = value[index]!;
    }
    if (body.length + token.length > bodyLimit) {
      truncated = true;
      break;
    }
    body += token;
  }
  return `"${body}${truncated ? "..." : ""}"`;
}

function truncateDiagnostic(value: string): string {
  if (value.length <= MAX_DIAGNOSTIC_LENGTH) return value;
  return `${value.slice(0, MAX_DIAGNOSTIC_LENGTH - 3)}...`;
}

function nonEmptyString(value: ModelValue | undefined, path: string): string {
  if (!isStringValue(value) || value.length === 0) {
    throw new TypeError(`${path} must be a non-empty string.`);
  }
  return value;
}

function array(value: ModelValue | undefined, path: string): readonly ModelValue[] {
  if (!Array.isArray(value)) throw new TypeError(`${path} must be an array.`);
  return value;
}

function record(value: ModelValue | undefined, path: string): ModelState {
  if (!isModelState(value)) throw new TypeError(`${path} must be an object.`);
  return value;
}

function isModelState<Value>(value: Value): value is Value & ModelState {
  return isRecordValue(value) && !(value instanceof DataView);
}

function isModelString(value: ModelValue | undefined): value is string {
  return isStringValue(value);
}

function parseSnapshotValue<Value>(value: Value): ModelValue {
  if (value === null) return null;
  if (isBooleanValue(value) || isNumberValue(value) || isStringValue(value)) return value;
  if (Array.isArray(value)) return value.map(parseSnapshotValue);
  if (!isRecordValue(value)) throw new TypeError("AnyWidget payload must contain JSON values.");
  const parsed: ModelState = {};
  for (const [key, child] of Object.entries(value)) parsed[key] = parseSnapshotValue(child);
  return parsed;
}
