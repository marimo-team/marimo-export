import { defineConfig } from "vite";

export default defineConfig({
  build: {
    chunkSizeWarningLimit: 900,
  },
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      "@marimo-team/export-reader": new URL("../../packages/reader/src/index", import.meta.url)
        .pathname,
      "@marimo-team/export-loader-anywidget": new URL(
        "../../packages/loader-anywidget/src/index",
        import.meta.url,
      ).pathname,
      "@marimo-team/export-loader-arrow": new URL(
        "../../packages/loader-arrow/src/index",
        import.meta.url,
      ).pathname,
      "@marimo-team/export-loader-parquet": new URL(
        "../../packages/loader-parquet/src/index",
        import.meta.url,
      ).pathname,
      "@marimo-team/export-loader-vegalite": new URL(
        "../../packages/loader-vegalite/src/index",
        import.meta.url,
      ).pathname,
      "#anywidget": new URL("../../packages/loader-anywidget/src", import.meta.url).pathname,
    },
  },
});
