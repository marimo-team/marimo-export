export const ANYWIDGET_SCHEMA = "marimo-export.anywidget.v1";

export type SnapshotState = Record<string, unknown>;

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

type PathToken = string | number;

const BASE64 = /^(?:[A-Za-z\d+/]{4})*(?:[A-Za-z\d+/]{2}==|[A-Za-z\d+/]{3}=)?$/;
const UNSAFE_PATH_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export function parseAnyWidgetPayload(value: unknown): AnyWidgetSnapshot {
  const payload = record(value, "AnyWidget payload");
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
    const unrelated = [...models.keys()].filter((id) => !reachable.has(id));
    throw new TypeError(
      `AnyWidget payload contains models outside the root closure: ${unrelated
        .map((id) => JSON.stringify(id))
        .join(", ")}.`,
    );
  }

  return Object.freeze({
    rootModelId,
    files,
    models,
  });
}

export function cloneModelState<T extends Record<string, unknown>>(state: T): T {
  return structuredClone(state);
}

export function readonlyModelState<T extends Record<string, unknown>>(state: T): Readonly<T> {
  return deepFreeze(cloneModelState(state));
}

function parseFiles(value: unknown): Readonly<Record<string, string>> {
  const input = record(value, "files");
  const files: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const [path, dataUrl] of Object.entries(input)) {
    if (path.length === 0) {
      throw new TypeError("AnyWidget file paths must be non-empty strings.");
    }
    if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:")) {
      throw new TypeError(`AnyWidget file ${JSON.stringify(path)} must contain a data URL.`);
    }
    validateDataUrl(dataUrl, `AnyWidget file ${JSON.stringify(path)}`);
    files[path] = dataUrl;
  }
  return Object.freeze(files);
}

function parseEsmSpec(
  value: unknown,
  files: Readonly<Record<string, string>>,
  modelId: string,
): EsmSpec | undefined {
  if (value === null) return undefined;
  const spec = record(value, `AnyWidget model ${JSON.stringify(modelId)} ESM spec`);
  exactKeys(spec, ["url", "hash"], `AnyWidget model ${JSON.stringify(modelId)} ESM spec`);
  const url = nonEmptyString(spec.url, `AnyWidget model ${JSON.stringify(modelId)} ESM URL`);
  const hash = nonEmptyString(spec.hash, `AnyWidget model ${JSON.stringify(modelId)} ESM hash`);
  if (!Object.hasOwn(files, url)) {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch (error) {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(modelId)} references missing virtual file ${JSON.stringify(url)}.`,
        { cause: error },
      );
    }
    if (
      parsed.protocol !== "data:" &&
      parsed.protocol !== "http:" &&
      parsed.protocol !== "https:"
    ) {
      throw new TypeError(
        `AnyWidget model ${JSON.stringify(modelId)} uses incompatible ESM URL protocol ${JSON.stringify(parsed.protocol)}.`,
      );
    }
    if (parsed.protocol === "data:") {
      validateDataUrl(url, `AnyWidget model ${JSON.stringify(modelId)} ESM URL`);
    }
  }
  return Object.freeze({ url, hash });
}

function parseBufferPaths(value: unknown, modelId: string): readonly (readonly PathToken[])[] {
  const input = array(value, `AnyWidget model ${JSON.stringify(modelId)} buffer_paths`);
  const seen = new Set<string>();
  return Object.freeze(
    input.map((value, index) => {
      const path = array(value, `AnyWidget model ${JSON.stringify(modelId)} buffer path ${index}`);
      if (path.length === 0) {
        throw new TypeError(`AnyWidget model ${JSON.stringify(modelId)} has an empty buffer path.`);
      }
      const parsed = path.map((token) => {
        if (typeof token === "number" && Number.isSafeInteger(token) && token >= 0) return token;
        if (typeof token === "string" && !UNSAFE_PATH_KEYS.has(token)) return token;
        throw new TypeError(
          `AnyWidget model ${JSON.stringify(modelId)} has an invalid buffer path token.`,
        );
      });
      const identity = JSON.stringify(parsed);
      if (seen.has(identity)) {
        throw new TypeError(
          `AnyWidget model ${JSON.stringify(modelId)} repeats buffer path ${identity}.`,
        );
      }
      seen.add(identity);
      return Object.freeze(parsed);
    }),
  );
}

function parseBuffers(value: unknown, modelId: string): readonly DataView[] {
  return Object.freeze(
    array(value, `AnyWidget model ${JSON.stringify(modelId)} buffers`).map((buffer, index) => {
      if (typeof buffer !== "string" || !BASE64.test(buffer)) {
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
  const comma = value.indexOf(",");
  if (comma === -1) throw new TypeError(`${path} is a malformed data URL.`);
  const metadata = value.slice(5, comma).toLowerCase().split(";");
  const body = value.slice(comma + 1);
  if (metadata.includes("base64")) {
    if (!BASE64.test(body)) throw new TypeError(`${path} contains malformed base64 data.`);
    return;
  }
  try {
    decodeURIComponent(body);
  } catch (error) {
    throw new TypeError(`${path} contains malformed percent-encoded data.`, { cause: error });
  }
}

function setBuffer(
  state: SnapshotState,
  path: readonly PathToken[],
  buffer: DataView,
  modelId: string,
) {
  let target: unknown = state;
  for (const token of path.slice(0, -1)) {
    target = ownChild(target, token, modelId, path);
  }
  const finalToken = path.at(-1)!;
  if (typeof finalToken === "number") {
    if (!Array.isArray(target) || finalToken >= target.length) invalidBufferPath(modelId, path);
    target[finalToken] = buffer as never;
    return;
  }
  if (!isRecord(target)) invalidBufferPath(modelId, path);
  target[finalToken] = buffer;
}

function ownChild(
  target: unknown,
  token: PathToken,
  modelId: string,
  path: readonly PathToken[],
): unknown {
  if (typeof token === "number") {
    if (!Array.isArray(target) || token >= target.length) invalidBufferPath(modelId, path);
    return target[token];
  }
  if (!isRecord(target) || !Object.hasOwn(target, token)) invalidBufferPath(modelId, path);
  return target[token];
}

function invalidBufferPath(modelId: string, path: readonly PathToken[]): never {
  throw new TypeError(
    `AnyWidget model ${JSON.stringify(modelId)} buffer path ${JSON.stringify(path)} does not target existing state.`,
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
      throw new TypeError(`AnyWidget model reference ${JSON.stringify(id)} is unresolved.`);
    }
    reachable.add(id);
    for (const reference of findWidgetReferences(model.state)) pending.push(reference);
  }
  return reachable;
}

function findWidgetReferences(value: unknown): readonly string[] {
  const references: string[] = [];
  const visit = (value: unknown): void => {
    if (typeof value === "string") {
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
    if (isRecord(value) && !(value instanceof DataView)) {
      for (const child of Object.values(value)) visit(child);
    }
  };
  visit(value);
  return references;
}

function cloneJsonObject(value: Record<string, unknown>): SnapshotState {
  assertJsonValue(value, "state");
  return structuredClone(value);
}

function assertJsonValue(value: unknown, path: string): void {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((child, index) => assertJsonValue(child, `${path}[${index}]`));
    return;
  }
  if (isRecord(value)) {
    for (const [key, child] of Object.entries(value)) assertJsonValue(child, `${path}.${key}`);
    return;
  }
  throw new TypeError(`${path} must contain JSON values.`);
}

function deepFreeze<T>(value: T): T {
  if (ArrayBuffer.isView(value)) return value;
  if (Array.isArray(value)) {
    for (const child of value) deepFreeze(child);
    return Object.freeze(value);
  }
  if (isRecord(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  }
  return value;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  path: string,
): void {
  const allowed = new Set(expected);
  const unexpected = Object.keys(value).filter((key) => !allowed.has(key));
  const missing = expected.filter((key) => !Object.hasOwn(value, key));
  if (unexpected.length > 0 || missing.length > 0) {
    throw new TypeError(
      `${path} fields are invalid. Missing: ${missing.join(", ") || "none"}. Unexpected: ${unexpected.join(", ") || "none"}.`,
    );
  }
}

function nonEmptyString(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${path} must be a non-empty string.`);
  }
  return value;
}

function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new TypeError(`${path} must be an array.`);
  return value;
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) throw new TypeError(`${path} must be an object.`);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
