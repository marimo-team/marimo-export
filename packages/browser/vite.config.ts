import { defineConfig } from "vite-plus";

export default defineConfig({
  fmt: {
    printWidth: 100,
  },
  pack: {
    entry: [
      "src/index.ts",
      "src/loader/anywidget.ts",
      "src/loader/arrow.ts",
      "src/loader/numpy.ts",
      "src/loader/parquet.ts",
      "src/loader/vegalite.ts",
    ],
    dts: true,
    fixedExtension: true,
    format: ["esm"],
    platform: "browser",
    target: "es2022",
  },
});
