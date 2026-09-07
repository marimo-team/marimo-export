import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { expect, it } from "vite-plus/test";

import {
  finalizeRecoveryReceipt,
  validateMainCI,
  validateRecovery,
  verifyRecoveryFiles,
} from "../../../scripts/recover-release.mjs";

const commit = "a".repeat(40);

it.each(["failure", "cancelled", null])(
  "rejects unsuccessful latest main CI even when an older run passed: %s",
  (conclusion) => {
    const original = {
      event: "push",
      head_branch: "main",
      head_sha: commit,
      path: ".github/workflows/ci.yml",
      repository: { full_name: "marimo-team/marimo-export" },
      head_repository: { full_name: "marimo-team/marimo-export" },
      created_at: "2026-09-07T10:00:00Z",
      run_attempt: 1,
      status: "completed",
      conclusion: "success",
    };
    expect(() => validateMainCI("marimo-team/marimo-export", commit, [original])).not.toThrow();
    expect(() =>
      validateMainCI("marimo-team/marimo-export", commit, [
        original,
        {
          ...original,
          run_attempt: 2,
          conclusion,
          status: conclusion ? "completed" : "in_progress",
        },
      ]),
    ).toThrow(/Latest main CI must pass/);
  },
);

it("requires CI evidence for the exact main commit and repository", () => {
  expect(() => validateMainCI("marimo-team/marimo-export", commit, [])).toThrow(
    /Latest main CI must pass/,
  );
  expect(() =>
    validateMainCI("marimo-team/marimo-export", commit, [
      {
        event: "push",
        head_branch: "main",
        head_sha: "b".repeat(40),
        path: ".github/workflows/ci.yml",
        repository: { full_name: "marimo-team/marimo-export" },
        head_repository: { full_name: "other/export" },
        created_at: "2026-09-07T10:00:00Z",
        run_attempt: 1,
        status: "completed",
        conclusion: "success",
      },
    ]),
  ).toThrow(/Latest main CI must pass/);
});
const evidence = () => ({
  version: "0.0.3",
  runId: "71",
  repository: "marimo-team/marimo-export",
  commit,
  run: {
    id: 71,
    repository: { full_name: "marimo-team/marimo-export" },
    head_repository: { full_name: "marimo-team/marimo-export" },
    path: ".github/workflows/publish.yml",
    event: "push",
    head_sha: commit,
    head_branch: "v0.0.3",
    status: "completed",
    run_attempt: 1,
  },
  jobs: [
    { name: "Build and verify", conclusion: "success", run_attempt: 1 },
    { name: "Attest build provenance", conclusion: "success", run_attempt: 1 },
  ],
  artifacts: [
    {
      id: 93,
      name: "release-artifacts",
      expired: false,
      size_in_bytes: 1024,
      workflow_run: { id: 71, head_sha: commit },
    },
  ],
});

it("recovers the original tagged and attested release artifact", () => {
  expect(validateRecovery(evidence())).toEqual({
    schema: 1,
    version: "0.0.3",
    tag: "v0.0.3",
    commit,
    source_run: 71,
    source_attempt: 1,
    artifact_id: 93,
  });
});

it.each([
  { head_sha: "b".repeat(40) },
  { event: "workflow_dispatch" },
  { path: ".github/workflows/ci.yml" },
  { head_branch: "v0.0.4" },
  { repository: { full_name: "other/export" } },
  { head_repository: { full_name: "other/export" } },
])("rejects source runs outside the tagged publication identity: %j", (change) => {
  const input = evidence();
  Object.assign(input.run, change);
  expect(() => validateRecovery(input)).toThrow(/Source run/);
});

it("requires original build and attestation success", () => {
  const input = evidence();
  input.jobs[1].conclusion = "failure";
  expect(() => validateRecovery(input)).toThrow(/Attest build provenance/);
});

it("rejects a failed latest build even when an earlier attempt passed", () => {
  const input = evidence();
  input.jobs.push({ name: "Build and verify", conclusion: "failure", run_attempt: 2 });
  expect(() => validateRecovery(input)).toThrow(/Build and verify/);
});

it("rejects expired and differently owned artifact payloads", () => {
  const input = evidence();
  input.artifacts[0].expired = true;
  expect(() => validateRecovery(input)).toThrow(/retained/);
  input.artifacts[0].expired = false;
  input.artifacts[0].workflow_run.head_sha = "b".repeat(40);
  expect(() => validateRecovery(input)).toThrow(/different source/);
});

it("verifies the exact original archives against their basename checksum manifest", async () => {
  const root = await mkdtemp(join(tmpdir(), "release-recovery-"));
  try {
    await mkdir(join(root, "npm"));
    await mkdir(join(root, "python"));
    const paths = [
      "npm/marimo-team-marimo-export-0.0.3.tgz",
      "python/marimo_export-0.0.3-py3-none-any.whl",
      "python/marimo_export-0.0.3.tar.gz",
    ];
    await Promise.all(paths.map((path) => writeFile(join(root, path), `archive:${path}`)));
    const checksums = await Promise.all(
      paths.map(
        async (path) =>
          `${createHash("sha256")
            .update(await readFile(join(root, path)))
            .digest("hex")}  ${basename(path)}`,
      ),
    );
    await writeFile(join(root, "SHA256SUMS"), `${checksums.join("\n")}\n`);
    await expect(verifyRecoveryFiles(root, "0.0.3")).resolves.toBeUndefined();
    await writeFile(join(root, paths[0]), "different npm bytes");
    await expect(verifyRecoveryFiles(root, "0.0.3")).rejects.toThrow(/checksum mismatch/);
    await writeFile(join(root, "python/extra.whl"), "unexpected archive");
    await expect(verifyRecoveryFiles(root, "0.0.3")).rejects.toThrow(/exact npm, wheel/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

it("binds the recovery receipt to the verified checksum and original signing attempt", async () => {
  const root = await mkdtemp(join(tmpdir(), "release-receipt-"));
  try {
    const dist = join(root, "dist");
    await mkdir(join(dist, "npm"), { recursive: true });
    await mkdir(join(dist, "python"));
    const paths = [
      "npm/marimo-team-marimo-export-0.0.3.tgz",
      "python/marimo_export-0.0.3-py3-none-any.whl",
      "python/marimo_export-0.0.3.tar.gz",
    ];
    const digest = createHash("sha256").update("original bytes").digest("hex");
    await Promise.all(paths.map((path) => writeFile(join(dist, path), "original bytes")));
    await writeFile(
      join(dist, "SHA256SUMS"),
      paths.map((path) => `${digest}  ${basename(path)}\n`).join(""),
    );
    const checksumDigest = createHash("sha256")
      .update(await readFile(join(dist, "SHA256SUMS")))
      .digest("hex");
    const receiptPath = join(root, "release-recovery.json");
    const attestationPath = join(root, "verified.json");
    const receipt = {
      ...validateRecovery(evidence()),
      repository: "marimo-team/marimo-export",
      source_attempt: 2,
    };
    await writeFile(receiptPath, JSON.stringify(receipt));
    const verificationResult = {
      signature: {
        certificate: {
          runInvocationURI:
            "https://github.com/marimo-team/marimo-export/actions/runs/71/attempts/1",
        },
      },
      statement: { subject: [{ digest: { sha256: checksumDigest } }] },
    };
    await writeFile(attestationPath, JSON.stringify([{ verificationResult }]));
    await finalizeRecoveryReceipt(dist, "0.0.3", receiptPath, attestationPath);
    expect(JSON.parse(await readFile(receiptPath, "utf8"))).toMatchObject({
      source_run: 71,
      source_attempt: 2,
      attestation_attempt: 1,
      checksums_sha256: checksumDigest,
    });
    verificationResult.signature.certificate.runInvocationURI =
      "https://github.com/marimo-team/marimo-export/actions/runs/72/attempts/1";
    await writeFile(attestationPath, JSON.stringify([{ verificationResult }]));
    await expect(
      finalizeRecoveryReceipt(dist, "0.0.3", receiptPath, attestationPath),
    ).rejects.toThrow(/original source run/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
