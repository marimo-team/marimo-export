import type {
  FormatData,
  FormatRecord,
  BlobRef,
  CaptureRecord,
  ExportManifest,
  ExportRootBundle,
  ExportRootIndex,
  IdentityRecord,
  JsonObject,
  JsonValue,
  ManifestScenario,
  ManifestValue,
  NotebookRecord,
  ProvenanceRecord,
  SourceRecord,
} from "./types.js";

const ROOT_INDEX_SCHEMA = "moexport.root_index.v1";
const BUNDLE_SCHEMA = "moexport.bundle.v1";

export function validateExportRootIndex(
  value: unknown,
  label = "export root index",
): ExportRootIndex {
  const record = object(value, label);
  const schema = literal(record.schema, ROOT_INDEX_SCHEMA, `${label}.schema`);
  const version = integer(record.version, `${label}.version`);
  const latest = record.latest === null ? null : rootBundle(record.latest, `${label}.latest`);
  const bundles = array(record.bundles, `${label}.bundles`).map((bundle, index) =>
    rootBundle(bundle, `${label}.bundles[${index}]`),
  );

  return {
    schema,
    version,
    latest,
    bundles,
  };
}

export function validateExportManifest(value: unknown, label = "export manifest"): ExportManifest {
  const record = object(value, label);
  const values = stringRecord(record.values, `${label}.values`, manifestValue);
  const scenarios = array(record.scenarios, `${label}.scenarios`).map((scenario, index) =>
    manifestScenario(scenario, `${label}.scenarios[${index}]`),
  );
  validateCatalog(values, scenarios, label);

  const manifest: ExportManifest = {
    schema: literal(record.schema, BUNDLE_SCHEMA, `${label}.schema`),
    version: integer(record.version, `${label}.version`),
    id: string(record.id, `${label}.id`),
    sha256: string(record.sha256, `${label}.sha256`),
    notebook: notebook(record.notebook, `${label}.notebook`),
    scenario_set: identity(record.scenario_set, `${label}.scenario_set`),
    capture: captureRecord(record.capture, `${label}.capture`),
    values,
    scenarios,
  };

  if (record.provenance !== undefined) {
    manifest.provenance = provenance(record.provenance, `${label}.provenance`);
  }

  return manifest;
}

export function safeBundlePath(path: string, label: string): string {
  if (!path || path.startsWith("/") || path.includes("\\")) {
    throw new Error(`Invalid ${label} ${JSON.stringify(path)}.`);
  }

  const parts = path.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error(`Invalid ${label} ${JSON.stringify(path)}.`);
  }

  return parts.join("/");
}

function rootBundle(value: unknown, label: string): ExportRootBundle {
  const record = object(value, label);
  return {
    id: string(record.id, `${label}.id`),
    sha256: string(record.sha256, `${label}.sha256`),
    manifest_href: safeBundlePath(
      string(record.manifest_href, `${label}.manifest_href`),
      `${label}.manifest_href`,
    ),
    updated_at: string(record.updated_at, `${label}.updated_at`),
    latest_invocation_href: safeBundlePath(
      string(record.latest_invocation_href, `${label}.latest_invocation_href`),
      `${label}.latest_invocation_href`,
    ),
  };
}

function notebook(value: unknown, label: string): NotebookRecord {
  const record = object(value, label);
  return {
    name: nullableString(record.name, `${label}.name`),
    source: record.source === null ? null : blobRef(record.source, `${label}.source`),
    source_sha256:
      record.source_sha256 === undefined
        ? null
        : nullableString(record.source_sha256, `${label}.source_sha256`),
  };
}

function identity(value: unknown, label: string): IdentityRecord {
  const record = object(value, label);
  return {
    id: string(record.id, `${label}.id`),
    sha256: string(record.sha256, `${label}.sha256`),
  };
}

function captureRecord(value: unknown, label: string): CaptureRecord {
  const record = object(value, label);
  return {
    id: string(record.id, `${label}.id`),
    request_sha256: string(record.request_sha256, `${label}.request_sha256`),
  };
}

function manifestValue(value: unknown, label: string): ManifestValue {
  const record = object(value, label);
  return {
    source: sourceRecord(record.source, `${label}.source`),
    formats: array(record.formats, `${label}.formats`).map((format, index) =>
      string(format, `${label}.formats[${index}]`),
    ),
  };
}

function sourceRecord(value: unknown, label: string): SourceRecord {
  const record = object(value, label);
  const type = string(record.type, `${label}.type`);
  if (type === "definition") {
    return { type, name: string(record.name, `${label}.name`) };
  }
  if (type === "expression") {
    return { type, expression: string(record.expression, `${label}.expression`) };
  }
  if (type === "cell_output") {
    const result: SourceRecord = {
      type,
      cell: jsonObject(record.cell, `${label}.cell`),
    };
    if (record.on_error !== undefined) {
      result.on_error = string(record.on_error, `${label}.on_error`);
    }
    return result;
  }
  if (type === "notebook_snapshot") {
    return jsonObject(record, label) as SourceRecord;
  }
  if (type === "report") {
    return {
      ...(jsonObject(record, label) as SourceRecord),
      type,
      cells: array(record.cells, `${label}.cells`).map((item, index) =>
        jsonValue(item, `${label}.cells[${index}]`),
      ),
    };
  }

  throw new Error(`${label}.type has unknown source type ${JSON.stringify(type)}.`);
}

function manifestScenario(value: unknown, label: string): ManifestScenario {
  const record = object(value, label);
  const scenario: ManifestScenario = {
    id: string(record.id, `${label}.id`),
    state: jsonObject(record.state, `${label}.state`),
    values: stringRecord(record.values, `${label}.values`, (formats, formatsLabel) =>
      stringRecord(formats, formatsLabel, formatRecord),
    ),
  };

  if (record.declared_state !== undefined) {
    scenario.declared_state =
      record.declared_state === null
        ? null
        : jsonObject(record.declared_state, `${label}.declared_state`);
  }

  return scenario;
}

function formatRecord(value: unknown, label: string): FormatRecord {
  const record = object(value, label);
  return {
    format_id: string(record.format_id, `${label}.format_id`),
    media_type: nullableString(record.media_type, `${label}.media_type`),
    data: formatData(record.data, `${label}.data`),
    metadata: record.metadata === null ? null : jsonObject(record.metadata, `${label}.metadata`),
  };
}

function formatData(value: unknown, label: string): FormatData {
  const record = object(value, label);
  const files = stringRecord(record.files, `${label}.files`, blobRef);
  const entry = nullableString(record.entry, `${label}.entry`);
  if (entry !== null && !(entry in files)) {
    throw new Error(`${label}.entry must name a file in ${label}.files.`);
  }

  return {
    type: literal(record.type, "bundle", `${label}.type`),
    files,
    entry,
  };
}

function blobRef(value: unknown, label: string): BlobRef {
  const record = object(value, label);
  return {
    href: safeBundlePath(string(record.href, `${label}.href`), `${label}.href`),
    media_type: nullableString(record.media_type, `${label}.media_type`),
    size: integer(record.size, `${label}.size`),
    sha256: string(record.sha256, `${label}.sha256`),
  };
}

function provenance(value: unknown, label: string): ProvenanceRecord {
  const record = object(value, label);
  const result: ProvenanceRecord = {};

  if (record.invocations_index_href !== undefined) {
    result.invocations_index_href =
      record.invocations_index_href === null
        ? null
        : safeBundlePath(
            string(record.invocations_index_href, `${label}.invocations_index_href`),
            `${label}.invocations_index_href`,
          );
  }
  if (record.source_spec_sha256 !== undefined) {
    result.source_spec_sha256 = nullableString(
      record.source_spec_sha256,
      `${label}.source_spec_sha256`,
    );
  }
  if (record.source_spec !== undefined) {
    result.source_spec =
      record.source_spec === null ? null : jsonObject(record.source_spec, `${label}.source_spec`);
  }

  return result;
}

function validateCatalog(
  values: Record<string, ManifestValue>,
  scenarios: ManifestScenario[],
  label: string,
): void {
  const scenarioIds = new Set<string>();

  for (const scenario of scenarios) {
    if (scenarioIds.has(scenario.id)) {
      throw new Error(
        `${label}.scenarios contains duplicate scenario ${JSON.stringify(scenario.id)}.`,
      );
    }
    scenarioIds.add(scenario.id);

    for (const [valueName, declaration] of Object.entries(values)) {
      const scenarioValue = scenario.values[valueName];
      if (!scenarioValue) {
        throw new Error(
          `${label}.scenarios.${scenario.id}.values must include declared value ${JSON.stringify(
            valueName,
          )}.`,
        );
      }

      for (const formatName of declaration.formats) {
        if (!scenarioValue[formatName]) {
          throw new Error(
            `${label}.scenarios.${scenario.id}.values.${valueName} must include declared format ${JSON.stringify(
              formatName,
            )}.`,
          );
        }
      }
    }

    for (const [valueName, formats] of Object.entries(scenario.values)) {
      const declaration = values[valueName];
      if (!declaration) {
        throw new Error(
          `${label}.scenarios.${scenario.id}.values contains undeclared value ${JSON.stringify(
            valueName,
          )}.`,
        );
      }

      const declaredFormats = new Set(declaration.formats);
      for (const formatName of Object.keys(formats)) {
        if (!declaredFormats.has(formatName)) {
          throw new Error(
            `${label}.scenarios.${scenario.id}.values.${valueName} contains undeclared format ${JSON.stringify(
              formatName,
            )}.`,
          );
        }
      }
    }
  }
}

function stringRecord<T>(
  value: unknown,
  label: string,
  parse: (value: unknown, label: string) => T,
): Record<string, T> {
  const record = object(value, label);
  const result: Record<string, T> = {};
  for (const [key, item] of Object.entries(record)) {
    result[key] = parse(item, `${label}.${key}`);
  }
  return result;
}

function jsonObject(value: unknown, label: string): JsonObject {
  const record = object(value, label);
  const result: JsonObject = {};
  for (const [key, item] of Object.entries(record)) {
    result[key] = jsonValue(item, `${label}.${key}`);
  }
  return result;
}

function jsonValue(value: unknown, label: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }

  if (Array.isArray(value)) {
    return value.map((item, index) => jsonValue(item, `${label}[${index}]`));
  }

  if (isRecord(value)) {
    return jsonObject(value, label);
  }

  throw new Error(`${label} must be JSON-compatible.`);
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`${label} must be an object.`);
  }
  return value;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array.`);
  }
  return value;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new Error(`${label} must be a string.`);
  }
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  return value === null ? null : string(value, label);
}

function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return value;
}

function literal<T extends string>(value: unknown, expected: T, label: string): T {
  if (value !== expected) {
    throw new Error(`${label} must be ${JSON.stringify(expected)}.`);
  }
  return expected;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
