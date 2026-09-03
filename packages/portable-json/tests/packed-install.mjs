import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
const temporaryRoot = await mkdtemp(join(tmpdir(), "portable-json-package-"));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

try {
  const pnpmTarball = join(temporaryRoot, "portable-json-pnpm.tgz");
  await run(pnpm, ["--config.ignore-scripts=true", "pack", "--out", pnpmTarball], packageRoot);

  const rootConsumer = join(temporaryRoot, "root");
  const zodConsumer = join(temporaryRoot, "zod");
  await Promise.all([
    createProject(rootConsumer, {
      name: "root",
      tarball: pnpmTarball,
      dependencies: {},
      source: `import { parsePortableJson, portableJsonObject } from "@marimo-team/portable-json";
const value = portableJsonObject({ rows: [1, 2] });
document.querySelector("#app")!.textContent = String(parsePortableJson('{"ready":true}'));
void value;
`,
    }),
    createProject(zodConsumer, {
      name: "zod",
      tarball: pnpmTarball,
      dependencies: { zod: "4.3.6" },
      source: `import { jsonObjectSchema } from "@marimo-team/portable-json/zod";
document.querySelector("#app")!.textContent = String(jsonObjectSchema.parse({ ready: true }).ready);
`,
    }),
  ]);
  await Promise.all([validateProject(rootConsumer, npm), validateProject(zodConsumer, pnpm)]);
  await Promise.all([
    inspectInstalledManifest(rootConsumer),
    inspectInstalledManifest(zodConsumer),
  ]);
  assert.equal(await pathExists(join(rootConsumer, "node_modules", "zod")), false);
  assert.equal(await pathExists(join(zodConsumer, "node_modules", "zod")), true);

  process.stdout.write("Packed portable JSON npm and pnpm consumer contracts passed.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

async function createProject(root, options) {
  await mkdir(root);
  await Promise.all([
    writeFile(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          name: `portable-json-packed-${options.name}-smoke`,
          version: "0.0.0",
          private: true,
          type: "module",
          scripts: { build: "vite build", typecheck: "tsc --noEmit" },
          dependencies: {
            "@marimo-team/portable-json": `file:${options.tarball}`,
            ...options.dependencies,
            typescript: "6.0.3",
            vite: "8.1.3",
          },
        },
        null,
        2,
      )}\n`,
    ),
    writeFile(
      join(root, "tsconfig.json"),
      `${JSON.stringify(
        {
          compilerOptions: {
            target: "ES2022",
            module: "ESNext",
            moduleResolution: "Bundler",
            lib: ["ES2022", "DOM", "DOM.Iterable"],
            strict: true,
            noEmit: true,
            skipLibCheck: true,
            exactOptionalPropertyTypes: true,
            noUncheckedIndexedAccess: true,
            verbatimModuleSyntax: true,
          },
          include: ["src.ts"],
        },
        null,
        2,
      )}\n`,
    ),
    writeFile(
      join(root, "pnpm-workspace.yaml"),
      `${JSON.stringify({ packages: ["."] }, null, 2)}\n`,
    ),
    writeFile(
      join(root, "index.html"),
      '<!doctype html><main id="app"></main><script type="module" src="/src.ts"></script>\n',
    ),
    writeFile(join(root, "src.ts"), options.source),
  ]);
}

async function validateProject(root, manager) {
  await run(manager, ["install", "--ignore-scripts"], root);
  await run(manager, ["run", "typecheck"], root);
  await run(manager, ["run", "build"], root);
}

async function inspectInstalledManifest(root) {
  const installedRoot = join(root, "node_modules", "@marimo-team", "portable-json");
  const installed = JSON.parse(await readFile(join(installedRoot, "package.json"), "utf8"));
  assert.equal(installed.version, manifest.version);
  assert.deepEqual(installed.repository, manifest.repository);
  assert.deepEqual(installed.exports, manifest.publishConfig.exports);
  assert.deepEqual(installed.publishConfig, { access: "public" });
  assert.equal(installed.dependencies, undefined);
  assert.deepEqual(installed.peerDependencies, { zod: "^4.3.6" });
  assert.deepEqual(installed.peerDependenciesMeta, { zod: { optional: true } });
  await Promise.all(
    exportTargets(installed.exports).map((target) => access(join(installedRoot, target))),
  );
}

function exportTargets(exports) {
  return Object.values(exports).flatMap((value) =>
    Object.prototype.toString.call(value) === "[object String]" ? [value] : Object.values(value),
  );
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch (cause) {
    if (cause instanceof Object && "code" in cause && cause.code === "ENOENT") return false;
    throw cause;
  }
}

function run(command, args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (code === 0) {
        resolve(stdout);
        return;
      }
      reject(
        new Error(
          `${command} exited with ${code === null ? `signal ${signal}` : `status ${code}`}.\n${stdout}${stderr}`,
        ),
      );
    });
  });
}
