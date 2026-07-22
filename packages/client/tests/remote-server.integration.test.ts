import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, test } from "vite-plus/test";
import { parse as parseYaml } from "yaml";

import { pullRemote, verifyExport } from "../src/node/checkout.js";
import { runCli } from "../src/node/cli.js";
import { directorySource } from "../src/node/source.js";
import { openExport } from "../src/reader.js";
import type { ExportOutput } from "../src/reader.js";
import { validateExportPlan, type ExportPlan } from "../src/remote/plan.js";
import { connectRemote } from "../src/remote/session.js";
import type { Remote } from "../src/remote/session.js";

const integration = process.env.MARIMO_EXPORT_REMOTE_INTEGRATION === "1" ? describe : describe.skip;
const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

integration("dedicated remote marimo server", () => {
  let remoteRoot: string;
  let counterRoot: string;
  let checkoutRoot: string;
  let widgetsCheckoutRoot: string;
  let notebook: string;
  let widgetsNotebook: string;
  let server: string;
  let authToken: string;
  let serverToken: string;
  let remote: Remote;
  let widgetsRemote: Remote | undefined;
  let processHandle: ReturnType<typeof spawn>;
  let logs = "";

  beforeAll(async () => {
    remoteRoot = await mkdtemp(resolve(tmpdir(), "marimo-export-remote-"));
    counterRoot = resolve(remoteRoot, "counters");
    checkoutRoot = resolve(remoteRoot, "published");
    widgetsCheckoutRoot = resolve(remoteRoot, "widgets-published");
    notebook = resolve(remoteRoot, "cache_matrix.py");
    widgetsNotebook = resolve(remoteRoot, "widgets.py");
    await writeFile(
      notebook,
      await readFile(resolve(workspace, "examples/_notebooks/cache_matrix.py")),
    );
    await writeFile(
      widgetsNotebook,
      await readFile(resolve(workspace, "examples/_notebooks/widgets.py")),
    );
    await writeFile(
      resolve(remoteRoot, "plan.json"),
      await readFile(resolve(workspace, "examples/_notebooks/cache_matrix.plan.json")),
    );
    await writeFile(
      resolve(remoteRoot, "pyproject.toml"),
      "[tool.marimo.runtime]\ncache_cells = true\n",
    );

    const port = await availablePort();
    server = `http://127.0.0.1:${port}/`;
    authToken = "marimo-export-integration-auth";
    processHandle = spawn(
      "uvx",
      [
        "--no-cache",
        "--from",
        `${resolve(workspace, "packages/producer")}[anywidget]`,
        "marimo",
        "edit",
        notebook,
        "--no-sandbox",
        "--headless",
        "--host",
        "127.0.0.1",
        "--port",
        String(port),
        "--token-password",
        authToken,
      ],
      {
        cwd: workspace,
        env: {
          ...process.env,
          MARIMO_EXPORT_COUNTER_DIR: counterRoot,
          NO_COLOR: "1",
          XDG_STATE_HOME: resolve(remoteRoot, "state"),
        },
      },
    );
    processHandle.stdout?.on("data", (chunk: Buffer) => {
      logs += chunk.toString();
    });
    processHandle.stderr?.on("data", (chunk: Buffer) => {
      logs += chunk.toString();
    });

    const page = await waitForServer(server, () => logs, authToken);
    const token = /"serverToken": "([^"]+)"/.exec(page)?.[1];
    if (token === undefined) throw new Error(`marimo server token is missing.\n${logs}`);
    serverToken = token;
    remote = await connectRemote({
      server,
      authToken,
      serverToken: token,
      target: { notebook },
    });
    expect(remote.session.owned).toBe(true);
  }, 60_000);

  afterAll(async () => {
    await widgetsRemote?.close().catch(() => undefined);
    await remote?.close().catch(() => undefined);
    await stopProcess(processHandle);
    await rm(remoteRoot, { recursive: true, force: true });
  });

  test("builds through marimo cache, pulls incrementally, and reads after Python stops", async () => {
    await expect(stat(resolve(counterRoot, "projected.txt"))).rejects.toMatchObject({
      code: "ENOENT",
    });
    const description = await remote.describe();
    expect(description).toMatchObject({
      protocol: "marimo-export.remote.v1",
      marimoExportVersion: "0.0.0",
      marimoVersion: "0.23.14",
      projections: { json: { available: true, extra: null } },
    });
    const plan = JSON.parse(await readFile(resolve(remoteRoot, "plan.json"), "utf8")) as ExportPlan;

    const cold = await remote.build(plan);
    expect(cold).toMatchObject({
      receipt: { scenarioCount: 3, projectionCount: 18 },
    });
    await waitForFile(resolve(counterRoot, "projected.txt"));
    await waitForFile(resolve(counterRoot, "projection.txt"));
    const coldProjected = await readCounter(counterRoot, "projected.txt");
    const coldProjection = await readCounter(counterRoot, "projection.txt");

    const warm = await remote.build(plan);
    expect(warm.ref).toEqual(cold.ref);
    expect(await readCounter(counterRoot, "projected.txt")).toBe(coldProjected);
    expect(await readCounter(counterRoot, "projection.txt")).toBe(coldProjection);
    await expect(
      stat(resolve(remoteRoot, "__marimo__/cache", cold.ref.key)),
    ).resolves.toBeDefined();

    const renamedPlan: ExportPlan = {
      schema: "marimo-export.plan.v1",
      ...(plan.inputs === undefined ? {} : { inputs: plan.inputs }),
      ...(plan.scenarios === undefined ? {} : { scenarios: plan.scenarios }),
      outputs: {
        renamed_projected: {
          source: "projected",
          formats: {
            data: {
              exporter: { definition: "counted_json", version: "1" },
            },
          },
        },
      },
    };
    const renamed = await remote.build(renamedPlan);
    expect(renamed.ref).not.toEqual(cold.ref);
    expect(await readCounter(counterRoot, "projected.txt")).toBe(coldProjected);
    expect(await readCounter(counterRoot, "projection.txt")).toBe(coldProjection);

    const versionedPlan: ExportPlan = {
      ...renamedPlan,
      outputs: {
        renamed_projected: {
          source: "projected",
          formats: {
            data: {
              exporter: { definition: "counted_json", version: "2" },
            },
          },
        },
      },
    };
    const versioned = await remote.build(versionedPlan);
    expect(versioned.ref).not.toEqual(renamed.ref);
    expect(await readCounter(counterRoot, "projected.txt")).toBe(coldProjected);
    const versionedProjection = await readCounter(counterRoot, "projection.txt");
    expect(versionedProjection).toBeGreaterThan(coldProjection);

    const versionedIndex = JSON.parse(
      await readFile(resolve(remoteRoot, "__marimo__/cache", versioned.ref.key), "utf8"),
    ) as {
      scenarios: Array<{
        outputs: { renamed_projected: { data: { payload: { key: string } } } };
      }>;
    };
    const deletedPayload = versionedIndex.scenarios[0]!.outputs.renamed_projected.data.payload.key;
    const deletedPayloadPath = resolve(remoteRoot, "__marimo__/cache", deletedPayload);
    await rm(deletedPayloadPath);
    const repaired = await remote.build(versionedPlan);
    expect(repaired.ref).toEqual(versioned.ref);
    expect(await readCounter(counterRoot, "projected.txt")).toBe(coldProjected);
    expect(await readCounter(counterRoot, "projection.txt")).toBe(versionedProjection);
    await expect(stat(deletedPayloadPath)).resolves.toBeDefined();

    const firstPull = await pullRemote(remote, cold.ref, { into: checkoutRoot });
    expect(firstPull.downloaded).toBeGreaterThan(0);
    const secondPull = await pullRemote(remote, cold.ref, { into: checkoutRoot });
    expect(secondPull).toMatchObject({
      files: firstPull.files,
      downloaded: 0,
      skipped: firstPull.files,
    });
    await expect(
      verifyExport({ source: directorySource(checkoutRoot), ref: cold.ref }),
    ).resolves.toMatchObject({ ok: true, files: firstPull.files, failures: [] });

    const widgetsPlan = validateExportPlan(
      parseYaml(
        await readFile(resolve(workspace, "examples/_notebooks/widgets.plan.yaml"), "utf8"),
      ),
    );
    expect(widgetsPlan.outputs.raw_counter?.formats.anywidget).toEqual({});
    expect(widgetsPlan.outputs.wrapped_dashboard?.formats.anywidget).toEqual({});

    const attachedWidgets = await connectRemote({
      server,
      authToken,
      serverToken,
      target: { notebook: widgetsNotebook },
    });
    widgetsRemote = attachedWidgets;
    expect(attachedWidgets.session.owned).toBe(true);
    await expect(attachedWidgets.describe()).resolves.toMatchObject({
      projections: { anywidget: { available: true, extra: "anywidget" } },
    });

    const widgetsCold = await attachedWidgets.build(widgetsPlan);
    expect(widgetsCold).toMatchObject({
      receipt: { scenarioCount: 3, projectionCount: 12 },
    });
    const widgetsWarm = await attachedWidgets.build(widgetsPlan);
    expect(widgetsWarm.ref).toEqual(widgetsCold.ref);

    const firstWidgetsPull = await pullRemote(attachedWidgets, widgetsCold.ref, {
      into: widgetsCheckoutRoot,
    });
    expect(firstWidgetsPull).toMatchObject({ files: 12, downloaded: 12, skipped: 0 });
    const secondWidgetsPull = await pullRemote(attachedWidgets, widgetsCold.ref, {
      into: widgetsCheckoutRoot,
    });
    expect(secondWidgetsPull).toMatchObject({
      files: firstWidgetsPull.files,
      downloaded: 0,
      skipped: firstWidgetsPull.files,
    });
    await expect(
      verifyExport({ source: directorySource(widgetsCheckoutRoot), ref: widgetsCold.ref }),
    ).resolves.toMatchObject({
      ok: true,
      files: firstWidgetsPull.files,
      failures: [],
    });
    await attachedWidgets.close();
    widgetsRemote = undefined;

    await remote.close();
    const cliRoot = resolve(remoteRoot, "cli-published");
    const cliOutputs = [captureCli(), captureCli()];
    const previousAuthToken = process.env.MARIMO_TOKEN;
    const previousToken = process.env.MARIMO_SERVER_TOKEN;
    process.env.MARIMO_TOKEN = authToken;
    process.env.MARIMO_SERVER_TOKEN = serverToken;
    try {
      for (const cliOutput of cliOutputs) {
        // oxlint-disable-next-line no-await-in-loop -- repeat publish must observe prior cleanup.
        const result = await runCli(
          [
            "publish",
            "--server",
            server,
            "--notebook",
            notebook,
            "--plan",
            resolve(remoteRoot, "plan.json"),
            "--out",
            cliRoot,
            "--timeout-ms",
            "30000",
            "--json",
          ],
          cliOutput.io,
        );
        expect(result.exitCode).toBe(0);
      }
    } finally {
      if (previousAuthToken === undefined) delete process.env.MARIMO_TOKEN;
      else process.env.MARIMO_TOKEN = previousAuthToken;
      if (previousToken === undefined) delete process.env.MARIMO_SERVER_TOKEN;
      else process.env.MARIMO_SERVER_TOKEN = previousToken;
    }
    for (const cliOutput of cliOutputs) {
      expect(JSON.parse(cliOutput.stdout())).toMatchObject({
        schema: "marimo-export.cli.v1",
        command: "publish",
        data: { verification: { ok: true } },
      });
    }
    await expect(verifyExport({ source: directorySource(cliRoot) })).resolves.toMatchObject({
      ok: true,
    });

    const exampleRoot = resolve(remoteRoot, "example-published");
    const remoteExample = JSON.parse(
      await runNodeExample("examples/remote-client.mjs", [], {
        MARIMO_EXPORT_SERVER: server,
        MARIMO_EXPORT_NOTEBOOK: notebook,
        MARIMO_EXPORT_PLAN: resolve(remoteRoot, "plan.json"),
        MARIMO_EXPORT_OUT: exampleRoot,
        MARIMO_TOKEN: authToken,
        MARIMO_SERVER_TOKEN: serverToken,
      }),
    ) as unknown;
    expect(remoteExample).toMatchObject({
      build: { receipt: { scenarioCount: 3, projectionCount: 18 } },
      into: exampleRoot,
    });

    await stopProcess(processHandle);
    const widgetsPublished = await openExport(directorySource(widgetsCheckoutRoot), {
      ref: widgetsCold.ref,
    });
    await assertWidgetsPublication(widgetsPublished);

    const checkoutExample = JSON.parse(
      await runNodeExample("examples/read-checkout.mjs", [exampleRoot]),
    ) as unknown;
    expect(checkoutExample).toEqual({
      scenarios: [
        {
          id: "baseline",
          inputs: { multiplier: 2, scale: 2 },
          projected: { value: 21 },
        },
        {
          id: "triple",
          inputs: { multiplier: 3, scale: 2 },
          projected: { value: 21 },
        },
        {
          id: "large",
          inputs: { multiplier: 3, scale: 5 },
          projected: { value: 51 },
        },
      ],
    });

    const published = await openExport(directorySource(checkoutRoot), { ref: cold.ref });
    await expect(published.scenario("large").output("reactive").json()).resolves.toEqual({
      value: 153,
    });
    await expect(published.scenario("large").output("reactive_markup").text()).resolves.toContain(
      "Reactive value:",
    );
    await expect(published.scenario("large").output("calculation").json()).resolves.toEqual({
      multiplier: 3,
      result: 153,
      scale: 5,
    });
  }, 120_000);
});

interface AnyWidgetNotification {
  readonly modelId: string;
  readonly state: Readonly<Record<string, unknown>>;
  readonly bufferPaths: readonly (readonly unknown[])[];
  readonly buffers: readonly string[];
}

interface AnyWidgetDocument {
  readonly rootModelId: string;
  readonly files: Readonly<Record<string, string>>;
  readonly notifications: readonly AnyWidgetNotification[];
}

async function assertWidgetsPublication(published: Awaited<ReturnType<typeof openExport>>) {
  const expectedScenarios = [
    { id: "baseline", seed: 2, accent: "#2563eb" },
    { id: "boosted", seed: 7, accent: "#2563eb" },
    { id: "violet", seed: 4, accent: "#7c3aed" },
  ] as const;
  expect(published.scenarios().map((scenario) => scenario.id)).toEqual(
    expectedScenarios.map((scenario) => scenario.id),
  );

  const reads: Array<
    Promise<{
      readonly kind: "raw" | "wrapped";
      readonly seed: number;
      readonly accent: string;
      readonly output: ExportOutput;
      readonly document: AnyWidgetDocument;
    }>
  > = [];
  for (const expected of expectedScenarios) {
    const scenario = published.scenario(expected.id);
    expect(scenario.inputs).toEqual({ accent: expected.accent, seed: expected.seed });
    for (const [kind, outputName] of [
      ["raw", "raw_counter"],
      ["wrapped", "wrapped_dashboard"],
    ] as const) {
      const output = scenario.output(outputName, "anywidget");
      expect(output).toMatchObject({
        formatId: "anywidget.v1",
        mediaType: "application/vnd.marimo-export.anywidget+json",
      });
      reads.push(
        output.json().then((value) => ({
          kind,
          seed: expected.seed,
          accent: expected.accent,
          output,
          document: anyWidgetDocument(value),
        })),
      );
    }
  }

  const payloads = await Promise.all(reads);
  expect(payloads).toHaveLength(6);
  for (const { kind, seed, accent, output, document } of payloads) {
    expect(document.rootModelId).toBe("model-0");
    expect(output.metadata).toEqual({
      models: document.notifications.length,
      root_model_id: document.rootModelId,
    });
    expect(Object.values(document.files)).not.toHaveLength(0);
    expect(Object.values(document.files).every((file) => file.startsWith("data:"))).toBe(true);

    const modelIds = new Set(document.notifications.map((notification) => notification.modelId));
    expect([...modelIds]).toEqual(document.notifications.map((_, index) => `model-${index}`));
    const references = document.notifications.flatMap((notification) =>
      collectModelReferences(notification.state),
    );
    expect(references.length).toBeGreaterThan(0);
    expect(references.every((reference) => modelIds.has(reference))).toBe(true);

    const root = document.notifications.find(
      (notification) => notification.modelId === document.rootModelId,
    );
    expect(root).toBeDefined();
    expect(root?.state.accent).toBe(accent);
    if (kind === "raw") {
      expect(root?.state.count).toBe(seed);
      expectBinaryState(document, "Raw counter", [seed, seed + 1, seed + 2, seed + 3]);
    } else {
      expect(root?.state.child).toMatch(/^(?:anywidget:|IPY_MODEL_)model-\d+$/);
      expectBinaryState(document, "Nested child", [seed + 4, seed + 5, seed + 6, seed + 7]);
    }
  }
}

function anyWidgetDocument(value: unknown): AnyWidgetDocument {
  if (!isRecord(value) || value.schema !== "marimo-export.anywidget.v1") {
    throw new TypeError("AnyWidget output must use the marimo-export.anywidget.v1 schema.");
  }
  if (typeof value.rootModelId !== "string" || !isRecord(value.files)) {
    throw new TypeError("AnyWidget output must declare its root model and virtual files.");
  }
  if (!Array.isArray(value.modelNotifications)) {
    throw new TypeError("AnyWidget output must contain model notifications.");
  }
  const files = Object.fromEntries(
    Object.entries(value.files).map(([name, file]) => {
      if (typeof file !== "string") throw new TypeError(`AnyWidget file ${name} must be a string.`);
      return [name, file];
    }),
  );
  const notifications = value.modelNotifications.map((notification, index) => {
    if (!isRecord(notification) || typeof notification.model_id !== "string") {
      throw new TypeError(`AnyWidget notification ${index} must declare a model ID.`);
    }
    if (!isRecord(notification.message) || !isRecord(notification.message.state)) {
      throw new TypeError(`AnyWidget notification ${index} must contain model state.`);
    }
    const bufferPaths = notification.message.buffer_paths;
    const buffers = notification.message.buffers;
    if (
      !Array.isArray(bufferPaths) ||
      !bufferPaths.every(Array.isArray) ||
      !Array.isArray(buffers) ||
      !buffers.every((buffer) => typeof buffer === "string")
    ) {
      throw new TypeError(`AnyWidget notification ${index} must contain encoded buffers.`);
    }
    return {
      modelId: notification.model_id,
      state: notification.message.state,
      bufferPaths,
      buffers,
    };
  });
  return { rootModelId: value.rootModelId, files, notifications };
}

function collectModelReferences(value: unknown): string[] {
  if (typeof value === "string") {
    if (value.startsWith("anywidget:")) return [value.slice("anywidget:".length)];
    if (value.startsWith("IPY_MODEL_")) return [value.slice("IPY_MODEL_".length)];
    return [];
  }
  if (Array.isArray(value)) return value.flatMap(collectModelReferences);
  if (isRecord(value)) return Object.values(value).flatMap(collectModelReferences);
  return [];
}

function expectBinaryState(
  document: AnyWidgetDocument,
  label: string,
  expectedBytes: readonly number[],
): void {
  const model = document.notifications.find((notification) => notification.state.label === label);
  expect(model).toBeDefined();
  const payloadIndex = model?.bufferPaths.findIndex(
    (path) => path.length === 1 && path[0] === "payload",
  );
  expect(payloadIndex).toBeGreaterThanOrEqual(0);
  const encoded = model?.buffers[payloadIndex ?? -1];
  expect(encoded).toBeDefined();
  expect([...Buffer.from(encoded ?? "", "base64")]).toEqual(expectedBytes);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function availablePort(): Promise<number> {
  const socket = createServer();
  await new Promise<void>((resolveReady, reject) => {
    socket.once("error", reject);
    socket.listen(0, "127.0.0.1", resolveReady);
  });
  const address = socket.address();
  if (address === null || typeof address === "string") throw new Error("failed to reserve a port");
  await new Promise<void>((resolveClosed, reject) => {
    socket.close((error) => (error === undefined ? resolveClosed() : reject(error)));
  });
  return address.port;
}

async function waitForServer(
  url: string,
  getLogs: () => string,
  authToken?: string,
): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    // oxlint-disable-next-line no-await-in-loop -- server readiness is polled sequentially.
    const response = await fetch(url, {
      ...(authToken === undefined ? {} : { headers: { Authorization: `Bearer ${authToken}` } }),
      redirect: "error",
    }).catch(() => undefined);
    if (response?.ok === true) return response.text();
    // oxlint-disable-next-line no-await-in-loop -- polling uses a bounded interval.
    await delay(100);
  }
  throw new Error(`marimo server did not start.\n${getLogs()}`);
}

async function readCounter(root: string, name: string): Promise<number> {
  return Number.parseInt(await readFile(resolve(root, name), "utf8"), 10);
}

async function waitForFile(path: string): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (
      // oxlint-disable-next-line no-await-in-loop -- file creation is polled sequentially.
      await stat(path).then(
        () => true,
        () => false,
      )
    )
      return;
    // oxlint-disable-next-line no-await-in-loop -- polling uses a bounded interval.
    await delay(50);
  }
  throw new Error(`file was not created: ${path}`);
}

async function stopProcess(child: ReturnType<typeof spawn> | undefined): Promise<void> {
  if (child === undefined || child.exitCode !== null) return;
  child.kill("SIGTERM");
  const exited = new Promise<void>((resolveExit) => child.once("exit", () => resolveExit()));
  await Promise.race([exited, delay(5_000)]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function runNodeExample(
  script: string,
  args: readonly string[],
  environment: Readonly<Record<string, string>> = {},
): Promise<string> {
  const child = spawn(process.execPath, [resolve(workspace, script), ...args], {
    cwd: workspace,
    env: { ...process.env, ...environment },
  });
  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk: Buffer) => {
    stdout += chunk.toString();
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr += chunk.toString();
  });

  const exitCode = await new Promise<number>((resolveExit, reject) => {
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`${script} did not exit within 60 seconds.\n${stderr}`));
    }, 60_000);
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      if (code === null) {
        reject(new Error(`${script} exited from signal ${String(signal)}.\n${stderr}`));
        return;
      }
      resolveExit(code);
    });
  });
  if (exitCode !== 0) {
    throw new Error(`${script} exited with code ${exitCode}.\n${stderr}`);
  }
  return stdout;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function captureCli() {
  const stdout: string[] = [];
  const stderr: string[] = [];
  return {
    io: {
      stdout: (data: string | Uint8Array) =>
        stdout.push(typeof data === "string" ? data : new TextDecoder().decode(data)),
      stderr: (text: string) => stderr.push(text),
    },
    stdout: () => stdout.join(""),
  };
}
