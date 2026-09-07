import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, copyFile, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export async function smokePackages(browserDependency, expectedVersion) {
  const browserSpec = await packageSpec(browserDependency);
  const temporaryRoot = await mkdtemp(resolve(tmpdir(), "marimo-export-npm-smoke-"));
  const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
  try {
    const root = resolve(temporaryRoot, "pnpm");
    await createConsumer(root, browserSpec);
    await run(pnpm, ["install", "--ignore-scripts"], root);
    await run(process.execPath, ["smoke.mjs", expectedVersion], root);
    process.stdout.write(
      `Verified marimo-export ${expectedVersion} through an isolated pnpm install.\n`,
    );
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [browserDependency, expectedVersion] = process.argv.slice(2);
  if (browserDependency === undefined || expectedVersion === undefined) {
    throw new Error("Usage: node scripts/smoke_npm_packages.mjs BROWSER_DEPENDENCY VERSION");
  }
  await smokePackages(browserDependency, expectedVersion);
}

async function packageSpec(value) {
  const candidate = isAbsolute(value) ? value : resolve(value);
  try {
    await access(candidate);
  } catch (error) {
    if (error instanceof Object && "code" in error && error.code === "ENOENT") return value;
    throw error;
  }
  return `file:${candidate}`;
}

export async function createConsumer(root, browserSpec) {
  const { packageManager } = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  assert.match(
    packageManager,
    /^pnpm@\d+\.\d+\.\d+(?:\+sha512\.[a-f0-9]+)?$/,
    "The release consumer requires the repository pnpm version pin",
  );
  await mkdir(root);
  await Promise.all([
    writeFile(
      resolve(root, "package.json"),
      `${JSON.stringify(
        {
          name: "marimo-export-release-smoke",
          version: "0.0.0",
          private: true,
          type: "module",
          packageManager,
          dependencies: {
            "@marimo-team/marimo-export": browserSpec,
          },
        },
        null,
        2,
      )}\n`,
    ),
    copyFile(
      new URL("./fixtures/npm-consumer/pnpm-workspace.yaml", import.meta.url),
      resolve(root, "pnpm-workspace.yaml"),
    ),
    writeFile(
      resolve(root, "smoke.mjs"),
      `import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { openExport } from "@marimo-team/marimo-export";
import {
  PreparedStateController,
  parsePreparedExportManifest,
} from "@marimo-team/marimo-export/prepared";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const require = createRequire(import.meta.url);
const expectedVersion = process.argv[2];
const browser = require("@marimo-team/marimo-export/package.json");

assert.equal(browser.version, expectedVersion);
assert.equal(browser.dependencies?.["@marimo-team/portable-json"], undefined);
assert.equal(typeof openExport, "function");
assert.equal(typeof PreparedStateController, "function");
assert.equal(typeof parsePreparedExportManifest, "function");
assert.equal(typeof jsonLoader, "function");
`,
    ),
  ]);
}

function run(command, arguments_, cwd) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, arguments_, {
      cwd,
      env: { ...process.env, npm_config_ignore_scripts: "true" },
      stdio: ["ignore", "pipe", "pipe"],
    });
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
        resolvePromise(stdout);
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
