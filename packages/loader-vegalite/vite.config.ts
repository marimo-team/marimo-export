import { defineConfig } from "vite-plus";

export default defineConfig({
  pack: { dts: true, entry: ["src/index.ts"], platform: "browser" },
  run: {
    tasks: {
      build: {
        command: "vp pack",
        dependsOn: [{ task: "build", from: "dependencies" }],
      },
      typecheck: { command: "tsc -p tsconfig.json --noEmit" },
    },
  },
});
