import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { documentationPages } from "../navigation.mjs";

const outputDirectory = fileURLToPath(new URL("../.vitepress/dist/", import.meta.url));
const siteUrl = new URL("https://marimo-team.github.io/marimo-export/");

const htmlForRoute = (route) => {
  if (route === "/") return "index.html";
  if (route.endsWith("/")) return `${route.slice(1)}index.html`;
  return `${route.slice(1)}.html`;
};

const markdownForRoute = (route) => {
  if (route === "/") return "index.md";
  if (route.endsWith("/")) return `${route.slice(1, -1)}.md`;
  return `${route.slice(1)}.md`;
};

const canonicalForRoute = (route) => new URL(route.slice(1), siteUrl).href;
const markdownUrlForRoute = (route) => new URL(markdownForRoute(route), siteUrl).href;

const missingFiles = async (files) => {
  const results = await Promise.all(
    files.map(async (file) => {
      try {
        const details = await stat(join(outputDirectory, file));
        return details.isFile() && details.size > 0 ? undefined : file;
      } catch {
        return file;
      }
    }),
  );
  return results.filter((file) => file !== undefined);
};

const duplicates = (values) => {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return [...counts].filter(([, count]) => count > 1).map(([value]) => value);
};

const compareSets = (expected, actual) => ({
  missing: [...expected].filter((value) => !actual.has(value)),
  unexpected: [...actual].filter((value) => !expected.has(value)),
});

const formatList = (label, values) =>
  values.length === 0 ? [] : [`${label}:`, ...values.map((value) => `  - ${value}`)];

const files = documentationPages.flatMap(({ link }) => [
  htmlForRoute(link),
  markdownForRoute(link),
]);
const requiredFiles = [...files, "llms.txt", "llms-full.txt", "sitemap.xml"];
const missing = await missingFiles(requiredFiles);

const llms = await readFile(join(outputDirectory, "llms.txt"), "utf8");
const llmsFull = await readFile(join(outputDirectory, "llms-full.txt"), "utf8");
const sitemap = await readFile(join(outputDirectory, "sitemap.xml"), "utf8");
const index = await readFile(join(outputDirectory, "index.html"), "utf8");

const llmsLinks = [...llms.matchAll(/\]\((https?:\/\/[^)]+\.md)\)/g)].map((match) => match[1]);
const llmsFullLinks = [
  ...llmsFull.matchAll(/^url:\s+(?:'(https?:\/\/[^']+\.md)'|>-\n\s+(https?:\/\/\S+\.md))$/gm),
].map((match) => match[1] ?? match[2]);
const sitemapLinks = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
const expectedMarkdownLinks = new Set(
  documentationPages.map(({ link }) => markdownUrlForRoute(link)),
);
const expectedCanonicalLinks = new Set(
  documentationPages.map(({ link }) => canonicalForRoute(link)),
);
const llmsDifference = compareSets(expectedMarkdownLinks, new Set(llmsLinks));
const llmsFullDifference = compareSets(expectedMarkdownLinks, new Set(llmsFullLinks));
const sitemapDifference = compareSets(expectedCanonicalLinks, new Set(sitemapLinks));

const baseName = process.env.BASE_PATH?.trim().replace(/^\/+|\/+$/g, "");
const assetPrefix = baseName ? `/${baseName}` : "";
const errors = [
  ...formatList("Missing or empty build artifacts", missing),
  ...formatList("Duplicate llms.txt links", duplicates(llmsLinks)),
  ...formatList("Missing llms.txt links", llmsDifference.missing),
  ...formatList("Unexpected llms.txt links", llmsDifference.unexpected),
  ...formatList("Duplicate llms-full.txt URLs", duplicates(llmsFullLinks)),
  ...formatList("Missing llms-full.txt URLs", llmsFullDifference.missing),
  ...formatList("Unexpected llms-full.txt URLs", llmsFullDifference.unexpected),
  ...formatList("Duplicate sitemap URLs", duplicates(sitemapLinks)),
  ...formatList("Missing sitemap URLs", sitemapDifference.missing),
  ...formatList("Unexpected sitemap URLs", sitemapDifference.unexpected),
  ...(index.includes(`href="${assetPrefix}/assets/`)
    ? []
    : [`Built index does not use the expected asset prefix ${assetPrefix || "/"}.`]),
];

if (errors.length > 0) {
  throw new Error(["Built documentation check failed.", ...errors].join("\n"));
}

console.log(
  `Built documentation contains ${documentationPages.length} HTML, Markdown, LLM, and sitemap routes.`,
);
