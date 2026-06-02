import { createMarimoWorkspaceClientFromTransport } from "./export-client.js";
import { createPost } from "./transport.js";
import type { MarimoWorkspaceClient } from "./export-client.js";
import type { ExportTransportFetch, WorkspaceNotebook, RunningNotebook } from "./types.js";

export interface MarimoWorkspaceClientOptions {
  server: string | URL;
  fetch?: ExportTransportFetch;
  headers?: HeadersInit;
  token?: string;
  serverToken?: string;
}

export type { MarimoWorkspaceClient };
export type { RunningNotebook, WorkspaceNotebook };

export function createMarimoWorkspaceClient(
  options: MarimoWorkspaceClientOptions,
): MarimoWorkspaceClient {
  return createMarimoWorkspaceClientFromTransport({
    POST: createPost(options),
  });
}
