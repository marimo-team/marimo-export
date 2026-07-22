export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export interface ReadOptions {
  readonly signal?: AbortSignal;
  readonly maxBytes?: number;
}

export interface ExportSource {
  read(path: string, options?: ReadOptions): Promise<Uint8Array>;
}

export type ExportKey = `marimo-export/indexes/${string}.json`;
export type PayloadKey = `marimo-export/payloads/sha256/${string}`;

export interface ExportRef {
  readonly key: ExportKey;
  readonly sha256: string;
  readonly size: number;
}

export interface PayloadRef {
  readonly key: PayloadKey;
  readonly sha256: string;
  readonly size: number;
}

export interface NotebookProvenance {
  readonly name: string;
  readonly sourceSha256: string;
}

export interface ProducerInfo {
  readonly marimoVersion: string;
  readonly marimoExportVersion: string;
}

const EXPORT_ERROR_CODES = [
  "ambiguous_format",
  "build_failed",
  "cache_read_failed",
  "decode_failed",
  "describe_failed",
  "integrity_failed",
  "internal_error",
  "invalid_index",
  "invalid_plan",
  "invalid_ref",
  "invalid_request",
  "missing_format",
  "missing_output",
  "missing_scenario",
  "output_too_large",
  "protocol_mismatch",
  "release_failed",
  "remote_closed",
  "remote_request_failed",
  "remote_timeout",
  "remote_unavailable",
  "scenario_failed",
  "session_close_failed",
  "session_open_failed",
  "session_timeout",
  "session_unavailable",
  "source_read_failed",
  "stage_failed",
  "timeout",
  "unsupported_format",
  "unsupported_marimo",
  "unsupported_mode",
  "usage_error",
] as const;

export type ExportErrorCode = (typeof EXPORT_ERROR_CODES)[number];

const exportErrorCodes = new Set<string>(EXPORT_ERROR_CODES);

export function isExportErrorCode(code: string): code is ExportErrorCode {
  return exportErrorCodes.has(code);
}

export class MarimoExportError extends Error {
  readonly code: ExportErrorCode;
  readonly details: JsonObject | undefined;

  constructor(
    code: ExportErrorCode,
    message: string,
    options: { cause?: unknown; details?: JsonObject } = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "MarimoExportError";
    this.code = code;
    this.details = options.details === undefined ? undefined : freezeJsonObject(options.details);
  }
}

function freezeJsonObject(value: JsonObject): JsonObject {
  return Object.freeze(
    Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeJsonValue(item)])),
  );
}

function freezeJsonValue(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return Object.freeze(value.map(freezeJsonValue));
  if (typeof value === "object" && value !== null) return freezeJsonObject(value as JsonObject);
  return value;
}
