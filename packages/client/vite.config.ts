import { defineConfig } from "vite-plus";

export default defineConfig({
  fmt: {
    printWidth: 100,
  },
  pack: {
    entry: ["src/index.ts", "src/remote.ts", "src/node.ts", "src/cli.ts"],
    deps: {
      neverBundle: [/^node:/],
      onlyBundle: ["yaml"],
    },
    dts: true,
    fixedExtension: true,
    format: ["esm"],
    platform: "neutral",
    target: "es2022",
  },
});
