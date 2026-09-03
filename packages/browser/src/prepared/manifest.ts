import { portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import { openExport } from "../export.js";
import { normalizeBase } from "../transport.js";
import type { ExportState, NotebookExport, OpenExportOptions } from "../types.js";
import { isJsonNumber, isJsonString } from "../value-types.js";
import { throwIfPreparedAborted } from "./cancellation.js";
import { PreparedExportError } from "./errors.js";

const MANIFEST_MAX_URL_BYTES = 8 * 1024;
const SHA256 = /^[\da-f]{64}$/u;
const encoder = new TextEncoder();

export interface PreparedExportManifest {
  readonly schema: "marimo-export.prepared.v1";
  readonly instance: string;
  readonly exportUrl: string;
  readonly inputs: JsonObject;
  readonly stateFingerprint: string;
  readonly refreshIntervalMs?: number;
}

export interface PreparedPublication {
  readonly manifest: PreparedExportManifest;
  readonly notebookExport: NotebookExport;
  readonly state: ExportState;
}

export interface OpenPreparedPublicationOptions extends OpenExportOptions {
  readonly openExport?: (
    base: string | URL,
    options?: OpenExportOptions,
  ) => Promise<NotebookExport>;
}

interface MutableOpenExportOptions {
  fetch?: typeof globalThis.fetch;
  signal?: AbortSignal;
}

export const parsePreparedExportManifest = <Input>(input: Input): PreparedExportManifest => {
  try {
    const root = portableJsonObject(input, "prepared manifest");
    requireFields(
      root,
      ["export_url", "inputs", "instance", "schema", "state_fingerprint"],
      ["refresh_interval_ms"],
    );
    if (root.schema !== "marimo-export.prepared.v1") {
      invalid("prepared manifest.schema must be marimo-export.prepared.v1");
    }
    const instance = digest(root.instance, "prepared manifest.instance");
    const exportUrl = boundedUrl(root.export_url);
    const inputs = portableJsonObject(root.inputs, "prepared manifest.inputs");
    if (Object.keys(inputs).some((name) => name.length === 0)) {
      invalid("prepared manifest.inputs must use nonempty input names");
    }
    const stateFingerprint = digest(root.state_fingerprint, "prepared manifest.state_fingerprint");
    const refreshIntervalMs = parseRefreshInterval(root.refresh_interval_ms);
    const manifest = {
      schema: "marimo-export.prepared.v1",
      instance,
      exportUrl,
      inputs,
      stateFingerprint,
    } as const;
    if (refreshIntervalMs === undefined) return Object.freeze(manifest);
    return Object.freeze({ ...manifest, refreshIntervalMs });
  } catch (error) {
    if (error instanceof PreparedExportError) {
      throw error;
    }
    throw new PreparedExportError("manifest_invalid", "The prepared export manifest is invalid.", {
      cause: error,
    });
  }
};

export const resolvePreparedPublication = (
  manifest: PreparedExportManifest,
  manifestUrl: URL,
  notebookExport: NotebookExport,
): PreparedPublication => {
  if (manifest.instance !== notebookExport.identity) {
    invalid("The prepared manifest does not match the immutable export identity.");
  }
  const expected = preparedExportBase(manifest, manifestUrl);
  if (notebookExport.base.href !== expected.href) {
    invalid("The prepared manifest export URL does not match the opened immutable export.");
  }
  let state: ExportState;
  try {
    state = notebookExport.resolve(manifest.inputs);
  } catch (error) {
    throw new PreparedExportError(
      "manifest_invalid",
      "The prepared manifest selects a state absent from the immutable export.",
      { cause: error },
    );
  }
  if (state.fingerprint !== manifest.stateFingerprint) {
    invalid("The prepared manifest state fingerprint does not match its selected inputs.");
  }
  return Object.freeze({ manifest, notebookExport, state });
};

export const preparedExportBase = (manifest: PreparedExportManifest, manifestUrl: URL): URL =>
  normalizeBase(new URL(manifest.exportUrl, manifestUrl));

export const openPreparedPublication = async (
  manifest: PreparedExportManifest,
  manifestUrl: URL,
  options: OpenPreparedPublicationOptions = {},
): Promise<PreparedPublication> => {
  throwIfPreparedAborted(options.signal);
  const base = preparedExportBase(manifest, manifestUrl);
  const opener = options.openExport ?? openExport;
  const openOptions: MutableOpenExportOptions = {};
  if (options.fetch !== undefined) openOptions.fetch = options.fetch;
  if (options.signal !== undefined) openOptions.signal = options.signal;
  const notebookExport = await opener(base, openOptions);
  throwIfPreparedAborted(options.signal);
  return resolvePreparedPublication(manifest, manifestUrl, notebookExport);
};

const requireFields = (
  value: JsonObject,
  required: readonly string[],
  optional: readonly string[],
): void => {
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !Object.hasOwn(value, key))) {
    invalid("The prepared export manifest is missing a required field.");
  }
  const keys = Object.keys(value);
  if (keys.some((key) => !allowed.has(key))) {
    invalid("The prepared export manifest contains an unknown field.");
  }
};

const digest = (value: JsonValue | undefined, path: string): string => {
  if (!isJsonString(value)) {
    invalid(`${path} must be a lowercase SHA-256 digest.`);
  }
  if (!SHA256.test(value)) {
    invalid(`${path} must be a lowercase SHA-256 digest.`);
  }
  return value;
};

const boundedUrl = (value: JsonValue | undefined): string => {
  if (!isJsonString(value)) {
    invalid(`prepared manifest.export_url must be at most ${MANIFEST_MAX_URL_BYTES} UTF-8 bytes.`);
  }
  if (value.length === 0 || encoder.encode(value).byteLength > MANIFEST_MAX_URL_BYTES) {
    invalid(`prepared manifest.export_url must be at most ${MANIFEST_MAX_URL_BYTES} UTF-8 bytes.`);
  }
  return value;
};

const parseRefreshInterval = (value: JsonValue | undefined): number | undefined => {
  if (value === undefined) {
    return undefined;
  }
  if (!isJsonNumber(value)) {
    invalid("prepared manifest.refresh_interval_ms must be 0 or between 250 and 60000.");
  }
  if (!Number.isSafeInteger(value) || (value !== 0 && (value < 250 || value > 60_000))) {
    invalid("prepared manifest.refresh_interval_ms must be 0 or between 250 and 60000.");
  }
  return value;
};

function invalid(message: string): never {
  throw new PreparedExportError("manifest_invalid", message);
}
