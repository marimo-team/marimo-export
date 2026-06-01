export const EXPORT_ARCHIVE_MEDIA_TYPE = "application/vnd.marimo.static-export+zip";

export type CaptureFetch = (request: Request) => Promise<Response>;
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };
export type JsonObject = { readonly [key: string]: JsonValue };

export interface CaptureClientOptionsBase {
  server: string | URL;
  fetch?: CaptureFetch;
  headers?: HeadersInit;
  token?: string;
  serverToken?: string;
  WebSocket?: typeof WebSocket;
}

export interface CapturePostOptions {
  params?: {
    header?: Record<string, string>;
  };
  body?: unknown;
}

export interface CapturePostResult {
  response: Response;
  data: unknown;
}

export interface CaptureClient {
  POST(path: string, options?: CapturePostOptions): Promise<CapturePostResult>;
  executeScratchpad(options: ExecuteScratchpadOptions): Promise<ScratchpadExecutionResult>;
  openNotebook(options: OpenNotebookOptions): Promise<RunningNotebook>;
}

export type ArchiveCaptureClient = CaptureClient;

export interface RunningNotebook {
  sessionId: string;
  name: string | null;
  path: string | null;
  initializationId: string | null;
}

export interface WorkspaceNotebook {
  id: string;
  name: string;
  path: string;
}

export interface CaptureExportRequest {
  sessionId?: string;
  notebook?: string;
  to?: string;
  runtime?: CaptureRuntimeOption;
  executionTimeoutMs?: number;
}

export interface CaptureExportResult {
  session: RunningNotebook;
  bundlePath: string;
  manifestPath: string;
  invocationPath: string;
  invocationIndexPath: string;
  manifest: Record<string, unknown>;
  invocation: Record<string, unknown>;
}

export interface CaptureExportArchiveResult {
  bytes: Uint8Array;
  mediaType: typeof EXPORT_ARCHIVE_MEDIA_TYPE;
  session: RunningNotebook;
}

export interface ExecuteScratchpadOptions {
  code: string;
  sessionId: string;
  timeoutMs?: number;
}

export interface OpenNotebookOptions {
  notebook: string;
  sessionId?: string;
  timeoutMs?: number;
}

export type CaptureRuntimeOption = "preinstalled" | CaptureRuntimeInstallOptions;

export interface CaptureRuntimeInstallOptions {
  install: string;
  module?: string;
  manager?: "uv" | "pip" | string;
  source?: "kernel" | "server";
  force?: boolean;
  timeoutMs?: number;
  pollIntervalMs?: number;
}

export type SourceSpecInput =
  | { def: string }
  | { expr: string }
  | { cell: string | number | JsonObject; on_error?: "raise" | "record" }
  | { snapshot?: JsonObject; notebook?: JsonObject }
  | { report: JsonObject }
  | ({ type: string } & JsonObject);

export interface RefExportInput {
  type: "ref";
  ref: string;
}

export interface CodeExportInput {
  type: "code";
  code: string;
}

export type ExportCallableInput = RefExportInput | CodeExportInput;

export interface ArtifactSpecInput {
  export: ExportCallableInput;
  options?: JsonObject;
}

export type ArtifactInput =
  | string
  | { artifact: string; options?: JsonObject }
  | Record<string, JsonObject | ArtifactSpecInput | null>;

export interface ValueSpecInput {
  source: SourceSpecInput;
  artifacts: Record<string, ArtifactSpecInput | JsonObject | null> | ArtifactInput[];
}

export interface ScenarioSpecInput {
  id?: string;
  state?: JsonObject;
  patches?: JsonObject;
}

export interface ExportSpecInput {
  scenarios?: ScenarioSpecInput[];
  provenance?: {
    source?: "none" | "hash" | "source";
    spec?: "none" | "hash" | "embed";
  };
  values: Record<string, ValueSpecInput>;
}

export interface ScratchpadExecutionResult {
  success: boolean;
  output: ScratchpadOutput | null;
  stdout: string[];
  stderr: string[];
}

export interface ScratchpadOutput {
  mimetype: string;
  data: string;
}
