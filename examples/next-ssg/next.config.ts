import type { NextConfig } from "next";

const packageEntry = (packageName: string) => `../../packages/${packageName}/dist/index.js`;

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  turbopack: {
    resolveAlias: {
      "@marimo-team/export-client": packageEntry("client"),
      "@marimo-team/export-loader-anywidget": packageEntry("loader-anywidget"),
      "@marimo-team/export-loader-arrow": packageEntry("loader-arrow"),
      "@marimo-team/export-loader-vegalite": packageEntry("loader-vegalite"),
      "@marimo-team/export-reader": packageEntry("reader"),
    },
  },
  transpilePackages: [
    "@marimo-team/export-client",
    "@marimo-team/export-loader-anywidget",
    "@marimo-team/export-loader-arrow",
    "@marimo-team/export-loader-vegalite",
    "@marimo-team/export-reader",
    "@marimo-team/marimo-api",
  ],
};

export default nextConfig;
