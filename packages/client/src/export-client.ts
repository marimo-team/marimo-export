import {
  archiveRequest,
  archiveWithClient,
  exportRequest,
  exportWithClient,
  listRunningNotebooks,
  listWorkspaceNotebookFiles,
  readWorkspaceNotebookSource,
} from "./export-core.js";
import type {
  ExportArchiveOptions,
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  MarimoExportTransport,
  MarimoWorkspaceTransport,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types.js";
import type { ExportSpecInput } from "./spec.js";

export interface MarimoExportClient {
  export(spec: ExportSpecInput, options?: ExportOptions): Promise<ExportResult>;
  archive(spec: ExportSpecInput, options?: ExportArchiveOptions): Promise<ExportArchiveResult>;
}

export interface MarimoWorkspaceClient {
  sessions: {
    list(): Promise<RunningNotebook[]>;
  };
  notebooks: {
    list(): Promise<WorkspaceNotebook[]>;
    source(path: string): Promise<string>;
  };
}

export function createMarimoExportClientFromTransport(
  client: MarimoExportTransport,
): MarimoExportClient {
  return {
    export(spec, request = {}) {
      return exportWithClient(spec, {
        client,
        ...exportRequest(request),
      });
    },
    archive(spec, request = {}) {
      return archiveWithClient(spec, {
        client,
        ...archiveRequest(request),
      });
    },
  };
}

export function createMarimoWorkspaceClientFromTransport(
  client: MarimoWorkspaceTransport,
): MarimoWorkspaceClient {
  return {
    sessions: {
      list() {
        return listRunningNotebooks(client);
      },
    },
    notebooks: {
      list() {
        return listWorkspaceNotebookFiles(client);
      },
      source(path) {
        return readWorkspaceNotebookSource(client, path);
      },
    },
  };
}
