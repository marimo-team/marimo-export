import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import packageMetadata from "../package.json" with { type: "json" };

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const workspaceRoot = fileURLToPath(new URL("../../..", import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), "marimo-export-package-"));
const installRoot = join(temporaryRoot, "install");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const packages = [
  packedPackage("@marimo-team/marimo-export", packageRoot, "marimo-export.tgz"),
  packedPackage(
    "@marimo-team/marimo-export-anywidget",
    join(workspaceRoot, "packages/loader-anywidget"),
    "marimo-export-anywidget.tgz",
  ),
  packedPackage(
    "@marimo-team/marimo-export-arrow",
    join(workspaceRoot, "packages/loader-arrow"),
    "marimo-export-arrow.tgz",
  ),
  packedPackage(
    "@marimo-team/marimo-export-parquet",
    join(workspaceRoot, "packages/loader-parquet"),
    "marimo-export-parquet.tgz",
  ),
  packedPackage(
    "@marimo-team/marimo-export-vegalite",
    join(workspaceRoot, "packages/loader-vegalite"),
    "marimo-export-vegalite.tgz",
  ),
];

try {
  await mkdir(installRoot);

  await Promise.all(
    packages.map((packageToPack) =>
      run(
        pnpm,
        ["--config.ignore-scripts=true", "pack", "--out", packageToPack.tarball],
        packageToPack.root,
      ),
    ),
  );

  await writeFile(
    join(installRoot, "package.json"),
    `${JSON.stringify(
      {
        name: "marimo-export-packed-smoke",
        version: "0.0.0",
        private: true,
        type: "module",
        dependencies: Object.fromEntries(
          packages.map((packageToInstall) => [
            packageToInstall.name,
            `file:${packageToInstall.tarball}`,
          ]),
        ),
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
  await run(pnpm, ["install", "--ignore-scripts"], installRoot);

  const probe = `
const [root, remote, node, anyWidgetPackage, arrowPackage, parquetPackage, vegaLitePackage] = await Promise.all([
  import("@marimo-team/marimo-export"),
  import("@marimo-team/marimo-export/remote"),
  import("@marimo-team/marimo-export/node"),
  import("@marimo-team/marimo-export-anywidget"),
  import("@marimo-team/marimo-export-arrow"),
  import("@marimo-team/marimo-export-parquet"),
  import("@marimo-team/marimo-export-vegalite"),
]);

function assertKeys(label, namespace, expected) {
  const actual = Object.keys(namespace).sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(label + " exports " + JSON.stringify(actual));
  }
}

assertKeys("root", root, ["MarimoExportError", "httpSource", "memorySource", "openExport"]);
assertKeys("remote", remote, ["connectRemote", "validateExportPlan"]);
assertKeys("node", node, ["directorySource", "pullExport", "pullRemote", "verifyExport"]);
assertKeys("AnyWidget", anyWidgetPackage, ["anywidget"]);
assertKeys("arrow", arrowPackage, ["arrow"]);
assertKeys("parquet", parquetPackage, ["parquet"]);
assertKeys("Vega-Lite", vegaLitePackage, ["vegaLite"]);

if (anyWidgetPackage.anywidget().formatId !== "anywidget.v1") {
  throw new Error("AnyWidget loader has the wrong format identity.");
}
if (arrowPackage.arrow().formatId !== "dataframe.arrow.v1") {
  throw new Error("Arrow loader has the wrong format identity.");
}
if (parquetPackage.parquet().formatId !== "dataframe.parquet.v1") {
  throw new Error("Parquet loader has the wrong format identity.");
}
if (vegaLitePackage.vegaLite().formatId !== "vegalite.v1") {
  throw new Error("Vega-Lite loader has the wrong format identity.");
}
`;
  await run(process.execPath, ["--input-type=module", "--eval", probe], installRoot);

  await writeFile(
    join(installRoot, "probe.ts"),
    `import { anywidget } from "@marimo-team/marimo-export-anywidget";\n` +
      `import { arrow } from "@marimo-team/marimo-export-arrow";\n` +
      `import { parquet } from "@marimo-team/marimo-export-parquet";\n` +
      `import { vegaLite } from "@marimo-team/marimo-export-vegalite";\n\n` +
      `interface CounterState {\n  count: number;\n  label?: string;\n}\n\n` +
      `interface CounterExports {\n  reset(): void;\n}\n\n` +
      `interface Price {\n  symbol: string;\n  close: number;\n}\n\n` +
      `const widgetLoader = anywidget<CounterState, CounterExports>();\n` +
      `const arrowLoader = arrow<Price>({ useBigInt: true });\n` +
      `const parquetLoader = parquet<Price>({ columns: ["symbol", "close"] });\n` +
      `const chartLoader = vegaLite({ actions: false });\n` +
      `void [widgetLoader, arrowLoader, parquetLoader, chartLoader];\n`,
  );
  const typescript = join(workspaceRoot, "node_modules/typescript/bin/tsc");
  await run(
    process.execPath,
    [
      typescript,
      "--noEmit",
      "--strict",
      "--skipLibCheck",
      "--target",
      "ES2022",
      "--module",
      "NodeNext",
      "--moduleResolution",
      "NodeNext",
      "--lib",
      "ES2022,DOM",
      "probe.ts",
    ],
    installRoot,
  );

  const executable = join(
    installRoot,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "marimo-export.cmd" : "marimo-export",
  );
  const version = await run(executable, ["--version"], installRoot);
  if (version !== `${packageMetadata.version}\n`) {
    throw new Error(
      `marimo-export --version returned ${JSON.stringify(version)} for package ${JSON.stringify(packageMetadata.version)}.`,
    );
  }

  process.stdout.write("Packed package contract passed.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

function packedPackage(name, root, filename) {
  return {
    name,
    root,
    tarball: join(temporaryRoot, filename),
  };
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
