import {
  createMarimoExportClientFromTransport,
  createMarimoWorkspaceClientFromTransport,
} from "./export-client";
import { createNotebookOpener, createPost, createScratchpadExecutor } from "./transport";
import type { MarimoExportTransport } from "./types";
import type { MarimoExportClient, MarimoWorkspaceClient } from "./export-client";

export interface MarimoExportClientOptions {
  server: string | URL;
  fetch?: (request: Request) => Promise<Response>;
  headers?: HeadersInit;
  token?: string;
  serverToken?: string;
  WebSocket?: typeof WebSocket;
}

export type { MarimoExportClient, MarimoWorkspaceClient };
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

export function createMarimoWorkspaceClient(
  options: MarimoExportClientOptions,
): MarimoWorkspaceClient {
  const marimo = createBrowserTransport(options);
  return createMarimoWorkspaceClientFromTransport(marimo);
}

function createBrowserTransport(options: MarimoExportClientOptions): MarimoExportTransport {
  return {
    POST: createPost(options),
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  };
}
