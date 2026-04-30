import { build } from "esbuild";

const shared = {
  bundle: true,
  format: "esm",
  logLevel: "info",
  sourcemap: true,
  target: "es2022",
};

await Promise.all([
  build({
    ...shared,
    entryPoints: ["src/index.ts"],
    outfile: "dist/index.js",
    platform: "node",
  }),
  build({
    ...shared,
    entryPoints: ["src/browser.ts"],
    outfile: "dist/browser.js",
    platform: "browser",
  }),
]);
