import assert from "node:assert/strict";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createServer } from "vite-plus";

const root = fileURLToPath(new URL("../", import.meta.url));
const browserRoot = new URL("../../../packages/browser/", import.meta.url);
const loaders = ["anywidget", "parquet", "vegalite"];

export const verifyDevelopmentTransform = async () => {
  const server = await createServer({
    root,
    logLevel: "silent",
    // Programmatic transforms do not serve optimizer output, so skip background discovery.
    optimizeDeps: { noDiscovery: true, include: [] },
  });

  try {
    const importer = fileURLToPath(new URL("../src/main.ts", import.meta.url));
    const browser = await server.pluginContainer.resolveId("@marimo-team/marimo-export", importer);
    const portableJson = await server.pluginContainer.resolveId(
      "@marimo-team/portable-json",
      fileURLToPath(new URL("src/types.ts", browserRoot)),
    );
    assert.match(browser?.id ?? "", /packages\/browser\/src\/index\.ts$/u);
    assert.doesNotMatch(browser?.id ?? "", /packages\/browser\/dist/u);
    assert.match(portableJson?.id ?? "", /packages\/portable-json\/src\/index\.ts$/u);
    assert.doesNotMatch(portableJson?.id ?? "", /packages\/portable-json\/dist/u);
    assert.ok(await server.transformRequest(`/@fs/${browser?.id}`));
    assert.ok(await server.transformRequest(`/@fs/${portableJson?.id}`));

    for (const loader of loaders) {
      const facade = fileURLToPath(new URL(`src/loader/${loader}.ts`, browserRoot));
      // Vite source transforms share dependency-optimizer state in one server.
      // oxlint-disable-next-line no-await-in-loop
      const transformed = await server.transformRequest(`/@fs/${facade}`);
      assert.ok(transformed, `${loader} facade did not transform`);
    }
  } finally {
    await server.close();
  }
};

if (process.argv[1] !== undefined && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await verifyDevelopmentTransform();
  await new Promise((resolveOutput) => {
    process.stdout.write("Development source transform passed.\n", resolveOutput);
  });
  process.exit(0);
}
