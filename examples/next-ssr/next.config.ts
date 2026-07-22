import type { NextConfig } from "next";

const config: NextConfig = {
  turbopack: {
    resolveAlias: {
      "@marimo-team/marimo-export": "../../packages/client/dist/index.mjs",
      "@marimo-team/marimo-export/node": "../../packages/client/dist/node.mjs",
      "@marimo-team/marimo-export-anywidget": "../../packages/loader-anywidget/dist/index.js",
    },
  },
};

export default config;
