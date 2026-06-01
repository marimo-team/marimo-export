import assert from "node:assert/strict";
import test from "node:test";
import {
  jsonLoader,
  readExportArchive,
  readExportDirectory,
  readExportManifest,
  readLatestExport,
  type StaticExport,
  type StaticExportArchive,
} from "@marimo-team/export-reader";
import {
  archiveBytes,
  dataFile,
  defaultJsonFormat,
  directoryFiles,
  fetchFixtureFile,
  hostedFiles,
  jsonPayload,
  jsonPayloadHref,
  manifestWith,
  readFixtureFile,
  unreachableFetch,
  validManifest,
} from "./fixtures/export-fixture.js";

const selection = { scenario: "default", value: "value", format: "json" };
const loaders = [jsonLoader<{ ok: boolean }>("json.v1")];

test("readLatestExport rejects index hrefs outside the hosted root", async () => {
  await assert.rejects(
    readLatestExport({
      root: "https://example.test/export/",
      index: "../index.json",
      fetch: unreachableFetch,
    }),
    /Invalid index href/,
  );
});

test("readExportManifest rejects manifest hrefs outside the hosted root", async () => {
  await assert.rejects(
    readExportManifest({
      root: "https://example.test/export/",
      manifest: "https://example.test/other/manifest.json",
      fetch: unreachableFetch,
    }),
    /Invalid bundle href/,
  );
});

test("readExportManifest rejects manifest file hrefs outside the export root", async () => {
  await assert.rejects(
    readExportManifest({
      root: "https://example.test/export/",
      manifest: "bundles/sha256-test/manifest.json",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            dataFile(defaultJsonFormat(manifest)).href = "../secret.json";
          }),
        ),
      ),
    }),
    /Invalid export manifest\.scenarios\[0\]\.values\.value\.json\.data\.files\.data\.href/,
  );
});

test("reader entry points expose one interface through every source adapter", async (t) => {
  const manifest = validManifest();
  const adapters = [
    {
      name: "hosted root",
      open: () =>
        readLatestExport({
          root: "https://example.test/export/",
          fetch: fetchFixtureFile(hostedFiles(manifest)),
          loaders,
        }),
    },
    {
      name: "local directory",
      open: () =>
        readExportDirectory({
          root: "export",
          readFile: readFixtureFile(directoryFiles(manifest)),
          url: (href) => `/export/${href}`,
          loaders,
        }),
    },
    {
      name: "archive",
      open: () => readExportArchive({ bytes: archiveBytes(manifest), loaders }),
    },
  ] satisfies Array<{
    name: string;
    open: () => Promise<StaticExport | StaticExportArchive>;
  }>;

  for (const adapter of adapters) {
    await t.test(adapter.name, async () => {
      await assertStaticExport(await adapter.open());
    });
  }
});

test("archive-backed exports revoke object URLs on dispose", async () => {
  const exp = await readExportArchive({ bytes: archiveBytes(validManifest()) });
  const originalRevoke = URL.revokeObjectURL;
  const revoked: string[] = [];
  URL.revokeObjectURL = (url) => {
    revoked.push(url);
    originalRevoke.call(URL, url);
  };

  try {
    const url = exp.get(selection).url();
    exp.dispose();
    assert.deepEqual(revoked, [url]);
  } finally {
    URL.revokeObjectURL = originalRevoke;
  }
});

test("format reads reject bytes that do not match the manifest digest", async () => {
  const manifest = manifestWith((next) => {
    dataFile(defaultJsonFormat(next)).sha256 = "0".repeat(64);
  });
  const exp = await readExportDirectory({
    root: "export",
    readFile: readFixtureFile(directoryFiles(manifest)),
  });

  await assert.rejects(exp.get(selection).bytes(), /SHA-256/);
});

async function assertStaticExport(exp: StaticExport | StaticExportArchive): Promise<void> {
  assert.deepEqual(exp.scenarios(), ["default"]);
  assert.deepEqual(exp.values(), ["value"]);
  assert.deepEqual(exp.formats("value"), ["json"]);

  const scenario = exp.scenario("default");
  assert.equal(scenario.id, "default");
  assert.deepEqual(
    exp.scenarioRecords().map((record) => record.id),
    ["default"],
  );

  const handle = exp.get(selection);
  assert.deepEqual(handle.selection, selection);
  assert.equal(handle.record.format_id, "json.v1");
  assert.deepEqual(scenario.get("value", "json").selection, selection);
  const url = handle.url();
  assert.notEqual(url, "");
  if ("dispose" in exp) {
    assert.equal(url.startsWith("blob:"), true);
  } else {
    assert.equal(url.endsWith(jsonPayloadHref), true);
  }
  assert.equal(await handle.text(), jsonPayload);
  assert.deepEqual(await handle.json(), { ok: true });
  assert.deepEqual(await handle.load(), { ok: true });
}
