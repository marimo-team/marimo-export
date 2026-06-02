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
export {
  createMarimoExportClientFromTransport,
  createMarimoWorkspaceClientFromTransport,
} from "./export-client";

export type {
  BuiltinFormatName,
  BuiltinFormatConfig,
  BuiltinFormatMap,
  CellSelector,
  CodeExport,
  DefinitionSource,
  ExportCallable,
  ExportSpec,
  ExportSpecParseResult,
  ExplicitFormat,
  ExplicitFormatMap,
  ExpressionSource,
  FormatInput,
  FormatMap,
  NamedBuiltinFormat,
  NotebookSnapshotOptions,
  NotebookSnapshotShorthand,
  NotebookSnapshotSource,
  ProvenanceSpec,
  RefExport,
  ReportCellInput,
  ReportSource,
  ReportSourceInput,
  ScenarioSpec,
  SourceSpec,
  ValueSpec,
} from "./spec";
export { builtinFormatNames, exportSpecSchema, parseExportSpec, safeParseExportSpec } from "./spec";

export type {
  ExecuteScratchpadOptions,
  ExportArchiveOptions,
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  MarimoExportTransport,
  RunningNotebook,
  ScratchpadExecutionMetadata,
  WorkspaceNotebook,
} from "./types";

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
