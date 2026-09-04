import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createServer } from "vite-plus";

const root = fileURLToPath(new URL("../", import.meta.url));
const browserRoot = new URL("../../../packages/browser/", import.meta.url);

export const verifyDevelopmentTransform = async () => {
  const source = await readFile(new URL("../src/main.ts", import.meta.url), "utf8");
  assert.match(source, /openExport\("\.\/export\/"\)/u);
  assert.doesNotMatch(source, /URLSearchParams/u);

  const server = await createServer({
    root,
    logLevel: "silent",
    optimizeDeps: { noDiscovery: true, include: [] },
  });

  try {
    const importer = fileURLToPath(new URL("../src/main.ts", import.meta.url));
    const browser = await server.pluginContainer.resolveId("@marimo-team/marimo-export", importer);
    assert.match(browser?.id ?? "", /packages\/browser\/src\/index\.ts$/u);
    assert.doesNotMatch(browser?.id ?? "", /packages\/browser\/dist/u);
    assert.ok(await server.transformRequest(`/@fs/${browser?.id}`));

    for (const loader of ["json", "marimo-output"]) {
      const facade = fileURLToPath(new URL(`src/loader/${loader}.ts`, browserRoot));
      // Vite source transforms share dependency-optimizer state in one server.
      // oxlint-disable-next-line no-await-in-loop
      assert.ok(await server.transformRequest(`/@fs/${facade}`));
    }
  } finally {
    await server.close();
  }
};

if (process.argv[1] !== undefined && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await verifyDevelopmentTransform();
  await new Promise((resolveOutput) => {
    process.stdout.write("Quickstart source transform passed.\n", resolveOutput);
  });
  process.exit(0);
}
