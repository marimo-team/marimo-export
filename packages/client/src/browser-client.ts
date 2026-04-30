import {
  captureExportArchiveWithClient,
  captureRequest,
  captureExportWithClient,
  ensureCaptureRuntime,
  listRunningNotebooks,
  resolveCaptureSession,
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
} from "./types";

export type BrowserFetch = CaptureFetch;
export type BrowserCaptureClientOptions = CaptureClientOptionsBase;
export type BrowserPostOptions = CapturePostOptions;
export type BrowserPostResult = CapturePostResult;
export type BrowserCaptureClient = CaptureClient;
export type BrowserArchiveCaptureClient = ArchiveCaptureClient;

export type CaptureExportOptions =
  | (CaptureExportRequest & BrowserCaptureClientOptions & { client?: never })
  | (CaptureExportRequest & { client: BrowserCaptureClient });

export type CaptureExportArchiveOptions =
  | (CaptureExportRequest & BrowserCaptureClientOptions & { client?: never })
  | (CaptureExportRequest & { client: BrowserArchiveCaptureClient });

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
} from "./types";

export { ensureCaptureRuntime, listRunningNotebooks, resolveCaptureSession };

export function createBrowserCaptureClient(
  options: BrowserCaptureClientOptions,
): BrowserArchiveCaptureClient {
  return {
    POST: createPost(options),
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  };
}

export async function captureExport(
  spec: Record<string, unknown>,
  options: CaptureExportOptions,
): Promise<CaptureExportResult> {
  const client = captureClient(options);
  return captureExportWithClient(spec, {
    client,
    ...captureRequest(options),
  });
}

export async function captureExportArchive(
  spec: Record<string, unknown>,
  options: CaptureExportArchiveOptions,
): Promise<CaptureExportArchiveResult> {
  return captureExportArchiveWithClient(spec, {
    client: archiveCaptureClient(options),
    ...captureRequest(options),
  });
}

function captureClient(options: CaptureExportOptions): BrowserCaptureClient {
  if ("client" in options && options.client !== undefined) {
    return options.client;
  }

  return createBrowserCaptureClient(options);
}

function archiveCaptureClient(options: CaptureExportArchiveOptions): BrowserArchiveCaptureClient {
  if ("client" in options && options.client !== undefined) {
    if (
      "executeScratchpad" in options.client &&
      typeof options.client.executeScratchpad === "function"
    ) {
      return options.client;
    }

    throw new Error(
      "captureExportArchive requires a client returned by createBrowserCaptureClient(...).",
    );
  }

  return createBrowserCaptureClient(options);
}
