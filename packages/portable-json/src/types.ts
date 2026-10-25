export const MAX_JSON_DEPTH = 256;
export const MAX_JSON_VALUES = 100_000;

export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export function jsonValueLimit(input: number | undefined): number {
  if (input === undefined) return MAX_JSON_VALUES;
  if (!Number.isSafeInteger(input) || input <= 0) {
    throw new TypeError("JSON value limits must be positive safe integers.");
  }
  return input;
}
