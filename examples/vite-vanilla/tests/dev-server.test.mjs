import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite-plus";

const root = fileURLToPath(new URL("../", import.meta.url));
const browserRoot = new URL("../../../packages/browser/", import.meta.url);
const loaders = ["anywidget", "parquet", "vegalite"];

await test("development server resolves the dashboard loader facades", async () => {
  const server = await createServer({
    root,
    logLevel: "silent",
  });

  try {
    await Promise.all(
      loaders.map(async (loader) => {
        const facade = fileURLToPath(new URL(`src/loader/${loader}.ts`, browserRoot));
        const transformed = await server.transformRequest(`/@fs/${facade}`);
        assert.ok(transformed, `${loader} facade did not transform`);
      }),
    );
  } finally {
    await server.close();
  }
});
