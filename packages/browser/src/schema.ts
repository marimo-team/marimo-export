import type { JsonObject, JsonValue } from "./types.js";
import { PublicationError } from "./types.js";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const FORMAT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;
const MEDIA_TOKEN_PATTERN = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;
const PYTHON_WHITESPACE =
  "\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PYTHON_BOUNDARY_WHITESPACE = new RegExp(
  `^[${PYTHON_WHITESPACE}]|[${PYTHON_WHITESPACE}]$`,
  "u",
);
const PYTHON_TRIM = new RegExp(`^[${PYTHON_WHITESPACE}]+|[${PYTHON_WHITESPACE}]+$`, "gu");
const MAX_JSON_DEPTH = 256;
const MAX_DIAGNOSTIC_LENGTH = 2_048;
const MAX_RENDERED_PATH_LENGTH = 1_024;
const MAX_UNKNOWN_FIELDS = 8;
const MAX_RENDERED_SEGMENT_LENGTH = 128;
const MAX_ASSET_KEY_UTF8_BYTES = 1_024;
const MAX_PORTABLE_COMPONENT_BYTES = 255;
const MAX_FORMAT_ID_BYTES = 255;
const MAX_MEDIA_TYPE_BYTES = 1_024;
const WINDOWS_RESERVED_COMPONENT_CHARACTER = /[<>:"|?*]/u;
const WINDOWS_RESERVED_BASENAME = /^(?:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$/u;

type DiagnosticPath = string | DiagnosticPathSegment;

interface DiagnosticPathSegment {
  readonly parent: DiagnosticPath;
  readonly kind: "field" | "key" | "index" | "label";
  readonly value: string | number;
}

export interface CacheAssetRef {
  readonly key: string;
  readonly sha256: string;
  readonly size: number;
}

export interface ManifestFormat {
  readonly format_id: string;
  readonly media_type: string;
  readonly metadata: JsonObject;
  readonly asset: CacheAssetRef;
}

export interface ManifestOutput {
  readonly formats: Readonly<Record<string, ManifestFormat>>;
}

export interface ManifestVariant {
  readonly controls: JsonObject;
  readonly outputs: Readonly<Record<string, ManifestOutput>>;
}

export interface PublicationManifest {
  readonly schema: "marimo-export.publication.v1";
  readonly asset_codec: "marimo.blob-asset.msgpack.v1";
  readonly notebook: {
    readonly filename: string;
    readonly document_sha256: string;
  };
  readonly producer: {
    readonly marimo: string;
    readonly marimo_export: string;
  };
  readonly variants: Readonly<Record<string, ManifestVariant>>;
}

export function parsePublicationManifest(input: unknown): PublicationManifest {
  try {
    const root = strictRecord(input, "index", [
      "schema",
      "asset_codec",
      "notebook",
      "producer",
      "variants",
    ]);
    const notebookPath = fieldPath("index", "notebook");
    const producerPath = fieldPath("index", "producer");
    const notebook = strictRecordAtPath(root.notebook, notebookPath, [
      "filename",
      "document_sha256",
    ]);
    const producer = strictRecordAtPath(root.producer, producerPath, ["marimo", "marimo_export"]);
    const variants = parseVariants(root.variants);
    if (Object.keys(variants).length === 0) {
      fail("index.variants must contain at least one variant.");
    }
    validateAssetReferences(variants);
    return Object.freeze({
      schema: literal(root.schema, "marimo-export.publication.v1", "index.schema"),
      asset_codec: literal(root.asset_codec, "marimo.blob-asset.msgpack.v1", "index.asset_codec"),
      notebook: Object.freeze({
        filename: notebookFilename(notebook.filename, "index.notebook.filename"),
        document_sha256: sha256(notebook.document_sha256, "index.notebook.document_sha256"),
      }),
      producer: Object.freeze({
        marimo: publicName(producer.marimo, "index.producer.marimo"),
        marimo_export: publicName(producer.marimo_export, "index.producer.marimo_export"),
      }),
      variants,
    });
  } catch (error) {
    if (error instanceof PublicationError) throw error;
    throw new PublicationError("publication_invalid", "Publication index validation failed.", {
      cause: error,
    });
  }
}

function parseVariants(input: unknown): Readonly<Record<string, ManifestVariant>> {
  const variantsPath = fieldPath("index", "variants");
  const variants = openRecord(input, variantsPath);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(variants).map(([name, value]) => {
        const variantName = publicName(name, labelPath(variantsPath, " key"));
        const path = keyPath(variantsPath, variantName);
        const variant = strictRecordAtPath(value, path, ["controls", "outputs"]);
        const outputsPath = fieldPath(path, "outputs");
        const outputs = parseOutputs(variant.outputs, outputsPath);
        if (Object.keys(outputs).length === 0) {
          failAt(outputsPath, "must contain at least one output.");
        }
        return [
          variantName,
          Object.freeze({
            controls: parseControls(variant.controls, fieldPath(path, "controls")),
            outputs,
          }),
        ];
      }),
    ),
  );
}

function parseOutputs(
  input: unknown,
  path: DiagnosticPath,
): Readonly<Record<string, ManifestOutput>> {
  const outputs = openRecord(input, path);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(outputs).map(([name, value]) => {
        const outputName = publicName(name, labelPath(path, " key"));
        const outputPath = keyPath(path, outputName);
        const output = strictRecordAtPath(value, outputPath, ["formats"]);
        const formatsPath = fieldPath(outputPath, "formats");
        const formats = parseFormats(output.formats, formatsPath);
        if (Object.keys(formats).length === 0) {
          failAt(formatsPath, "must contain at least one format.");
        }
        return [outputName, Object.freeze({ formats })];
      }),
    ),
  );
}

function parseFormats(
  input: unknown,
  path: DiagnosticPath,
): Readonly<Record<string, ManifestFormat>> {
  const formats = openRecord(input, path);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(formats).map(([name, value]) => {
        const formatName = publicName(name, labelPath(path, " key"));
        const formatPath = keyPath(path, formatName);
        const format = strictRecordAtPath(value, formatPath, [
          "format_id",
          "media_type",
          "metadata",
          "asset",
        ]);
        return [
          formatName,
          Object.freeze({
            format_id: formatId(format.format_id, fieldPath(formatPath, "format_id")),
            media_type: mediaType(format.media_type, fieldPath(formatPath, "media_type")),
            metadata: parseJsonObjectAtPath(format.metadata, fieldPath(formatPath, "metadata")),
            asset: parseAssetRef(format.asset, fieldPath(formatPath, "asset")),
          }),
        ];
      }),
    ),
  );
}

function parseAssetRef(input: unknown, path: DiagnosticPath): CacheAssetRef {
  const asset = strictRecordAtPath(input, path, ["key", "sha256", "size"]);
  const keyPath = fieldPath(path, "key");
  const key = assertCacheKeyAtPath(publicName(asset.key, keyPath), keyPath);
  return Object.freeze({
    key,
    sha256: sha256(asset.sha256, fieldPath(path, "sha256")),
    size: positiveInteger(asset.size, fieldPath(path, "size")),
  });
}

function validateAssetReferences(variants: Readonly<Record<string, ManifestVariant>>): void {
  const identities = new Map<string, string>();
  for (const variant of Object.values(variants)) {
    for (const output of Object.values(variant.outputs)) {
      for (const format of Object.values(output.formats)) {
        const identity = [
          format.asset.sha256,
          String(format.asset.size),
          format.format_id,
          format.media_type,
          canonicalJson(format.metadata),
        ].join("\0");
        const existing = identities.get(format.asset.key);
        if (existing !== undefined && existing !== identity) {
          fail(
            `Asset ${quoteDiagnosticString(format.asset.key)} has conflicting publication metadata.`,
          );
        }
        identities.set(format.asset.key, identity);
      }
    }
  }
}

export function assertCacheKey(input: string, path = "asset key"): string {
  return assertCacheKeyAtPath(input, path);
}

function assertCacheKeyAtPath(input: string, path: DiagnosticPath): string {
  const components = input.split("/");
  if (
    utf8ByteLength(input) > MAX_ASSET_KEY_UTF8_BYTES ||
    input.startsWith("/") ||
    components.some((component) => !isPortablePathComponent(component))
  ) {
    failAt(path, "must be a portable relative POSIX path.");
  }
  if (!input.endsWith(".bin")) failAt(path, "must end in .bin.");
  return input;
}

export function parseJsonObject(input: unknown, path = "value"): JsonObject {
  return parseJsonObjectAtDepth(input, path, 0);
}

function parseJsonObjectAtPath(input: unknown, path: DiagnosticPath): JsonObject {
  return parseJsonObjectAtDepth(input, path, 0);
}

function parseControls(input: unknown, path: DiagnosticPath): JsonObject {
  const controls = parseJsonObjectAtPath(input, path);
  for (const name of Object.keys(controls)) publicName(name, labelPath(path, " key"));
  return controls;
}

export function parseJsonValue(input: unknown, path = "value"): JsonValue {
  return parseJsonValueAtDepth(input, path, 0);
}

function parseJsonObjectAtDepth(input: unknown, path: DiagnosticPath, depth: number): JsonObject {
  assertJsonDepth(depth, path);
  const value = openRecord(input, path);
  return Object.freeze(
    Object.fromEntries(
      Object.entries(value).map(([key, item]) => {
        assertUnicodeScalarStringAtPath(key, labelPath(path, " key"));
        return [key, parseJsonValueAtDepth(item, keyPath(path, key), depth + 1)];
      }),
    ),
  );
}

function parseJsonValueAtDepth(input: unknown, path: DiagnosticPath, depth: number): JsonValue {
  assertJsonDepth(depth, path);
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "string") return assertUnicodeScalarStringAtPath(input, path);
  if (typeof input === "number") {
    validateJsonNumber(input, path);
    return input;
  }
  if (Array.isArray(input)) {
    return Object.freeze(
      input.map((item, index) => parseJsonValueAtDepth(item, indexPath(path, index), depth + 1)),
    );
  }
  return parseJsonObjectAtDepth(input, path, depth);
}

export function canonicalJson(value: JsonValue): string {
  if (value === null || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    return JSON.stringify(assertUnicodeScalarString(value, "Canonical JSON value"));
  }
  if (typeof value === "number") {
    validateJsonNumber(value, "Canonical JSON value");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const object = value as JsonObject;
  const keys = Object.keys(object);
  for (const key of keys) assertUnicodeScalarString(key, "Canonical JSON object key");
  return `{${keys
    .sort(compareUnicodeScalarStrings)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key]!)}`)
    .join(",")}}`;
}

/** Compare valid Unicode strings using code-point ordering. */
export function compareUnicodeScalarStrings(left: string, right: string): number {
  const leftCodePoints = left[Symbol.iterator]();
  const rightCodePoints = right[Symbol.iterator]();
  while (true) {
    const leftCodePoint = leftCodePoints.next();
    const rightCodePoint = rightCodePoints.next();
    if (leftCodePoint.done || rightCodePoint.done) {
      if (leftCodePoint.done && rightCodePoint.done) return 0;
      return leftCodePoint.done ? -1 : 1;
    }
    const difference = leftCodePoint.value.codePointAt(0)! - rightCodePoint.value.codePointAt(0)!;
    if (difference !== 0) return difference;
  }
}

export function strictRecord(
  input: unknown,
  path: string,
  fields: readonly string[],
): Record<string, unknown> {
  return strictRecordAtPath(input, path, fields);
}

function strictRecordAtPath(
  input: unknown,
  path: DiagnosticPath,
  fields: readonly string[],
): Record<string, unknown> {
  const value = openRecord(input, path);
  const allowed = new Set(fields);
  let extraCount = 0;
  const extras: string[] = [];
  for (const key of Object.keys(value)) {
    if (allowed.has(key)) continue;
    extraCount += 1;
    if (extras.length < MAX_UNKNOWN_FIELDS) extras.push(key);
  }
  if (extraCount > 0) {
    failAt(path, `contains unexpected fields: ${renderUnknownFields(extras, extraCount)}.`);
  }
  for (const field of fields) {
    if (!Object.hasOwn(value, field)) failAt(fieldPath(path, field), "is required.");
  }
  return value;
}

export function nonEmptyString(input: unknown, path: string): string {
  return nonEmptyStringAtPath(input, path);
}

function nonEmptyStringAtPath(input: unknown, path: DiagnosticPath): string {
  if (typeof input !== "string" || input.length === 0) {
    failAt(path, "must be a non-empty string.");
  }
  return assertUnicodeScalarStringAtPath(input, path);
}

export function assertUnicodeScalarString(value: string, path: string): string {
  return assertUnicodeScalarStringAtPath(value, path);
}

function assertUnicodeScalarStringAtPath(value: string, path: DiagnosticPath): string {
  if (containsUnpairedSurrogate(value)) {
    failAt(path, "must contain only Unicode scalar values.");
  }
  return value;
}

export function containsUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      if (index + 1 >= value.length) return true;
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) return true;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function openRecord(input: unknown, path: DiagnosticPath): Record<string, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    failAt(path, "must be an object.");
  }
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) {
    failAt(path, "must be a plain object.");
  }
  return input as Record<string, unknown>;
}

function assertJsonDepth(depth: number, path: DiagnosticPath): void {
  if (depth > MAX_JSON_DEPTH) failAt(path, "exceeds the maximum JSON nesting depth.");
}

function publicName(input: unknown, path: DiagnosticPath): string {
  const value = nonEmptyStringAtPath(input, path);
  if (hasPythonBoundaryWhitespace(value) || containsControlCharacter(value)) {
    failAt(path, "must not contain surrounding whitespace or control characters.");
  }
  return value;
}

export function containsControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit < 32 || codeUnit === 127) return true;
  }
  return false;
}

export function isPortablePathComponent(value: string): boolean {
  return (
    value.length > 0 &&
    value !== "." &&
    value !== ".." &&
    !hasPythonBoundaryWhitespace(value) &&
    !value.includes("/") &&
    !value.includes("\\") &&
    !containsControlCharacter(value) &&
    !containsUnpairedSurrogate(value) &&
    utf8ByteLength(value) <= MAX_PORTABLE_COMPONENT_BYTES &&
    !WINDOWS_RESERVED_COMPONENT_CHARACTER.test(value) &&
    !value.endsWith(".") &&
    !value.endsWith(" ") &&
    !isWindowsReservedComponent(value)
  );
}

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (const scalar of value) {
    const codePoint = scalar.codePointAt(0)!;
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

function isWindowsReservedComponent(value: string): boolean {
  const basename = value
    .split(".", 1)[0]!
    .replace(/[ .]+$/u, "")
    .toUpperCase();
  return WINDOWS_RESERVED_BASENAME.test(basename);
}

function containsNonPrintableAscii(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit < 0x20 || codeUnit > 0x7e) return true;
  }
  return false;
}

export function hasPythonBoundaryWhitespace(value: string): boolean {
  return PYTHON_BOUNDARY_WHITESPACE.test(value);
}

function notebookFilename(input: unknown, path: DiagnosticPath): string {
  const value = nonEmptyStringAtPath(input, path);
  if (value.includes("/") || value.includes("\0")) {
    failAt(path, "must be a POSIX base filename.");
  }
  return value;
}

function formatId(input: unknown, path: DiagnosticPath): string {
  const value = nonEmptyStringAtPath(input, path);
  if (value.length > MAX_FORMAT_ID_BYTES || !isFormatId(value)) {
    failAt(path, "contains characters outside the format ID contract.");
  }
  return value;
}

export function isFormatId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_FORMAT_ID_BYTES &&
    FORMAT_ID_PATTERN.test(value)
  );
}

function mediaType(input: unknown, path: DiagnosticPath): string {
  const value = nonEmptyStringAtPath(input, path);
  const base = value.split(";", 1)[0]!.replace(PYTHON_TRIM, "");
  const parts = base.split("/");
  if (
    hasPythonBoundaryWhitespace(value) ||
    value.length > MAX_MEDIA_TYPE_BYTES ||
    containsNonPrintableAscii(value) ||
    parts.length !== 2 ||
    parts.some((part) => !MEDIA_TOKEN_PATTERN.test(part))
  ) {
    failAt(path, "must use type/subtype syntax.");
  }
  return value;
}

function sha256(input: unknown, path: DiagnosticPath): string {
  const value = nonEmptyStringAtPath(input, path);
  if (!SHA256_PATTERN.test(value)) failAt(path, "must be a lowercase SHA-256 digest.");
  return value;
}

function positiveInteger(input: unknown, path: DiagnosticPath): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input <= 0) {
    failAt(path, "must be a positive safe integer.");
  }
  return input;
}

function validateJsonNumber(value: number, path: DiagnosticPath): void {
  if (!Number.isFinite(value)) failAt(path, "must be a finite JSON number.");
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    failAt(path, "must be a safe JSON integer.");
  }
}

function literal<T extends string>(input: unknown, expected: T, path: DiagnosticPath): T {
  if (input !== expected) failAt(path, `must be ${JSON.stringify(expected)}.`);
  return expected;
}

function fieldPath(parent: DiagnosticPath, value: string): DiagnosticPathSegment {
  return { parent, kind: "field", value };
}

function keyPath(parent: DiagnosticPath, value: string): DiagnosticPathSegment {
  return { parent, kind: "key", value };
}

function indexPath(parent: DiagnosticPath, value: number): DiagnosticPathSegment {
  return { parent, kind: "index", value };
}

function labelPath(parent: DiagnosticPath, value: string): DiagnosticPathSegment {
  return { parent, kind: "label", value };
}

function renderPath(path: DiagnosticPath): string {
  const segments: DiagnosticPathSegment[] = [];
  let root = path;
  while (typeof root !== "string") {
    segments.push(root);
    root = root.parent;
  }

  let rendered = escapeDiagnosticText(root, MAX_RENDERED_PATH_LENGTH);
  if (rendered.length === MAX_RENDERED_PATH_LENGTH && root.length > rendered.length) {
    return rendered;
  }
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = renderPathSegment(segments[index]!);
    if (rendered.length + segment.length > MAX_RENDERED_PATH_LENGTH) {
      return appendTruncationMarker(rendered, MAX_RENDERED_PATH_LENGTH);
    }
    rendered += segment;
  }
  return rendered;
}

function renderPathSegment(segment: DiagnosticPathSegment): string {
  if (segment.kind === "index") return `[${String(segment.value)}]`;
  const value = segment.value as string;
  if (segment.kind === "label") return escapeDiagnosticText(value, MAX_RENDERED_SEGMENT_LENGTH);
  if (segment.kind === "field" && /^[A-Za-z_][A-Za-z0-9_]*$/.test(value)) {
    return `.${value}`;
  }
  return `[${quoteDiagnosticString(value)}]`;
}

function renderUnknownFields(fields: readonly string[], total: number): string {
  const rendered = fields.map((field) => quoteDiagnosticString(field)).join(", ");
  const remaining = total - fields.length;
  return remaining === 0 ? rendered : `${rendered}, ... (+${remaining} more)`;
}

function quoteDiagnosticString(value: string): string {
  const bodyLimit = MAX_RENDERED_SEGMENT_LENGTH - 2;
  const body = escapeDiagnosticText(value, bodyLimit, true);
  return `"${body}"`;
}

function escapeDiagnosticText(value: string, limit: number, quoted = false): string {
  let rendered = "";
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    let token: string;
    if (quoted && (codeUnit === 0x22 || codeUnit === 0x5c)) {
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
    if (rendered.length + token.length > limit) {
      return appendTruncationMarker(rendered, limit);
    }
    rendered += token;
  }
  return rendered;
}

function appendTruncationMarker(value: string, limit: number): string {
  const marker = "...";
  if (limit <= marker.length) return marker.slice(0, limit);
  return `${value.slice(0, limit - marker.length)}${marker}`;
}

function failAt(path: DiagnosticPath, message: string): never {
  fail(`${renderPath(path)} ${message}`);
}

function fail(message: string): never {
  throw new PublicationError(
    "publication_invalid",
    message.length > MAX_DIAGNOSTIC_LENGTH
      ? appendTruncationMarker(message, MAX_DIAGNOSTIC_LENGTH)
      : message,
  );
}
