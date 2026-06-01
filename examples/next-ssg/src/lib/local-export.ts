import fs from "node:fs/promises";
import path from "node:path";

import { createMarimoExportClient } from "@marimo-team/export-client";
import { readExportDirectory, type StaticExport } from "@marimo-team/export-reader";

import { exportPublicRoot } from "@/lib/export-paths";
import { marimoNotebook, marimoServerToken, marimoServerUrl } from "@/lib/marimo-env";
import { financePairs } from "@/lib/pairs";
import { buildFinanceSpec } from "@/lib/spec";

const LOCAL_EXPORT_ROOT = path.join(process.cwd(), "public", "export", "finance");
const LOCAL_EXPORT_PARENT = path.dirname(LOCAL_EXPORT_ROOT);
const CAPTURE_LOCK = path.join(LOCAL_EXPORT_PARENT, ".finance-capture.lock");

let exportPromise: Promise<StaticExport> | undefined;

export interface SummaryPayload {
  rows: number;
  columns: string[];
  symbols: string[];
  date_start: string;
  date_end: string;
  latest: Array<{
    symbol: string;
    close: number;
    close_change: number | null;
  }>;
}

export interface SampleRow {
  Date: string;
  Symbol: string;
  Open: number;
  Close: number;
  "Close Change": number | null;
}

export interface FinanceOverview {
  manifestId: string;
  notebookName: string | null;
  notebookSha: string | null;
  scenarios: string[];
}

export interface FinancePairPage {
  scenario: string;
  summary: SummaryPayload;
  changeDescHtml: string;
  sampleRows: SampleRow[];
  pngUrl: string;
  manifestId: string;
  notebookName: string | null;
  notebookSha: string | null;
}

export const loadFinanceExport = (): Promise<StaticExport> => {
  exportPromise ??= ensureFinanceExport();
  return exportPromise;
};

export const getFinanceOverview = async (): Promise<FinanceOverview> => {
  const exp = await loadFinanceExport();
  return {
    manifestId: exp.manifest.id,
    notebookName: exp.manifest.notebook.name,
    notebookSha: exp.manifest.notebook.source_sha256 ?? null,
    scenarios: exp.scenarios(),
  };
};

export const getFinancePairPage = async (scenario: string): Promise<FinancePairPage> => {
  const exp = await loadFinanceExport();
  const summary = await exp
    .get({ scenario, value: "summary", format: "json" })
    .json<SummaryPayload>();
  const sampleRows = await exp
    .get({ scenario, value: "sample_rows", format: "json" })
    .json<SampleRow[]>();
  const changeDescHtml = await exp.get({ scenario, value: "change_desc", format: "html" }).text();
  const pngFile = exp.get({ scenario, value: "chart", format: "png" }).entry().ref;

  return {
    scenario,
    summary,
    changeDescHtml,
    sampleRows,
    pngUrl: `${exportPublicRoot}${pngFile.href}`,
    manifestId: exp.manifest.id,
    notebookName: exp.manifest.notebook.name,
    notebookSha: exp.manifest.notebook.source_sha256 ?? null,
  };
};

const ensureFinanceExport = async (): Promise<StaticExport> => {
  const shouldCapture = await needsCapture();
  if (shouldCapture) {
    await captureFinanceBundle();
  }

  return readExportDirectory({
    root: LOCAL_EXPORT_ROOT,
    readFile: (file) => fs.readFile(file),
    url: (href) => `${exportPublicRoot}${href}`,
  });
};

const needsCapture = async (): Promise<boolean> => {
  if (process.env.MARIMO_CAPTURE === "0" || process.env.MARIMO_CAPTURE === "false") {
    return false;
  }

  if (process.env.MARIMO_CAPTURE === "force") {
    return true;
  }

  try {
    await fs.access(path.join(LOCAL_EXPORT_ROOT, "index.json"));
    return false;
  } catch {
    return true;
  }
};

const captureFinanceBundle = async (): Promise<void> => {
  const server = marimoServerUrl();
  const serverToken = marimoServerToken();
  const notebook = marimoNotebook();
  const sessionId = process.env.MARIMO_SESSION_ID;
  const sessionTarget = sessionId ? { sessionId } : { notebook };
  const client = createMarimoExportClient({
    server,
    ...(serverToken ? { serverToken } : {}),
  });

  const hasLock = await acquireCaptureLock();
  if (!hasLock) {
    await waitForFile(path.join(LOCAL_EXPORT_ROOT, "index.json"));
    return;
  }

  try {
    await fs.rm(LOCAL_EXPORT_ROOT, { recursive: true, force: true });
    await fs.mkdir(LOCAL_EXPORT_ROOT, { recursive: true });

    await client.export(buildFinanceSpec(financePairs), {
      ...sessionTarget,
      outputRoot: LOCAL_EXPORT_ROOT,
    });
  } finally {
    await fs.rm(CAPTURE_LOCK, { recursive: true, force: true });
  }
};

const acquireCaptureLock = async (): Promise<boolean> => {
  await fs.mkdir(LOCAL_EXPORT_PARENT, { recursive: true });

  try {
    await fs.mkdir(CAPTURE_LOCK);
    return true;
  } catch (error) {
    if (!isAlreadyExists(error)) {
      throw error;
    }

    return false;
  }
};

const waitForFile = async (file: string, timeoutMs = 120_000): Promise<void> => {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    try {
      await fs.access(file);
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  throw new Error(`Timed out waiting for marimo export bundle file: ${file}`);
};

const isAlreadyExists = (error: unknown): boolean =>
  error instanceof Error && "code" in error && error.code === "EEXIST";
