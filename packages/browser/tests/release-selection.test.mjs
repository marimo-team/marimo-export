import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { expect, it } from "vite-plus/test";

const require = createRequire(import.meta.resolve("vite-plus/package.json"));
const { parse } = require("yaml");
const picomatch = require("picomatch");
const filters = parse(
  await readFile(new URL("../../../.github/filters.yml", import.meta.url), "utf8"),
);
const contracts = [
  "quality",
  "python_contracts",
  "frontend_contracts",
  "distribution",
  "release_contracts",
];
const selected = (path) =>
  contracts.filter((name) => {
    const patterns = filters[name].flat(Infinity);
    return picomatch(
      patterns.filter((pattern) => !pattern.startsWith("!")),
      {
        dot: true,
        ignore: patterns
          .filter((pattern) => pattern.startsWith("!"))
          .map((pattern) => pattern.slice(1)),
      },
    )(path);
  });

it.each([
  ".github/workflows/publish.yml",
  "scripts/recover-release.mjs",
  "scripts/smoke_npm_packages.mjs",
  "scripts/fixtures/npm-consumer/pnpm-workspace.yaml",
])("release delivery changes select executable contracts and package verification: %s", (path) => {
  expect(selected(path)).toEqual([
    "quality",
    "frontend_contracts",
    "distribution",
    "release_contracts",
  ]);
});

it.each([".github/workflows/ci.yml", "uv.lock", "packages/python/src/marimo_export/cli.py"])(
  "Python execution and dependency changes retain Python validation: %s",
  (path) => {
    expect(selected(path)).toContain("python_contracts");
  },
);

it("recovery completion requires every publication and verification stage", async () => {
  const workflow = parse(
    await readFile(new URL("../../../.github/workflows/publish.yml", import.meta.url), "utf8"),
  );
  const command = workflow.jobs.complete.steps[0].run;
  const stages = {
    BUILD: "success",
    ATTEST: "skipped",
    EXPECTED_ATTEST: "skipped",
    PUBLISH_NPM: "success",
    VERIFY_NPM: "success",
    PUBLISH_PYPI: "success",
    VERIFY_PYPI: "success",
    RELEASE_NOTES: "success",
  };
  const execute = (changes) =>
    spawnSync("bash", ["-e", "-o", "pipefail", "-c", command], {
      encoding: "utf8",
      env: { ...process.env, ...stages, ...changes },
    });
  expect(execute({}).status).toBe(0);
  expect(execute({ PUBLISH_PYPI: "skipped" }).status).not.toBe(0);
  expect(execute({ VERIFY_PYPI: "failure" }).status).not.toBe(0);
  expect(execute({ ATTEST: "failure" }).status).not.toBe(0);
});
