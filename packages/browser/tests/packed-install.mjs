import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const workspaceRoot = fileURLToPath(new URL("../../..", import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), "marimo-export-browser-package-"));
const installRoot = join(temporaryRoot, "install");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const packages = [
  packedPackage("@marimo-team/marimo-export", packageRoot, "marimo-export.tgz"),
  packedPackage(
    "@marimo-team/marimo-export-loader-anywidget",
    join(workspaceRoot, "packages/loader-anywidget"),
    "loader-anywidget.tgz",
  ),
  packedPackage(
    "@marimo-team/marimo-export-loader-vegalite",
    join(workspaceRoot, "packages/loader-vegalite"),
    "loader-vegalite.tgz",
  ),
];

try {
  await mkdir(installRoot);
  await Promise.all(
    packages.map((item) =>
      run(pnpm, ["--config.ignore-scripts=true", "pack", "--out", item.tarball], item.root),
    ),
  );

  await writeFile(
    join(installRoot, "package.json"),
    `${JSON.stringify(
      {
        name: "marimo-export-packed-browser-smoke",
        version: "0.0.0",
        private: true,
        type: "module",
        scripts: { build: "vp build" },
        dependencies: {
          ...Object.fromEntries(packages.map((item) => [item.name, `file:${item.tarball}`])),
          "vite-plus": "0.2.4",
        },
      },
      null,
      2,
    )}\n`,
  );
  await writeFile(
    join(installRoot, "pnpm-workspace.yaml"),
    `${JSON.stringify(
      {
        packages: ["."],
        overrides: {
          "@marimo-team/marimo-export": `file:${packages[0].tarball}`,
        },
      },
      null,
      2,
    )}\n`,
  );
  await writeFile(
    join(installRoot, "index.html"),
    '<!doctype html><main id="app"></main><script type="module" src="/src.ts"></script>\n',
  );
  await writeFile(
    join(installRoot, "src.ts"),
    `import { openPublication, PublicationError } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

const loaders = [anyWidgetLoader(), vegaLiteLoader()];
const root = document.querySelector("#app");
if (root === null) throw new PublicationError("not_found", "Missing application root.");
void openPublication("/publication/", { loaders }).then((publication) => {
  root.textContent = publication.notebook.filename;
});
`,
  );

  await run(pnpm, ["install", "--ignore-scripts"], installRoot);
  await run(pnpm, ["run", "build"], installRoot);
  process.stdout.write("Packed browser package contract passed.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

function packedPackage(name, root, filename) {
  return { name, root, tarball: join(temporaryRoot, filename) };
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
