import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
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
