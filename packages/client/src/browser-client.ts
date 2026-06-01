import { createMarimoExportClientFromTransport } from "./export-client";
import { createNotebookOpener, createPost, createScratchpadExecutor } from "./transport";
import type { MarimoExportTransport } from "./types";
import type { MarimoExportClient } from "./export-client";

export interface MarimoExportClientOptions {
  server: string | URL;
  fetch?: (request: Request) => Promise<Response>;
  headers?: HeadersInit;
  token?: string;
  serverToken?: string;
  WebSocket?: typeof WebSocket;
}

export type { MarimoExportClient };
export type {
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";
export type { ExportSpec } from "./spec";

export function createMarimoExportClient(options: MarimoExportClientOptions): MarimoExportClient {
  const marimo = createBrowserTransport(options);
  return createMarimoExportClientFromTransport(marimo);
}

function createBrowserTransport(options: MarimoExportClientOptions): MarimoExportTransport {
  return {
    POST: createPost(options),
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  };
}
