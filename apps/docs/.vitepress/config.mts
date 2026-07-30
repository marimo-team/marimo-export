import { createRequire } from "node:module";

import { defineConfig } from "vitepress";

const require = createRequire(import.meta.url);
const repository = "https://github.com/marimo-team/marimo-export";
const basePath = process.env.BASE_PATH?.replace(/\/$/, "");
const base = basePath ? `${basePath}/` : "/";

export default defineConfig({
  base,
  cleanUrls: true,
  description: "Precompute marimo notebook states for interactive apps served as static files.",
  head: [
    ["meta", { name: "theme-color", content: "#3451b2" }],
    ["meta", { property: "og:title", content: "marimo-export" }],
    [
      "meta",
      {
        property: "og:description",
        content: "Precompute marimo notebook states for interactive apps served as static files.",
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
      { text: "Try it", link: "/getting-started" },
      { text: "Choose states", link: "/export-spec" },
      { text: "Build or capture", link: "/cli" },
      { text: "Browser API", link: "/browser-api" },
    ],
    outline: { level: [2, 3], label: "On this page" },
    search: { provider: "local" },
    sidebar: [
      {
        text: "Start",
        items: [
          { text: "Overview", link: "/" },
          { text: "Run the market dashboard", link: "/getting-started" },
        ],
      },
      {
        text: "Prepare notebook results",
        items: [
          { text: "Choose states and results", link: "/export-spec" },
          { text: "Build or capture", link: "/cli" },
          { text: "Python API", link: "/python-api" },
        ],
      },
      {
        text: "Build the web app",
        items: [
          { text: "Browser API", link: "/browser-api" },
          { text: "Output formats", link: "/representations" },
          { text: "Deploy safely", link: "/trust" },
        ],
      },
    ],
    socialLinks: [{ icon: "github", link: repository }],
  },
  title: "marimo-export",
});
