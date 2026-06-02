export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | JsonObject;
export type JsonObject = { [key: string]: JsonValue };

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export type LocalReadFileResult = ArrayBuffer | ArrayBufferView | Blob | string;
export type LocalReadFile = (path: string) => Promise<LocalReadFileResult>;
export type LocalUrlResolver = (href: string, path: string, mediaType: string | null) => string;

export interface BlobRef {
  href: string;
  media_type: string | null;
  size: number;
  sha256: string;
}

export interface FormatDataBundle {
  type: "bundle";
  files: Record<string, BlobRef>;
  entry: string | null;
}

export type FormatData = FormatDataBundle;

export interface NotebookRecord {
  name: string | null;
  source: BlobRef | null;
  source_sha256?: string | null;
}

export interface IdentityRecord {
  id: string;
  sha256: string;
}

export type SourceRecord =
  | { type: "definition"; name: string }
  | { type: "expression"; expression: string }
  | { type: "cell_output"; cell: JsonObject; on_error?: string }
  | { type: "notebook_snapshot"; [key: string]: JsonValue }
  | { type: "report"; cells: JsonValue[]; [key: string]: JsonValue };

export interface CaptureRecord {
  id: string;
  request_sha256: string;
}

export interface FormatRecord {
  format_id: string;
  media_type: string | null;
  data: FormatData;
  metadata: JsonObject | null;
}

export interface ManifestValue {
  source: SourceRecord;
  formats: string[];
}

export interface ManifestScenario {
  id: string;
  state: JsonObject;
  declared_state?: JsonObject | null;
  values: Record<string, Record<string, FormatRecord>>;
}

export interface ProvenanceRecord {
  invocations_index_href?: string | null;
  source_spec_sha256?: string | null;
  source_spec?: JsonObject | null;
}

export interface ExportManifest {
  schema: string;
  version: number;
  id: string;
  sha256: string;
  notebook: NotebookRecord;
  scenario_set: IdentityRecord;
  capture: CaptureRecord;
  values: Record<string, ManifestValue>;
  scenarios: ManifestScenario[];
  provenance?: ProvenanceRecord;
}

export interface ExportRootBundle {
  id: string;
  sha256: string;
  manifest_href: string;
  updated_at: string;
  latest_invocation_href: string;
}

export interface ExportRootIndex {
  schema: string;
  version: number;
  latest: ExportRootBundle | null;
  bundles: ExportRootBundle[];
}

export interface FormatSelection {
  scenario: string;
  value: string;
  format: string;
}

export type ExportArchiveInput = ArrayBuffer | ArrayBufferView | Blob;

export type ReadExportOptions =
  | {
      root: string | URL;
      manifest?: string;
      index?: string;
      loaders?: FormatLoader[];
      fetch?: FetchLike;
      readFile?: never;
      url?: never;
      bytes?: never;
    }
  | {
      root: string;
      manifest?: string;
      index?: string;
      loaders?: FormatLoader[];
      readFile: LocalReadFile;
      url?: LocalUrlResolver;
      fetch?: never;
      bytes?: never;
    }
  | {
      bytes: ExportArchiveInput;
      manifest?: string;
      loaders?: FormatLoader[];
      root?: never;
      index?: never;
      readFile?: never;
      url?: never;
      fetch?: never;
    };

export interface StaticExport {
  manifest: ExportManifest;
  scenarios(): string[];
  scenario(id: string): ExportScenario;
  scenarioRecord(id: string): ManifestScenario;
  scenarioRecords(): ManifestScenario[];
  values(): string[];
  formats(value: string): string[];
  get(selection: FormatSelection): FormatHandle;
}

export interface ExportScenario {
  id: string;
  record: ManifestScenario;
  state: JsonObject;
  values(): string[];
  formats(value: string): string[];
  get(value: string, format: string): FormatHandle;
}

export interface StaticExportArchive extends StaticExport {
  dispose(): void;
}

export interface FormatHandle {
  selection: FormatSelection;
  record: FormatRecord;
  entry(): FormatFile;
  file(key: string): FormatFile;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
  load<T>(loader: FormatLoader<T>): Promise<T>;
  load(): Promise<unknown>;
}

export interface FormatFile {
  ref: BlobRef;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
}

export interface FormatLoaderContext {
  record: FormatRecord;
  selection: FormatSelection;
  entry(): FormatFile;
  file(key: string): FormatFile;
}

export type FormatLoaderSelector =
  | {
      formatId: string;
      formatIds?: never;
    }
  | {
      formatId?: never;
      formatIds: readonly string[];
    };

export type FormatLoader<T = unknown> = FormatLoaderSelector & {
  load(context: FormatLoaderContext): T | Promise<T>;
};
