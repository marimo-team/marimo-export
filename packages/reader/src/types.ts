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

export interface ArtifactDataBundle {
  type: "bundle";
  files: Record<string, BlobRef>;
  entry: string | null;
}

export type ArtifactData = ArtifactDataBundle;

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

export interface ArtifactRecord {
  format_id: string;
  media_type: string | null;
  data: ArtifactData;
  metadata: JsonObject | null;
}

export interface ManifestValue {
  source: SourceRecord;
  artifacts: string[];
}

export interface ManifestScenario {
  id: string;
  state: JsonObject;
  declared_state?: JsonObject | null;
  values: Record<string, Record<string, ArtifactRecord>>;
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

export interface ArtifactSelection {
  scenario: string;
  value: string;
  artifact: string;
}

export type ExportSourceInput =
  | {
      kind: "root";
      root: string | URL;
      index?: string;
      manifest?: string;
      fetch?: FetchLike;
    }
  | {
      kind: "directory";
      root: string;
      index?: string;
      manifest?: string;
      readFile: LocalReadFile;
      url?: LocalUrlResolver;
    }
  | {
      kind: "archive";
      bytes: ExportArchiveInput;
      manifest?: string;
    };

export interface OpenExportOptions {
  loaders?: ArtifactLoader[];
}

export interface HostedManifestOptions {
  root: string | URL;
  manifest: string;
  loaders?: ArtifactLoader[];
  fetch?: FetchLike;
}

export interface HostedIndexOptions {
  root: string | URL;
  index?: string;
  fetch?: FetchLike;
}

export interface HostedLatestOptions {
  root: string | URL;
  index?: string;
  loaders?: ArtifactLoader[];
  fetch?: FetchLike;
}

export interface DirectoryManifestOptions {
  root: string;
  manifest: string;
  loaders?: ArtifactLoader[];
  readFile: LocalReadFile;
  url?: LocalUrlResolver;
}

export interface DirectoryLatestOptions {
  root: string;
  index?: string;
  loaders?: ArtifactLoader[];
  readFile: LocalReadFile;
  url?: LocalUrlResolver;
}

export type ExportArchiveInput = ArrayBuffer | ArrayBufferView | Blob;

export interface ArchiveManifestOptions {
  bytes: ExportArchiveInput;
  manifest?: string;
  loaders?: ArtifactLoader[];
}

export interface StaticExport {
  manifest: ExportManifest;
  scenarios(): string[];
  scenario(id: string): ManifestScenario;
  scenarioRecords(): ManifestScenario[];
  values(): string[];
  artifacts(value: string): string[];
  artifact(selection: ArtifactSelection): ArtifactHandle;
}

export interface StaticExportArchive extends StaticExport {
  dispose(): void;
}

export interface ArtifactHandle {
  artifact: ArtifactRecord;
  entry(): ArtifactFile;
  file(key: string): ArtifactFile;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
  load<T = unknown>(): Promise<T>;
}

export interface ArtifactFile {
  ref: BlobRef;
  url(): string;
  fetch(init?: RequestInit): Promise<Response>;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
}

export interface ArtifactLoaderContext {
  artifact: ArtifactRecord;
  selection: ArtifactSelection;
  entry(): ArtifactFile;
  file(key: string): ArtifactFile;
}

export interface ArtifactLoader<T = unknown> {
  supports: string | readonly string[];
  load(context: ArtifactLoaderContext): T | Promise<T>;
}
