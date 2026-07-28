export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export interface ReadOptions {
  readonly signal?: AbortSignal;
  /** Maximum decoded projection size in bytes. */
  readonly maxBytes?: number;
  /** Maximum containers, scalar values, and object keys in projected JSON. */
  readonly maxJsonValues?: number;
}

export type PublicationErrorCode =
  | "asset_invalid"
  | "decode_failed"
  | "integrity_failed"
  | "loader_unavailable"
  | "not_found"
  | "publication_invalid"
  | "read_failed"
  | "read_limit_exceeded";

export class PublicationError extends Error {
  readonly code: PublicationErrorCode;
  readonly details: JsonObject | undefined;

  constructor(
    code: PublicationErrorCode,
    message: string,
    options: { readonly cause?: unknown; readonly details?: JsonObject } = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "PublicationError";
    this.code = code;
    this.details = options.details === undefined ? undefined : freezeJsonObject(options.details);
  }
}

export function freezeJsonObject(value: JsonObject): JsonObject {
  return Object.freeze(
    Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeJsonValue(item)])),
  );
}

export function freezeJsonValue(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return Object.freeze(value.map(freezeJsonValue));
  if (typeof value === "object" && value !== null) return freezeJsonObject(value as JsonObject);
  return value;
}
