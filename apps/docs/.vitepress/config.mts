import { createRequire } from "node:module";

import { defineConfig } from "vitepress";

const require = createRequire(import.meta.url);
const repository = "https://github.com/marimo-team/marimo-export";
const basePath = process.env.BASE_PATH?.replace(/\/$/, "");
const base = basePath ? `${basePath}/` : "/";

export default defineConfig({
  base,
  cleanUrls: true,
  description: "Capture live marimo notebook results as verified static publications.",
  head: [
    ["link", { rel: "icon", href: `${base}favicon.svg`, type: "image/svg+xml" }],
    ["meta", { name: "theme-color", content: "#3451b2" }],
    ["meta", { property: "og:title", content: "marimo-export" }],
    [
      "meta",
      {
        property: "og:description",
        content: "Capture live marimo notebook results as verified static publications.",
      },
    ],
  ],
  lastUpdated: true,
  srcDir: "../../docs",
  srcExclude: ["README.md"],
  vite: {
    resolve: {
      // Markdown lives outside apps/docs, so Vite resolves its injected Vue
      // imports from the workspace root unless they are anchored here.
      alias: [
        { find: /^vue$/, replacement: require.resolve("vue") },
        {
          find: /^vue\/server-renderer$/,
          replacement: require.resolve("vue/server-renderer"),
        },
      ],
    },
  },
  themeConfig: {
    editLink: {
      pattern: `${repository}/edit/main/docs/:path`,
      text: "Edit this page on GitHub",
    },
    nav: [
      { text: "Guide", link: "/getting-started" },
      { text: "Specification", link: "/export-specification" },
      { text: "Read", link: "/read-publications" },
      { text: "CLI", link: "/cli" },
    ],
    outline: { level: [2, 3], label: "On this page" },
    search: { provider: "local" },
    sidebar: [
      {
        text: "Capture",
        items: [
          { text: "Overview", link: "/" },
          { text: "Getting started", link: "/getting-started" },
          { text: "Export specifications", link: "/export-specification" },
          { text: "Live capture", link: "/live-capture" },
        ],
      },
      {
        text: "Consume",
        items: [
          { text: "Read publications", link: "/read-publications" },
          { text: "AnyWidget", link: "/anywidget" },
          { text: "CLI", link: "/cli" },
          { text: "Trust and integrity", link: "/trust" },
        ],
      },
    ],
    socialLinks: [{ icon: "github", link: repository }],
  },
  title: "marimo-export",
});
