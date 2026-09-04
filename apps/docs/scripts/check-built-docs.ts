import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { documentationExamples } from "../example.ts";
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
const socialImagePath = "brand/marimo-export-og.png";
const marketApplicationTab = documentationExamples.market.tabs.find(
  ({ key }) => key === "application",
);
const marketNotebookTab = documentationExamples.market.tabs.find(({ key }) => key === "notebook");
const quickstartApplicationTab = documentationExamples.quickstart.tabs.find(
  ({ key }) => key === "application",
);
const quickstartNotebookTab = documentationExamples.quickstart.tabs.find(
  ({ key }) => key === "notebook",
);
if (
  marketApplicationTab === undefined ||
  marketNotebookTab === undefined ||
  quickstartApplicationTab === undefined ||
  quickstartNotebookTab === undefined
) {
  throw new Error("Documentation example tabs are incomplete.");
}
const marketApplicationPath = marketApplicationTab.href.replace(/^\//, "");
const marketNotebookPath = marketNotebookTab.href.replace(/^\//, "");
const quickstartApplicationPath = quickstartApplicationTab.href.replace(/^\//, "");
const quickstartNotebookPath = quickstartNotebookTab.href.replace(/^\//, "");
const quickstartApplicationRoot = dirname(quickstartApplicationPath);
const marketIndexPath = join(dirname(marketApplicationPath), "export", "index.json");
const quickstartIndexPath = join(quickstartApplicationRoot, "export", "index.json");
const requiredFiles = [
  ...files,
  socialImagePath,
  "llms.txt",
  "llms-full.txt",
  "sitemap.xml",
  marketApplicationPath,
  marketIndexPath,
  marketNotebookPath,
  quickstartApplicationPath,
  quickstartNotebookPath,
  quickstartIndexPath,
];
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
const socialMetadata = new Map(
  [...index.matchAll(/<meta (?:property|name)="([^"]+)" content="([^"]*)">/g)]
    .map((match) => [match[1], match[2]] as const)
    .filter((entry): entry is readonly [string, string] => entry[0] !== undefined),
);
const expectedSocialMetadata = new Map([
  ["og:title", "marimo-export: Prepare notebook results. Read them anywhere."],
  [
    "og:description",
    "Select the states and outputs to publish from a marimo notebook. marimo-export writes a portable, verified notebook export. Browser applications and agents read it after Python stops, without a Python runtime or the notebook's Python source code.",
  ],
  ["og:image", new URL(socialImagePath, siteUrl).href],
  ["og:image:width", "2400"],
  ["og:image:height", "1260"],
  [
    "og:image:alt",
    "marimo-export mark pointing from prepared notebook states toward a portable export.",
  ],
  ["twitter:card", "summary_large_image"],
  ["twitter:title", "marimo-export: Prepare notebook results. Read them anywhere."],
  [
    "twitter:description",
    "Select the states and outputs to publish from a marimo notebook. marimo-export writes a portable, verified notebook export. Browser applications and agents read it after Python stops, without a Python runtime or the notebook's Python source code.",
  ],
  ["twitter:image", new URL(socialImagePath, siteUrl).href],
]);
const incorrectSocialMetadata = [...expectedSocialMetadata].flatMap(([name, expected]) => {
  const actual = socialMetadata.get(name);
  return actual === expected
    ? []
    : [`${name}: expected ${expected}, received ${actual ?? "missing"}`];
});

const baseName = process.env.BASE_PATH?.trim().replace(/^\/+|\/+$/g, "");
const assetPrefix = baseName ? `/${baseName}` : "";
const firstNavigationLink = topNavigation.flatMap((item) =>
  "link" in item
    ? [item.link]
    : item.items.flatMap((child) => ("link" in child ? [child.link] : [])),
)[0];
if (firstNavigationLink === undefined) throw new Error("Top navigation has no linked item.");
const navigationHref = `${assetPrefix}${firstNavigationLink}`;
const escapedBaseReferences = baseName
  ? [...renderedDocumentation.matchAll(/\b(?:href|src)="(\/(?!\/)[^"#?]+)"/g)]
      .map((match) => match[1])
      .filter(isString)
      .filter((reference) => !reference.startsWith(`${assetPrefix}/`))
  : [];
const marketApplication = await readFile(
  join(outputDirectory, marketApplicationPath),
  "utf8",
).catch(() => "");
const marketNotebook = await readFile(join(outputDirectory, marketNotebookPath), "utf8").catch(
  () => "",
);
const quickstartApplication = await readFile(
  join(outputDirectory, quickstartApplicationPath),
  "utf8",
).catch(() => "");
const quickstartNotebook = await readFile(
  join(outputDirectory, quickstartNotebookPath),
  "utf8",
).catch(() => "");
const quickstartIndex = await readFile(join(outputDirectory, quickstartIndexPath), "utf8").catch(
  () => "",
);
const quickstartAssets = await readdir(
  join(outputDirectory, quickstartApplicationRoot, "export", "assets"),
).catch(() => []);
const quickstartFiles = await readdir(join(outputDirectory, quickstartApplicationRoot), {
  recursive: true,
}).catch(() => []);
const marketApplicationHref = `${assetPrefix}${marketApplicationTab.href}`;
const quickstartApplicationHref = `${assetPrefix}${quickstartApplicationTab.href}`;
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
  ...formatList("Incorrect social metadata", incorrectSocialMetadata),
  ...(index.includes(`href="${assetPrefix}/assets/`)
    ? []
    : [`Built index does not use the expected stylesheet prefix ${assetPrefix || "/"}.`]),
  ...(index.includes(`src="${assetPrefix}/assets/`)
    ? []
    : [`Built index does not use the expected script prefix ${assetPrefix || "/"}.`]),
  ...(index.includes(`href="${navigationHref}"`)
    ? []
    : [`Built index does not use the expected navigation prefix ${assetPrefix || "/"}.`]),
  ...(renderedDocumentation.includes(`src="${marketApplicationHref}"`)
    ? []
    : [`Built documentation does not embed the market dashboard at ${marketApplicationHref}.`]),
  ...(renderedDocumentation.includes(`src="${quickstartApplicationHref}"`)
    ? []
    : [`Built documentation does not embed the quickstart at ${quickstartApplicationHref}.`]),
  ...(marketApplication && !/\b(?:href|src)="\/(?!\/)/.test(marketApplication)
    ? []
    : ["Built market dashboard assets are not document-relative."]),
  ...(quickstartApplication && !/\b(?:href|src)="\/(?!\/)/.test(quickstartApplication)
    ? []
    : ["Built quickstart application assets are not document-relative."]),
  ...(marketNotebook.includes("<marimo-code hidden") &&
  marketNotebook.includes("<marimo-filename hidden>finance.py")
    ? []
    : ["Built finance notebook is missing its source or captured output."]),
  ...(quickstartNotebook.includes("<marimo-code hidden") &&
  quickstartNotebook.includes("<marimo-filename hidden>report.py")
    ? []
    : ["Built quickstart notebook is missing its source or captured output."]),
  ...(!quickstartApplication.includes("<marimo-code") &&
  !quickstartApplication.includes("<marimo-filename") &&
  quickstartApplication.includes("Ships no Python source or runtime") &&
  quickstartFiles.every((file) => !file.endsWith(".py"))
    ? []
    : ["Built quickstart application crosses the Python producer boundary."]),
  ...(quickstartIndex.includes('"inputs":["days"]') &&
  quickstartIndex.includes('"outputs":["report","summary"]') &&
  quickstartIndex.includes('"codec":"marimo.json.v1"') &&
  quickstartIndex.includes('"codec":"marimo.output.v1"') &&
  quickstartAssets.length === 2 &&
  quickstartAssets.every((asset) => asset.endsWith(".output.json"))
    ? []
    : ["Built documentation quickstart export is incomplete."]),
  ...formatList("Root-absolute references outside the configured base path", [
    ...new Set(escapedBaseReferences),
  ]),
];

if (errors.length > 0) {
  throw new Error(["Built documentation check failed.", ...errors].join("\n"));
}

console.log(
  `Built documentation contains ${documentationPages.length} routes, two static notebooks, and two verified static applications.`,
);
