import { createMarimoClient } from "@marimo-team/marimo-api";
import {
  createMarimoExportClientFromTransport,
  createMarimoWorkspaceClientFromTransport,
} from "./export-client";
import {
  baseUrl,
  createNotebookOpener,
  createScratchpadExecutor,
  requestHeaders,
} from "./transport";
import type { MarimoExportTransport } from "./types";
import type { MarimoExportClient, MarimoWorkspaceClient } from "./export-client";

type MarimoApiClient = ReturnType<typeof createMarimoClient>;

type MarimoTransportClient = MarimoApiClient & MarimoExportTransport;

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
  ExportArchiveOptions,
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";
export type { ExportSpec } from "./spec";

export function createMarimoExportClient(options: MarimoExportClientOptions): MarimoExportClient {
  const marimo = createMarimoTransport(options);
  return createMarimoExportClientFromTransport(marimo);
}

export function createMarimoWorkspaceClient(
  options: MarimoExportClientOptions,
): MarimoWorkspaceClient {
  const marimo = createMarimoTransport(options);
  return createMarimoWorkspaceClientFromTransport(marimo);
}

function createMarimoTransport(options: MarimoExportClientOptions): MarimoTransportClient {
  const { server, fetch } = options;
  const client = createMarimoClient({
    baseUrl: baseUrl(server),
    ...(fetch ? { fetch } : {}),
    headers: requestHeaders(options),
  });

  return Object.assign(client, {
    executeScratchpad: createScratchpadExecutor(options),
    openNotebook: createNotebookOpener(options),
  }) as MarimoTransportClient;
}
