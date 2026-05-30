export const EXPORT_ARCHIVE_MEDIA_TYPE = "application/vnd.marimo.static-export+zip";
export const DEFAULT_MOEXPORT_PACKAGE =
  "moexport @ https://files.peter.gy/pkg/py/moexport/moexport-0.1.0-py3-none-any.whl";

export type CaptureFetch = (request: Request) => Promise<Response>;

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
  bundle?: string;
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

export type CaptureRuntimeOption = false | CaptureRuntimeInstallOptions;

export interface CaptureRuntimeInstallOptions {
  package?: string;
  module?: string;
  manager?: string;
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
