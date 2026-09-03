import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
const temporaryRoot = await mkdtemp(join(tmpdir(), "marimo-export-browser-package-"));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const pnpmTarball = join(temporaryRoot, "marimo-export-pnpm.tgz");
const peerNames = Object.keys(manifest.peerDependencies);

const loaderProjects = [
  {
    name: "prepared",
    peers: [],
    source: `import {
  PreparedStateController,
  parsePreparedExportManifest,
} from "@marimo-team/marimo-export/prepared";
document.querySelector("#app")!.textContent = typeof PreparedStateController;
void parsePreparedExportManifest;
`,
  },
  {
    name: "anywidget",
    peers: ["@anywidget/types"],
    source: `import {
  anyWidgetLoader,
  PreparedWidgetGraph,
  type PreparedWidgetGraphPort,
} from "@marimo-team/marimo-export/loader/anywidget";
document.querySelector("#app")!.textContent = anyWidgetLoader().codec + typeof PreparedWidgetGraph;
const port: PreparedWidgetGraphPort<unknown, unknown> | undefined = undefined;
void port;
`,
  },
  {
    name: "arrow",
    peers: ["@uwdata/flechette", "lz4js"],
    source: `import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";
document.querySelector("#app")!.textContent = arrowTableLoader().codec;
`,
  },
  {
    name: "html",
    peers: [],
    source: `import { htmlLoader } from "@marimo-team/marimo-export/loader/html";
document.querySelector("#app")!.textContent = htmlLoader().codec;
`,
  },
  {
    name: "json",
    peers: [],
    source: `import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
document.querySelector("#app")!.textContent = jsonLoader().codec;
`,
  },
  {
    name: "marimo-cell",
    peers: [],
    source: `import { marimoCellLoader } from "@marimo-team/marimo-export/loader/marimo-cell";
document.querySelector("#app")!.textContent = marimoCellLoader().codec;
`,
  },
  {
    name: "marimo-output",
    peers: [],
    source: `import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";
document.querySelector("#app")!.textContent = marimoOutputLoader().codec;
`,
  },
  {
    name: "numpy",
    peers: [],
    source: `import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
document.querySelector("#app")!.textContent = numpyLoader().codec;
`,
  },
  {
    name: "parquet",
    peers: ["hyparquet"],
    source: `import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
document.querySelector("#app")!.textContent = parquetRowsLoader().codec;
`,
  },
  {
    name: "text",
    peers: [],
    source: `import { textLoader } from "@marimo-team/marimo-export/loader/text";
document.querySelector("#app")!.textContent = textLoader().codec;
`,
  },
  {
    name: "vegalite",
    peers: ["vega-embed"],
    source: `import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
document.querySelector("#app")!.textContent = vegaLiteLoader().codec;
`,
  },
];

try {
  await run(pnpm, ["--config.ignore-scripts=true", "pack", "--out", pnpmTarball], packageRoot);

  const coreRoot = join(temporaryRoot, "core");
  await createProject(coreRoot, {
    name: "core",
    peers: [],
    source: `import {
  openExport,
  NotebookExportError,
  isNotebookExportError,
  scalarLoader,
} from "@marimo-team/marimo-export";

const root = document.querySelector("#app");
if (root === null) throw new NotebookExportError("export_invalid", "Missing application root.");
if (!isNotebookExportError(new NotebookExportError("export_invalid", "typed"))) {
  throw new Error("NotebookExportError brand is unavailable");
}
root.textContent = scalarLoader().codec;
void openExport;
`,
    tarball: pnpmTarball,
  });
  await validateProject(coreRoot, [], npm);
  await inspectInstalledManifest(coreRoot);

  await Promise.all(
    loaderProjects.map(async (project) => {
      const root = join(temporaryRoot, project.name);
      await createProject(root, { ...project, tarball: pnpmTarball });
      await validateProject(root, project.peers, pnpm);
      if (project.name === "text") await inspectInstalledManifest(root);
    }),
  );

  process.stdout.write("Packed browser npm and pnpm consumer contracts passed.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

async function createProject(root, options) {
  await mkdir(root);
  const peerDependencies = Object.fromEntries(
    options.peers.map((name) => [name, manifest.peerDependencies[name]]),
  );
  await Promise.all([
    writeFile(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          name: `marimo-export-packed-${options.name}-smoke`,
          version: "0.0.0",
          private: true,
          type: "module",
          scripts: { build: "vite build", typecheck: "tsc --noEmit" },
          dependencies: {
            "@marimo-team/marimo-export": `file:${options.tarball}`,
            ...peerDependencies,
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
      `${JSON.stringify(
        {
          packages: ["."],
        },
        null,
        2,
      )}\n`,
    ),
    writeFile(
      join(root, "index.html"),
      '<!doctype html><main id="app"></main><script type="module" src="/src.ts"></script>\n',
    ),
    writeFile(join(root, "src.ts"), options.source),
  ]);
}

async function validateProject(root, expectedPeers, manager) {
  await run(manager, ["install", "--ignore-scripts"], root);
  await assertPeerClosure(root, new Set(expectedPeers));
  await run(manager, ["run", "typecheck"], root);
  await run(manager, ["run", "build"], root);
}

async function assertPeerClosure(root, expected) {
  await Promise.all(
    peerNames.map(async (name) => {
      const path = join(root, "node_modules", ...name.split("/"));
      const present = await pathExists(path);
      assert.equal(
        present,
        expected.has(name),
        `${name} was ${present ? "present" : "absent"} in the ${[...expected].join(", ") || "core"} consumer`,
      );
    }),
  );
}

async function inspectInstalledManifest(root) {
  const installedRoot = join(root, "node_modules", "@marimo-team", "marimo-export");
  const installed = JSON.parse(await readFile(join(installedRoot, "package.json"), "utf8"));
  assert.equal(installed.version, manifest.version);
  assert.deepEqual(installed.repository, manifest.repository);
  assert.deepEqual(installed.exports, manifest.publishConfig.exports);
  assert.deepEqual(installed.publishConfig, { access: "public" });
  assert.deepEqual(installed.dependencies, {
    "@msgpack/msgpack": "^3.1.3",
  });
  assert.equal(
    JSON.stringify({
      dependencies: installed.dependencies,
      peerDependencies: installed.peerDependencies,
    }).includes("catalog:"),
    false,
  );
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
