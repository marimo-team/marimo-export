import { defineConfig } from "astro/config";
import { fileURLToPath } from "node:url";

const appSrc = fileURLToPath(new URL("./src", import.meta.url));
const exportClientSrc = fileURLToPath(new URL("../../packages/client/src/index", import.meta.url));

export default defineConfig({
  vite: {
    resolve: {
      alias: {
        "@": appSrc,
        "@marimo-team/export-client": exportClientSrc,
      },
    },
  },
});
