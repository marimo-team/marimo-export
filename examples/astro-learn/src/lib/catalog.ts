import {
  createExportClient,
  type ExportClient,
  type MarimoArchiveCaptureClient,
  type WorkspaceNotebook,
} from "@marimo-team/export-client";

import { fixtureCatalog } from "@/data/catalog";
import type { CatalogStats, LearnCatalog, LearnNotebook, TopicGroup } from "@/types";

let catalogPromise: Promise<LearnCatalog> | undefined;
const includedTopics = new Set(["altair", "optimization", "tools"]);

export function getCatalog(): Promise<LearnCatalog> {
  catalogPromise ??= loadCatalog();
  return catalogPromise;
}

export function catalogStats(notebooks: LearnNotebook[]): CatalogStats {
  const topics = new Set(notebooks.map((notebook) => notebook.topic));
  return {
    total: notebooks.length,
    topics: topics.size,
    cells: notebooks.reduce((total, notebook) => total + notebook.cell_count, 0),
  };
}

export function groupByTopic(notebooks: LearnNotebook[]): TopicGroup[] {
  const groups = new Map<string, LearnNotebook[]>();
  for (const notebook of notebooks) {
    const group = groups.get(notebook.topic) ?? [];
    group.push(notebook);
    groups.set(notebook.topic, group);
  }

  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([topic, items]) => {
      const sorted = [...items].sort((left, right) => left.path.localeCompare(right.path));
      return {
        topic,
        title: topic.replaceAll("-", " "),
        notebooks: sorted,
      };
    });
}

async function loadCatalog(): Promise<LearnCatalog> {
  if (!process.env.MARIMO_LEARN_SERVER_URL) {
    return {
      notebooks: limitByPath(fixtureCatalog.notebooks),
    };
  }

  const client = createExportClient({
    server: process.env.MARIMO_LEARN_SERVER_URL,
    serverToken: process.env.MARIMO_LEARN_SERVER_TOKEN ?? "learn",
  });
  const workspace = await client.listWorkspaceNotebooks();
  const notebooks = limitByPath(workspace.filter(isLearnNotebook));
  const records = await Promise.all(notebooks.map((notebook) => notebookRecord(client, notebook)));

  return {
    notebooks: records,
  };
}

async function notebookRecord(
  client: ExportClient,
  notebook: WorkspaceNotebook,
): Promise<LearnNotebook> {
  const source = await readNotebookSource(client.marimo, notebook.path);
  const summary = description(source);
  return {
    name: notebook.name,
    path: notebook.path,
    slug: slug(notebook.path),
    title: title(source, notebook.name),
    ...(summary ? { description: summary } : {}),
    topic: topic(notebook.path),
    cell_count: cellCount(source),
  };
}

async function readNotebookSource(
  client: MarimoArchiveCaptureClient,
  path: string,
): Promise<string> {
  const { response, data } = await client.POST("/api/files/file_details", {
    body: { path },
  });
  if (!response.ok) {
    return "";
  }
  return typeof data?.contents === "string" ? data.contents : "";
}

function limitByPath<T extends { path: string }>(notebooks: T[]): T[] {
  const limit = Number(process.env.MARIMO_LEARN_LIMIT ?? 0);
  const sorted = [...notebooks].sort((left, right) => left.path.localeCompare(right.path));
  return Number.isFinite(limit) && limit > 0 ? sorted.slice(0, limit) : sorted;
}

function isLearnNotebook(notebook: WorkspaceNotebook): boolean {
  return notebook.path.endsWith(".py") && includedTopics.has(topic(notebook.path));
}

function slug(path: string): string {
  return path
    .replace(/\.py$/, "")
    .split("/")
    .map((part) => part.replaceAll("_", "-"))
    .join("--");
}

function topic(path: string): string {
  return path.split("/")[0] ?? "notebooks";
}

function title(source: string, fallback: string): string {
  const heading = firstMarkdownHeading(source);
  if (heading) {
    return heading;
  }

  return fallback.replace(/\.py$/, "").replaceAll("_", " ");
}

function description(source: string): string | undefined {
  const block = firstMarkdownBlock(source);
  if (!block) {
    return undefined;
  }

  const lines = block
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  return lines[0]?.slice(0, 180);
}

function firstMarkdownHeading(source: string): string | undefined {
  for (const block of markdownBlocks(source)) {
    for (const line of block.split("\n")) {
      const match = /^\s*#\s+(.+)$/.exec(line);
      if (match?.[1]) {
        return match[1].trim();
      }
    }
  }

  for (const line of source.split("\n")) {
    const match = /^\s*#\s+(.+)$/.exec(line);
    if (match?.[1] && !match[1].startsWith("///")) {
      return match[1].trim();
    }
  }

  return undefined;
}

function firstMarkdownBlock(source: string): string | undefined {
  return markdownBlocks(source)[0];
}

function markdownBlocks(source: string): string[] {
  return [...source.matchAll(/mo\.md\(\s*r?(["']{3})([\s\S]*?)\1\s*\)/g)].map(
    (match) => match[2] ?? "",
  );
}

function cellCount(source: string): number {
  return [...source.matchAll(/@app\.cell(?:\b|\()/g)].length;
}
