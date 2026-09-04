import type { JsonObject, JsonValue } from "./types.js";
import { MAX_JSON_DEPTH, MAX_JSON_VALUES } from "./types.js";

const MAX_DIAGNOSTIC_PATH = 512;
const TRUNCATED_PATH = "...";

interface ConversionState {
  readonly active: WeakSet<object>;
  count: number;
}

/** Return a detached portable JSON value. */
export function portableJsonValue<Input>(input: Input, path = "value"): JsonValue {
  return convertValue(input, path, 0, { active: new WeakSet(), count: 0 });
}

/** Return a detached portable JSON object. */
export function portableJsonObject<Input>(input: Input, path = "value"): JsonObject {
  const value = portableJsonValue(input, path);
  if (!isJsonObject(value)) {
    throw new TypeError(`${path} must be an object.`);
  }
  return value;
}

function convertValue<Input>(
  input: Input,
  path: string,
  depth: number,
  state: ConversionState,
): JsonValue {
  if (depth > MAX_JSON_DEPTH) {
    throw new TypeError(`${path} exceeds the maximum JSON nesting depth.`);
  }
  countValue(state, path);
  if (input === null) return null;
  if (isBoolean(input)) return input;
  if (isString(input)) return unicodeScalar(input, path);
  if (isNumber(input)) return portableNumber(input, path);
  if (Array.isArray(input)) return convertArray(input, path, depth, state);
  if (!isJsonObject(input)) {
    throw new TypeError(`${path} must be JSON-compatible.`);
  }
  return convertObject(input, path, depth, state);
}

function convertArray<Input>(
  input: readonly Input[],
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

function convertObject<Input extends object>(
  input: Input,
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

function enterContainer<Value extends object>(
  value: Value,
  path: string,
  state: ConversionState,
): void {
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

function isBoolean<Value>(value: Value): value is Value & boolean {
  return isPrimitiveWithPrototype(value, Boolean.prototype);
}

function isString<Value>(value: Value): value is Value & string {
  return isPrimitiveWithPrototype(value, String.prototype);
}

function isNumber<Value>(value: Value): value is Value & number {
  return isPrimitiveWithPrototype(value, Number.prototype);
}

function isPrimitiveWithPrototype<Value, Prototype>(value: Value, prototype: Prototype): boolean {
  const boxed = Object(value);
  return boxed !== value && Object.getPrototypeOf(boxed) === prototype;
}

function isJsonObject<Value>(value: Value): value is Value & JsonObject {
  return (
    value !== null &&
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
