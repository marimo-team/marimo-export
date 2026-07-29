import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
const temporaryRoot = await mkdtemp(join(tmpdir(), "marimo-export-browser-package-"));
const tarball = join(temporaryRoot, "marimo-export.tgz");
const coreRoot = join(temporaryRoot, "core");
const loadersRoot = join(temporaryRoot, "loaders");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

try {
  await run(pnpm, ["--config.ignore-scripts=true", "pack", "--out", tarball], packageRoot);

  await createProject(coreRoot, {
    name: "marimo-export-packed-core-smoke",
    source: `import {
  openPublication,
  PublicationError,
  scalarLoader,
} from "@marimo-team/marimo-export";

const root = document.querySelector("#app");
if (root === null) throw new PublicationError("publication_invalid", "Missing application root.");
root.textContent = scalarLoader().codec;
void openPublication;
`,
  });
  await run(pnpm, ["install", "--ignore-scripts"], coreRoot);
  await assertOptionalPeersAbsent(coreRoot);
  await run(pnpm, ["run", "typecheck"], coreRoot);
  await run(pnpm, ["run", "build"], coreRoot);

  await createProject(loadersRoot, {
    name: "marimo-export-packed-loader-smoke",
    dependencies: manifest.peerDependencies,
    source: `import { openPublication, PublicationError } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const loaders = [
  anyWidgetLoader(),
  arrowTableLoader(),
  numpyLoader(),
  parquetRowsLoader(),
  vegaLiteLoader(),
];
const root = document.querySelector("#app");
if (root === null) throw new PublicationError("publication_invalid", "Missing application root.");
void loaders;
void openPublication("/publication/").then((publication) => {
  root.textContent = publication.notebook.filename;
});
`,
  });
  await run(pnpm, ["install", "--ignore-scripts"], loadersRoot);
  await run(pnpm, ["run", "typecheck"], loadersRoot);
  await run(pnpm, ["run", "build"], loadersRoot);

  process.stdout.write("Packed browser core and loader contracts passed.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

async function assertOptionalPeersAbsent(root) {
  await Promise.all(
    Object.keys(manifest.peerDependencies).map(async (name) => {
      const path = join(root, "node_modules", ...name.split("/"));
      try {
        await access(path);
      } catch (error) {
        if (error !== null && typeof error === "object" && error.code === "ENOENT") return;
        throw error;
      }
      throw new Error(`Core-only install unexpectedly linked optional peer ${name}.`);
    }),
  );
}

async function createProject(root, options) {
  await mkdir(root);
  await Promise.all([
    writeFile(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          name: options.name,
          version: "0.0.0",
          private: true,
          type: "module",
          scripts: { build: "vp build", typecheck: "tsc --noEmit" },
          dependencies: {
            "@marimo-team/marimo-export": `file:${tarball}`,
            ...options.dependencies,
            typescript: "6.0.3",
            "vite-plus": "0.2.4",
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
