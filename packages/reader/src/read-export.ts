import type {
  ArtifactHandle,
  ArtifactLoader,
  ArtifactLoaderContext,
  ArtifactRecord,
  ArtifactSelection,
  BlobRef,
  ExportArchiveInput,
  ExportManifest,
  ExportRootIndex,
  FetchLike,
  ReadExportArchiveOptions,
  ReadExportOptions,
  ReadExportIndexOptions,
  ReadLatestExportOptions,
  StaticExport,
  StaticExportArchive,
} from "#reader/types";
import { unzipSync } from "fflate";

const DEFAULT_ROOT_INDEX = "index.json";

export async function readExportIndex(options: ReadExportIndexOptions): Promise<ExportRootIndex> {
  const root = rootUrl(options.root);
  const fetchImpl = options.fetch ?? globalFetch();
  return fetchJson<ExportRootIndex>(
    fetchImpl,
    resolveHref(root, safeBundlePath(options.index ?? DEFAULT_ROOT_INDEX, "index href")),
    "export root index",
  );
}

export async function readLatestExport(options: ReadLatestExportOptions): Promise<StaticExport> {
  const index = await readExportIndex(options);
  if (!index.latest) {
    throw new Error("Export root index does not contain a latest bundle.");
  }

  const readOptions: ReadExportOptions = {
    root: options.root,
    manifest: index.latest.manifest_href,
  };
  if (options.loaders !== undefined) {
    readOptions.loaders = options.loaders;
  }
  if (options.fetch !== undefined) {
    readOptions.fetch = options.fetch;
  }

  return readExport(readOptions);
}

export async function readExport(options: ReadExportOptions): Promise<StaticExport> {
  const root = rootUrl(options.root);
  const fetchImpl = options.fetch ?? globalFetch();
  const source = new UrlExportSource(root, fetchImpl);
  const manifest = await source.json<ExportManifest>(options.manifest, "export manifest");
  return new StaticExportReader({
    manifest,
    source,
    loaders: options.loaders ?? [],
  });
}

export async function readExportArchive(
  options: ReadExportArchiveOptions,
): Promise<StaticExportArchive> {
  const source = ArchiveExportSource.from(await archiveBytes(options.bytes));
  const manifestHref = options.manifest ?? (await latestArchiveManifestHref(source));
  const manifest = await source.json<ExportManifest>(manifestHref, "export manifest");
  return new StaticExportArchiveReader({
    manifest,
    source,
    loaders: options.loaders ?? [],
  });
}

export function defineLoader<T>(loader: ArtifactLoader<T>): ArtifactLoader<T> {
  return loader;
}

export function jsonLoader<T = unknown>(formats: string | readonly string[]): ArtifactLoader<T> {
  return defineLoader({
    formats,
    load(context) {
      return context.json<T>();
    },
  });
}

export function textLoader(formats: string | readonly string[]): ArtifactLoader<string> {
  return defineLoader({
    formats,
    load(context) {
      return context.text();
    },
  });
}

export function htmlLoader(formats: string | readonly string[]): ArtifactLoader<string> {
  return textLoader(formats);
}

interface ReaderOptions {
  manifest: ExportManifest;
  source: ExportSource;
  loaders: ArtifactLoader[];
}

class StaticExportReader implements StaticExport {
  readonly manifest: ExportManifest;

  readonly #source: ExportSource;
  readonly #loaders: ArtifactLoader[];

  constructor(options: ReaderOptions) {
    this.manifest = options.manifest;
    this.#source = options.source;
    this.#loaders = options.loaders;
  }

  scenarios(): string[] {
    return this.manifest.scenarios.map((scenario) => scenario.id);
  }

  values(): string[] {
    return Object.keys(this.manifest.values);
  }

  formats(value: string): string[] {
    const record = this.manifest.values[value];
    if (!record) {
      throw new Error(`Export value ${JSON.stringify(value)} does not exist.`);
    }

    return [...record.formats];
  }

  get(selection: ArtifactSelection): ArtifactHandle {
    const scenario = this.manifest.scenarios.find(
      (candidate) => candidate.id === selection.scenario,
    );
    if (!scenario) {
      throw new Error(`Export scenario ${JSON.stringify(selection.scenario)} does not exist.`);
    }

    const value = scenario.values[selection.value];
    if (!value) {
      throw new Error(
        `Export value ${JSON.stringify(selection.value)} does not exist in scenario ${JSON.stringify(
          selection.scenario,
        )}.`,
      );
    }

    const artifact = value[selection.format];
    if (!artifact) {
      throw new Error(
        `Export format ${JSON.stringify(selection.format)} does not exist for value ${JSON.stringify(
          selection.value,
        )}.`,
      );
    }

    return new BundleArtifactHandle({
      artifact,
      selection,
      source: this.#source,
      loaders: this.#loaders,
    });
  }
}

class StaticExportArchiveReader extends StaticExportReader implements StaticExportArchive {
  readonly #source: ExportSource;

  constructor(options: ReaderOptions) {
    super(options);
    this.#source = options.source;
  }

  dispose(): void {
    this.#source.dispose?.();
  }
}

interface HandleOptions {
  artifact: ArtifactRecord;
  selection: ArtifactSelection;
  source: ExportSource;
  loaders: ArtifactLoader[];
}

class BundleArtifactHandle implements ArtifactHandle, ArtifactLoaderContext {
  readonly artifact: ArtifactRecord;
  readonly selection: ArtifactSelection;

  readonly #source: ExportSource;
  readonly #loaders: ArtifactLoader[];

  constructor(options: HandleOptions) {
    this.artifact = options.artifact;
    this.selection = options.selection;
    this.#source = options.source;
    this.#loaders = options.loaders;
  }

  file(key?: string): BlobRef {
    const selectedKey = key ?? this.artifact.data.entry;
    if (!selectedKey) {
      throw new Error(
        `Artifact ${this.artifact.format_id} has no entry file; pass an explicit file key.`,
      );
    }

    const file = this.artifact.data.files[selectedKey];
    if (!file) {
      throw new Error(
        `Artifact ${this.artifact.format_id} has no file named ${JSON.stringify(selectedKey)}.`,
      );
    }

    return file;
  }

  url(key?: string): string {
    const file = this.file(key);
    return this.#source.url(file.href, file.media_type);
  }

  async fetch(key?: string, init?: RequestInit): Promise<Response> {
    const file = this.file(key);
    return this.#source.fetch(file.href, file.media_type, init);
  }

  async bytes(key?: string): Promise<Uint8Array> {
    return this.#source.bytes(this.file(key).href);
  }

  async text(key?: string): Promise<string> {
    return this.#source.text(this.file(key).href);
  }

  async json<T = unknown>(key?: string): Promise<T> {
    return this.#source.json<T>(this.file(key).href);
  }

  async load<T = unknown>(): Promise<T> {
    const loader = this.#loaders.find((candidate) =>
      loaderSupports(candidate, this.artifact.format_id),
    );
    if (!loader) {
      throw new Error(
        `No loader registered for artifact format ${JSON.stringify(
          this.artifact.format_id,
        )}. Use .url(), .bytes(), .text(), or .json() directly, or pass a loader to readExport(...).`,
      );
    }

    return (await loader.load(this)) as T;
  }
}

interface ExportSource {
  url(href: string, mediaType?: string | null): string;
  fetch(href: string, mediaType?: string | null, init?: RequestInit): Promise<Response>;
  bytes(href: string): Promise<Uint8Array>;
  text(href: string): Promise<string>;
  json<T>(href: string, label?: string): Promise<T>;
  dispose?(): void;
}

class UrlExportSource implements ExportSource {
  readonly #root: URL;
  readonly #fetch: FetchLike;

  constructor(root: URL, fetchImpl: FetchLike) {
    this.#root = root;
    this.#fetch = fetchImpl;
  }

  url(href: string): string {
    return resolveHref(this.#root, safeBundlePath(href, "bundle href"));
  }

  async fetch(href: string, _mediaType?: string | null, init?: RequestInit): Promise<Response> {
    const url = this.url(href);
    const response = await this.#fetch(url, init);
    if (!response.ok) {
      throw new Error(
        `Failed to load bundle file from ${url}: ${response.status} ${response.statusText}`,
      );
    }

    return response;
  }

  async bytes(href: string): Promise<Uint8Array> {
    const response = await this.fetch(href);
    return new Uint8Array(await response.arrayBuffer());
  }

  async text(href: string): Promise<string> {
    return (await this.fetch(href)).text();
  }

  async json<T>(href: string, label = "bundle JSON"): Promise<T> {
    const url = this.url(href);
    const response = await this.#fetch(url);

    if (!response.ok) {
      throw new Error(
        `Failed to load ${label} from ${url}: ${response.status} ${response.statusText}`,
      );
    }

    return (await response.json()) as T;
  }
}

class ArchiveExportSource implements ExportSource {
  readonly #files: ReadonlyMap<string, Uint8Array>;
  readonly #objectUrls = new Map<string, string>();

  private constructor(files: ReadonlyMap<string, Uint8Array>) {
    this.#files = files;
  }

  static from(bytes: Uint8Array): ArchiveExportSource {
    const entries = unzipSync(bytes);
    const files = new Map<string, Uint8Array>();

    for (const [name, data] of Object.entries(entries)) {
      const path = archivePath(name);
      if (!path) {
        continue;
      }
      if (files.has(path)) {
        throw new Error(`Archive contains duplicate file ${JSON.stringify(path)}.`);
      }
      files.set(path, data);
    }

    return new ArchiveExportSource(files);
  }

  url(href: string, mediaType?: string | null): string {
    const path = bundleHref(href);
    const existing = this.#objectUrls.get(path);
    if (existing) {
      return existing;
    }
    if (typeof Blob === "undefined" || typeof URL.createObjectURL !== "function") {
      throw new Error(
        "Archive-backed artifact URLs require Blob URL support. Use .bytes(), .text(), .json(), or .load() instead.",
      );
    }

    const url = URL.createObjectURL(
      new Blob([arrayBuffer(this.read(path))], mediaType ? { type: mediaType } : undefined),
    );
    this.#objectUrls.set(path, url);
    return url;
  }

  async fetch(href: string, mediaType?: string | null): Promise<Response> {
    const init: ResponseInit = mediaType ? { headers: { "Content-Type": mediaType } } : {};
    return new Response(arrayBuffer(this.read(bundleHref(href))), init);
  }

  async bytes(href: string): Promise<Uint8Array> {
    return this.read(bundleHref(href));
  }

  async text(href: string): Promise<string> {
    return new TextDecoder().decode(this.read(bundleHref(href)));
  }

  async json<T>(href: string, label = "bundle JSON"): Promise<T> {
    try {
      return JSON.parse(await this.text(href)) as T;
    } catch (error) {
      throw new Error(`Failed to parse ${label} from archive path ${JSON.stringify(href)}.`, {
        cause: error,
      });
    }
  }

  dispose(): void {
    for (const url of this.#objectUrls.values()) {
      URL.revokeObjectURL(url);
    }
    this.#objectUrls.clear();
  }

  private read(path: string): Uint8Array {
    const file = this.#files.get(path);
    if (!file) {
      throw new Error(`Archive does not contain bundle file ${JSON.stringify(path)}.`);
    }

    return file;
  }
}

function loaderSupports(loader: ArtifactLoader, format: string): boolean {
  return Array.isArray(loader.formats)
    ? loader.formats.includes(format)
    : loader.formats === format;
}

function rootUrl(root: string | URL): URL {
  const value = root.toString();
  const base =
    typeof globalThis.location === "undefined" ? "http://localhost/" : globalThis.location.href;
  const withTrailingSlash = value.endsWith("/") ? value : `${value}/`;
  return new URL(withTrailingSlash, base);
}

function resolveHref(root: URL, href: string): string {
  return new URL(href, root).href;
}

async function latestArchiveManifestHref(source: ArchiveExportSource): Promise<string> {
  const index = await source.json<ExportRootIndex>(DEFAULT_ROOT_INDEX, "export root index");
  if (!index.latest) {
    throw new Error("Export archive index does not contain a latest bundle.");
  }

  return index.latest.manifest_href;
}

async function archiveBytes(input: ExportArchiveInput): Promise<Uint8Array> {
  if (input instanceof Uint8Array) {
    return input;
  }

  if (input instanceof ArrayBuffer) {
    return new Uint8Array(input);
  }

  if (ArrayBuffer.isView(input)) {
    return new Uint8Array(input.buffer).slice(
      input.byteOffset,
      input.byteOffset + input.byteLength,
    );
  }

  if (typeof Blob !== "undefined" && input instanceof Blob) {
    return new Uint8Array(await input.arrayBuffer());
  }

  throw new TypeError("readExportArchive requires ArrayBuffer, ArrayBufferView, or Blob bytes.");
}

function archivePath(name: string): string | null {
  if (name.endsWith("/")) {
    return null;
  }

  return safeBundlePath(name, "archive entry");
}

function bundleHref(href: string): string {
  return safeBundlePath(href, "bundle href");
}

function safeBundlePath(path: string, label: string): string {
  if (!path || path.startsWith("/") || path.includes("\\")) {
    throw new Error(`Invalid ${label} ${JSON.stringify(path)}.`);
  }

  const parts = path.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error(`Invalid ${label} ${JSON.stringify(path)}.`);
  }

  return parts.join("/");
}

function arrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

async function fetchJson<T>(fetchImpl: FetchLike, url: string, label: string): Promise<T> {
  const response = await fetchImpl(url);

  if (!response.ok) {
    throw new Error(
      `Failed to load ${label} from ${url}: ${response.status} ${response.statusText}`,
    );
  }

  return (await response.json()) as T;
}

function globalFetch(): FetchLike {
  if (typeof globalThis.fetch !== "function") {
    throw new Error(
      "readExport requires fetch. Pass a fetch implementation in readExport({ fetch }).",
    );
  }

  return globalThis.fetch.bind(globalThis);
}
