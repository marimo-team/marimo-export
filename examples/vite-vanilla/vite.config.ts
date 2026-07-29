import { resolve } from "node:path";

import { defineConfig } from "vite-plus";

export default defineConfig({
  build: {
    target: "es2022",
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
