import { spawn } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { publishExample } from "./example-publication.ts";

const packageRoot = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const repositoryRoot = resolve(packageRoot, "../..");
const cacheRoot = join(packageRoot, ".vitepress", "cache");
const quickstartRoot = join(repositoryRoot, "examples", "quickstart");
const quickstartNotebook = join(quickstartRoot, "report.py");
const quickstartSpec = join(quickstartRoot, "report.export.yaml");
const quickstartDestination = join(packageRoot, "public", "examples", "quickstart");
const marketRoot = join(repositoryRoot, "examples", "vite-vanilla");
const marketNotebook = join(marketRoot, "finance.py");
const marketDestination = join(packageRoot, "public", "examples", "market-dashboard");

const run = (command: string, arguments_: readonly string[], environment = process.env) =>
  new Promise<void>((resolveCommand, rejectCommand) => {
    const child = spawn(command, arguments_, {
      cwd: repositoryRoot,
      env: environment,
      stdio: "inherit",
    });
    child.on("error", rejectCommand);
    child.on("close", (code) => {
      if (code === 0) {
        resolveCommand();
        return;
      }
      rejectCommand(
        new Error(`${command} ${arguments_.join(" ")} exited with ${code ?? "no status"}.`),
      );
    });
  });

const isFile = async (path: string): Promise<boolean> => {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
};

const verifyApplicationBundle = async (bundle: string, label: string): Promise<void> => {
  const entrypoint = join(bundle, "index.html");
  const exportIndex = join(bundle, "export", "index.json");
  if (!(await isFile(entrypoint)) || !(await isFile(exportIndex))) {
    throw new Error(`${label} is missing its application or export entrypoint.`);
  }
  const document = await readFile(entrypoint, "utf8");
  if (/\b(?:href|src)="\/(?!\/)/.test(document)) {
    throw new Error(`${label} contains a root-absolute asset URL.`);
  }
  const references = [...document.matchAll(/\b(?:href|src)="([^"#?]+)"/g)]
    .map((match) => match[1])
    .filter((reference): reference is string =>
      Boolean(reference && !reference.startsWith("data:")),
    );
  const missingReference = (
    await Promise.all(
      references.map(async (reference) => ({
        exists: await isFile(resolve(dirname(entrypoint), reference)),
        reference,
      })),
    )
  ).find(({ exists }) => !exists);
  if (missingReference !== undefined) {
    throw new Error(`${label} references a missing file: ${missingReference.reference}`);
  }
  const assets = await readdir(join(bundle, "export", "assets"));
  if (assets.length === 0) {
    throw new Error(`${label} contains no exported assets.`);
  }
};

const verifyQuickstart = async (root: string): Promise<void> => {
  const indexPath = join(root, "index.json");
  if (!(await isFile(indexPath))) {
    throw new Error("The quickstart export is missing index.json.");
  }
  const index = await readFile(indexPath, "utf8");
  const required = ['"aliases":{"monthly":', '"inputs":["days"]', '"outputs":["report","summary"]'];
  const missing = required.filter((value) => !index.includes(value));
  const jsonOutputs = index.match(/"codec":"marimo\.json\.v1"/g) ?? [];
  const reportOutputs = index.match(/"codec":"marimo\.output\.v1"/g) ?? [];
  const stateInputs = index.match(/"inputs":\{"days":(?:7|30)\}/g) ?? [];
  if (
    missing.length > 0 ||
    jsonOutputs.length !== 2 ||
    reportOutputs.length !== 2 ||
    stateInputs.length !== 2
  ) {
    throw new Error("The quickstart export state-output relation is incomplete.");
  }
  const assets = await readdir(join(root, "assets"));
  if (assets.length !== 2 || assets.some((asset) => !asset.endsWith(".output.json"))) {
    throw new Error("The quickstart export must contain two rendered report assets.");
  }
};

const verifyQuickstartBundle = async (bundle: string): Promise<void> => {
  await verifyApplicationBundle(bundle, "The quickstart application bundle");
  await verifyQuickstart(join(bundle, "export"));
  const files = await readdir(bundle, { recursive: true });
  if (files.some((file) => file.endsWith(".py"))) {
    throw new Error("The quickstart application bundle must not contain Python source files.");
  }
  const document = await readFile(join(bundle, "index.html"), "utf8");
  if (
    document.includes("<marimo-code") ||
    document.includes("<marimo-filename") ||
    !document.includes("Ships no Python source or runtime")
  ) {
    throw new Error("The quickstart application bundle crosses the producer boundary.");
  }
};

const verifyNotebook = async (entrypoint: string, notebook: string): Promise<void> => {
  if (!(await isFile(entrypoint))) {
    throw new Error("The documentation notebook export did not create index.html.");
  }
  const document = await readFile(entrypoint, "utf8");
  if (
    !document.includes(`<marimo-filename hidden>${basename(notebook)}`) ||
    !document.includes("<marimo-code hidden")
  ) {
    throw new Error("The documentation notebook export is missing its source or captured output.");
  }
};

const buildQuickstart = async (): Promise<void> => {
  await mkdir(dirname(quickstartDestination), { recursive: true });
  const notebookSource = await readFile(quickstartNotebook);
  const stagingRoot = await mkdtemp(join(cacheRoot, "quickstart-"));
  const publicRoot = join(stagingRoot, "public");
  const publicationRoot = join(stagingRoot, "publication");
  const applicationRoot = join(publicationRoot, "application");
  const notebookRoot = join(publicationRoot, "notebook");
  const notebookEntrypoint = join(notebookRoot, "index.html");
  try {
    await mkdir(publicRoot, { recursive: true });
    await mkdir(notebookRoot, { recursive: true });
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export",
      "marimo",
      "export",
      "html",
      quickstartNotebook,
      "--output",
      notebookEntrypoint,
    ]);
    await verifyNotebook(notebookEntrypoint, quickstartNotebook);
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export",
      "marimo-export",
      "build",
      quickstartNotebook,
      "--spec",
      quickstartSpec,
      "--output",
      join(publicRoot, "export"),
      "--repository",
      join(stagingRoot, "repository"),
    ]);
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export",
      "marimo-export",
      "verify",
      join(publicRoot, "export"),
    ]);
    await run("pnpm", ["--filter", "@marimo-team/marimo-export-example-quickstart", "build"], {
      ...process.env,
      MARIMO_EXPORT_EXAMPLE_OUT_DIR: applicationRoot,
      MARIMO_EXPORT_EXAMPLE_PUBLIC_DIR: publicRoot,
    });
    await verifyQuickstartBundle(applicationRoot);
    if (!(await readFile(quickstartNotebook)).equals(notebookSource)) {
      throw new Error("The quickstart documentation build changed the notebook source.");
    }
    await publishExample({
      destination: quickstartDestination,
      previous: join(cacheRoot, `quickstart-previous-${process.pid}`),
      staging: publicationRoot,
    });
    await rm(stagingRoot, { force: true, recursive: true });
  } catch (error) {
    await rm(stagingRoot, { force: true, recursive: true });
    throw error;
  }
};

const buildMarketDashboard = async (): Promise<void> => {
  await mkdir(dirname(marketDestination), { recursive: true });
  const stagingRoot = await mkdtemp(join(cacheRoot, "market-dashboard-"));
  const publicRoot = join(stagingRoot, "public");
  const publicationRoot = join(stagingRoot, "publication");
  const applicationRoot = join(publicationRoot, "application");
  const notebookRoot = join(publicationRoot, "notebook");
  const notebookEntrypoint = join(notebookRoot, "index.html");
  try {
    await mkdir(publicRoot, { recursive: true });
    await mkdir(notebookRoot, { recursive: true });
    await cp(join(marketRoot, "public", "fonts"), join(publicRoot, "fonts"), {
      recursive: true,
    });
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export-vite-vanilla-example",
      "marimo",
      "export",
      "html",
      marketNotebook,
      "--sandbox",
      "--output",
      notebookEntrypoint,
    ]);
    await verifyNotebook(notebookEntrypoint, marketNotebook);
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export-vite-vanilla-example",
      "marimo-export",
      "build",
      marketNotebook,
      "--spec",
      join(marketRoot, "finance.export.yaml"),
      "--output",
      join(publicRoot, "export"),
    ]);
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export-vite-vanilla-example",
      "marimo-export",
      "verify",
      join(publicRoot, "export"),
    ]);
    await run("pnpm", ["--filter", "@marimo-team/marimo-export-example-vite-vanilla", "build"], {
      ...process.env,
      MARIMO_EXPORT_EXAMPLE_OUT_DIR: applicationRoot,
      MARIMO_EXPORT_EXAMPLE_PUBLIC_DIR: publicRoot,
    });
    await verifyApplicationBundle(applicationRoot, "The market dashboard bundle");
    await publishExample({
      destination: marketDestination,
      previous: join(cacheRoot, `market-dashboard-previous-${process.pid}`),
      staging: publicationRoot,
    });
    await rm(stagingRoot, { force: true, recursive: true });
  } catch (error) {
    await rm(stagingRoot, { force: true, recursive: true });
    throw error;
  }
};

const main = async (): Promise<void> => {
  await mkdir(cacheRoot, { recursive: true });
  await buildQuickstart();
  await buildMarketDashboard();
  console.log("Built and published the quickstart and market dashboard documentation examples.");
};

main().catch((error: Error) => {
  console.error(error.message);
  process.exitCode = 1;
});
