import { resolve } from "node:path";

import { defineConfig } from "vite-plus";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        arrow: resolve(import.meta.dirname, "arrow-only.html"),
        index: resolve(import.meta.dirname, "index.html"),
      },
    },
  },
});
