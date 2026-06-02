import { createMarimoClient } from "@marimo-team/marimo-api";
import { createMarimoExportClientFromTransport } from "./export-client.js";
import {
  baseUrl,
  createNotebookOpener,
  createScratchpadExecutor,
  requestHeaders,
} from "./transport.js";
import type { MarimoExportTransport } from "./types.js";
import type { MarimoExportClient } from "./export-client.js";

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

export type { MarimoExportClient };

export type {
  ExportSpec,
  ExportSpecInput,
  ExportSpecIssue,
  ExportSpecParseResult,
} from "./spec.js";
export { parseExportSpec, safeParseExportSpec } from "./spec.js";

export type {
  ExportArchiveOptions,
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  Runtime,
  RuntimeOption,
} from "./types.js";

export function createMarimoExportClient(options: MarimoExportClientOptions): MarimoExportClient {
  const marimo = createMarimoTransport(options);
  return createMarimoExportClientFromTransport(marimo);
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
