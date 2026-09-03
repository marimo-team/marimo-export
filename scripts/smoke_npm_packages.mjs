import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, resolve } from "node:path";

const [browserInput, expectedVersion] = process.argv.slice(2);
if (browserInput === undefined || expectedVersion === undefined) {
  throw new Error("Usage: node scripts/smoke_npm_packages.mjs BROWSER_SPEC VERSION");
}

const browserSpec = await packageSpec(browserInput);
const temporaryRoot = await mkdtemp(resolve(tmpdir(), "marimo-export-npm-smoke-"));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const node = process.execPath;

try {
  for (const manager of [npm, pnpm]) {
    const name = manager === npm ? "npm" : "pnpm";
    const root = resolve(temporaryRoot, name);
    // Keep each package manager isolated from the other's temporary install.
    // oxlint-disable-next-line no-await-in-loop
    await createConsumer(root);
    const installArguments =
      manager === npm
        ? ["install", "--ignore-scripts", "--no-audit", "--no-fund"]
        : ["install", "--ignore-scripts"];
    // oxlint-disable-next-line no-await-in-loop
    await run(manager, installArguments, root);
    // oxlint-disable-next-line no-await-in-loop
    await run(node, ["smoke.mjs", expectedVersion], root);
  }
  process.stdout.write(
    `Verified marimo-export ${expectedVersion} through isolated npm and pnpm installs.\n`,
  );
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
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

async function createConsumer(root) {
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
          dependencies: {
            "@marimo-team/marimo-export": browserSpec,
          },
        },
        null,
        2,
      )}\n`,
    ),
    writeFile(
      resolve(root, "pnpm-workspace.yaml"),
      `${JSON.stringify(
        {
          packages: ["."],
        },
        null,
        2,
      )}\n`,
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
