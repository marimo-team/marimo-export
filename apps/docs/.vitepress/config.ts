import { fileURLToPath } from "node:url";
import {
  defineConfig,
  type DefaultTheme,
  type HeadConfig,
  type Plugin,
  type UserConfig,
} from "vitepress";
import llmstxt from "vitepress-plugin-llms";

import { documentationSidebar, llmsSidebar, topNavigation } from "../navigation.ts";

const repository = "https://github.com/marimo-team/marimo-export";
const siteUrl = new URL("https://marimo-team.github.io/marimo-export/");
const description =
  "Select the states and outputs to publish from a marimo notebook. marimo-export writes a portable, verified notebook export that browser applications and agents read without a Python runtime or a copy of the notebook source.";
const socialTitle = "Prepare notebook results. Share them anywhere.";
const socialImageAlt =
  "marimo-export mark pointing from prepared notebook states toward a portable export.";
const socialImageUrl = new URL("brand/marimo-export-og.png", siteUrl).href;
const baseName = process.env.BASE_PATH?.trim().replace(/^\/+|\/+$/g, "");
const basePath = baseName ? `/${baseName}` : "";
const publicDir = fileURLToPath(new URL("../public", import.meta.url));
const publicPath = (path: string): string => `${basePath}${path}`;
const llmsDomain = basePath ? siteUrl.origin : siteUrl.href.replace(/\/$/, "");
const themeNavigation: DefaultTheme.NavItem[] = topNavigation;
const themeSidebar: DefaultTheme.Sidebar = documentationSidebar;
const llmsNavigation: DefaultTheme.Sidebar = llmsSidebar;
const canonicalUrl = (page: string): string => {
  const route = page
    .replace(/^\/+/, "")
    .replace(/(^|\/)index\.md$/, "$1")
    .replace(/\.md$/, "");
  return new URL(route, siteUrl).href;
};

const vitePlugins = <Value>(value: Value): [Plugin, Plugin] => {
  // SAFETY: vitepress-plugin-llms returns two Vite plugins whose standard hooks
  // are loaded by the pinned VitePress release during every documentation build.
  return value as [Plugin, Plugin];
};

const llmsPlugins = vitePlugins(
  llmstxt({
    domain: llmsDomain,
    excludeIndexPage: false,
    sidebar: llmsNavigation,
  }),
);
const viteConfig: UserConfig["vite"] = {
  plugins: llmsPlugins,
  publicDir,
  server: {
    host: "127.0.0.1",
    port: Number(process.env.PORT ?? 54173),
    strictPort: true,
  },
};

export default defineConfig({
  base: basePath ? `${basePath}/` : "/",
  cleanUrls: true,
  description,
  head: [
    [
      "link",
      {
        href: publicPath("/brand/marimo-export-favicon-light.svg"),
        media: "(prefers-color-scheme: light)",
        rel: "icon",
        type: "image/svg+xml",
      },
    ],
    [
      "link",
      {
        href: publicPath("/brand/marimo-export-favicon-dark.svg"),
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
  ],
  lang: "en-US",
  lastUpdated: true,
  sitemap: { hostname: siteUrl.href },
  srcDir: "../../docs",
  title: "marimo-export",
  transformHead({ description: pageDescription, page, title }): HeadConfig[] {
    const canonical = canonicalUrl(page);
    const summary = pageDescription || description;
    const pageTitle = title || socialTitle;
    return [
      ["link", { href: canonical, rel: "canonical" }],
      ["meta", { property: "og:type", content: "website" }],
      ["meta", { property: "og:site_name", content: "marimo-export" }],
      ["meta", { property: "og:locale", content: "en_US" }],
      ["meta", { property: "og:title", content: pageTitle }],
      ["meta", { property: "og:description", content: summary }],
      ["meta", { property: "og:url", content: canonical }],
      ["meta", { property: "og:image", content: socialImageUrl }],
      ["meta", { property: "og:image:secure_url", content: socialImageUrl }],
      ["meta", { property: "og:image:type", content: "image/png" }],
      ["meta", { property: "og:image:width", content: "2400" }],
      ["meta", { property: "og:image:height", content: "1260" }],
      ["meta", { property: "og:image:alt", content: socialImageAlt }],
      ["meta", { name: "twitter:card", content: "summary_large_image" }],
      ["meta", { name: "twitter:title", content: pageTitle }],
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
    nav: themeNavigation,
    outline: [2, 3],
    search: { provider: "local" },
    sidebar: themeSidebar,
    siteTitle: false,
    socialLinks: [{ icon: "github", link: repository }],
  },
  vite: viteConfig,
});
