import { createMarimoClient } from "@marimo-team/marimo-api";
import {
  captureArchiveWithClient,
  captureBundleWithClient,
  captureRequest,
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
  CaptureArchiveResult,
  CaptureClient,
  CaptureClientOptionsBase,
  CaptureOptions,
  CaptureResult,
  ExportSpecInput,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";

export type MarimoApiClient = ReturnType<typeof createMarimoClient>;
export type MarimoCaptureClient = MarimoApiClient & CaptureClient;
export type MarimoArchiveCaptureClient = MarimoCaptureClient;

export type ExportClientOptions = CaptureClientOptionsBase;

export interface ExportClient {
  readonly marimo: MarimoArchiveCaptureClient;
  capture(spec: ExportSpecInput, options?: CaptureOptions): Promise<CaptureResult>;
  captureArchive(spec: ExportSpecInput, options?: CaptureOptions): Promise<CaptureArchiveResult>;
  listSessions(): Promise<RunningNotebook[]>;
  listWorkspaceNotebooks(): Promise<WorkspaceNotebook[]>;
}

export type {
  CaptureArchiveResult,
  CaptureFetch,
  CaptureOptions,
  CaptureResult,
  ExecuteScratchpadOptions,
  ExportSpecInput,
  OpenNotebookOptions,
  RunningNotebook,
  RuntimeInstallOptions,
  RuntimeOption,
  ScratchpadExecutionResult,
  ScratchpadOutput,
  WorkspaceNotebook,
} from "./types";

export function createExportClient(options: ExportClientOptions): ExportClient {
  const marimo = createMarimoTransport(options);
  return {
    marimo,
    capture(spec, request = {}) {
      return captureBundleWithClient(spec, {
        client: marimo,
        ...captureRequest(request),
      });
    },
    captureArchive(spec, request = {}) {
      return captureArchiveWithClient(spec, {
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
