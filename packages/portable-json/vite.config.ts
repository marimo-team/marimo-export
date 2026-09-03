import { defineConfig } from "vite-plus";

export default defineConfig({
  pack: {
    dts: true,
    entry: ["src/index.ts", "src/zod.ts"],
    fixedExtension: true,
    format: ["esm"],
    platform: "browser",
    target: "es2022",
  },
});
