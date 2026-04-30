import { createMarimoClient } from "@marimo-team/marimo-api";
import {
  captureExportArchiveWithClient as captureExportArchiveWithBaseClient,
  captureRequest,
  captureExportWithClient as captureExportWithBaseClient,
  ensureCaptureRuntime,
  listRunningNotebooks,
  resolveCaptureSession,
} from "./capture-core";
import { isRecord } from "./support";
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
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";

export type MarimoApiClient = ReturnType<typeof createMarimoClient>;
export type MarimoCaptureClient = MarimoApiClient & CaptureClient;
export type MarimoArchiveCaptureClient = MarimoCaptureClient;

export type CaptureClientOptions = CaptureClientOptionsBase;

export type CaptureExportOptions =
  | (CaptureExportRequest & CaptureClientOptions & { client?: never })
  | (CaptureExportRequest & { client: MarimoCaptureClient });

export type CaptureExportArchiveOptions =
  | (CaptureExportRequest & CaptureClientOptions & { client?: never })
  | (CaptureExportRequest & { client: MarimoArchiveCaptureClient });

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

export { ensureCaptureRuntime, listRunningNotebooks, resolveCaptureSession };

export function createCaptureClient(options: CaptureClientOptions): MarimoArchiveCaptureClient {
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
  spec: Record<string, unknown>,
  options: CaptureExportOptions,
): Promise<CaptureExportResult> {
  const client = hasCaptureClient(options) ? options.client : createCaptureClient(options);
  return captureExportWithClient(spec, {
    client,
    ...captureRequest(options),
  });
}

export async function captureExportWithClient(
  spec: Record<string, unknown>,
  options: CaptureExportRequest & { client: MarimoCaptureClient },
): Promise<CaptureExportResult> {
  return captureExportWithBaseClient(spec, options);
}

export async function captureExportArchive(
  spec: Record<string, unknown>,
  options: CaptureExportArchiveOptions,
): Promise<CaptureExportArchiveResult> {
  return captureExportArchiveWithBaseClient(spec, {
    client: archiveCaptureClient(options),
    ...captureRequest(options),
  });
}

export async function listWorkspaceNotebooks(
  client: MarimoCaptureClient,
): Promise<WorkspaceNotebook[]> {
  const { response: httpResponse, data } = await client.POST("/api/home/workspace_files", {
    body: {
      includeMarkdown: false,
    },
  });
  const { ok, status, statusText } = httpResponse;

  if (!ok) {
    throw new Error(`Failed to list marimo workspace notebooks: ${status} ${statusText}`);
  }

  const files = isRecord(data) && Array.isArray(data.files) ? data.files : [];
  return flattenWorkspaceFiles(files)
    .filter(isMarimoWorkspaceFile)
    .map((file) => ({
      id: String(file.id),
      name: String(file.name),
      path: String(file.path),
    }));
}

function hasCaptureClient(
  options: CaptureExportOptions,
): options is CaptureExportRequest & { client: MarimoCaptureClient } {
  return "client" in options && options.client !== undefined;
}

function hasArchiveCaptureClient(
  options: CaptureExportArchiveOptions,
): options is CaptureExportRequest & { client: MarimoArchiveCaptureClient } {
  return (
    "client" in options &&
    options.client !== undefined &&
    "executeScratchpad" in options.client &&
    typeof options.client.executeScratchpad === "function"
  );
}

function archiveCaptureClient(options: CaptureExportArchiveOptions): MarimoArchiveCaptureClient {
  if (hasArchiveCaptureClient(options)) {
    return options.client;
  }

  if ("client" in options) {
    throw new Error("captureExportArchive requires a client returned by createCaptureClient(...).");
  }

  return createCaptureClient(options);
}

type WorkspaceFileNode = Record<string, unknown> & {
  children?: unknown;
  id: unknown;
  isMarimoFile: unknown;
  name: unknown;
  path: unknown;
};

function flattenWorkspaceFiles(files: unknown[]): WorkspaceFileNode[] {
  const flattened: WorkspaceFileNode[] = [];
  for (const file of files) {
    if (!isWorkspaceFileNode(file)) {
      continue;
    }

    flattened.push(file);
    if (Array.isArray(file.children)) {
      flattened.push(...flattenWorkspaceFiles(file.children));
    }
  }
  return flattened;
}

function isWorkspaceFileNode(value: unknown): value is WorkspaceFileNode {
  return (
    isRecord(value) &&
    "id" in value &&
    "isMarimoFile" in value &&
    "name" in value &&
    "path" in value
  );
}

function isMarimoWorkspaceFile(file: WorkspaceFileNode): boolean {
  return file.isMarimoFile === true;
}
