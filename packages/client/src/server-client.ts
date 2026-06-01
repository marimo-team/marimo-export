import { createMarimoClient } from "@marimo-team/marimo-api";
import {
  captureExportArchiveWithClient as captureExportArchiveWithBaseClient,
  captureRequest,
  captureExportWithClient as captureExportWithBaseClient,
  listRunningNotebooks,
  listWorkspaceNotebooks as listWorkspaceNotebooksWithClient,
} from "./capture-core";
import {
  baseUrl,
  createNotebookOpener,
  createScratchpadExecutor,
  requestHeaders,
} from "./transport";
import type {
  CaptureClient,
  CaptureClientOptionsBase,
  CaptureExportArchiveResult,
  CaptureExportRequest,
  CaptureExportResult,
  ExportSpecInput,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";

export type MarimoApiClient = ReturnType<typeof createMarimoClient>;
export type MarimoCaptureClient = MarimoApiClient & CaptureClient;
export type MarimoArchiveCaptureClient = MarimoCaptureClient;

export type ExportClientOptions = CaptureClientOptionsBase;

export type CaptureExportOptions =
  | (CaptureExportRequest & ExportClientOptions & { client?: never })
  | (CaptureExportRequest & { client: ExportClient });

export type CaptureExportArchiveOptions =
  | (CaptureExportRequest & ExportClientOptions & { client?: never })
  | (CaptureExportRequest & { client: ExportClient });

export interface ExportClient {
  readonly marimo: MarimoArchiveCaptureClient;
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

export function createExportClient(options: ExportClientOptions): ExportClient {
  const marimo = createMarimoTransport(options);
  return {
    marimo,
    capture(spec, request = {}) {
      return captureExportWithBaseClient(spec, {
        client: marimo,
        ...captureRequest(request),
      });
    },
    captureArchive(spec, request = {}) {
      return captureExportArchiveWithBaseClient(spec, {
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

function createMarimoTransport(options: ExportClientOptions): MarimoArchiveCaptureClient {
  const { server, fetch } = options;
  const client = createMarimoClient({
    baseUrl: baseUrl(server),
    ...(fetch ? { fetch } : {}),
    headers: requestHeaders(options),
  });

  return Object.assign(client, {
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  }) as MarimoArchiveCaptureClient;
}

export async function captureExport(
  spec: ExportSpecInput,
  options: CaptureExportOptions,
): Promise<CaptureExportResult> {
  const client = hasExportClient(options) ? options.client : createExportClient(options);
  return client.capture(spec, captureRequest(options));
}

export async function captureExportArchive(
  spec: ExportSpecInput,
  options: CaptureExportArchiveOptions,
): Promise<CaptureExportArchiveResult> {
  const client = hasExportClient(options) ? options.client : createExportClient(options);
  return client.captureArchive(spec, captureRequest(options));
}

function hasExportClient(
  options: CaptureExportOptions | CaptureExportArchiveOptions,
): options is CaptureExportRequest & { client: ExportClient } {
  return "client" in options && options.client !== undefined;
}
