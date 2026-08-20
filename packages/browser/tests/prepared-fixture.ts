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

export const preparedExportFixture = (options: PreparedExportFixtureOptions): NotebookExport => {
  const base = new URL(options.base ?? "https://example.test/export/");
  const identity = options.identity ?? "1".repeat(64);
  const inputNames = Object.freeze(Object.keys(options.inputs[0] ?? {}));
  const statesByInputs = new Map<string, ExportState>();
  let notebookExport: NotebookExport;
  const states = options.inputs.map((inputs, index) => {
    const normalized = portableJsonObject(inputs);
    const state = {
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
    } as ExportState;
    statesByInputs.set(jsonKey(normalized), state);
    return state;
  });
  notebookExport = {
    base,
    controlBindings: Object.freeze({ ...options.controlBindings }),
    identity,
    inputNames,
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
  } as unknown as NotebookExport;
  return notebookExport;
};

export const preparedManifestFixture = (
  notebookExport: NotebookExport,
  inputs: JsonObject,
  options: {
    readonly exportUrl?: string;
    readonly refreshIntervalMs?: number;
  } = {},
): PreparedExportManifest => {
  const state = notebookExport.resolve(inputs);
  return Object.freeze({
    schema: "marimo-export.prepared.v1",
    instance: notebookExport.identity,
    exportUrl: options.exportUrl ?? notebookExport.base.href,
    inputs: state.inputs,
    stateFingerprint: state.fingerprint,
    ...(options.refreshIntervalMs === undefined
      ? {}
      : { refreshIntervalMs: options.refreshIntervalMs }),
  });
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

export const manifestWire = (
  manifest: PreparedExportManifest,
): Readonly<Record<string, JsonValue>> =>
  Object.freeze({
    schema: manifest.schema,
    instance: manifest.instance,
    export_url: manifest.exportUrl,
    inputs: manifest.inputs,
    state_fingerprint: manifest.stateFingerprint,
    ...(manifest.refreshIntervalMs === undefined
      ? {}
      : { refresh_interval_ms: manifest.refreshIntervalMs }),
  });

const jsonKey = (value: JsonValue): string => {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(jsonKey).join(",")}]`;
  }
  const object = value as JsonObject;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${jsonKey(object[key]!)}`)
    .join(",")}}`;
};
