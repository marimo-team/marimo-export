import { portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import type { ControlBinding, ExportOutput, ExportState, NotebookExport } from "../src/types.js";
import { NotebookExportError } from "../src/types.js";
import type { PreparedExportManifest, PreparedPublication } from "../src/prepared/manifest.js";

interface PreparedExportFixtureOptions {
  readonly base?: string;
  readonly controlBindings?: Readonly<Record<string, ControlBinding>>;
  readonly identity?: string;
  readonly inputs: readonly JsonObject[];
}

interface PreparedManifestFixtureOptions {
  readonly exportUrl?: string;
  readonly refreshIntervalMs?: number;
}

export const preparedExportFixture = (options: PreparedExportFixtureOptions): NotebookExport => {
  const base = new URL(options.base ?? "https://example.test/export/");
  const identity = options.identity ?? "1".repeat(64);
  const inputNames = Object.freeze(Object.keys(options.inputs[0] ?? {}));
  const statesByInputs = new Map<string, ExportState>();
  let notebookExport: NotebookExport;
  const states = options.inputs.map((inputs, index) => {
    const normalized = portableJsonObject(inputs);
    const state: ExportState = {
      aliases: Object.freeze([`state-${index}`]),
      fingerprint: (index + 1).toString(16).padStart(64, "0"),
      inputs: normalized,
      get notebookExport() {
        return notebookExport;
      },
      output(): ExportOutput {
        throw new NotebookExportError("output_not_found", "Fixture has no outputs.");
      },
      outputs: () => Object.freeze([]),
      resolve(patch: JsonObject): ExportState {
        const merged = portableJsonObject({ ...normalized, ...patch });
        return notebookExport.resolve(merged);
      },
    };
    statesByInputs.set(jsonKey(normalized), state);
    return state;
  });
  notebookExport = {
    base,
    controlBindings: Object.freeze({ ...options.controlBindings }),
    identity,
    inputNames,
    specSha256: "f".repeat(64),
    defaultState: states[0]!,
    notebook: { documentSha256: "d".repeat(64), filename: "fixture.py" },
    outputNames: Object.freeze([]),
    producer: {
      implementationSha256: "e".repeat(64),
      marimo: "0.24.0",
      marimoExport: "0.0.0",
    },
    resolve(inputs: JsonObject): ExportState {
      const state = statesByInputs.get(jsonKey(inputs));
      if (state === undefined) {
        throw new NotebookExportError(
          "state_unavailable",
          "The requested input vector is absent from this fixture.",
        );
      }
      return state;
    },
    state(alias: string): ExportState {
      const state = states.find((candidate) => candidate.aliases.includes(alias));
      if (state === undefined) {
        throw new NotebookExportError("state_not_found", "Fixture state alias was not found.");
      }
      return state;
    },
    states: () => states,
    verify: async () => ({ assets: 0, bytesVerified: 0, outputs: 0, states: states.length }),
  };
  return notebookExport;
};

export const preparedManifestFixture = (
  notebookExport: NotebookExport,
  inputs: JsonObject,
  options: PreparedManifestFixtureOptions = {},
): PreparedExportManifest => {
  const state = notebookExport.resolve(inputs);
  const manifest = {
    schema: "marimo-export.prepared.v1",
    instance: notebookExport.identity,
    exportUrl: options.exportUrl ?? notebookExport.base.href,
    inputs: state.inputs,
    stateFingerprint: state.fingerprint,
  } as const;
  if (options.refreshIntervalMs === undefined) return Object.freeze(manifest);
  return Object.freeze({ ...manifest, refreshIntervalMs: options.refreshIntervalMs });
};

export const preparedPublicationFixture = (
  notebookExport: NotebookExport,
  inputs: JsonObject,
  options: Parameters<typeof preparedManifestFixture>[2] = {},
): PreparedPublication =>
  Object.freeze({
    manifest: preparedManifestFixture(notebookExport, inputs, options),
    notebookExport,
    state: notebookExport.resolve(inputs),
  });

export const manifestWire = (manifest: PreparedExportManifest): JsonObject => {
  const wire = {
    schema: manifest.schema,
    instance: manifest.instance,
    export_url: manifest.exportUrl,
    inputs: manifest.inputs,
    state_fingerprint: manifest.stateFingerprint,
  } as const;
  if (manifest.refreshIntervalMs === undefined) return Object.freeze(wire);
  return Object.freeze({ ...wire, refresh_interval_ms: manifest.refreshIntervalMs });
};

const jsonKey = (value: JsonValue): string => {
  if (value === null || isJsonPrimitive(value)) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(jsonKey).join(",")}]`;
  }
  if (!isJsonObject(value)) throw new TypeError("Prepared fixture value must be JSON.");
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${jsonKey(value[key]!)}`)
    .join(",")}}`;
};

const isJsonPrimitive = (value: JsonValue): value is string | number | boolean =>
  Object.prototype.toString.call(value) !== "[object Object]" && !Array.isArray(value);

const isJsonObject = (value: JsonValue): value is JsonObject =>
  value !== null &&
  !Array.isArray(value) &&
  Object.prototype.toString.call(value) === "[object Object]";
