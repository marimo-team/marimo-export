import { mkdir, mkdtemp, readFile, rm, symlink, truncate, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import packageMetadata from "../package.json" with { type: "json" };

import { pullExport } from "../src/node/checkout.js";
import { runCli } from "../src/node/cli.js";
import { directorySource, exportPath } from "../src/node/source.js";
import { openExport } from "../src/reader.js";
import { REMOTE_PROTOCOL, RESPONSE_PREFIX } from "../src/remote/client.js";
import { memorySource } from "../src/source.js";
import { exportFixture } from "./fixture.js";

const roots: string[] = [];
const expiresAt = 2_000_000_000_000;

afterEach(async () => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("marimo-export CLI", () => {
  test("reports the package version", async () => {
    const output = capture();

    expect((await runCli(["--version"], output.io)).exitCode).toBe(0);
    expect(output.stdout()).toBe(`${packageMetadata.version}\n`);
    expect(output.stderr()).toBe("");
  });

  test("inspects and reads a verified output", async () => {
    const { root } = await checkout();
    const inspect = capture();
    expect((await runCli(["inspect", root, "--json"], inspect.io)).exitCode).toBe(0);
    const inspected = JSON.parse(inspect.stdout()) as {
      schema: string;
      command: string;
      data: { notebook: { name: string }; scenarios: Array<{ id: string; outputCount: number }> };
    };
    expect(inspected).toMatchObject({
      schema: "marimo-export.cli.v1",
      command: "inspect",
      data: { notebook: { name: "finance.py" } },
    });
    expect(inspected.data.scenarios.map((scenario) => scenario.id)).toEqual(["microsoft", "apple"]);
    expect(inspected.data.scenarios[0]!.outputCount).toBe(3);

    const scenario = capture();
    expect(
      (await runCli(["inspect", root, "--scenario", "microsoft", "--json"], scenario.io)).exitCode,
    ).toBe(0);
    expect(JSON.parse(scenario.stdout())).toMatchObject({
      data: {
        scenario: { id: "microsoft", inputs: { symbol: "MSFT", window: 30 } },
        outputs: expect.arrayContaining([expect.objectContaining({ name: "prices" })]),
      },
    });

    const read = capture();
    expect(
      (await runCli(["read", root, "microsoft", "prices", "--format", "json", "--json"], read.io))
        .exitCode,
    ).toBe(0);
    expect(JSON.parse(read.stdout())).toMatchObject({
      command: "read",
      data: {
        ref: expect.objectContaining({ key: expect.stringContaining("marimo-export/indexes/") }),
        scenario: { id: "microsoft", inputs: { symbol: "MSFT", window: 30 } },
        output: { name: "prices", formatName: "json", formatId: "json.v1" },
        data: [{ symbol: "MSFT", price: 420 }],
      },
    });

    const text = capture();
    expect(
      (await runCli(["read", root, "microsoft", "prices", "--format", "text"], text.io)).exitCode,
    ).toBe(0);
    expect(text.stdoutBytes()).toEqual((await exportFixture()).textPayload);
  });

  test("checks declared size before fetching an output", async () => {
    const fixture = await exportFixture();
    const requested: string[] = [];
    vi.stubGlobal("fetch", async (input: string | URL | Request) => {
      const url = new URL(urlOf(input));
      requested.push(url.pathname);
      const value = fixture.objects[url.pathname.slice("/export/".length)];
      return value === undefined
        ? new Response(null, { status: 404 })
        : new Response(Buffer.from(value));
    });
    const output = capture();

    const result = await runCli(
      [
        "read",
        "https://static.test/export/",
        "microsoft",
        "prices",
        "--format",
        "json",
        "--max-bytes",
        "1",
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(1);
    expect(requested).toEqual(["/export/index.json"]);
    expect(JSON.parse(output.stderr())).toMatchObject({
      error: { code: "output_too_large" },
    });
  });

  test("uses exit 2 for usage errors and 130 for cancellation", async () => {
    const usage = capture();
    expect((await runCli(["inspect", ".", "--unknown", "--json"], usage.io)).exitCode).toBe(2);
    expect(JSON.parse(usage.stderr())).toMatchObject({ error: { code: "usage_error" } });

    const controller = new AbortController();
    controller.abort(new DOMException("cancelled", "AbortError"));
    const cancelled = capture();
    expect(
      (await runCli(["inspect", ".", "--json"], cancelled.io, controller.signal)).exitCode,
    ).toBe(130);
    expect(JSON.parse(cancelled.stderr())).toMatchObject({ error: { code: "cancelled" } });
  });

  test("returns status 1 with a structured verification report", async () => {
    const fixture = await exportFixture();
    const { root } = await checkout(fixture);
    const key = fixture.index.scenarios[0]!.outputs.prices!.json!.payload.key;
    await writeFile(exportPath(root, `cache/${key}`), "corrupt");
    const output = capture();

    expect((await runCli(["verify", root, "--json"], output.io)).exitCode).toBe(1);
    expect(JSON.parse(output.stdout())).toMatchObject({
      command: "verify",
      data: { ok: false, failures: [expect.objectContaining({ key })] },
    });
  });

  test("build emits and saves a durable remote build record", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const planPath = join(root, "plan.yaml");
    const recordPath = join(root, "build.json");
    await writeFile(
      planPath,
      [
        "schema: marimo-export.plan.v1",
        "outputs:",
        "  prices:",
        "    source: prices",
        "    formats:",
        "      json: {}",
      ].join("\n"),
    );
    vi.stubGlobal("WebSocket", ReadyWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(urlOf(input)).pathname;
      if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
      if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
      if (path === "/api/home/shutdown_session") return jsonResponse({ files: [] });
      const request = remoteRequest(init);
      expect(request.operation).toBe("build");
      return remoteResponse(request.request_id, {
        ref: fixture.ref,
        receipt: { elapsed_ms: 3, scenario_count: 2, projection_count: 3 },
      });
    });
    const output = capture();

    const result = await runCli(
      [
        "build",
        "--server",
        "https://marimo.test",
        "--notebook",
        "examples/_notebooks/finance.py",
        "--plan",
        planPath,
        "--record",
        recordPath,
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(0);
    expect(JSON.parse(output.stdout())).toMatchObject({
      schema: "marimo-export.build.v1",
      server: "https://marimo.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref: fixture.ref,
    });
    expect(JSON.parse(await readFile(recordPath, "utf8"))).toMatchObject({
      schema: "marimo-export.build.v1",
      target: { notebook: "examples/_notebooks/finance.py" },
    });
    expect(output.stderr()).toContain("Building export cache.");

    const checkoutRoot = join(root, "piped-publication");
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(urlOf(input));
      if (url.pathname === "/api/home/running_notebooks") return jsonResponse({ files: [] });
      if (url.pathname === "/api/kernel/instantiate") {
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/api/home/shutdown_session") return jsonResponse({ files: [] });
      if (url.pathname.startsWith("/public/stage/")) {
        const value = fixture.objects[url.pathname.slice("/public/stage/".length)];
        return value === undefined
          ? new Response(null, { status: 404 })
          : new Response(Buffer.from(value));
      }
      const request = remoteRequest(init);
      return remoteResponse(
        request.request_id,
        request.operation === "stage"
          ? {
              id: "stage-pipe",
              url: "/public/stage/",
              notebook_key: null,
              expires_at_ms: expiresAt,
            }
          : { released: true },
      );
    });
    const piped = capture(output.stdout());
    expect((await runCli(["pull", "-", "--out", checkoutRoot], piped.io)).exitCode).toBe(0);
    await expect(
      openExport(directorySource(checkoutRoot), { ref: fixture.ref }),
    ).resolves.toMatchObject({ ref: fixture.ref });
  });

  test("pull reads a build record from stdin and releases the remote stage", async () => {
    vi.stubEnv("MARIMO_TOKEN", "");
    vi.stubEnv("MARIMO_SERVER_TOKEN", "");
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const checkoutRoot = join(root, "published");
    const operations: string[] = [];
    const build = {
      schema: "marimo-export.build.v1",
      server: "https://marimo.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref: fixture.ref,
      receipt: { elapsedMs: 3, scenarioCount: 2, projectionCount: 3 },
    };
    vi.stubGlobal("WebSocket", ReadyWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(urlOf(input));
      if (url.pathname === "/api/home/running_notebooks") return jsonResponse({ files: [] });
      if (url.pathname === "/api/kernel/instantiate") {
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/api/home/shutdown_session") return jsonResponse({ files: [] });
      if (url.pathname.startsWith("/public/stage/")) {
        const path = url.pathname.slice("/public/stage/".length);
        const value = fixture.objects[path];
        return value === undefined
          ? new Response(null, { status: 404 })
          : new Response(Buffer.from(value));
      }
      const request = remoteRequest(init);
      operations.push(request.operation);
      return remoteResponse(
        request.request_id,
        request.operation === "stage"
          ? {
              id: "stage-1",
              url: "/public/stage/",
              notebook_key: null,
              expires_at_ms: expiresAt,
            }
          : { released: true },
      );
    });
    const output = capture(JSON.stringify(build));

    const result = await runCli(["pull", "-", "--out", checkoutRoot, "--json"], output.io);

    expect(result.exitCode).toBe(0);
    expect(JSON.parse(output.stdout())).toMatchObject({
      command: "pull",
      data: { files: 3, downloaded: 3, skipped: 0 },
    });
    expect(operations).toEqual(["stage", "release"]);
    const published = await openExport(directorySource(checkoutRoot), { ref: fixture.ref });
    await expect(published.scenario("microsoft").output("prices", "json").json()).resolves.toEqual([
      { symbol: "MSFT", price: 420 },
    ]);
  });

  test("requires a reopenable target before reading a split build plan", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture();
    const stdin = vi.fn(async () => new Promise<string>(() => undefined));
    output.io.stdin = stdin;

    const result = await runCli(
      [
        "build",
        "--server",
        "https://marimo.test",
        "--session",
        "s_running",
        "--plan",
        "-",
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(2);
    expect(JSON.parse(output.stderr())).toMatchObject({
      error: {
        code: "usage_error",
        message: expect.stringContaining("Use publish --session ID"),
      },
    });
    expect(stdin).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  test("requires a reopenable target before saving a publish record", async () => {
    const root = await temporaryRoot();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture();
    const stdin = vi.fn(async () => new Promise<string>(() => undefined));
    output.io.stdin = stdin;

    const result = await runCli(
      [
        "publish",
        "--server",
        "https://marimo.test",
        "--session",
        "s_running",
        "--plan",
        "-",
        "--out",
        join(root, "publication"),
        "--record",
        join(root, "build.json"),
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(2);
    expect(JSON.parse(output.stderr())).toMatchObject({
      error: {
        code: "usage_error",
        message: expect.stringContaining("Omit --record"),
      },
    });
    expect(stdin).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  test("publishes through an explicit session on one connection", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const planPath = join(root, "plan.yaml");
    const destination = join(root, "publication");
    const operations: string[] = [];
    await writeFile(
      planPath,
      [
        "schema: marimo-export.plan.v1",
        "outputs:",
        "  prices:",
        "    source: prices",
        "    formats:",
        "      json: {}",
      ].join("\n"),
    );
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(urlOf(input));
      if (url.pathname === "/api/sessions") {
        return jsonResponse({ s_running: { filename: "finance.py", path: "/srv/finance.py" } });
      }
      if (url.pathname.startsWith("/public/stage/")) {
        const value = fixture.objects[url.pathname.slice("/public/stage/".length)];
        return value === undefined
          ? new Response(null, { status: 404 })
          : new Response(Buffer.from(value));
      }
      const request = remoteRequest(init);
      operations.push(request.operation);
      if (request.operation === "build") {
        return remoteResponse(request.request_id, {
          ref: fixture.ref,
          receipt: { elapsed_ms: 3, scenario_count: 2, projection_count: 3 },
        });
      }
      if (request.operation === "stage") {
        return remoteResponse(request.request_id, {
          id: "stage-publish",
          url: "/public/stage/",
          notebook_key: null,
          expires_at_ms: expiresAt,
        });
      }
      return remoteResponse(request.request_id, { released: true });
    });
    const output = capture();

    const result = await runCli(
      [
        "publish",
        "--server",
        "https://marimo.test",
        "--session",
        "s_running",
        "--plan",
        planPath,
        "--out",
        destination,
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(0);
    expect(operations).toEqual(["build", "stage", "release"]);
    expect(JSON.parse(output.stdout())).toMatchObject({
      command: "publish",
      data: {
        build: {
          ref: fixture.ref,
          receipt: { elapsedMs: 3, scenarioCount: 2, projectionCount: 3 },
        },
        verification: { ok: true },
      },
    });
    expect(Object.keys(JSON.parse(output.stdout()).data.build).sort()).toEqual(["receipt", "ref"]);
    await expect(
      openExport(directorySource(destination), { ref: fixture.ref }),
    ).resolves.toBeDefined();
  });

  test("rejects a session build record before opening a pull target", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture(
      JSON.stringify({
        schema: "marimo-export.build.v1",
        server: "https://marimo.test/",
        target: { sessionId: "s_expired" },
        ref: fixture.ref,
        receipt: { elapsedMs: 3, scenarioCount: 2, projectionCount: 3 },
      }),
    );

    const result = await runCli(
      ["pull", "-", "--out", join(root, "publication"), "--json"],
      output.io,
    );

    expect(result.exitCode).toBe(2);
    expect(JSON.parse(output.stderr())).toMatchObject({
      error: {
        code: "usage_error",
        message: expect.stringContaining("pull can open a fresh session"),
      },
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("requires an explicit matching server before sending ambient credentials", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const build = {
      schema: "marimo-export.build.v1",
      server: "https://trusted.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref: fixture.ref,
      receipt: { elapsedMs: 3, scenarioCount: 2, projectionCount: 3 },
    };
    vi.stubEnv("MARIMO_TOKEN", "secret");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    const missing = capture(JSON.stringify(build));
    expect((await runCli(["pull", "-", "--out", root, "--json"], missing.io)).exitCode).toBe(2);
    expect(JSON.parse(missing.stderr())).toMatchObject({ error: { code: "usage_error" } });

    const mismatched = capture(JSON.stringify(build));
    expect(
      (
        await runCli(
          ["pull", "-", "--out", root, "--server", "https://attacker.test", "--json"],
          mismatched.io,
        )
      ).exitCode,
    ).toBe(2);
    expect(fetch).not.toHaveBeenCalled();
  });

  test("bounds static HTTP operations with the command timeout", async () => {
    vi.stubGlobal(
      "fetch",
      (_input: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), {
            once: true,
          });
        }),
    );
    const output = capture();

    const result = await runCli(
      ["inspect", "https://static.test/export/", "--timeout-ms", "5", "--json"],
      output.io,
    );

    expect(result.exitCode).toBe(1);
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "timeout" } });
  });

  test("bounds a build record read from standard input", async () => {
    const root = await temporaryRoot();
    const output = capture();
    output.io.stdin = async () => new Promise<string>(() => undefined);

    const result = await runCli(
      ["pull", "-", "--out", join(root, "publication"), "--timeout-ms", "5", "--json"],
      output.io,
    );

    expect(result.exitCode).toBe(1);
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "timeout" } });
  });

  test.each([
    ["inspect", ["inspect", "ftp://invalid", "--ref", "-"]],
    ["read", ["read", "ftp://invalid", "scenario", "output", "--ref", "-"]],
    ["verify", ["verify", "ftp://invalid", "--ref", "-"]],
  ] as const)("validates the %s source before reading a ref from stdin", async (_command, argv) => {
    const output = capture();
    const stdin = vi.fn(async () => new Promise<string>(() => undefined));
    output.io.stdin = stdin;

    const result = await runCli([...argv, "--timeout-ms", "5", "--json"], output.io);

    expect(result.exitCode).toBe(2);
    expect(stdin).not.toHaveBeenCalled();
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "usage_error" } });
  });

  test("rejects an oversized plan before connecting", async () => {
    const root = await temporaryRoot();
    const planPath = join(root, "plan.yaml");
    await writeFile(planPath, "schema: marimo-export.plan.v1\n");
    await truncate(planPath, 17 * 1024 * 1024);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture();

    const result = await runCli(
      [
        "build",
        "--server",
        "https://marimo.test",
        "--notebook",
        "examples/_notebooks/finance.py",
        "--plan",
        planPath,
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(2);
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "usage_error" } });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("rejects an oversized standard-input document before local or remote work", async () => {
    const root = await temporaryRoot();
    const destination = join(root, "publication");
    const output = capture("x".repeat(17 * 1024 * 1024));
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    const result = await runCli(["pull", "-", "--out", destination, "--json"], output.io);

    expect(result.exitCode).toBe(2);
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "usage_error" } });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("validates transfer options before connecting", async () => {
    const fixture = await exportFixture();
    const build = {
      schema: "marimo-export.build.v1",
      server: "https://marimo.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref: fixture.ref,
      receipt: { elapsedMs: 3, scenarioCount: 2, projectionCount: 3 },
    };
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture(JSON.stringify(build));

    const result = await runCli(
      ["pull", "-", "--out", "published", "--concurrency", "65", "--json"],
      output.io,
    );

    expect(result.exitCode).toBe(2);
    expect(fetch).not.toHaveBeenCalled();
  });

  test.each([
    ["destination root", "root"],
    ["cache directory", "cache"],
    ["index file", "index"],
    ["index directory", "index-directory"],
  ] as const)("rejects an unsafe %s before connecting", async (_label, kind) => {
    const root = await temporaryRoot();
    const planPath = join(root, "plan.yaml");
    const destination = join(root, "publication");
    const elsewhere = join(root, "elsewhere");
    await mkdir(elsewhere);
    await writeFile(
      planPath,
      [
        "schema: marimo-export.plan.v1",
        "outputs:",
        "  prices:",
        "    source: prices",
        "    formats:",
        "      json: {}",
      ].join("\n"),
    );
    if (kind === "root") {
      await symlink(elsewhere, destination);
    } else {
      await mkdir(destination);
      if (kind === "cache") await symlink(elsewhere, join(destination, "cache"));
      if (kind === "index")
        await symlink(join(elsewhere, "index.json"), join(destination, "index.json"));
      if (kind === "index-directory") await mkdir(join(destination, "index.json"));
    }
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const output = capture();

    const result = await runCli(
      [
        "publish",
        "--server",
        "https://marimo.test",
        "--session",
        "s_running",
        "--plan",
        planPath,
        "--out",
        destination,
        "--json",
      ],
      output.io,
    );

    expect(result.exitCode).toBe(1);
    expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "invalid_request" } });
    expect(fetch).not.toHaveBeenCalled();
  });

  test.each(["index.json", "cache/build.json"])(
    "rejects a colliding publication record at %s before connecting",
    async (record) => {
      const root = await temporaryRoot();
      const planPath = join(root, "plan.yaml");
      const destination = join(root, "publication");
      await writeFile(
        planPath,
        [
          "schema: marimo-export.plan.v1",
          "outputs:",
          "  prices:",
          "    source: prices",
          "    formats:",
          "      json: {}",
        ].join("\n"),
      );
      const fetch = vi.fn();
      vi.stubGlobal("fetch", fetch);
      const output = capture();

      const result = await runCli(
        [
          "publish",
          "--server",
          "https://marimo.test",
          "--notebook",
          "examples/_notebooks/finance.py",
          "--plan",
          planPath,
          "--out",
          destination,
          "--record",
          join(destination, record),
          "--json",
        ],
        output.io,
      );

      expect(result.exitCode).toBe(2);
      expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "usage_error" } });
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  test.each(["record alias", "publication alias"] as const)(
    "rejects a %s into the same publication before connecting",
    async (aliasDirection) => {
      const root = await temporaryRoot();
      const actual = join(root, "actual");
      const alias = join(root, "alias");
      const planPath = join(root, "plan.yaml");
      await mkdir(actual);
      await symlink(actual, alias);
      await writeFile(
        planPath,
        [
          "schema: marimo-export.plan.v1",
          "outputs:",
          "  prices:",
          "    source: prices",
          "    formats:",
          "      json: {}",
        ].join("\n"),
      );
      const directPublication = join(actual, "publication");
      const aliasedPublication = join(alias, "publication");
      const destination =
        aliasDirection === "publication alias" ? aliasedPublication : directPublication;
      const record = join(
        aliasDirection === "record alias" ? aliasedPublication : directPublication,
        "index.json",
      );
      const fetch = vi.fn();
      vi.stubGlobal("fetch", fetch);
      const output = capture();

      const result = await runCli(
        [
          "publish",
          "--server",
          "https://marimo.test",
          "--notebook",
          "examples/_notebooks/finance.py",
          "--plan",
          planPath,
          "--out",
          destination,
          "--record",
          record,
          "--json",
        ],
        output.io,
      );

      expect(result.exitCode).toBe(2);
      expect(JSON.parse(output.stderr())).toMatchObject({ error: { code: "usage_error" } });
      expect(fetch).not.toHaveBeenCalled();
    },
  );
});

async function checkout(
  input?: Awaited<ReturnType<typeof exportFixture>>,
): Promise<{ root: string }> {
  const fixture = input ?? (await exportFixture());
  const root = await temporaryRoot();
  await pullExport({ source: memorySource(fixture.objects), into: root, ref: fixture.ref });
  return { root };
}

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "marimo-export-cli-"));
  roots.push(root);
  return root;
}

function capture(stdin = ""): {
  io: {
    stdout(data: string | Uint8Array): void;
    stderr(text: string): void;
    stdin(): Promise<string>;
  };
  stdout(): string;
  stdoutBytes(): Uint8Array;
  stderr(): string;
} {
  const stdout: Uint8Array[] = [];
  const stderr: string[] = [];
  return {
    io: {
      stdout: (data) =>
        stdout.push(typeof data === "string" ? new TextEncoder().encode(data) : data.slice()),
      stderr: (text) => stderr.push(text),
      stdin: async () => stdin,
    },
    stdout: () => new TextDecoder().decode(concatenate(stdout)),
    stdoutBytes: () => concatenate(stdout),
    stderr: () => stderr.join(""),
  };
}

function concatenate(chunks: readonly Uint8Array[]): Uint8Array {
  const bytes = new Uint8Array(chunks.reduce((length, chunk) => length + chunk.byteLength, 0));
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function remoteRequest(init: RequestInit | undefined): {
  request_id: string;
  operation: string;
} {
  if (typeof init?.body !== "string") throw new TypeError("expected remote request body");
  const body = JSON.parse(init.body) as { code: string };
  const assignment = body.code.split("\n", 1)[0];
  if (assignment === undefined) throw new TypeError("expected request assignment");
  return JSON.parse(JSON.parse(assignment.slice("request_json = ".length)) as string) as {
    request_id: string;
    operation: string;
  };
}

function remoteResponse(requestId: string, data: unknown): Response {
  const envelope = {
    protocol: REMOTE_PROTOCOL,
    request_id: requestId,
    ok: true,
    data,
  };
  const stdout = JSON.stringify({ data: `${RESPONSE_PREFIX}${JSON.stringify(envelope)}\n` });
  const done = JSON.stringify({ success: true, output: null });
  return new Response(`event: stdout\ndata: ${stdout}\n\nevent: done\ndata: ${done}\n\n`);
}

class ReadyWebSocket {
  private readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(_url: string | URL) {
    queueMicrotask(() =>
      this.emit({
        data: JSON.stringify({
          op: "kernel-ready",
          data: { resumed: false, kiosk: false, auto_instantiated: false },
        }),
      }),
    );
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {}

  private emit(event: { data: string }): void {
    for (const listener of this.listeners.get("message") ?? []) listener(event as MessageEvent);
  }
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}

function urlOf(input: string | URL | Request): string {
  return input instanceof Request ? input.url : input instanceof URL ? input.href : input;
}
