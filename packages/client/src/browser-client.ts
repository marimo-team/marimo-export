import {
  captureExportArchiveWithClient,
  captureRequest,
  captureExportWithClient,
  listRunningNotebooks,
  listWorkspaceNotebooks as listWorkspaceNotebooksWithClient,
} from "./capture-core";
import { createNotebookOpener, createPost, createScratchpadExecutor } from "./transport";
import type {
  ArchiveCaptureClient,
  CaptureClient,
  CaptureClientOptionsBase,
  CaptureExportArchiveResult,
  CaptureExportRequest,
  CaptureExportResult,
  CaptureFetch,
  CapturePostOptions,
  CapturePostResult,
  ExportSpecInput,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";

export type BrowserFetch = CaptureFetch;
export type BrowserCaptureClientOptions = CaptureClientOptionsBase;
export type BrowserPostOptions = CapturePostOptions;
export type BrowserPostResult = CapturePostResult;
export type BrowserCaptureClient = CaptureClient;
export type BrowserArchiveCaptureClient = ArchiveCaptureClient;
export type BrowserExportClientOptions = CaptureClientOptionsBase;

export type CaptureExportOptions =
  | (CaptureExportRequest & BrowserExportClientOptions & { client?: never })
  | (CaptureExportRequest & { client: BrowserExportClient });

export type CaptureExportArchiveOptions =
  | (CaptureExportRequest & BrowserExportClientOptions & { client?: never })
  | (CaptureExportRequest & { client: BrowserExportClient });

export interface BrowserExportClient {
  readonly marimo: BrowserArchiveCaptureClient;
  capture(spec: ExportSpecInput, options?: CaptureExportRequest): Promise<CaptureExportResult>;
  captureArchive(
    spec: ExportSpecInput,
    options?: CaptureExportRequest,
  ): Promise<CaptureExportArchiveResult>;
  listSessions(): Promise<RunningNotebook[]>;
  listWorkspaceNotebooks(): Promise<WorkspaceNotebook[]>;
}

export type {
  CaptureExportArchiveResult,
  CaptureExportRequest,
  CaptureExportResult,
  CaptureRuntimeInstallOptions,
  CaptureRuntimeOption,
  ExecuteScratchpadOptions,
  OpenNotebookOptions,
  RunningNotebook,
  ScratchpadExecutionResult,
  ScratchpadOutput,
  WorkspaceNotebook,
} from "./types";

export function createExportClient(options: BrowserExportClientOptions): BrowserExportClient {
  const marimo = createBrowserTransport(options);
  return {
    marimo,
    capture(spec, request = {}) {
      return captureExportWithClient(spec, {
        client: marimo,
        ...captureRequest(request),
      });
    },
    captureArchive(spec, request = {}) {
      return captureExportArchiveWithClient(spec, {
        client: marimo,
        ...captureRequest(request),
      });
    },
    listSessions() {
      return listRunningNotebooks(marimo);
    },
    listWorkspaceNotebooks() {
      return listWorkspaceNotebooksWithClient(marimo);
    },
  };
}

function createBrowserTransport(options: BrowserExportClientOptions): BrowserArchiveCaptureClient {
  return {
    POST: createPost(options),
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  };
}

export async function captureExport(
  spec: ExportSpecInput,
  options: CaptureExportOptions,
): Promise<CaptureExportResult> {
  const client = exportClient(options);
  return client.capture(spec, captureRequest(options));
}

export async function captureExportArchive(
  spec: ExportSpecInput,
  options: CaptureExportArchiveOptions,
): Promise<CaptureExportArchiveResult> {
  const client = exportClient(options);
  return client.captureArchive(spec, captureRequest(options));
}

function exportClient(
  options: CaptureExportOptions | CaptureExportArchiveOptions,
): BrowserExportClient {
  if ("client" in options && options.client !== undefined) {
    return options.client;
  }

  return createExportClient(options);
}
