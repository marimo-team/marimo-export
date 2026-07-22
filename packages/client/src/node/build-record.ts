import type { RemoteBuild } from "../remote/build.js";
import { parseExportRef } from "../schema.js";
import { MarimoExportError } from "../types.js";

export const REMOTE_BUILD_RECORD_SCHEMA = "marimo-export.build.v1" as const;

export interface RemoteBuildRecord {
  readonly schema: typeof REMOTE_BUILD_RECORD_SCHEMA;
  readonly server: string;
  readonly target: { readonly notebook: string };
  readonly ref: RemoteBuild["ref"];
  readonly receipt: RemoteBuild["receipt"];
}

export function createRemoteBuildRecord(options: {
  readonly server: string;
  readonly notebook: string;
  readonly build: RemoteBuild;
}): RemoteBuildRecord {
  return parseRemoteBuildRecord({
    schema: REMOTE_BUILD_RECORD_SCHEMA,
    server: options.server,
    target: { notebook: options.notebook },
    ref: options.build.ref,
    receipt: options.build.receipt,
  });
}

export function parseRemoteBuildRecord(input: unknown): RemoteBuildRecord {
  const root = exactObject(
    input,
    ["schema", "server", "target", "ref", "receipt"],
    "remote build record",
  );
  if (root.schema !== REMOTE_BUILD_RECORD_SCHEMA) {
    throw invalid(`remote build record.schema must be ${REMOTE_BUILD_RECORD_SCHEMA}.`);
  }
  const target = exactBuildRecordTarget(root.target);
  const receipt = exactObject(
    root.receipt,
    ["elapsedMs", "scenarioCount", "projectionCount"],
    "remote build record.receipt",
  );
  return Object.freeze({
    schema: REMOTE_BUILD_RECORD_SCHEMA,
    server: parseServer(root.server),
    target: Object.freeze({
      notebook: nonEmptyString(target.notebook, "remote build record.target.notebook"),
    }),
    ref: Object.freeze(parseExportRef(root.ref, "remote build record.ref")),
    receipt: Object.freeze({
      elapsedMs: nonNegativeNumber(receipt.elapsedMs, "remote build record.receipt.elapsedMs"),
      scenarioCount: nonNegativeInteger(
        receipt.scenarioCount,
        "remote build record.receipt.scenarioCount",
      ),
      projectionCount: nonNegativeInteger(
        receipt.projectionCount,
        "remote build record.receipt.projectionCount",
      ),
    }),
  });
}

function parseServer(input: unknown): string {
  const value = nonEmptyString(input, "remote build record.server");
  let url: URL;
  try {
    url = new URL(value);
  } catch (error) {
    throw invalid("remote build record.server must be an absolute URL.", error);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw invalid("remote build record.server must use HTTP or HTTPS.");
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw invalid("remote build record.server must not contain credentials.");
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw invalid("remote build record.server must not contain a query or fragment.");
  }
  return url.toString();
}

function exactBuildRecordTarget(input: unknown): Record<"notebook", unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    throw invalid("remote build record.target must be an object.");
  }
  const value = input as Record<string, unknown>;
  const present = Object.keys(value);
  if (present.length !== 1 || present[0] !== "notebook") {
    throw invalid(
      "remote build record.target must contain exactly notebook so pull can open a fresh session.",
    );
  }
  return value as Record<"notebook", unknown>;
}

function exactObject(input: unknown, keys: readonly string[], path: string) {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    throw invalid(`${path} must be an object.`);
  }
  const value = input as Record<string, unknown>;
  const extras = Object.keys(value).filter((key) => !keys.includes(key));
  if (extras.length > 0) {
    throw invalid(`${path} contains unexpected fields: ${extras.join(", ")}.`);
  }
  for (const key of keys) {
    if (!(key in value)) throw invalid(`${path}.${key} is required.`);
  }
  return value;
}

function nonEmptyString(input: unknown, path: string): string {
  if (typeof input !== "string" || input.length === 0) {
    throw invalid(`${path} must be a non-empty string.`);
  }
  return input;
}

function nonNegativeNumber(input: unknown, path: string): number {
  if (typeof input !== "number" || !Number.isFinite(input) || input < 0) {
    throw invalid(`${path} must be a non-negative number.`);
  }
  return input;
}

function nonNegativeInteger(input: unknown, path: string): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 0) {
    throw invalid(`${path} must be a non-negative safe integer.`);
  }
  return input;
}

function invalid(message: string, cause?: unknown): MarimoExportError {
  return new MarimoExportError("invalid_ref", message, cause === undefined ? {} : { cause });
}
