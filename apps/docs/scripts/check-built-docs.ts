import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { documentationExample } from "../example.ts";
import { documentationPages, topNavigation } from "../navigation.ts";

const outputDirectory = fileURLToPath(new URL("../.vitepress/dist/", import.meta.url));
const siteUrl = new URL("https://marimo-team.github.io/marimo-export/");

const htmlForRoute = (route: string): string => {
  if (route === "/") return "index.html";
  if (route.endsWith("/")) return `${route.slice(1)}index.html`;
  return `${route.slice(1)}.html`;
};

const markdownForRoute = (route: string): string => {
  if (route === "/") return "index.md";
  if (route.endsWith("/")) return `${route.slice(1, -1)}.md`;
  return `${route.slice(1)}.md`;
};

const canonicalForRoute = (route: string): string => new URL(route.slice(1), siteUrl).href;
const markdownUrlForRoute = (route: string): string =>
  new URL(markdownForRoute(route), siteUrl).href;

const missingFiles = async (files: readonly string[]): Promise<string[]> => {
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
  return results.filter((file): file is string => file !== undefined);
};

const duplicates = (values: readonly string[]): string[] => {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return [...counts].filter(([, count]) => count > 1).map(([value]) => value);
};

const compareSets = (expected: ReadonlySet<string>, actual: ReadonlySet<string>) => ({
  missing: [...expected].filter((value) => !actual.has(value)),
  unexpected: [...actual].filter((value) => !expected.has(value)),
});

const formatList = (label: string, values: readonly string[]): string[] =>
  values.length === 0 ? [] : [`${label}:`, ...values.map((value) => `  - ${value}`)];

const isString = (value: string | undefined): value is string => value !== undefined;

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
const renderedDocumentation = (
  await Promise.all(
    files
      .filter((file) => file.endsWith(".html"))
      .map((file) => readFile(join(outputDirectory, file), "utf8")),
  )
).join("\n");

const llmsLinks = [...llms.matchAll(/\]\((https?:\/\/[^)]+\.md)\)/g)]
  .map((match) => match[1])
  .filter(isString);
const llmsFullLinks = [
  ...llmsFull.matchAll(
    /^url:\s+(?:'(https?:\/\/[^']+\.md)'|(https?:\/\/\S+\.md)|>-\n\s+(https?:\/\/\S+\.md))$/gm,
  ),
]
  .map((match) => match[1] ?? match[2] ?? match[3])
  .filter(isString);
const sitemapLinks = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)]
  .map((match) => match[1])
  .filter(isString);
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
const firstNavigation = topNavigation[0];
if (firstNavigation === undefined) throw new Error("Top navigation is empty.");
const navigationHref = `${assetPrefix}${firstNavigation.link}`;
const escapedBaseReferences = baseName
  ? [...renderedDocumentation.matchAll(/\b(?:href|src)="(\/(?!\/)[^"#?]+)"/g)]
      .map((match) => match[1])
      .filter(isString)
      .filter((reference) => !reference.startsWith(`${assetPrefix}/`))
  : [];
const applicationTab = documentationExample.tabs.find(({ key }) => key === "application");
const notebookTab = documentationExample.tabs.find(({ key }) => key === "notebook");
if (applicationTab === undefined || notebookTab === undefined) {
  throw new Error("Documentation example tabs are incomplete.");
}
const applicationPath = applicationTab.href.replace(/^\//, "");
const notebookPath = notebookTab.href.replace(/^\//, "");
const exampleIndex = await readFile(join(outputDirectory, applicationPath), "utf8").catch(() => "");
const notebookIndex = await readFile(join(outputDirectory, notebookPath), "utf8").catch(() => "");
const exampleHref = `${assetPrefix}${applicationTab.href}`;
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
    : [`Built index does not use the expected stylesheet prefix ${assetPrefix || "/"}.`]),
  ...(index.includes(`src="${assetPrefix}/assets/`)
    ? []
    : [`Built index does not use the expected script prefix ${assetPrefix || "/"}.`]),
  ...(index.includes(`href="${navigationHref}"`)
    ? []
    : [`Built index does not use the expected navigation prefix ${assetPrefix || "/"}.`]),
  ...(renderedDocumentation.includes(`src="${exampleHref}"`)
    ? []
    : [`Built documentation does not embed the market dashboard at ${exampleHref}.`]),
  ...((
    await missingFiles([
      applicationPath,
      "examples/market-dashboard/application/export/index.json",
      notebookPath,
    ])
  ).length === 0
    ? []
    : ["Built documentation is missing the market dashboard application, export, or notebook."]),
  ...(exampleIndex && !/\b(?:href|src)="\/(?!\/)/.test(exampleIndex)
    ? []
    : ["Built market dashboard assets are not document-relative."]),
  ...(notebookIndex.includes("<marimo-code hidden") &&
  notebookIndex.includes("<marimo-filename hidden>finance.py")
    ? []
    : ["Built documentation notebook is missing its source or captured output."]),
  ...formatList("Root-absolute references outside the configured base path", [
    ...new Set(escapedBaseReferences),
  ]),
];

if (errors.length > 0) {
  throw new Error(["Built documentation check failed.", ...errors].join("\n"));
}

console.log(
  `Built documentation contains ${documentationPages.length} routes, one static notebook, and one verified static application.`,
);
