export const MAX_JSON_DEPTH = 256;
export const MAX_JSON_VALUES = 100_000;

export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export type UnparsedJsonValue = unknown;
