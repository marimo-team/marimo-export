import {
  captureArchiveWithClient,
  captureBundleWithClient,
  captureRequest,
  listRunningNotebooks,
  listWorkspaceNotebooks as listWorkspaceNotebooksWithClient,
} from "./capture-core";
import { createNotebookOpener, createPost, createScratchpadExecutor } from "./transport";
import type {
  ArchiveCaptureClient,
  CaptureArchiveResult,
  CaptureClient,
  CaptureClientOptionsBase,
  CaptureFetch,
  CaptureOptions,
  CapturePostOptions,
  CapturePostResult,
  CaptureResult,
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

export interface BrowserExportClient {
  readonly marimo: BrowserArchiveCaptureClient;
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

export function createExportClient(options: BrowserExportClientOptions): BrowserExportClient {
  const marimo = createBrowserTransport(options);
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

function createBrowserTransport(options: BrowserExportClientOptions): BrowserArchiveCaptureClient {
  return {
    POST: createPost(options),
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  };
}
