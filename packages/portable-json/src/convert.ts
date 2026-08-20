import type { JsonObject, JsonValue, UnparsedJsonValue } from "./types.js";
import { MAX_JSON_DEPTH, MAX_JSON_VALUES } from "./types.js";

const MAX_DIAGNOSTIC_PATH = 512;
const TRUNCATED_PATH = "...";

interface ConversionState {
  readonly active: WeakSet<object>;
  count: number;
}

/** Return a detached portable JSON value. */
export function portableJsonValue(input: UnparsedJsonValue, path = "value"): JsonValue {
  if (typeof path !== "string") throw new TypeError("JSON path must be a string.");
  return convertValue(input, path, 0, { active: new WeakSet(), count: 0 });
}

/** Return a detached portable JSON object. */
export function portableJsonObject(input: UnparsedJsonValue, path = "value"): JsonObject {
  const value = portableJsonValue(input, path);
  if (Array.isArray(value) || value === null || typeof value !== "object") {
    throw new TypeError(`${path} must be an object.`);
  }
  return value as JsonObject;
}

function convertValue(
  input: UnparsedJsonValue,
  path: string,
  depth: number,
  state: ConversionState,
): JsonValue {
  if (depth > MAX_JSON_DEPTH) {
    throw new TypeError(`${path} exceeds the maximum JSON nesting depth.`);
  }
  countValue(state, path);
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "string") return unicodeScalar(input, path);
  if (typeof input === "number") return portableNumber(input, path);
  if (Array.isArray(input)) return convertArray(input, path, depth, state);
  if (!isJsonObject(input)) {
    throw new TypeError(`${path} must be JSON-compatible.`);
  }
  return convertObject(input, path, depth, state);
}

function convertArray(
  input: readonly UnparsedJsonValue[],
  path: string,
  depth: number,
  state: ConversionState,
): readonly JsonValue[] {
  enterContainer(input, path, state);
  try {
    if (input.length > MAX_JSON_VALUES - state.count) {
      throw new TypeError(`${path} exceeds the maximum JSON value count.`);
    }
    const values: JsonValue[] = [];
    for (let index = 0; index < input.length; index += 1) {
      if (!Object.hasOwn(input, index)) {
        throw new TypeError(`${childPath(path, `[${index}]`)} must be present.`);
      }
      values.push(convertValue(input[index], childPath(path, `[${index}]`), depth + 1, state));
    }
    return Object.freeze(values);
  } finally {
    state.active.delete(input);
  }
}

function convertObject(
  input: Readonly<Record<string, UnparsedJsonValue>>,
  path: string,
  depth: number,
  state: ConversionState,
): JsonObject {
  enterContainer(input, path, state);
  try {
    return Object.freeze(
      Object.fromEntries(
        Object.entries(input).map(([key, item]) => {
          const keyPath = childPath(path, "<object key>");
          countValue(state, keyPath);
          unicodeScalar(key, keyPath);
          return [key, convertValue(item, childPath(path, key), depth + 1, state)];
        }),
      ),
    );
  } finally {
    state.active.delete(input);
  }
}

function enterContainer(value: object, path: string, state: ConversionState): void {
  if (state.active.has(value)) throw new TypeError(`${path} contains a cyclic container.`);
  state.active.add(value);
}

function countValue(state: ConversionState, path: string): void {
  state.count += 1;
  if (state.count > MAX_JSON_VALUES) {
    throw new TypeError(`${path} exceeds the maximum JSON value count.`);
  }
}

function portableNumber(value: number, path: string): number {
  if (!Number.isFinite(value)) throw new TypeError(`${path} must not contain NaN or infinity.`);
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    throw new TypeError(`${path} integer must be within the JavaScript safe range.`);
  }
  return Object.is(value, -0) ? 0 : value;
}

function unicodeScalar(value: string, path: string): string {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        index += 1;
        continue;
      }
      throw new TypeError(`${path} must contain Unicode scalar values.`);
    }
    if (code >= 0xdc00 && code <= 0xdfff) {
      throw new TypeError(`${path} must contain Unicode scalar values.`);
    }
  }
  return value;
}

function isJsonObject(value: unknown): value is Readonly<Record<string, UnparsedJsonValue>> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.prototype.toString.call(value) === "[object Object]"
  );
}

function childPath(path: string, segment: string): string {
  if (path === TRUNCATED_PATH || path.length >= MAX_DIAGNOSTIC_PATH) return TRUNCATED_PATH;
  const remaining = MAX_DIAGNOSTIC_PATH - path.length - 1;
  const shown = boundedSegment(segment, remaining);
  return shown.length === 0 ? TRUNCATED_PATH : `${path}.${shown}`;
}

function boundedSegment(value: string, maximumLength: number): string {
  if (maximumLength < 2) return "";
  const quoted = JSON.stringify(value);
  if (quoted.length <= maximumLength) return quoted;
  if (maximumLength < 5) return '""';
  return `${quoted.slice(0, maximumLength - 4)}..."`;
}
