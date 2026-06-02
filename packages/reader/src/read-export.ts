import type {
  Export,
  ExportArchive,
  ExportEntry,
  ExportFile,
  ExportLoader,
  ExportLoaderContext,
  ExportLoaderSelector,
  ExportOptions,
  FormatRecord,
  FormatSelection,
  BlobRef,
  ExportScenario,
  ExportArchiveInput,
  ExportManifest,
  ExportRootIndex,
  FetchLike,
  LocalReadFile,
  LocalReadFileResult,
  LocalUrlResolver,
  ManifestScenario,
} from "./types.js";
import { unzipSync } from "fflate";
import { safeBundlePath, validateExportManifest, validateExportRootIndex } from "./schema.js";

const DEFAULT_ROOT_INDEX = "index.json";

type HostedReadOptions = {
  root: string | URL;
  fetch?: FetchLike;
};

type DirectoryReadOptions = {
  root: string;
  readFile: LocalReadFile;
  url?: LocalUrlResolver;
};

type ArchiveReadOptions = {
  bytes: ExportArchiveInput;
};

export function readExport(
  options: ExportOptions & { bytes: ExportArchiveInput },
): Promise<ExportArchive>;
export function readExport(options: ExportOptions & { root: string | URL }): Promise<Export>;
export function readExport(options: ExportOptions): Promise<Export | ExportArchive> {
  if (isArchiveReadOptions(options)) {
    return openArchiveExport(options);
  }
  if (isDirectoryReadOptions(options)) {
    return openDirectoryExport(options);
  }
  return openHostedExport(options);
}

function isArchiveReadOptions(options: ExportOptions): options is ArchiveReadOptions {
  return "bytes" in options;
}

function isDirectoryReadOptions(options: ExportOptions): options is DirectoryReadOptions {
  return "readFile" in options;
}

async function loadRootIndex(options: HostedReadOptions): Promise<ExportRootIndex> {
  const root = rootUrl(options.root);
  const fetchImpl = options.fetch ?? globalFetch();
  return validateExportRootIndex(
    await fetchJson(
      fetchImpl,
      resolveHref(root, safeBundlePath(DEFAULT_ROOT_INDEX, "index href")),
      "export root index",
    ),
  );
}

async function openHostedExport(options: HostedReadOptions): Promise<Export> {
  const index = await loadRootIndex(options);
  if (!index.latest) {
    throw new Error("Export root index does not contain a latest bundle.");
  }

  const root = rootUrl(options.root);
  const fetchImpl = options.fetch ?? globalFetch();
  const source = new UrlExportSource(root, fetchImpl);
  const manifest = validateExportManifest(
    await source.json(index.latest.manifest_href, "export manifest"),
  );
  return new ExportReader({
    manifest,
    source,
  });
}

async function openDirectoryExport(options: DirectoryReadOptions): Promise<Export> {
  const source = new LocalExportSource(options.root, options.readFile, options.url);
  const index = validateExportRootIndex(
    await source.json(safeBundlePath(DEFAULT_ROOT_INDEX, "index href"), "export root index"),
  );
  if (!index.latest) {
    throw new Error("Export root index does not contain a latest bundle.");
  }

  const manifest = validateExportManifest(
    await source.json(index.latest.manifest_href, "export manifest"),
  );
  return new ExportReader({
    manifest,
    source,
  });
}

async function openArchiveExport(options: ArchiveReadOptions): Promise<ExportArchive> {
  const source = ArchiveExportSource.from(await archiveBytes(options.bytes));
  const manifestHref = await latestArchiveManifestHref(source);
  const manifest = validateExportManifest(await source.json(manifestHref, "export manifest"));
  return new ArchiveExportReader({
    manifest,
    source,
  });
}

export function defineLoader<T>(loader: ExportLoader<T>): ExportLoader<T> {
  return loader;
}

export function jsonLoader<T = unknown>(formatIds: string | readonly string[]): ExportLoader<T> {
  return defineLoader({
    ...loaderFormats(formatIds),
    load(context) {
      return context.entry().json<T>();
    },
  });
}

export function textLoader(formatIds: string | readonly string[]): ExportLoader<string> {
  return defineLoader({
    ...loaderFormats(formatIds),
    load(context) {
      return context.entry().text();
    },
  });
}

export function htmlLoader(formatIds: string | readonly string[]): ExportLoader<string> {
  return textLoader(formatIds);
}

interface ReaderOptions {
  manifest: ExportManifest;
  source: ExportSource;
}

class ExportReader implements Export {
  readonly id: string;
  readonly notebook: Export["notebook"];
  readonly sourceSpecSha256: string | null;
  readonly raw: Export["raw"];

  readonly #manifest: ExportManifest;
  readonly #source: ExportSource;

  constructor(options: ReaderOptions) {
    this.id = options.manifest.id;
    this.notebook = {
      name: options.manifest.notebook.name,
      sourceSha256: options.manifest.notebook.source_sha256 ?? null,
    };
    this.sourceSpecSha256 = options.manifest.provenance?.source_spec_sha256 ?? null;
    this.raw = {
      manifest: cloneJson(options.manifest),
    };
    this.#manifest = options.manifest;
    this.#source = options.source;
  }

  scenarios(): string[] {
    return this.#manifest.scenarios.map((scenario) => scenario.id);
  }

  scenario(id: string): ExportScenario {
    return new ExportScenarioReader({
      scenario: this.scenarioById(id),
      reader: this,
    });
  }

  values(): string[] {
    return Object.keys(this.#manifest.values);
  }

  formats(value: string): string[] {
    const record = this.#manifest.values[value];
    if (!record) {
      throw new Error(`Export value ${JSON.stringify(value)} does not exist.`);
    }

    return [...record.formats];
  }

  get(selection: FormatSelection): ExportEntry {
    const scenario = this.scenarioById(selection.scenario);

    const value = scenario.values[selection.value];
    if (!value) {
      throw new Error(
        `Export value ${JSON.stringify(selection.value)} does not exist in scenario ${JSON.stringify(
          selection.scenario,
        )}.`,
      );
    }

    const record = value[selection.format];
    if (!record) {
      throw new Error(
        `Export format ${JSON.stringify(selection.format)} does not exist for value ${JSON.stringify(selection.value)}.`,
      );
    }

    return new BundleExportEntry({
      record,
      selection,
      source: this.#source,
    });
  }

  private scenarioById(id: string): ManifestScenario {
    const scenario = this.#manifest.scenarios.find((candidate) => candidate.id === id);
    if (!scenario) {
      throw new Error(`Export scenario ${JSON.stringify(id)} does not exist.`);
    }
    return scenario;
  }
}

interface ScenarioOptions {
  scenario: ManifestScenario;
  reader: ExportReader;
}

class ExportScenarioReader implements ExportScenario {
  readonly id: string;
  readonly state: ManifestScenario["state"];

  readonly #scenario: ManifestScenario;
  readonly #reader: ExportReader;

  constructor(options: ScenarioOptions) {
    this.id = options.scenario.id;
    this.state = cloneJson(options.scenario.state);
    this.#scenario = options.scenario;
    this.#reader = options.reader;
  }

  values(): string[] {
    return Object.keys(this.#scenario.values);
  }

  formats(value: string): string[] {
    return this.#reader.formats(value);
  }

  get(value: string, format: string): ExportEntry {
    return this.#reader.get({
      scenario: this.id,
      value,
      format,
    });
  }
}

class ArchiveExportReader extends ExportReader implements ExportArchive {
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
  record: FormatRecord;
  selection: FormatSelection;
  source: ExportSource;
}

class BundleExportEntry implements ExportEntry, ExportLoaderContext {
  readonly selection: FormatSelection;
  readonly formatId: string;
  readonly mediaType: string | null;
  readonly metadata: FormatRecord["metadata"];
  readonly raw: ExportEntry["raw"];

  readonly #record: FormatRecord;
  readonly #source: ExportSource;

  constructor(options: HandleOptions) {
    this.selection = options.selection;
    this.formatId = options.record.format_id;
    this.mediaType = options.record.media_type;
    this.metadata = options.record.metadata === null ? null : cloneJson(options.record.metadata);
    this.raw = {
      record: cloneJson(options.record),
    };
    this.#record = options.record;
    this.#source = options.source;
  }

  entry(): ExportFile {
    const selectedKey = this.#record.data.entry;
    if (!selectedKey) {
      throw new Error(`Format ${this.formatId} has no entry file. Pass an explicit file key.`);
    }
    return this.file(selectedKey);
  }

  files(): string[] {
    return Object.keys(this.#record.data.files);
  }

  file(key: string): ExportFile {
    const ref = this.blob(key);
    return new BundleExportFile({
      ref,
      selection: this.selection,
      source: this.#source,
    });
  }

  url(): string {
    return this.entry().url();
  }

  fetch(init?: RequestInit): Promise<Response> {
    return this.entry().fetch(init);
  }

  bytes(): Promise<Uint8Array> {
    return this.entry().bytes();
  }

  text(): Promise<string> {
    return this.entry().text();
  }

  json<T = unknown>(): Promise<T> {
    return this.entry().json<T>();
  }

  private blob(key: string): BlobRef {
    const file = this.#record.data.files[key];
    if (!file) {
      throw new Error(`Format ${this.formatId} has no file named ${JSON.stringify(key)}.`);
    }

    return file;
  }

  async load<T>(loader: ExportLoader<T>): Promise<T> {
    if (!loaderSupports(loader, this.formatId)) {
      throw new Error(
        `Loader does not support format id ${JSON.stringify(
          this.formatId,
        )}. Use .entry().url(), .entry().bytes(), .entry().text(), .entry().json(), or pass a matching loader.`,
      );
    }

    return loader.load(this);
  }
}

interface FileOptions {
  ref: BlobRef;
  selection: FormatSelection;
  source: ExportSource;
}

class BundleExportFile implements ExportFile {
  readonly ref: BlobRef;

  readonly #selection: FormatSelection;
  readonly #source: ExportSource;

  constructor(options: FileOptions) {
    this.ref = options.ref;
    this.#selection = options.selection;
    this.#source = options.source;
  }

  url(): string {
    return this.#source.url(this.ref.href, this.ref.media_type);
  }

  async fetch(init?: RequestInit): Promise<Response> {
    const response = await this.#source.fetch(this.ref.href, this.ref.media_type, init);
    const bytes = new Uint8Array(await response.arrayBuffer());
    await verifyBlobRef(this.ref, bytes);
    return new Response(arrayBuffer(bytes), {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  async bytes(): Promise<Uint8Array> {
    const bytes = await this.#source.bytes(this.ref.href);
    await verifyBlobRef(this.ref, bytes);
    return bytes;
  }

  async text(): Promise<string> {
    return new TextDecoder().decode(await this.bytes());
  }

  async json<T = unknown>(): Promise<T> {
    try {
      return JSON.parse(await this.text()) as T;
    } catch (error) {
      throw new Error(
        `Failed to parse format JSON for ${this.#selection.scenario}/${this.#selection.value}/${this.#selection.format}.`,
        { cause: error },
      );
    }
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

class LocalExportSource implements ExportSource {
  readonly #root: string;
  readonly #readFile: LocalReadFile;
  readonly #url: LocalUrlResolver | undefined;

  constructor(root: string, readFile: LocalReadFile, url?: LocalUrlResolver) {
    this.#root = root.replace(/[/\\]+$/, "");
    this.#readFile = readFile;
    this.#url = url;
  }

  url(href: string, mediaType?: string | null): string {
    const safeHref = safeBundlePath(href, "bundle href");
    const path = this.path(safeHref);
    if (this.#url) {
      return this.#url(safeHref, path, mediaType ?? null);
    }
    return `file://${path}`;
  }

  async fetch(href: string, mediaType?: string | null): Promise<Response> {
    const bytes = await this.bytes(href);
    const init: ResponseInit = mediaType ? { headers: { "Content-Type": mediaType } } : {};
    return new Response(arrayBuffer(bytes), init);
  }

  async bytes(href: string): Promise<Uint8Array> {
    return localBytes(await this.#readFile(this.path(safeBundlePath(href, "bundle href"))));
  }

  async text(href: string): Promise<string> {
    return new TextDecoder().decode(await this.bytes(href));
  }

  async json<T>(href: string, label = "bundle JSON"): Promise<T> {
    try {
      return JSON.parse(await this.text(href)) as T;
    } catch (error) {
      throw new Error(`Failed to parse ${label} from local path ${JSON.stringify(href)}.`, {
        cause: error,
      });
    }
  }

  private path(href: string): string {
    return `${this.#root}/${href}`;
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
        "Archive-backed format URLs require Blob URL support. Use .bytes(), .text(), .json(), or .load(loader) instead.",
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

function loaderSupports(loader: ExportLoader, formatId: string): boolean {
  if (loader.formatId === formatId) {
    return true;
  }
  return loader.formatIds?.includes(formatId) ?? false;
}

function loaderFormats(formatIds: string | readonly string[]): ExportLoaderSelector {
  return typeof formatIds === "string" ? { formatId: formatIds } : { formatIds };
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
  const index = validateExportRootIndex(await source.json(DEFAULT_ROOT_INDEX, "export root index"));
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

  throw new TypeError("readExport requires ArrayBuffer, ArrayBufferView, or Blob bytes.");
}

async function localBytes(input: LocalReadFileResult): Promise<Uint8Array> {
  if (typeof input === "string") {
    return new TextEncoder().encode(input);
  }
  return archiveBytes(input);
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

function arrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

async function verifyBlobRef(ref: BlobRef, bytes: Uint8Array): Promise<void> {
  if (bytes.byteLength !== ref.size) {
    throw new Error(
      `Bundle file ${JSON.stringify(ref.href)} has ${bytes.byteLength} bytes, expected ${ref.size}.`,
    );
  }

  const actual = await sha256Hex(bytes);
  if (actual !== ref.sha256.toLowerCase()) {
    throw new Error(
      `Bundle file ${JSON.stringify(ref.href)} has SHA-256 ${actual}, expected ${ref.sha256}.`,
    );
  }
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error("Format file SHA-256 verification requires Web Crypto.");
  }

  const digest = await subtle.digest("SHA-256", arrayBuffer(bytes));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function fetchJson(fetchImpl: FetchLike, url: string, label: string): Promise<unknown> {
  const response = await fetchImpl(url);

  if (!response.ok) {
    throw new Error(
      `Failed to load ${label} from ${url}: ${response.status} ${response.statusText}`,
    );
  }

  return response.json();
}

function globalFetch(): FetchLike {
  if (typeof globalThis.fetch !== "function") {
    throw new Error("readExport(...) requires fetch. Pass a fetch implementation in the options.");
  }

  return globalThis.fetch.bind(globalThis);
}
