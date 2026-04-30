import { defineConfig } from "astro/config";
import { fileURLToPath } from "node:url";

const appSrc = fileURLToPath(new URL("./src", import.meta.url));

export default defineConfig({
  vite: {
    resolve: {
      alias: {
        "@": appSrc,
      },
    },
  },
});
