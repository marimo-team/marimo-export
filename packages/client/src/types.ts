export const EXPORT_ARCHIVE_MEDIA_TYPE = "application/vnd.marimo.static-export+zip";

export type ExportTransportFetch = (request: Request) => Promise<Response>;
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };
export type JsonObject = { readonly [key: string]: JsonValue };

export interface ExportTransportOptions {
  server: string | URL;
  fetch?: ExportTransportFetch;
  headers?: HeadersInit;
  token?: string;
  serverToken?: string;
  WebSocket?: typeof WebSocket;
}

export interface ExportTransportPostOptions {
  params?: {
    header?: Record<string, string>;
  };
  body?: unknown;
}

export interface ExportTransportPostResult {
  response: Response;
  data: unknown;
}

export interface MarimoWorkspaceTransport {
  POST(path: string, options?: ExportTransportPostOptions): Promise<ExportTransportPostResult>;
}

export interface MarimoExportTransport extends MarimoWorkspaceTransport {
  executeScratchpad(options: ExecuteScratchpadOptions): Promise<ScratchpadExecutionResult>;
  openNotebook(options: OpenNotebookOptions): Promise<RunningNotebook>;
}

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

export interface ExportArchiveOptions {
  sessionId?: string;
  notebook?: string;
  paths?: readonly string[];
  runtime?: RuntimeOption;
  timeoutMs?: number;
}

export interface ExportOptions extends ExportArchiveOptions {
  outputRoot?: string;
}

export interface ExportResult {
  sessionId: string;
  sessionName: string | null;
  sessionPath: string | null;
  sessionInitializationId: string | null;
  bundlePath: string;
  manifestPath: string;
  invocationPath: string;
  invocationIndexPath: string;
  manifest: Record<string, unknown>;
  invocation: Record<string, unknown>;
}

export interface ExportArchiveResult {
  bytes: Uint8Array;
  mediaType: typeof EXPORT_ARCHIVE_MEDIA_TYPE;
  sessionId: string;
  sessionName: string | null;
  sessionPath: string | null;
  sessionInitializationId: string | null;
}

export interface ExecuteScratchpadOptions {
  code: string;
  sessionId: string;
  metadata?: ScratchpadExecutionMetadata;
  timeoutMs?: number;
}

export type ScratchpadExecutionMetadata =
  | {
      kind: "export";
      marker: string;
      outputRoot?: string;
      paths?: readonly string[];
      spec: unknown;
    }
  | {
      kind: "archive";
      marker: string;
      paths?: readonly string[];
      spec: unknown;
    }
  | {
      kind: "import";
      marker: string;
      moduleName: string;
    };

export interface OpenNotebookOptions {
  notebook: string;
  sessionId?: string;
  timeoutMs?: number;
}

export type RuntimeOption = "preinstalled" | Runtime;

export interface Runtime {
  package: string;
  module?: string;
  manager?: "uv" | "pip" | string;
  source?: "kernel" | "server";
  force?: boolean;
  timeoutMs?: number;
  pollIntervalMs?: number;
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
