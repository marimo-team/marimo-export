import { spawn } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { publishExample } from "./example-publication.ts";

const packageRoot = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const repositoryRoot = resolve(packageRoot, "../..");
const exampleRoot = join(repositoryRoot, "examples", "vite-vanilla");
const notebookPath = join(exampleRoot, "finance.py");
const cacheRoot = join(packageRoot, ".vitepress", "cache");
const destination = join(packageRoot, "public", "examples", "market-dashboard");

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

const verifyBundle = async (bundle: string): Promise<void> => {
  const entrypoint = join(bundle, "index.html");
  const exportIndex = join(bundle, "export", "index.json");
  if (!(await isFile(entrypoint)) || !(await isFile(exportIndex))) {
    throw new Error("The market dashboard bundle is missing its application or export entrypoint.");
  }
  const document = await readFile(entrypoint, "utf8");
  if (/\b(?:href|src)="\/(?!\/)/.test(document)) {
    throw new Error("The market dashboard bundle contains a root-absolute asset URL.");
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
    throw new Error(
      `The market dashboard bundle references a missing file: ${missingReference.reference}`,
    );
  }
  const assets = await readdir(join(bundle, "export", "assets"));
  if (assets.length === 0) {
    throw new Error("The market dashboard bundle contains no exported assets.");
  }
};

const verifyNotebook = async (entrypoint: string): Promise<void> => {
  if (!(await isFile(entrypoint))) {
    throw new Error("The documentation notebook export did not create index.html.");
  }
  const document = await readFile(entrypoint, "utf8");
  if (
    !document.includes(`<marimo-filename hidden>${basename(notebookPath)}`) ||
    !document.includes("<marimo-code hidden")
  ) {
    throw new Error("The documentation notebook export is missing its source or captured output.");
  }
};

const main = async (): Promise<void> => {
  await mkdir(cacheRoot, { recursive: true });
  await mkdir(dirname(destination), { recursive: true });
  const stagingRoot = await mkdtemp(join(cacheRoot, "market-dashboard-"));
  const publicRoot = join(stagingRoot, "public");
  const publicationRoot = join(stagingRoot, "publication");
  const applicationRoot = join(publicationRoot, "application");
  const notebookRoot = join(publicationRoot, "notebook");
  const notebookEntrypoint = join(notebookRoot, "index.html");
  try {
    await mkdir(publicRoot, { recursive: true });
    await mkdir(notebookRoot, { recursive: true });
    await cp(join(exampleRoot, "public", "fonts"), join(publicRoot, "fonts"), {
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
      notebookPath,
      "--sandbox",
      "--output",
      notebookEntrypoint,
    ]);
    await verifyNotebook(notebookEntrypoint);
    await run("uv", [
      "run",
      "--locked",
      "--package",
      "marimo-export-vite-vanilla-example",
      "marimo-export",
      "build",
      notebookPath,
      "--spec",
      join(exampleRoot, "finance.export.yaml"),
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
    await verifyBundle(applicationRoot);
    await publishExample({
      destination,
      previous: join(cacheRoot, `market-dashboard-previous-${process.pid}`),
      staging: publicationRoot,
    });
    await rm(stagingRoot, { force: true, recursive: true });
    console.log("Built and published the notebook and market dashboard documentation example.");
  } catch (error) {
    await rm(stagingRoot, { force: true, recursive: true });
    throw error;
  }
};

main().catch((error: Error) => {
  console.error(error.message);
  process.exitCode = 1;
});
