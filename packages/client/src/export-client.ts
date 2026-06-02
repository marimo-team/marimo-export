import {
  archiveWithClient,
  exportRequest,
  exportWithClient,
  listRunningNotebooks,
  listWorkspaceNotebookFiles,
  readWorkspaceNotebookSource,
} from "./export-core";
import type {
  ExportArchiveResult,
  ExportOptions,
  ExportResult,
  MarimoExportTransport,
  RunningNotebook,
  WorkspaceNotebook,
} from "./types";
import type { ExportSpec } from "./spec";

export interface MarimoExportClient {
  export(spec: ExportSpec, options?: ExportOptions): Promise<ExportResult>;
  archive(spec: ExportSpec, options?: ExportOptions): Promise<ExportArchiveResult>;
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
        ...exportRequest(request),
      });
    },
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
