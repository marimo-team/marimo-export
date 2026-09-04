import { resolve } from "node:path";

import { defineConfig } from "vite-plus";

const documentationPublicDir = process.env.MARIMO_EXPORT_EXAMPLE_PUBLIC_DIR;
const documentationOutDir = process.env.MARIMO_EXPORT_EXAMPLE_OUT_DIR;

if ((documentationPublicDir === undefined) !== (documentationOutDir === undefined)) {
  throw new Error(
    "MARIMO_EXPORT_EXAMPLE_PUBLIC_DIR and MARIMO_EXPORT_EXAMPLE_OUT_DIR must be set together.",
  );
}

export default defineConfig({
  base: documentationOutDir === undefined ? "/" : "./",
  build: {
    emptyOutDir: true,
    outDir: documentationOutDir ?? "dist",
    target: "es2022",
  },
  publicDir: documentationPublicDir ?? "public",
  optimizeDeps: {
    exclude: ["@marimo-export/internal-loader-anywidget"],
  },
  resolve: {
    // Mirror the root TypeScript path during Vite's source-workspace transform.
    alias: [
      {
        find: /^#loaders\/(.+)$/,
        replacement: resolve(import.meta.dirname, "../../packages/loader-$1/src/index.ts"),
      },
    ],
  },
});
