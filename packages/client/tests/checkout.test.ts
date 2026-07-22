import { spawn } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  symlink,
  truncate,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, test } from "vite-plus/test";

import { pullExport, pullRemote, verifyExport } from "../src/node/checkout.js";
import { matchesRegularFile } from "../src/node/safe-file.js";
import { directorySource, exportPath } from "../src/node/source.js";
import { openExport } from "../src/reader.js";
import type { Remote } from "../src/remote/session.js";
import { memorySource } from "../src/source.js";
import type { ExportSource } from "../src/types.js";
import { exportFixture } from "./fixture.js";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("export transfer", () => {
  test("pulls the deduplicated projection closure and skips verified files", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const source = memorySource(fixture.objects);

    await expect(pullExport({ source, into: root, ref: fixture.ref })).resolves.toEqual({
      files: 3,
      downloaded: 3,
      skipped: 0,
      bytes: expect.any(Number),
    });
    expect(await readFile(join(root, "index.json"))).toEqual(Buffer.from(fixture.indexBytes));

    await expect(pullExport({ source, into: root, ref: fixture.ref })).resolves.toMatchObject({
      files: 3,
      downloaded: 0,
      skipped: 3,
    });
    await expect(
      verifyExport({ source: directorySource(root), ref: fixture.ref }),
    ).resolves.toEqual(expect.objectContaining({ ok: true, files: 3, failures: [] }));
    const published = await openExport(directorySource(root), { ref: fixture.ref });
    await expect(published.scenario("microsoft").output("empty").bytes()).resolves.toEqual(
      new Uint8Array(),
    );
  });

  test("owns source bytes before verification and commit", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const payload = fixture.index.scenarios[0]!.outputs.prices!.json!.payload;
    const payloadPath = `cache/${payload.key}`;
    const shared = fixture.objects[payloadPath]!;
    const source: ExportSource = {
      read(path) {
        const bytes = fixture.objects[path];
        if (bytes === undefined) return Promise.reject(new Error(`Missing ${path}`));
        if (path !== payloadPath) return Promise.resolve(new Uint8Array(bytes));
        const result = Promise.resolve(shared);
        void result.then(() => queueMicrotask(() => shared.fill(0)));
        return result;
      },
    };

    await pullExport({ source, into: root, ref: fixture.ref });

    expect(shared.every((byte) => byte === 0)).toBe(true);
    await expect(
      verifyExport({ source: directorySource(root), ref: fixture.ref }),
    ).resolves.toEqual(expect.objectContaining({ ok: true, failures: [] }));
  });

  test("leaves no index when a payload transfer fails", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    let payloadReads = 0;
    const source: ExportSource = {
      async read(path, options) {
        if (path !== "index.json" && ++payloadReads === 2) throw new Error("network failed");
        return memorySource(fixture.objects).read(path, options);
      },
    };

    await expect(pullExport({ source, into: root, concurrency: 1 })).rejects.toThrow(
      "network failed",
    );
    await expect(stat(join(root, "index.json"))).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("aborts an in-flight peer after the first payload failure", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const fallback = memorySource(fixture.objects);
    const peerStarted = deferred<void>();
    const peerAborted = deferred<void>();
    let payloadReads = 0;
    const source: ExportSource = {
      async read(path, options) {
        if (path === "index.json") return fallback.read(path, options);
        payloadReads += 1;
        if (payloadReads === 1) {
          await peerStarted.promise;
          throw new Error("primary payload failed");
        }
        if (payloadReads === 2) {
          peerStarted.resolve();
          return new Promise<Uint8Array>((_resolve, reject) => {
            options?.signal?.addEventListener(
              "abort",
              () => {
                peerAborted.resolve();
                reject(options.signal?.reason);
              },
              { once: true },
            );
          });
        }
        return fallback.read(path, options);
      },
    };

    await expect(pullExport({ source, into: root, concurrency: 2 })).rejects.toThrow(
      "primary payload failed",
    );
    await peerAborted.promise;
    expect(payloadReads).toBe(2);
  });

  test("rejects a symlinked destination root", async () => {
    const fixture = await exportFixture();
    const parent = await temporaryRoot();
    const outside = await temporaryRoot();
    const root = join(parent, "publication");
    await symlink(outside, root, "dir");

    await expect(pullExport({ source: memorySource(fixture.objects), into: root })).rejects.toThrow(
      "must be a directory",
    );
    await expect(readdir(outside)).resolves.toEqual([]);
  });

  test("does not write through a symlinked cache directory", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const outside = await temporaryRoot();
    await symlink(outside, join(root, "cache"), "dir");

    await expect(pullExport({ source: memorySource(fixture.objects), into: root })).rejects.toThrow(
      "must be a directory",
    );
    await expect(readdir(outside)).resolves.toEqual([]);
    await expect(stat(join(root, "index.json"))).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("rejects an unsafe remote destination before opening a stage", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const outside = await temporaryRoot();
    await symlink(outside, join(root, "cache"), "dir");
    let openings = 0;
    const unused = async (): Promise<never> => {
      throw new Error("remote should not run");
    };
    const remote: Remote = {
      session: { id: "s_explicit", name: null, path: null, owned: false },
      describe: unused,
      build: unused,
      async open() {
        openings += 1;
        throw new Error("remote should not run");
      },
      close: async () => undefined,
    };

    await expect(pullRemote(remote, fixture.ref, { into: root })).rejects.toThrow(
      "must be a directory",
    );
    expect(openings).toBe(0);
  });

  test("rejects a symlinked payload leaf", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const outside = await temporaryRoot();
    const payload = fixture.index.scenarios[0]!.outputs.prices!.json!.payload;
    const destination = exportPath(root, `cache/${payload.key}`);
    const external = join(outside, "payload.bin");
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(external, "external");
    await symlink(external, destination);

    await expect(pullExport({ source: memorySource(fixture.objects), into: root })).rejects.toThrow(
      "must be a regular file",
    );
    await expect(readFile(external, "utf8")).resolves.toBe("external");
    await expect(stat(join(root, "index.json"))).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("keeps an external index target unchanged", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const outside = await temporaryRoot();
    const external = join(outside, "index.json");
    let reads = 0;
    const source: ExportSource = {
      async read(path, options) {
        reads += 1;
        return memorySource(fixture.objects).read(path, options);
      },
    };
    await writeFile(external, "external");
    await symlink(external, join(root, "index.json"));

    await expect(pullExport({ source, into: root })).rejects.toThrow("must be a regular file");
    expect(reads).toBe(0);
    await expect(readFile(external, "utf8")).resolves.toBe("external");
  });

  test("reports corrupt local payloads through the verification boundary", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    await pullExport({ source: memorySource(fixture.objects), into: root });
    const key = fixture.index.scenarios[0]!.outputs.prices!.json!.payload.key;
    await writeFile(exportPath(root, `cache/${key}`), "corrupt");

    await expect(verifyExport({ source: directorySource(root) })).resolves.toEqual(
      expect.objectContaining({
        ok: false,
        failures: [expect.objectContaining({ key, message: expect.stringContaining("bytes") })],
      }),
    );
  });

  test("replaces a size-mismatched sparse payload without reading it wholesale", async () => {
    const fixture = await exportFixture();
    const root = await temporaryRoot();
    const source = memorySource(fixture.objects);
    await pullExport({ source, into: root });
    const payload = fixture.index.scenarios[0]!.outputs.prices!.json!.payload;
    const path = exportPath(root, `cache/${payload.key}`);
    await truncate(path, 3 * 1024 ** 3);

    await expect(pullExport({ source, into: root })).resolves.toMatchObject({
      downloaded: 1,
      skipped: 2,
    });
    await expect(stat(path)).resolves.toMatchObject({ size: payload.size });
  });

  test("checks cancellation between existing-file hash chunks", async () => {
    const root = await temporaryRoot();
    const path = join(root, "payload.bin");
    const bytes = new Uint8Array(256 * 1024);
    await writeFile(path, bytes);
    const reason = new DOMException("cancelled", "AbortError");
    let checks = 0;
    const signal = {
      throwIfAborted() {
        checks += 1;
        if (checks === 3) throw reason;
      },
    } as AbortSignal;

    await expect(
      matchesRegularFile(
        path,
        {
          key: `marimo-export/payloads/sha256/${"0".repeat(64)}`,
          sha256: "0".repeat(64),
          size: bytes.byteLength,
        },
        signal,
      ),
    ).rejects.toBe(reason);
  });

  test("rejects oversized sparse local reads before allocation", async () => {
    const root = await temporaryRoot();
    const path = join(root, "index.json");
    await writeFile(path, "{}");
    await truncate(path, 3 * 1024 ** 3);

    await expect(
      directorySource(root).read("index.json", { maxBytes: 1024 }),
    ).rejects.toMatchObject({ code: "output_too_large" });
  });

  test("rejects a FIFO source without waiting for a writer", async () => {
    if (process.platform === "win32") return;
    const root = await temporaryRoot();
    const path = join(root, "index.json");
    await runProcess("mkfifo", [path]);

    await expect(
      directorySource(root).read("index.json", { maxBytes: 1024 }),
    ).rejects.toMatchObject({ code: "source_read_failed" });
  });

  test("preserves directory source cancellation", async () => {
    const controller = new AbortController();
    controller.abort(new DOMException("cancelled", "AbortError"));
    const source = directorySource(".");

    await expect(source.read("index.json", { signal: controller.signal })).rejects.toBe(
      controller.signal.reason,
    );
  });

  test.each([-1, 1.5])(
    "preserves invalid directory read limit TypeErrors for %s",
    async (maxBytes) => {
      const source = directorySource(".");

      await expect(source.read("index.json", { maxBytes })).rejects.toBeInstanceOf(TypeError);
    },
  );
});

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "marimo-export-transfer-"));
  roots.push(root);
  return root;
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T | PromiseLike<T>): void;
} {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

async function runProcess(command: string, args: string[]): Promise<void> {
  await new Promise<void>((resolveProcess, reject) => {
    const child = spawn(command, args, { stdio: "ignore" });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolveProcess();
      else reject(new Error(`${command} exited with ${String(code)}.`));
    });
  });
}
