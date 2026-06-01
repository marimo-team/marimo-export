import {
  EXPORT_ARCHIVE_MEDIA_TYPE,
  type ExportArchiveResult,
  type ExportOptions,
  type ExportResult,
  type MarimoExportTransport,
  type RunningNotebook,
  type RuntimeOption,
  type WorkspaceNotebook,
} from "./types";
import type { ExportSpec } from "./spec";
import { isRecord, sleep } from "./support";

export async function exportWithClient(
  spec: ExportSpec,
  options: ExportOptions & { client: MarimoExportTransport },
): Promise<ExportResult> {
  const { client, outputRoot, runtime } = options;
  const session = await resolveExportSession(client, options);
  await ensureExportRuntime(client, session, runtime);
  const marker = exportMarker();
  const code = exportCode(spec, outputRoot, marker);
  const { stdout } = await client.executeScratchpad({
    code,
    sessionId: session.sessionId,
    ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
  });
  const payload = exportPayload(stdout, marker);
  return {
    session,
    bundlePath: stringField(payload, "bundle_path"),
    manifestPath: stringField(payload, "manifest_path"),
    invocationPath: stringField(payload, "invocation_path"),
    invocationIndexPath: stringField(payload, "invocation_index_path"),
    manifest: objectField(payload, "manifest"),
    invocation: objectField(payload, "invocation"),
  };
}

export async function archiveWithClient(
  spec: ExportSpec,
  options: ExportOptions & { client: MarimoExportTransport },
): Promise<ExportArchiveResult> {
  const { client, outputRoot, runtime } = options;
  const session = await resolveExportSession(client, options);
  await ensureExportRuntime(client, session, runtime);
  const marker = archiveMarker();
  const code = archiveCode(spec, outputRoot, marker);
  const { stdout } = await client.executeScratchpad({
    code,
    sessionId: session.sessionId,
    ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
  });
  const payload = archivePayload(stdout, marker);

  return {
    bytes: base64ToBytes(payload),
    mediaType: EXPORT_ARCHIVE_MEDIA_TYPE,
    session,
  };
}

export async function listRunningNotebooks(
  client: MarimoExportTransport,
): Promise<RunningNotebook[]> {
  const { response: httpResponse, data } = await client.POST("/api/home/running_notebooks");
  const { ok, status, statusText } = httpResponse;

  if (!ok) {
    throw new Error(`Failed to list running marimo notebooks: ${status} ${statusText}`);
  }

  const files = isRecord(data) && Array.isArray(data.files) ? data.files : [];
  return files.filter(hasSessionId).map((file) => ({
    sessionId: String(file.sessionId),
    name: typeof file.name === "string" ? file.name : null,
    path: typeof file.path === "string" ? file.path : null,
    initializationId: typeof file.initializationId === "string" ? file.initializationId : null,
  }));
}

export async function listWorkspaceNotebookFiles(
  client: MarimoExportTransport,
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

export async function resolveExportSession(
  client: MarimoExportTransport,
  options: Pick<ExportOptions, "sessionId" | "notebook"> = {},
): Promise<RunningNotebook> {
  if (options.sessionId) {
    return {
      sessionId: options.sessionId,
      name: null,
      path: options.notebook ?? null,
      initializationId: null,
    };
  }

  const running = await listRunningNotebooks(client);
  const notebookQuery = options.notebook;
  if (notebookQuery) {
    const matches = running.filter((notebook) => notebookMatches(notebook, notebookQuery));
    if (matches.length === 1) {
      return matches[0] as RunningNotebook;
    }

    if (
      matches.length > 1 &&
      matches.every((match) => sessionKey(match) === sessionKey(matches[0]))
    ) {
      return matches[0] as RunningNotebook;
    }

    if (matches.length === 0) {
      return client.openNotebook({ notebook: notebookQuery });
    }

    throw new Error(
      `More than one running marimo session matched ${JSON.stringify(
        notebookQuery,
      )}. Pass sessionId explicitly. Matches: ${matches
        .map((match) => match.path ?? match.name ?? match.sessionId)
        .join(", ")}`,
    );
  }

  if (running.length === 1) {
    return running[0] as RunningNotebook;
  }

  if (running.length === 0) {
    throw new Error("No running marimo sessions found. Open a notebook or pass sessionId.");
  }

  throw new Error(
    `Found ${running.length} running marimo sessions. Pass notebook or sessionId. Sessions: ${running
      .map((session) => session.path ?? session.name ?? session.sessionId)
      .join(", ")}`,
  );
}

export async function ensureExportRuntime(
  client: MarimoExportTransport,
  session: Pick<RunningNotebook, "sessionId">,
  options: RuntimeOption | undefined = "preinstalled",
): Promise<void> {
  if (options === "preinstalled") {
    if (await canImportModule(client, session.sessionId, "moexport")) {
      return;
    }
    throw new Error(
      'moexport is not importable in the marimo kernel. Pass runtime: { package: "moexport @ ..." } to install it before export.',
    );
  }

  if (options === undefined) {
    return;
  }

  const packageSpec = options.package;
  const moduleName = options.module ?? "moexport";
  const manager = options.manager ?? "uv";
  const source = options.source ?? "kernel";

  if (!options.force && (await canImportModule(client, session.sessionId, moduleName))) {
    return;
  }

  const { response: httpResponse } = await client.POST("/api/kernel/install_missing_packages", {
    params: {
      header: {
        "Marimo-Session-Id": session.sessionId,
      },
    },
    body: {
      manager,
      source,
      versions: {
        [packageSpec]: "",
      },
    },
  });
  const { ok, status, statusText } = httpResponse;

  if (!ok) {
    throw new Error(`Failed to install ${moduleName} in marimo kernel: ${status} ${statusText}`);
  }

  await waitForImport(client, {
    moduleName,
    sessionId: session.sessionId,
    timeoutMs: options.timeoutMs ?? 120_000,
    pollIntervalMs: options.pollIntervalMs ?? 1_000,
  });
}

export function exportRequest(options: ExportOptions): ExportOptions {
  return {
    ...(options.outputRoot !== undefined ? { outputRoot: options.outputRoot } : {}),
    ...(options.notebook !== undefined ? { notebook: options.notebook } : {}),
    ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
    ...(options.runtime !== undefined ? { runtime: options.runtime } : {}),
    ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
  };
}

function hasSessionId(value: unknown): value is Record<string, unknown> & { sessionId: unknown } {
  return isRecord(value) && Boolean(value.sessionId);
}

function notebookMatches(notebook: RunningNotebook, query: string): boolean {
  const path = notebook.path ?? "";
  const name = notebook.name ?? "";
  return path === query || name === query || path.endsWith(`/${query}`);
}

function sessionKey(notebook: RunningNotebook | undefined): string {
  return notebook ? `${notebook.path ?? ""}\0${notebook.name ?? ""}` : "";
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

function exportCode(spec: ExportSpec, outputRoot: string | undefined, marker: string): string {
  const specJson = JSON.stringify(spec);
  const toExpression = outputRoot === undefined ? "None" : JSON.stringify(outputRoot);

  return [
    "import json",
    "import moexport as mox",
    `__moexport_spec = json.loads(${JSON.stringify(specJson)})`,
    `__moexport_result = await mox.capture(__moexport_spec, to=${toExpression})`,
    "__moexport_payload = {",
    '    "bundle_path": __moexport_result.bundle_path,',
    '    "manifest_path": __moexport_result.manifest_path,',
    '    "invocation_path": __moexport_result.invocation_path,',
    '    "invocation_index_path": __moexport_result.invocation_index_path,',
    '    "manifest": __moexport_result.manifest,',
    '    "invocation": __moexport_result.invocation,',
    "}",
    `print(${JSON.stringify(marker)} + json.dumps(__moexport_payload, allow_nan=False))`,
  ].join("\n");
}

function archiveCode(spec: ExportSpec, outputRoot: string | undefined, marker: string): string {
  const specJson = JSON.stringify(spec);
  const toExpression = outputRoot === undefined ? "None" : JSON.stringify(outputRoot);

  return [
    "import json",
    "import importlib",
    "import moexport.archive as __moexport_archive",
    "__moexport_archive = importlib.reload(__moexport_archive)",
    `__moexport_spec = json.loads(${JSON.stringify(specJson)})`,
    `await __moexport_archive.emit_bundle_archive(__moexport_spec, to=${toExpression}, marker=${JSON.stringify(marker)})`,
  ].join("\n");
}

function archiveMarker(): string {
  return `__MOEXPORT_ARCHIVE_${Date.now()}_${Math.random().toString(36).slice(2)}__`;
}

function exportMarker(): string {
  return `__MOEXPORT_RESULT_${Date.now()}_${Math.random().toString(36).slice(2)}__`;
}

function archivePayload(stdout: string[], marker: string): string {
  return markedPayload(stdout, marker, "archive");
}

function exportPayload(stdout: string[], marker: string): Record<string, unknown> {
  const payload = markedPayload(stdout, marker, "export result");
  const parsed = JSON.parse(payload) as unknown;
  if (!isRecord(parsed)) {
    throw new Error("marimo export completed with a non-object result payload.");
  }
  return parsed;
}

function markedPayload(stdout: string[], marker: string, label: string): string {
  const text = stdout.join("");
  const start = text.indexOf(marker);

  if (start < 0) {
    throw new Error(`marimo export completed without a ${label} payload.`);
  }

  const payload = text.slice(start + marker.length);
  const end = payload.search(/\r?\n/);
  return (end < 0 ? payload : payload.slice(0, end)).trim();
}

function stringField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== "string") {
    throw new Error(`marimo export result field ${key} must be a string.`);
  }
  return value;
}

function objectField(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  if (!isRecord(value)) {
    throw new Error(`marimo export result field ${key} must be an object.`);
  }
  return value;
}

function base64ToBytes(value: string): Uint8Array {
  if (typeof globalThis.atob !== "function") {
    throw new Error("archive requires atob to decode the archive payload.");
  }

  const binary = globalThis.atob(value);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return bytes;
}

async function canImportModule(
  client: MarimoExportTransport,
  sessionId: string,
  moduleName: string,
): Promise<boolean> {
  try {
    await client.executeScratchpad({
      sessionId,
      code: `import importlib.util\nif importlib.util.find_spec(${JSON.stringify(
        moduleName,
      )}) is None:\n    raise ModuleNotFoundError(${JSON.stringify(moduleName)})`,
    });
    return true;
  } catch {
    return false;
  }
}

async function waitForImport(
  client: MarimoExportTransport,
  {
    moduleName,
    pollIntervalMs,
    sessionId,
    timeoutMs,
  }: {
    moduleName: string;
    pollIntervalMs: number;
    sessionId: string;
    timeoutMs: number;
  },
): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (await canImportModule(client, sessionId, moduleName)) {
      return;
    }
    await sleep(pollIntervalMs);
  }

  throw new Error(`Timed out waiting for ${moduleName} to become importable in the marimo kernel.`);
}
