import { defineConfig } from "vitepress";

const repository = "https://github.com/marimo-team/marimo-export";
const basePath = process.env.BASE_PATH?.replace(/\/$/, "");
const base = basePath ? `${basePath}/` : "/";

export default defineConfig({
  base,
  cleanUrls: true,
  description:
    "Run saved marimo notebooks across finite scenarios and consume verified projections anywhere.",
  head: [
    ["link", { rel: "icon", href: `${base}favicon.svg`, type: "image/svg+xml" }],
    ["meta", { name: "theme-color", content: "#3451b2" }],
    ["meta", { property: "og:title", content: "marimo-export" }],
    [
      "meta",
      {
        property: "og:description",
        content:
          "Run saved marimo notebooks across finite scenarios and consume verified projections anywhere.",
      },
    ],
  ],
  lastUpdated: true,
  srcDir: "../../docs",
  srcExclude: ["README.md"],
  themeConfig: {
    editLink: {
      pattern: `${repository}/edit/main/docs/:path`,
      text: "Edit this page on GitHub",
    },
    nav: [
      { text: "Guide", link: "/getting-started" },
      { text: "Read exports", link: "/read-exports" },
      { text: "CLI", link: "/cli" },
    ],
    outline: { level: [2, 3], label: "On this page" },
    search: { provider: "local" },
    sidebar: [
      {
        text: "Guide",
        items: [
          { text: "Overview", link: "/" },
          { text: "Getting started", link: "/getting-started" },
          { text: "Export plans", link: "/export-plans" },
          { text: "Remote execution", link: "/remote-execution" },
        ],
      },
      {
        text: "Consume",
        items: [
          { text: "Read exports", link: "/read-exports" },
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
