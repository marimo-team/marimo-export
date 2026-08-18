import { fileURLToPath } from "node:url";
import { defineConfig, type HeadConfig, type Plugin, type UserConfig } from "vitepress";
import llmstxt from "vitepress-plugin-llms";

const repository = "https://github.com/marimo-team/marimo-export";
const siteUrl = new URL("https://marimo-team.github.io/marimo-export/");
const description =
  "Precompute selected marimo notebook results as one verified export for applications, agents, Python automation, and custom clients.";
const socialTitle = "Precompute notebook results. Use them anywhere.";
const socialImageAlt = `marimo-export: ${socialTitle}`;
const socialImageUrl = new URL("brand/marimo-export-og.png", siteUrl).href;
const baseName = process.env.BASE_PATH?.trim().replace(/^\/+|\/+$/g, "");
const basePath = baseName ? `/${baseName}` : "";
const publicDir = fileURLToPath(new URL("../public", import.meta.url));
const publicPath = (path: string): string => `${basePath}${path}`;
const llmsDomain = basePath ? siteUrl.origin : siteUrl.href.replace(/\/$/, "");
const canonicalUrl = (page: string): string => {
  const route = page
    .replace(/^\/+/, "")
    .replace(/(^|\/)index\.md$/, "$1")
    .replace(/\.md$/, "");
  return new URL(route, siteUrl).href;
};
const guideItems = [
  { text: "Guide overview", link: "/guide/" },
  { text: "Run the market dashboard", link: "/guide/getting-started" },
  { text: "Choose states and results", link: "/guide/choose-states" },
  { text: "Build or capture", link: "/guide/build-and-capture" },
  { text: "Consume an export", link: "/guide/consume-an-export" },
  { text: "Use with agents", link: "/guide/agents-and-automation" },
  { text: "Build a browser application", link: "/guide/browser-applications" },
  { text: "Deploy an export", link: "/guide/deploy" },
];
const referenceItems = [
  { text: "Reference overview", link: "/reference/" },
  { text: "ExportSpec", link: "/reference/export-spec" },
  { text: "Export format", link: "/reference/export-format" },
  { text: "CLI", link: "/reference/cli" },
  { text: "Python API", link: "/reference/python-api" },
  { text: "Browser API", link: "/reference/browser-api" },
  { text: "Output representations", link: "/reference/representations" },
];
const introductionItems = [
  { text: "marimo-export", link: "/" },
  { text: "How notebook exports work", link: "/overview" },
];
// SAFETY: vitepress-plugin-llms returns two Vite plugins whose standard hooks
// are loaded by the pinned VitePress release during every documentation build.
const llmsPlugins = llmstxt({
  domain: llmsDomain,
  excludeIndexPage: false,
  sidebar: [
    { text: "Introduction", items: introductionItems },
    { text: "Guide", items: guideItems },
    { text: "Reference", items: referenceItems },
  ],
}) as unknown as [Plugin, Plugin];
const viteConfig: UserConfig["vite"] = { plugins: llmsPlugins, publicDir };

export default defineConfig({
  base: basePath ? `${basePath}/` : "/",
  cleanUrls: true,
  description,
  head: [
    [
      "link",
      {
        href: publicPath("/brand/marimo-export-mark-light.svg"),
        media: "(prefers-color-scheme: light)",
        rel: "icon",
        type: "image/svg+xml",
      },
    ],
    [
      "link",
      {
        href: publicPath("/brand/marimo-export-mark-dark.svg"),
        media: "(prefers-color-scheme: dark)",
        rel: "icon",
        type: "image/svg+xml",
      },
    ],
    [
      "link",
      {
        href: publicPath("/brand/marimo-export-mark-dark.png"),
        rel: "apple-touch-icon",
      },
    ],
    [
      "meta",
      {
        content: "#ffffff",
        media: "(prefers-color-scheme: light)",
        name: "theme-color",
      },
    ],
    [
      "meta",
      {
        content: "#1b1b1f",
        media: "(prefers-color-scheme: dark)",
        name: "theme-color",
      },
    ],
  ],
  lang: "en-US",
  lastUpdated: true,
  srcDir: "../../docs",
  title: "marimo-export",
  transformHead({ description: pageDescription, page }): HeadConfig[] {
    const canonical = canonicalUrl(page);
    const summary = pageDescription || description;
    return [
      ["link", { href: canonical, rel: "canonical" }],
      ["meta", { property: "og:type", content: "website" }],
      ["meta", { property: "og:site_name", content: "marimo-export" }],
      ["meta", { property: "og:locale", content: "en_US" }],
      ["meta", { property: "og:title", content: socialTitle }],
      ["meta", { property: "og:description", content: summary }],
      ["meta", { property: "og:url", content: canonical }],
      ["meta", { property: "og:image", content: socialImageUrl }],
      ["meta", { property: "og:image:secure_url", content: socialImageUrl }],
      ["meta", { property: "og:image:type", content: "image/png" }],
      ["meta", { property: "og:image:width", content: "2400" }],
      ["meta", { property: "og:image:height", content: "1260" }],
      ["meta", { property: "og:image:alt", content: socialImageAlt }],
      ["meta", { name: "twitter:card", content: "summary_large_image" }],
      ["meta", { name: "twitter:title", content: socialTitle }],
      ["meta", { name: "twitter:description", content: summary }],
      ["meta", { name: "twitter:image", content: socialImageUrl }],
      ["meta", { name: "twitter:image:alt", content: socialImageAlt }],
    ];
  },
  themeConfig: {
    editLink: {
      pattern: `${repository}/edit/main/docs/:path`,
      text: "Edit this page on GitHub",
    },
    footer: {
      copyright: "Copyright © 2026-Present marimo-export maintainers.",
      message: "Released under the Apache 2.0 License.",
    },
    logo: {
      light: "/brand/marimo-export-lockup-horizontal-light.svg",
      dark: "/brand/marimo-export-lockup-horizontal-dark.svg",
      alt: "marimo-export",
    },
    nav: [
      { text: "How it works", link: "/overview" },
      {
        text: "Guide",
        items: guideItems,
      },
      { text: "Reference", link: "/reference/" },
    ],
    outline: [2, 3],
    search: { provider: "local" },
    sidebar: {
      "/guide/": [
        {
          text: "Guide",
          collapsed: false,
          items: guideItems,
        },
      ],
      "/reference/": [
        {
          text: "Reference",
          collapsed: false,
          items: referenceItems,
        },
      ],
      "/": [
        {
          text: "Introduction",
          collapsed: false,
          items: [
            ...introductionItems,
            { text: "Run the market dashboard", link: "/guide/getting-started" },
          ],
        },
        {
          text: "Guide",
          collapsed: true,
          items: guideItems,
        },
        {
          text: "Reference",
          collapsed: true,
          items: referenceItems,
        },
      ],
    },
    siteTitle: false,
    socialLinks: [{ icon: "github", link: repository }],
  },
  vite: viteConfig,
});
