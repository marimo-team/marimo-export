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

export type ExportOptions =
  | {
      root: string | URL;
      fetch?: FetchLike;
      readFile?: never;
      url?: never;
      bytes?: never;
    }
  | {
      root: string;
      readFile: LocalReadFile;
      url?: LocalUrlResolver;
      fetch?: never;
      bytes?: never;
    }
  | {
      bytes: ExportArchiveInput;
      root?: never;
      readFile?: never;
      url?: never;
      fetch?: never;
    };

export interface ExportNotebook {
  name: string | null;
  sourceSha256: string | null;
}

export interface ExportRaw {
  manifest: ExportManifest;
}

export interface Export {
  id: string;
  notebook: ExportNotebook;
  sourceSpecSha256: string | null;
  raw: ExportRaw;
  scenarios(): string[];
  scenario(id: string): ExportScenario;
  values(): string[];
  formats(value: string): string[];
  get(selection: ExportSelection): ExportEntry;
}

export interface ExportScenario {
  id: string;
  state: JsonObject;
  values(): string[];
  formats(value: string): string[];
  get(value: string, format: string): ExportEntry;
}

export interface ExportArchive extends Export {
  dispose(): void;
}

export type ExportSelection = FormatSelection;
export type ExportBlob = BlobRef;

export interface ExportEntry {
  selection: FormatSelection;
  formatId: string;
  mediaType: string | null;
  metadata: JsonObject | null;
  raw: {
    record: FormatRecord;
  };
  entry(): ExportFile;
  files(): string[];
  file(key: string): ExportFile;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
  load<T>(loader: ExportLoader<T>): Promise<T>;
}

export interface ExportFile {
  ref: BlobRef;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
}

export interface ExportLoaderContext {
  selection: FormatSelection;
  formatId: string;
  mediaType: string | null;
  metadata: JsonObject | null;
  raw: {
    record: FormatRecord;
  };
  entry(): ExportFile;
  files(): string[];
  file(key: string): ExportFile;
}

export type ExportLoaderSelector =
  | {
      formatId: string;
      formatIds?: never;
    }
  | {
      formatId?: never;
      formatIds: readonly string[];
    };

export type ExportLoader<T = unknown> = ExportLoaderSelector & {
  load(context: ExportLoaderContext): T | Promise<T>;
};
