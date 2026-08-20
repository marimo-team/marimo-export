import { execFile as execFileCallback } from "node:child_process";
import { copyFile, lstat, mkdir, mkdtemp, readlink, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFile = promisify(execFileCallback);
const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const archiveRoot = await mkdtemp(join(tmpdir(), "marimo-export-clean-workspace-"));
const git = process.platform === "win32" ? "git.exe" : "git";
const node = process.execPath;
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

try {
  const { stdout } = await execFile(
    git,
    ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    {
      cwd: repositoryRoot,
      encoding: "buffer",
      maxBuffer: 16 * 1024 * 1024,
    },
  );
  const files = stdout
    .toString("utf8")
    .split("\0")
    .filter((relative) => relative.length > 0);
  await Promise.all(files.map((relative) => copyArchiveFile(relative)));

  await run(pnpm, ["install", "--offline", "--frozen-lockfile", "--ignore-scripts"], archiveRoot);
  await run(node, ["tests/dev-transform.mjs"], join(archiveRoot, "examples/vite-vanilla"));

  process.stdout.write("Clean workspace source transform passed.\n");
} finally {
  await rm(archiveRoot, { recursive: true, force: true });
}

async function copyArchiveFile(relative) {
  const source = join(repositoryRoot, relative);
  const destination = join(archiveRoot, relative);
  let metadata;
  try {
    metadata = await lstat(source);
  } catch (error) {
    if (error !== null && typeof error === "object" && error.code === "ENOENT") return;
    throw error;
  }
  await mkdir(dirname(destination), { recursive: true });
  if (metadata.isSymbolicLink()) {
    await symlink(await readlink(source), destination);
    return;
  }
  await copyFile(source, destination);
}

async function run(command, args, cwd) {
  try {
    await execFile(command, args, {
      cwd,
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
    });
  } catch (error) {
    if (error instanceof Error) {
      const stdout = "stdout" in error && typeof error.stdout === "string" ? error.stdout : "";
      const stderr = "stderr" in error && typeof error.stderr === "string" ? error.stderr : "";
      if (stdout || stderr) error.message += `\n${stdout}${stderr}`;
    }
    throw error;
  }
}
