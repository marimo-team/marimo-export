import { createMarimoExportClientFromTransport } from "./export-client.js";
import { createNotebookOpener, createPost, createScratchpadExecutor } from "./transport.js";
import type { MarimoExportTransport } from "./types.js";
import type { MarimoExportClient } from "./export-client.js";

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
