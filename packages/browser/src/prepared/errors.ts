import { isPropertyOwner, isStringValue } from "../value-types.js";

const PREPARED_EXPORT_ERROR_BRAND = Symbol.for("@marimo-team/marimo-export.PreparedExportError.v1");
const PREPARED_EXPORT_ERROR_CODES = [
  "manifest_invalid",
  "manifest_read_failed",
  "query_ambiguous",
  "query_miss",
] as const;
const PREPARED_EXPORT_ERROR_CODE_SET: ReadonlySet<string> = new Set(PREPARED_EXPORT_ERROR_CODES);

export type PreparedExportErrorCode = (typeof PREPARED_EXPORT_ERROR_CODES)[number];

export interface PreparedExportErrorOptions {
  readonly cause?: unknown;
}

export class PreparedExportError extends Error {
  readonly code: PreparedExportErrorCode;
  override readonly cause: unknown;

  constructor(
    code: PreparedExportErrorCode,
    message: string,
    options: PreparedExportErrorOptions = {},
  ) {
    if (!PREPARED_EXPORT_ERROR_CODE_SET.has(code)) {
      throw new TypeError("PreparedExportError code must be a known code.");
    }
    if (!isStringValue(message)) {
      throw new TypeError("PreparedExportError message must be a string.");
    }
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    Object.defineProperty(this, PREPARED_EXPORT_ERROR_BRAND, { value: true });
    this.name = "PreparedExportError";
    this.code = code;
    this.cause = options.cause;
    Object.freeze(this);
  }
}

export const isPreparedExportError = <Value>(
  value: Value,
): value is Value & PreparedExportError => {
  if (!isPropertyOwner(value) || !Object.isFrozen(value)) {
    return false;
  }
  try {
    return (
      Object.getOwnPropertyDescriptor(value, PREPARED_EXPORT_ERROR_BRAND)?.value === true &&
      Object.getOwnPropertyDescriptor(value, "name")?.value === "PreparedExportError" &&
      isStringValue(Object.getOwnPropertyDescriptor(value, "message")?.value) &&
      PREPARED_EXPORT_ERROR_CODE_SET.has(
        String(Object.getOwnPropertyDescriptor(value, "code")?.value),
      )
    );
  } catch {
    return false;
  }
};
