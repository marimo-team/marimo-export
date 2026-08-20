const PREPARED_EXPORT_ERROR_BRAND = Symbol.for("@marimo-team/marimo-export.PreparedExportError.v1");
const PREPARED_EXPORT_ERROR_CODES = [
  "manifest_invalid",
  "manifest_read_failed",
  "query_ambiguous",
  "query_miss",
] as const;
const PREPARED_EXPORT_ERROR_CODE_SET: ReadonlySet<string> = new Set(PREPARED_EXPORT_ERROR_CODES);

export type PreparedExportErrorCode = (typeof PREPARED_EXPORT_ERROR_CODES)[number];

export class PreparedExportError extends Error {
  readonly code: PreparedExportErrorCode;
  override readonly cause: unknown;

  constructor(
    code: PreparedExportErrorCode,
    message: string,
    options: { readonly cause?: unknown } = {},
  ) {
    if (!PREPARED_EXPORT_ERROR_CODE_SET.has(code)) {
      throw new TypeError("PreparedExportError code must be a known code.");
    }
    if (typeof message !== "string") {
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

export const isPreparedExportError = (value: unknown): value is PreparedExportError => {
  if (value === null || typeof value !== "object" || !Object.isFrozen(value)) {
    return false;
  }
  try {
    return (
      Object.getOwnPropertyDescriptor(value, PREPARED_EXPORT_ERROR_BRAND)?.value === true &&
      Object.getOwnPropertyDescriptor(value, "name")?.value === "PreparedExportError" &&
      typeof Object.getOwnPropertyDescriptor(value, "message")?.value === "string" &&
      PREPARED_EXPORT_ERROR_CODE_SET.has(
        String(Object.getOwnPropertyDescriptor(value, "code")?.value),
      )
    );
  } catch {
    return false;
  }
};
