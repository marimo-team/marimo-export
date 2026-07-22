import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  vite: {
    ssr: {
      noExternal: ["@marimo-team/marimo-export"],
    },
  },
});
