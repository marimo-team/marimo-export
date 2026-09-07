import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFile, lstat, readFile, readdir, writeFile } from "node:fs/promises";
import { basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";

function releaseVersion(value) {
  if (!/^\d+\.\d+\.\d+$/.test(value ?? "")) throw new Error("Release version must use X.Y.Z");
  return value;
}

export function validateMainCI(repository, commit, runs) {
  const matching = runs.filter(
    (run) =>
      run.event === "push" &&
      run.head_branch === "main" &&
      run.head_sha === commit &&
      run.path === ".github/workflows/ci.yml" &&
      run.repository?.full_name === repository &&
      run.head_repository?.full_name === repository,
  );
  if (
    matching.some(
      (run) =>
        !Number.isFinite(Date.parse(run.created_at)) || !Number.isSafeInteger(run.run_attempt),
    )
  ) {
    throw new Error(`Invalid main CI evidence for ${commit}`);
  }
  matching.sort(
    (left, right) =>
      Date.parse(right.created_at) - Date.parse(left.created_at) ||
      right.run_attempt - left.run_attempt,
  );
  const latest = matching[0];
  if (!latest || latest.status !== "completed" || latest.conclusion !== "success") {
    throw new Error(`Latest main CI must pass for ${commit}`);
  }
}

export function validateRecovery({ version, runId, repository, commit, run, jobs, artifacts }) {
  releaseVersion(version);
  if (!Number.isSafeInteger(Number(runId)) || !/^[1-9]\d*$/.test(String(runId))) {
    throw new Error("Source run must be a positive run ID");
  }
  if (
    run.id !== Number(runId) ||
    run.repository?.full_name !== repository ||
    run.head_repository?.full_name !== repository ||
    run.path !== ".github/workflows/publish.yml" ||
    run.event !== "push" ||
    run.head_sha !== commit ||
    run.head_branch !== `v${version}` ||
    run.status !== "completed" ||
    !Number.isSafeInteger(run.run_attempt) ||
    run.run_attempt < 1
  )
    throw new Error("Source run does not identify the completed tagged release workflow");
  for (const name of ["Build and verify", "Attest build provenance"]) {
    const matching = jobs.filter((job) => job.name === name);
    if (matching.some((job) => !Number.isSafeInteger(job.run_attempt) || job.run_attempt < 1)) {
      throw new Error(`Source release has invalid attempt evidence for ${name}`);
    }
    const attempt = Math.max(...matching.map((job) => job.run_attempt));
    const latest = matching.filter((job) => job.run_attempt === attempt);
    if (latest.length !== 1 || latest[0].conclusion !== "success") {
      throw new Error(`Source release requires successful ${name}`);
    }
  }
  const candidates = artifacts.filter((artifact) => artifact.name === "release-artifacts");
  if (candidates.length !== 1 || candidates[0].expired || candidates[0].size_in_bytes <= 0) {
    throw new Error("Source release requires one retained release-artifacts payload");
  }
  const artifact = candidates[0];
  if (artifact.workflow_run?.id !== Number(runId) || artifact.workflow_run?.head_sha !== commit) {
    throw new Error("Release artifact belongs to a different source run or commit");
  }
  return {
    schema: 1,
    version,
    tag: `v${version}`,
    commit,
    source_run: run.id,
    source_attempt: run.run_attempt,
    artifact_id: artifact.id,
  };
}

export async function verifyRecoveryFiles(directory, version) {
  releaseVersion(version);
  const expected = [
    `npm/marimo-team-marimo-export-${version}.tgz`,
    `python/marimo_export-${version}-py3-none-any.whl`,
    `python/marimo_export-${version}.tar.gz`,
  ].sort((left, right) => left.localeCompare(right));
  const entries = (await readdir(directory, { recursive: true, withFileTypes: true })).filter(
    (entry) => !entry.isDirectory(),
  );
  const actual = [];
  for (const entry of entries) {
    if (!entry.isFile()) throw new Error("Release payload must contain regular files");
    const path = resolve(entry.parentPath, entry.name);
    actual.push(path.slice(resolve(directory).length + 1).replaceAll("\\", "/"));
  }
  if (
    JSON.stringify(actual.sort((left, right) => left.localeCompare(right))) !==
    JSON.stringify(["SHA256SUMS", ...expected].sort((left, right) => left.localeCompare(right)))
  ) {
    throw new Error("Release payload does not contain the exact npm, wheel, and source archives");
  }
  const lines = (await readFile(resolve(directory, "SHA256SUMS"), "utf8")).trim().split("\n");
  const checksums = new Map();
  for (const line of lines) {
    const match = /^([a-f0-9]{64})  (.+)$/.exec(line);
    if (!match || checksums.has(match[2])) throw new Error("Invalid or duplicate release checksum");
    checksums.set(match[2], match[1]);
  }
  if (
    JSON.stringify([...checksums.keys()].sort((left, right) => left.localeCompare(right))) !==
    JSON.stringify(
      expected.map((path) => basename(path)).sort((left, right) => left.localeCompare(right)),
    )
  ) {
    throw new Error("Release checksums must name the exact versioned archives");
  }
  const pathsByName = new Map(expected.map((path) => [basename(path), path]));
  await Promise.all(
    [...checksums].map(async ([name, expectedDigest]) => {
      const path = pathsByName.get(name);
      const file = resolve(directory, path);
      if (!(await lstat(file)).isFile()) throw new Error(`Release archive is not regular: ${path}`);
      const digest = createHash("sha256")
        .update(await readFile(file))
        .digest("hex");
      if (digest !== expectedDigest) throw new Error(`Release checksum mismatch: ${path}`);
    }),
  );
}

const command = (name, args) =>
  execFileSync(name, args, {
    encoding: "utf8",
    timeout: 30_000,
    maxBuffer: 4 * 1024 * 1024,
  }).trim();
const api = (path) => JSON.parse(command("gh", ["api", path]));

export async function finalizeRecoveryReceipt(directory, version, receiptPath, attestationPath) {
  await verifyRecoveryFiles(directory, version);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  if (receipt.schema !== 1 || receipt.version !== version || receipt.tag !== `v${version}`) {
    throw new Error("Recovery receipt identifies a different release");
  }
  const digest = createHash("sha256")
    .update(await readFile(resolve(directory, "SHA256SUMS")))
    .digest("hex");
  const verified = JSON.parse(await readFile(attestationPath, "utf8"));
  const prefix = `https://github.com/${receipt.repository}/actions/runs/${receipt.source_run}/attempts/`;
  const attempts = verified.flatMap(({ verificationResult: result }) => {
    const invocation =
      result.signature?.certificate?.runInvocationURI ??
      result.statement?.predicate?.runDetails?.metadata?.invocationId;
    if (
      !result.statement?.subject?.some((subject) => subject.digest?.sha256 === digest) ||
      !invocation?.startsWith(prefix)
    )
      return [];
    const suffix = invocation.slice(prefix.length);
    const attempt = Number(suffix);
    return /^[1-9]\d*$/.test(suffix) &&
      Number.isSafeInteger(attempt) &&
      attempt <= receipt.source_attempt
      ? [attempt]
      : [];
  });
  if (attempts.length === 0)
    throw new Error("Verified checksum provenance does not identify the original source run");
  await writeFile(
    receiptPath,
    `${JSON.stringify({ ...receipt, checksums_sha256: digest, attestation_attempt: Math.max(...attempts) }, null, 2)}\n`,
  );
}

async function main() {
  const [operation, version, source] = process.argv.slice(2);
  releaseVersion(version);
  if (operation === "verify") {
    await verifyRecoveryFiles(source ?? "dist", version);
    return;
  }
  if (operation === "receipt") {
    await finalizeRecoveryReceipt(
      source ?? "dist",
      version,
      process.env.RECOVERY_RECEIPT,
      process.env.RECOVERY_ATTESTATION,
    );
    return;
  }
  if (operation !== "inspect" || process.env.GITHUB_REF !== "refs/heads/main") {
    throw new Error("Release recovery inspection must run from main");
  }
  if (!/^[1-9]\d*$/.test(source ?? "")) throw new Error("Source run must be a positive run ID");
  const repository = process.env.GITHUB_REPOSITORY;
  if (!repository || !process.env.GITHUB_OUTPUT)
    throw new Error("GitHub repository and outputs are required");
  const tag = `refs/tags/v${version}`;
  if (command("git", ["cat-file", "-t", tag]) !== "tag")
    throw new Error("Release tag must be annotated");
  const commit = command("git", ["rev-parse", `${tag}^{commit}`]);
  command("git", ["merge-base", "--is-ancestor", commit, "origin/main"]);
  const recoveryCommit = process.env.GITHUB_SHA;
  if (!/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(recoveryCommit ?? "")) {
    throw new Error("Recovery requires the current main commit identity");
  }
  for (const revision of new Set([commit, recoveryCommit])) {
    const ci = api(
      `repos/${repository}/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=${revision}&per_page=100`,
    );
    validateMainCI(repository, revision, ci.workflow_runs);
  }
  const run = api(`repos/${repository}/actions/runs/${source}`);
  const jobs = JSON.parse(
    command("gh", [
      "api",
      "--paginate",
      "--slurp",
      `repos/${repository}/actions/runs/${source}/jobs?filter=all&per_page=100`,
    ]),
  ).flatMap((page) => page.jobs);
  const artifacts = JSON.parse(
    command("gh", [
      "api",
      "--paginate",
      "--slurp",
      `repos/${repository}/actions/runs/${source}/artifacts?per_page=100`,
    ]),
  ).flatMap((page) => page.artifacts);
  const result = validateRecovery({
    version,
    runId: source,
    repository,
    commit,
    run,
    jobs,
    artifacts,
  });
  if (process.env.RECOVERY_RECEIPT) {
    const recoveryRun = Number(process.env.GITHUB_RUN_ID);
    const recoveryAttempt = Number(process.env.GITHUB_RUN_ATTEMPT);
    if (
      !Number.isSafeInteger(recoveryRun) ||
      recoveryRun < 1 ||
      !Number.isSafeInteger(recoveryAttempt) ||
      recoveryAttempt < 1 ||
      !/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(process.env.GITHUB_SHA ?? "")
    ) {
      throw new Error("Recovery receipt requires the current GitHub run identity");
    }
    await writeFile(
      process.env.RECOVERY_RECEIPT,
      `${JSON.stringify(
        {
          ...result,
          repository,
          workflow: ".github/workflows/publish.yml",
          recovery_run: recoveryRun,
          recovery_attempt: recoveryAttempt,
          recovery_commit: process.env.GITHUB_SHA,
          recovery_ref: process.env.GITHUB_REF,
        },
        null,
        2,
      )}\n`,
    );
  }
  await appendFile(
    process.env.GITHUB_OUTPUT,
    Object.entries(result)
      .map(([key, value]) => `${key}=${value}\n`)
      .join(""),
  );
  process.stdout.write(
    `Recovering ${result.tag} from run ${source}, commit ${commit}, artifact ${result.artifact_id}\n`,
  );
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
