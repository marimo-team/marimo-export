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
  "pages",
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
  expect(new Set(selected(path))).toEqual(
    new Set(["quality", "frontend_contracts", "distribution", "release_contracts"]),
  );
});

it.each([".github/workflows/ci.yml", "uv.lock", "packages/python/src/marimo_export/cli.py"])(
  "Python execution and dependency changes retain Python validation: %s",
  (path) => {
    expect(selected(path)).toContain("python_contracts");
  },
);

it.each([
  "examples/quickstart/src/main.ts",
  "examples/quickstart/index.html",
  "examples/quickstart/package.json",
  "examples/quickstart/tests/dev-transform.mjs",
  "examples/quickstart/tsconfig.json",
  "examples/quickstart/vite.config.ts",
])("quickstart frontend changes select execution and documentation builds: %s", (path) => {
  expect(selected(path)).toEqual(expect.arrayContaining(["frontend_contracts", "pages"]));
});

it.each([
  "examples/quickstart/report.py",
  "examples/quickstart/report.export.yaml",
  "pyproject.toml",
])("documentation producer inputs select Python and documentation builds: %s", (path) => {
  expect(selected(path)).toEqual(expect.arrayContaining(["python_contracts", "pages"]));
});

it.each(["anywidget", "arrow", "numpy", "parquet", "vegalite"])(
  "bundled %s loader source selects packed-consumer validation",
  (loader) => {
    expect(selected(`packages/loader-${loader}/src/index.ts`)).toEqual(
      expect.arrayContaining(["frontend_contracts", "distribution"]),
    );
  },
);

it.each(["package.json", "tsconfig.json", "vite.config.ts"])(
  "loader build inputs select packed-consumer validation: %s",
  (input) => {
    expect(selected(`packages/loader-parquet/${input}`)).toEqual(
      expect.arrayContaining(["frontend_contracts", "distribution"]),
    );
  },
);

it("registry verification selects its retry tests", () => {
  expect(selected("scripts/verify-npm.sh")).toEqual(
    expect.arrayContaining(["frontend_contracts", "distribution", "release_contracts"]),
  );
});

it("shared setup and build commands select their consumers", () => {
  expect(selected(".github/actions/setup-js/action.yml")).toEqual(
    expect.arrayContaining(["quality", "frontend_contracts", "distribution", "pages"]),
  );
  expect(selected("Makefile")).toEqual(
    expect.arrayContaining(["python_contracts", "distribution", "pages"]),
  );
});

it("the required gate enforces selected jobs and classified outcomes", async () => {
  const workflow = parse(
    await readFile(new URL("../../../.github/workflows/ci.yml", import.meta.url), "utf8"),
  );
  const command = workflow.jobs.required.steps[0].run;
  const stages = {
    CHANGES: "success",
    QUALITY: "success",
    QUALITY_REQUIRED: "true",
    TEST_PYTHON: "success",
    TEST_PYTHON_REQUIRED: "true",
    TEST_FRONTEND: "success",
    TEST_FRONTEND_REQUIRED: "true",
    PACKAGE: "success",
    PACKAGE_REQUIRED: "true",
    RELEASE_CONTRACTS: "success",
    RELEASE_CONTRACTS_REQUIRED: "true",
  };
  const execute = (changes) =>
    spawnSync("bash", ["-e", "-o", "pipefail", "-c", command], {
      encoding: "utf8",
      env: { ...process.env, ...stages, ...changes },
    });
  expect(execute({}).status).toBe(0);
  for (const stage of ["QUALITY", "TEST_PYTHON", "TEST_FRONTEND", "PACKAGE", "RELEASE_CONTRACTS"]) {
    expect(execute({ [stage]: "skipped" }).status, stage).not.toBe(0);
    expect(execute({ [stage]: "skipped", [`${stage}_REQUIRED`]: "false" }).status, stage).toBe(0);
  }
  for (const outcome of ["failure", "cancelled"]) {
    expect(execute({ PACKAGE: outcome }).status, outcome).not.toBe(0);
  }
  expect(execute({ PACKAGE_REQUIRED: "" }).status).not.toBe(0);
  expect(execute({ CHANGES: "failure" }).status).not.toBe(0);
});

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
