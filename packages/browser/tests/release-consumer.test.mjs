import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { join } from "node:path";
import { expect, it } from "vite-plus/test";

import { createConsumer } from "../../../scripts/smoke_npm_packages.mjs";

const { Document, parse, parseAllDocuments } = createRequire(
  import.meta.resolve("vite-plus/package.json"),
)("yaml");

it("the isolated consumer uses the repository toolchain and a writable pnpm workspace", async () => {
  const root = await mkdtemp(join(tmpdir(), "release-consumer-"));
  try {
    const fixture = join(root, "package");
    const consumer = join(root, "consumer");
    await mkdir(fixture);
    await writeFile(
      join(fixture, "package.json"),
      JSON.stringify({ name: "@marimo-team/marimo-export", version: "0.0.3" }),
    );
    await createConsumer(consumer, `file:${fixture}`);
    const manifest = JSON.parse(await readFile(join(consumer, "package.json"), "utf8"));
    const repository = JSON.parse(
      await readFile(new URL("../../../package.json", import.meta.url), "utf8"),
    );
    expect(manifest.packageManager).toBe(repository.packageManager);
    const workspace = parse(await readFile(join(consumer, "pnpm-workspace.yaml"), "utf8"));
    expect(workspace.minimumReleaseAgeExclude).toEqual(["@marimo-team/*"]);
    // Offline installation needs pnpm 12's locked package-manager metadata.
    const [environment] = parseAllDocuments(
      await readFile(new URL("../../../pnpm-lock.yaml", import.meta.url), "utf8"),
    );
    const project = new Document({ lockfileVersion: "9.0", importers: {} });
    await writeFile(
      join(consumer, "pnpm-lock.yaml"),
      environment.toString() + project.toString({ directives: true }),
    );
    execFileSync(
      "pnpm",
      [
        "install",
        "--lockfile-only",
        "--offline",
        "--ignore-scripts",
        `--config.cache-dir=${join(root, "cache")}`,
      ],
      { cwd: consumer, stdio: "pipe", timeout: 15_000 },
    );
    expect(await readFile(join(consumer, "pnpm-lock.yaml"), "utf8")).toContain(
      "@marimo-team/marimo-export",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}, 20_000);
