import { defineConfig } from "vite-plus";

export default defineConfig({
  fmt: {
    printWidth: 100,
  },
  pack: {
    deps: {
      alwaysBundle: ["@marimo-export/internal-loader-anywidget", "@marimo-team/portable-json"],
      dts: {
        alwaysBundle: ["@marimo-export/internal-loader-anywidget", "@marimo-team/portable-json"],
      },
    },
    entry: [
      "src/index.ts",
      "src/prepared/index.ts",
      "src/loader/anywidget.ts",
      "src/loader/arrow.ts",
      "src/loader/html.ts",
      "src/loader/json.ts",
      "src/loader/marimo-cell.ts",
      "src/loader/marimo-output.ts",
      "src/loader/numpy.ts",
      "src/loader/parquet.ts",
      "src/loader/text.ts",
      "src/loader/vegalite.ts",
    ],
    dts: {
      eager: true,
    },
    fixedExtension: true,
    format: ["esm"],
    platform: "browser",
    target: "es2022",
  },
});
