import { build, type BuildOptions } from "esbuild";

const shared = {
  bundle: true,
  format: "esm",
  logLevel: "info",
  sourcemap: true,
  target: "es2022",
} satisfies BuildOptions;

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
  build({
    ...shared,
    entryPoints: ["src/workspace.ts"],
    outfile: "dist/workspace.js",
    platform: "browser",
  }),
]);
