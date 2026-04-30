import {
  DEFAULT_MOEXPORT_PACKAGE,
  EXPORT_ARCHIVE_MEDIA_TYPE,
  type CaptureClient,
  type CaptureExportArchiveResult,
  type CaptureExportRequest,
  type CaptureExportResult,
  type CaptureRuntimeOption,
  type RunningNotebook,
} from "./types";
import { isRecord, sleep } from "./support";

export async function captureExportWithClient(
  spec: Record<string, unknown>,
  options: CaptureExportRequest & { client: CaptureClient },
): Promise<CaptureExportResult> {
  const { client, bundle, runtime } = options;
  const session = await resolveCaptureSession(client, options);
  await ensureCaptureRuntime(client, session, runtime);
  const code = captureCode(spec, bundle);
  const { response: httpResponse, data } = await client.POST("/api/kernel/scratchpad/run", {
    params: {
      header: {
        "Marimo-Session-Id": session.sessionId,
      },
    },
    body: { code },
  });
  const { ok, status, statusText } = httpResponse;

  if (!ok) {
    throw new Error(`Failed to dispatch marimo capture request: ${status} ${statusText}`);
  }

  const success = isRecord(data) && typeof data.success === "boolean" ? data.success : true;
  if (!success) {
    throw new Error("marimo capture request was not accepted.");
  }

  return {
    success,
    dispatched: true,
    session,
  };
}

export async function captureExportArchiveWithClient(
  spec: Record<string, unknown>,
  options: CaptureExportRequest & { client: CaptureClient },
): Promise<CaptureExportArchiveResult> {
  const { client, bundle, runtime } = options;
  const session = await resolveCaptureSession(client, options);
  await ensureCaptureRuntime(client, session, runtime);
  const marker = archiveMarker();
  const code = captureArchiveCode(spec, bundle, marker);
  const { stdout } = await client.executeScratchpad({
    code,
    sessionId: session.sessionId,
    ...(options.executionTimeoutMs !== undefined ? { timeoutMs: options.executionTimeoutMs } : {}),
  });
  const payload = archivePayload(stdout, marker);

  return {
    bytes: base64ToBytes(payload),
    mediaType: EXPORT_ARCHIVE_MEDIA_TYPE,
    session,
  };
}

export async function listRunningNotebooks(client: CaptureClient): Promise<RunningNotebook[]> {
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

export async function resolveCaptureSession(
  client: CaptureClient,
  options: Pick<CaptureExportRequest, "sessionId" | "notebook"> = {},
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

export async function ensureCaptureRuntime(
  client: CaptureClient,
  session: Pick<RunningNotebook, "sessionId">,
  options: CaptureRuntimeOption | undefined = {},
): Promise<void> {
  if (options === false) {
    return;
  }

  const packageSpec = options.package ?? DEFAULT_MOEXPORT_PACKAGE;
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

export function captureRequest(options: CaptureExportRequest): CaptureExportRequest {
  return {
    ...(options.bundle !== undefined ? { bundle: options.bundle } : {}),
    ...(options.notebook !== undefined ? { notebook: options.notebook } : {}),
    ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
    ...(options.runtime !== undefined ? { runtime: options.runtime } : {}),
    ...(options.executionTimeoutMs !== undefined
      ? { executionTimeoutMs: options.executionTimeoutMs }
      : {}),
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

function captureCode(spec: Record<string, unknown>, bundle: string | undefined): string {
  const specJson = JSON.stringify(spec);
  const bundleExpression = bundle === undefined ? "None" : JSON.stringify(bundle);

  return [
    "import json",
    "import moexport as mox",
    `__moexport_spec = json.loads(${JSON.stringify(specJson)})`,
    `await mox.export(__moexport_spec, bundle=${bundleExpression})`,
  ].join("\n");
}

function captureArchiveCode(
  spec: Record<string, unknown>,
  bundle: string | undefined,
  marker: string,
): string {
  const specJson = JSON.stringify(spec);
  const bundleExpression = bundle === undefined ? "None" : JSON.stringify(bundle);

  return [
    "import json",
    "import importlib",
    "import moexport.archive as __moexport_archive",
    "__moexport_archive = importlib.reload(__moexport_archive)",
    `__moexport_spec = json.loads(${JSON.stringify(specJson)})`,
    `await __moexport_archive.emit_bundle_archive(__moexport_spec, bundle=${bundleExpression}, marker=${JSON.stringify(marker)})`,
  ].join("\n");
}

function archiveMarker(): string {
  return `__MOEXPORT_ARCHIVE_${Date.now()}_${Math.random().toString(36).slice(2)}__`;
}

function archivePayload(stdout: string[], marker: string): string {
  const text = stdout.join("");
  const start = text.indexOf(marker);

  if (start < 0) {
    throw new Error("marimo archive capture completed without an archive payload.");
  }

  const payload = text.slice(start + marker.length);
  const end = payload.search(/\r?\n/);
  return (end < 0 ? payload : payload.slice(0, end)).trim();
}

function base64ToBytes(value: string): Uint8Array {
  if (typeof globalThis.atob !== "function") {
    throw new Error("captureExportArchive requires atob to decode the archive payload.");
  }

  const binary = globalThis.atob(value);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return bytes;
}

async function canImportModule(
  client: CaptureClient,
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
  client: CaptureClient,
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
