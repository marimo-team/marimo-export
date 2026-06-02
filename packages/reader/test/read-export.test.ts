import assert from "node:assert/strict";
import test from "node:test";
import {
  jsonLoader,
  readExport,
  type Export,
  type ExportArchive,
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
  rootIndexFor,
  validManifest,
} from "./fixtures/export-fixture.js";

const selection = { scenario: "default", value: "value", format: "json" };
const json = jsonLoader<{ ok: boolean }>("json.v1");

test("readExport rejects latest manifest hrefs outside the hosted root", async () => {
  const index = rootIndexFor(validManifest());
  assert.ok(index.latest);
  index.latest.manifest_href = "../secret.json";

  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile({
        "https://example.test/export/index.json": index,
      }),
    }),
    /Invalid export root index\.latest\.manifest_href/,
  );
});

test("readExport rejects manifest file hrefs outside the export root", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
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

test("readExport opens the latest hosted, directory, and archive bundle by default", async (t) => {
  const oldManifest = manifestWith((manifest) => {
    manifest.id = "sha256-old";
    manifest.sha256 = "old";
  });
  const latestManifest = manifestWith((manifest) => {
    manifest.id = "sha256-latest";
    manifest.sha256 = "latest";
  });
  const rootIndex = rootIndexFor(latestManifest, [oldManifest, latestManifest]);

  const adapters = [
    {
      name: "hosted root",
      open: () =>
        readExport({
          root: "https://example.test/export/",
          fetch: fetchFixtureFile({
            "https://example.test/export/index.json": rootIndex,
            [`https://example.test/export/bundles/${oldManifest.id}/manifest.json`]: oldManifest,
            [`https://example.test/export/bundles/${latestManifest.id}/manifest.json`]:
              latestManifest,
            [`https://example.test/export/${jsonPayloadHref}`]: jsonPayload,
          }),
        }),
    },
    {
      name: "local directory",
      open: () =>
        readExport({
          root: "export",
          readFile: readFixtureFile({
            "export/index.json": rootIndex,
            [`export/bundles/${oldManifest.id}/manifest.json`]: oldManifest,
            [`export/bundles/${latestManifest.id}/manifest.json`]: latestManifest,
            [`export/${jsonPayloadHref}`]: jsonPayload,
          }),
          url: (href) => `/export/${href}`,
        }),
    },
    {
      name: "archive",
      open: () =>
        readExport({
          bytes: archiveBytes(latestManifest, {
            "index.json": rootIndex,
            [`bundles/${oldManifest.id}/manifest.json`]: oldManifest,
            [`bundles/${latestManifest.id}/manifest.json`]: latestManifest,
            [jsonPayloadHref]: jsonPayload,
          }),
        }),
    },
  ] satisfies Array<{
    name: string;
    open: () => Promise<Export | ExportArchive>;
  }>;

  for (const adapter of adapters) {
    await t.test(adapter.name, async () => {
      await assertExport(await adapter.open(), latestManifest.id);
    });
  }
});

test("archive-backed exports revoke object URLs on dispose", async () => {
  const exp = await readExport({ bytes: archiveBytes(validManifest()) });
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
  const exp = await readExport({
    root: "export",
    readFile: readFixtureFile(directoryFiles(manifest)),
  });

  await assert.rejects(exp.get(selection).bytes(), /SHA-256/);
});

async function assertExport(
  exp: Export | ExportArchive,
  manifestId = "sha256-test",
): Promise<void> {
  assert.equal(exp.id, manifestId);
  assert.equal(exp.notebook.name, "demo.py");
  assert.equal(exp.notebook.sourceSha256, "notebook-sha");
  assert.deepEqual(exp.scenarios(), ["default"]);
  assert.deepEqual(exp.values(), ["value"]);
  assert.deepEqual(exp.formats("value"), ["json"]);

  const scenario = exp.scenario("default");
  assert.equal(scenario.id, "default");
  assert.deepEqual(scenario.state, {});

  const handle = exp.get(selection);
  assert.deepEqual(handle.selection, selection);
  assert.equal(handle.formatId, "json.v1");
  assert.equal(handle.mediaType, "application/json");
  assert.deepEqual(handle.files(), ["data"]);
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
  assert.deepEqual(await handle.load(json), { ok: true });
}
