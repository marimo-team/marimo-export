import { readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { documentationPages, documentationSections, topNavigation } from "../navigation.ts";

const docsDirectory = fileURLToPath(new URL("../../../docs/", import.meta.url));

const collectMarkdownFiles = async (directory: string, relative = ""): Promise<string[]> => {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const child = relative ? `${relative}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        return collectMarkdownFiles(join(directory, entry.name), child);
      }
      return entry.isFile() && entry.name.endsWith(".md") ? [child] : [];
    }),
  );
  return files.flat();
};

const markdownForRoute = (route: string): string => {
  if (route === "/") {
    return "index.md";
  }
  if (route.endsWith("/")) {
    return `${route.slice(1)}index.md`;
  }
  return `${route.slice(1)}.md`;
};

const duplicates = (values: readonly string[]): string[] => {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts].filter(([, count]) => count > 1).map(([value]) => value);
};

const formatList = (label: string, values: readonly string[]): string[] =>
  values.length === 0 ? [] : [`${label}:`, ...values.map((value) => `  - ${value}`)];

const topNavigationRoutes = topNavigation.flatMap((item) => {
  if ("link" in item) {
    return [item.link];
  }
  return item.items.map(({ link }) => link).filter((link) => link.startsWith("/"));
});

export const checkNavigation = async () => {
  const markdownFiles = (await collectMarkdownFiles(docsDirectory)).sort();
  const routes = documentationPages.map(({ link }) => link);
  const manifestFiles = routes.map(markdownForRoute);
  const routeSet = new Set(routes);
  const errors = [
    ...formatList("Duplicate manifest routes", duplicates(routes)),
    ...formatList("Duplicate Markdown targets", duplicates(manifestFiles)),
    ...formatList(
      "Malformed manifest routes",
      routes.filter(
        (route) =>
          !route.startsWith("/") ||
          route.includes("//") ||
          route.endsWith(".md") ||
          route.includes("?") ||
          route.includes("#"),
      ),
    ),
    ...formatList(
      "Manifest routes with no Markdown page",
      manifestFiles.filter((file) => !markdownFiles.includes(file)),
    ),
    ...formatList(
      "Markdown pages missing from the manifest",
      markdownFiles.filter((file) => !manifestFiles.includes(file)),
    ),
    ...formatList(
      "Top navigation routes missing from the manifest",
      topNavigationRoutes.filter((route) => !routeSet.has(route)),
    ),
    ...formatList(
      "Sections with no pages",
      documentationSections.filter(({ items }) => items.length === 0).map(({ text }) => text),
    ),
  ];

  if (errors.length > 0) {
    throw new Error(["Documentation navigation check failed.", ...errors].join("\n"));
  }

  return { routes: routes.length, sections: documentationSections.length };
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : undefined;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    const result = await checkNavigation();
    console.log(
      `Documentation navigation covers ${result.routes} routes across ${result.sections} sections.`,
    );
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
