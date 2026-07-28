import { defineConfig } from "vite-plus";

export default defineConfig({
  fmt: {
    printWidth: 100,
  },
  pack: {
    entry: ["src/index.ts"],
    dts: true,
    fixedExtension: true,
    format: ["esm"],
    platform: "browser",
    target: "es2022",
  },
});
