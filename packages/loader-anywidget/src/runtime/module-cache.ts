import type { AnyWidget } from "@anywidget/types";

import { embeddedFileKey, parseDataUrl, type EsmSpec } from "../payload.js";
import { resolveAnyWidgetModule } from "./binding.js";
import { isPropertyOwner } from "./value-types.js";

export const MAX_PAGE_MODULES = 1_024;
export const PAGE_MODULE_CACHE_SYMBOL = Symbol.for(
  "@marimo-team/marimo-export:anywidget-module-cache:v1",
);
const PAGE_MODULE_CACHE_VERSION = 1;
const encoder = new TextEncoder();

interface PageModuleCache {
  readonly version: typeof PAGE_MODULE_CACHE_VERSION;
  readonly modules: Map<string, Promise<AnyWidget>>;
}

interface ModuleSource {
  readonly cacheKey: string;
  readonly importUrl: string;
  readonly embedded: string | undefined;
}

export async function loadPageAnyWidget(
  spec: EsmSpec,
  files: Readonly<Record<string, string>>,
): Promise<AnyWidget> {
  const source = await moduleSource(spec, files);
  const modules = pageModuleCache().modules;
  const existing = modules.get(source.cacheKey);
  if (existing !== undefined) return existing;
  if (modules.size >= MAX_PAGE_MODULES) {
    throw new Error(`AnyWidget page module cache exceeds ${MAX_PAGE_MODULES} unique modules.`);
  }

  let promise: Promise<AnyWidget>;
  promise = startImport(source, spec.url).catch((cause: unknown) => {
    if (modules.get(source.cacheKey) === promise) modules.delete(source.cacheKey);
    throw cause;
  });
  modules.set(source.cacheKey, promise);
  return promise;
}

export async function anyWidgetModuleCacheKey(
  spec: EsmSpec,
  files: Readonly<Record<string, string>>,
): Promise<string> {
  return (await moduleSource(spec, files)).cacheKey;
}

async function moduleSource(
  spec: EsmSpec,
  files: Readonly<Record<string, string>>,
): Promise<ModuleSource> {
  const key = embeddedFileKey(spec.url);
  const embedded = files[key];
  if (embedded !== undefined) {
    return {
      cacheKey: await digestIdentity([spec.hash, "embedded", key, embedded]),
      importUrl: spec.url,
      embedded,
    };
  }
  const importUrl = new URL(spec.url).href;
  const kind = importUrl.startsWith("data:") ? "data" : "url";
  return {
    cacheKey: await digestIdentity([spec.hash, kind, importUrl]),
    importUrl,
    embedded: undefined,
  };
}

function startImport(source: ModuleSource, diagnosticUrl: string): Promise<AnyWidget> {
  if (source.embedded === undefined) {
    return resolveImportedWidget(importModule(source.importUrl), diagnosticUrl);
  }

  const objectUrl = URL.createObjectURL(dataUrlToBlob(source.embedded));
  return importEmbeddedWidget(objectUrl, diagnosticUrl);
}

async function importEmbeddedWidget(objectUrl: string, diagnosticUrl: string): Promise<AnyWidget> {
  try {
    return resolveAnyWidgetModule(await importModule(objectUrl), diagnosticUrl);
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function resolveImportedWidget(
  task: Promise<object>,
  diagnosticUrl: string,
): Promise<AnyWidget> {
  return resolveAnyWidgetModule(await task, diagnosticUrl);
}

function importModule(url: string): Promise<object> {
  // Keep the runtime URL opaque so the browser owns module loading and its
  // page-lifetime ESM registry.
  return import(/* @vite-ignore */ /* webpackIgnore: true */ /* turbopackIgnore: true */ url);
}

function dataUrlToBlob(dataUrl: string): Blob {
  const { body, isBase64, mediaType } = parseDataUrl(dataUrl, "AnyWidget ESM data URL");
  const bytes = isBase64 ? base64Bytes(body) : new TextEncoder().encode(decodeURIComponent(body));
  return new Blob([bytes], { type: mediaType });
}

function base64Bytes(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function digestIdentity(parts: readonly string[]): Promise<string> {
  const bytes = encoder.encode(JSON.stringify(parts));
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function pageModuleCache(): PageModuleCache {
  const existing = Object.getOwnPropertyDescriptor(globalThis, PAGE_MODULE_CACHE_SYMBOL)?.value;
  if (existing !== undefined) {
    if (isPageModuleCache(existing)) return existing;
    throw new Error("AnyWidget page module cache has an incompatible value.");
  }

  const created: PageModuleCache = Object.freeze({
    version: PAGE_MODULE_CACHE_VERSION,
    modules: new Map(),
  });
  Object.defineProperty(globalThis, PAGE_MODULE_CACHE_SYMBOL, {
    configurable: false,
    enumerable: false,
    value: created,
    writable: false,
  });
  return created;
}

function isPageModuleCache<Value>(value: Value): value is Value & PageModuleCache {
  if (!isPropertyOwner(value) || !("version" in value) || !("modules" in value)) return false;
  return value.version === PAGE_MODULE_CACHE_VERSION && value.modules instanceof Map;
}
