import { decodeBlobAsset } from "./blob-asset.js";
import { verifyBytes } from "./integrity.js";
import type { FormatLoader, FormatLoaderContext, JsonDecoder, MountedView } from "./loader.js";
import {
  compareUnicodeScalarStrings,
  isFormatId,
  parseJsonValue,
  parsePublicationManifest,
} from "./schema.js";
import { jsonValueLimit, parseStrictJson, trimJsonWhitespace } from "./strict-json.js";
import type {
  CacheAssetRef,
  ManifestFormat,
  ManifestOutput,
  ManifestVariant,
  PublicationManifest,
} from "./schema.js";
import { enforceLimit, httpSource, readLimit } from "./source.js";
import type { HttpSourceOptions, PublicationSource } from "./source.js";
import type { JsonObject, JsonValue, ReadOptions } from "./types.js";
import { PublicationError } from "./types.js";
import type { DecodedBlobAsset } from "./blob-asset.js";

const DEFAULT_INDEX_MAX_BYTES = 16 * 1024 * 1024;
const DEFAULT_ASSET_MAX_BYTES = 64 * 1024 * 1024;
const MAX_SELECTOR_NAMES = 16;
const MAX_SELECTOR_UTF8_BYTES = 2_048;
const MAX_SELECTOR_MESSAGE_CODE_POINTS = 4_096;

export interface OpenPublicationOptions extends HttpSourceOptions {
  readonly loaders?: readonly FormatLoader[];
  readonly signal?: AbortSignal;
  readonly maxIndexBytes?: number;
  readonly maxAssetBytes?: number;
}

export interface NotebookProvenance {
  readonly filename: string;
  readonly documentSha256: string;
}

export interface ProducerProvenance {
  readonly marimo: string;
  readonly marimoExport: string;
}

export interface Publication {
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  variants(): readonly PublishedVariant[];
  variant(name: string): PublishedVariant;
}

export interface PublishedVariant {
  readonly name: string;
  readonly controls: JsonObject;
  outputs(): readonly PublishedOutput[];
  output(name: string): PublishedOutput;
}

export interface PublishedOutput {
  readonly name: string;
  formats(): readonly PublishedFormat[];
  format(name: string): PublishedFormat;
}

export interface PublishedFormat {
  readonly name: string;
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  filename(options?: ReadOptions): Promise<string | null>;
  bytes(options?: ReadOptions): Promise<Uint8Array>;
  text(options?: ReadOptions): Promise<string>;
  json(options?: ReadOptions): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>, options?: ReadOptions): Promise<T>;
  blob(options?: ReadOptions): Promise<Blob>;
  load<T>(loader: FormatLoader<T>, options?: ReadOptions): Promise<T>;
  mount(element: HTMLElement, options?: ReadOptions): Promise<MountedView>;
}

export async function openPublication(
  root: string | URL,
  options: OpenPublicationOptions = {},
): Promise<Publication> {
  options.signal?.throwIfAborted();
  const indexLimit = openLimit(options.maxIndexBytes, DEFAULT_INDEX_MAX_BYTES, "maxIndexBytes");
  const assetLimit = openLimit(options.maxAssetBytes, DEFAULT_ASSET_MAX_BYTES, "maxAssetBytes");
  const source = httpSource(root, {
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
    ...(options.headers === undefined ? {} : { headers: options.headers }),
  });
  return openPublicationFromSource(source, {
    ...(options.loaders === undefined ? {} : { loaders: options.loaders }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
    indexLimit,
    assetLimit,
  });
}

interface SourceOpenOptions {
  readonly loaders?: readonly FormatLoader[];
  readonly signal?: AbortSignal;
  readonly indexLimit?: number;
  readonly assetLimit?: number;
}

export async function openPublicationFromSource(
  source: PublicationSource,
  options: SourceOpenOptions = {},
): Promise<Publication> {
  options.signal?.throwIfAborted();
  const indexLimit = openLimit(options.indexLimit, DEFAULT_INDEX_MAX_BYTES, "indexLimit");
  const assetLimit = openLimit(options.assetLimit, DEFAULT_ASSET_MAX_BYTES, "assetLimit");
  const loaders = loaderRegistry(options.loaders ?? []);
  const bytes = new Uint8Array(
    await source.read("index.json", {
      ...(options.signal === undefined ? {} : { signal: options.signal }),
      maxBytes: indexLimit,
    }),
  );
  options.signal?.throwIfAborted();
  let input: unknown;
  try {
    input = parseStrictJson(decodeUtf8(trimJsonWhitespace(bytes)));
  } catch (error) {
    throw new PublicationError(
      "publication_invalid",
      "Publication index must contain UTF-8 JSON.",
      { cause: error },
    );
  }
  const manifest = parsePublicationManifest(input);
  const reader = new AssetReader(source, assetLimit);
  return new PublicationValue(manifest, reader, loaders);
}

class PublicationValue implements Publication {
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly #variants: readonly PublishedVariantValue[];
  readonly #byName: ReadonlyMap<string, PublishedVariantValue>;

  constructor(
    manifest: PublicationManifest,
    reader: AssetReader,
    loaders: ReadonlyMap<string, FormatLoader>,
  ) {
    this.notebook = Object.freeze({
      filename: manifest.notebook.filename,
      documentSha256: manifest.notebook.document_sha256,
    });
    this.producer = Object.freeze({
      marimo: manifest.producer.marimo,
      marimoExport: manifest.producer.marimo_export,
    });
    this.#variants = Object.freeze(
      sortedEntries(manifest.variants).map(
        ([name, variant]) => new PublishedVariantValue(name, variant, reader, loaders),
      ),
    );
    this.#byName = new Map(this.#variants.map((variant) => [variant.name, variant]));
    Object.freeze(this);
  }

  variants(): readonly PublishedVariant[] {
    return this.#variants;
  }

  variant(name: string): PublishedVariant {
    const variant = this.#byName.get(name);
    if (variant === undefined) {
      throw missing("variant", name, this.#byName.keys());
    }
    return variant;
  }
}

function openLimit(input: number | undefined, fallback: number, name: string): number {
  const limit = readLimit(input) ?? fallback;
  if (limit === 0) throw new TypeError(`${name} must be a positive safe integer.`);
  return limit;
}

class PublishedVariantValue implements PublishedVariant {
  readonly name: string;
  readonly controls: JsonObject;
  readonly #outputs: readonly PublishedOutputValue[];
  readonly #byName: ReadonlyMap<string, PublishedOutputValue>;

  constructor(
    name: string,
    variant: ManifestVariant,
    reader: AssetReader,
    loaders: ReadonlyMap<string, FormatLoader>,
  ) {
    this.name = name;
    this.controls = variant.controls;
    this.#outputs = Object.freeze(
      sortedEntries(variant.outputs).map(
        ([outputName, output]) => new PublishedOutputValue(outputName, output, reader, loaders),
      ),
    );
    this.#byName = new Map(this.#outputs.map((output) => [output.name, output]));
    Object.freeze(this);
  }

  outputs(): readonly PublishedOutput[] {
    return this.#outputs;
  }

  output(name: string): PublishedOutput {
    const output = this.#byName.get(name);
    if (output === undefined) throw missing("output", name, this.#byName.keys());
    return output;
  }
}

class PublishedOutputValue implements PublishedOutput {
  readonly name: string;
  readonly #formats: readonly PublishedFormatValue[];
  readonly #byName: ReadonlyMap<string, PublishedFormatValue>;

  constructor(
    name: string,
    output: ManifestOutput,
    reader: AssetReader,
    loaders: ReadonlyMap<string, FormatLoader>,
  ) {
    this.name = name;
    this.#formats = Object.freeze(
      sortedEntries(output.formats).map(
        ([formatName, format]) =>
          new PublishedFormatValue(formatName, format, reader, loaders.get(format.format_id)),
      ),
    );
    this.#byName = new Map(this.#formats.map((format) => [format.name, format]));
    Object.freeze(this);
  }

  formats(): readonly PublishedFormat[] {
    return this.#formats;
  }

  format(name: string): PublishedFormat {
    const format = this.#byName.get(name);
    if (format === undefined) throw missing("format", name, this.#byName.keys());
    return format;
  }
}

class PublishedFormatValue implements PublishedFormat {
  readonly name: string;
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly #manifest: ManifestFormat;
  readonly #reader: AssetReader;
  readonly #registeredLoader: FormatLoader | undefined;

  constructor(
    name: string,
    manifest: ManifestFormat,
    reader: AssetReader,
    registeredLoader: FormatLoader | undefined,
  ) {
    this.name = name;
    this.formatId = manifest.format_id;
    this.mediaType = manifest.media_type;
    this.metadata = manifest.metadata;
    this.#manifest = manifest;
    this.#reader = reader;
    this.#registeredLoader = registeredLoader;
    Object.freeze(this);
  }

  async bytes(options: ReadOptions = {}): Promise<Uint8Array> {
    return (await this.#asset(options)).data;
  }

  async filename(options: ReadOptions = {}): Promise<string | null> {
    return (await this.#asset(options)).filename;
  }

  async text(options: ReadOptions = {}): Promise<string> {
    const bytes = await this.bytes(options);
    try {
      return decodeText(bytes, this.mediaType);
    } catch (error) {
      options.signal?.throwIfAborted();
      throw new PublicationError(
        "decode_failed",
        `Format ${JSON.stringify(this.name)} cannot be decoded using its media type charset.`,
        { cause: error },
      );
    }
  }

  json(options?: ReadOptions): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>, options?: ReadOptions): Promise<T>;
  async json<T>(
    decodeOrOptions: JsonDecoder<T> | ReadOptions = {},
    decoderOptions: ReadOptions = {},
  ): Promise<T | JsonValue> {
    const options = typeof decodeOrOptions === "function" ? decoderOptions : decodeOrOptions;
    options.signal?.throwIfAborted();
    const maximumValues = jsonValueLimit(options.maxJsonValues);
    const bytes = await this.bytes(options);
    let input: unknown;
    try {
      input = parseStrictJson(decodeUtf8(trimJsonWhitespace(bytes)), maximumValues);
    } catch (error) {
      throw new PublicationError(
        "decode_failed",
        `Format ${JSON.stringify(this.name)} does not contain valid JSON.`,
        { cause: error },
      );
    }
    let value: JsonValue;
    try {
      value = parseJsonValue(input, `format ${JSON.stringify(this.name)}`);
    } catch (error) {
      throw new PublicationError(
        "decode_failed",
        `Format ${JSON.stringify(this.name)} contains values outside the JSON contract.`,
        { cause: error },
      );
    }
    if (typeof decodeOrOptions !== "function") return value;
    const decoded = decodeOrOptions(value);
    options.signal?.throwIfAborted();
    return decoded;
  }

  async blob(options: ReadOptions = {}): Promise<Blob> {
    const bytes = await this.bytes(options);
    return new Blob([bytes.buffer as ArrayBuffer], { type: this.mediaType });
  }

  async load<T>(loader: FormatLoader<T>, options: ReadOptions = {}): Promise<T> {
    if (loader.formatId !== this.formatId) {
      throw new PublicationError(
        "loader_unavailable",
        `Loader ${JSON.stringify(loader.formatId)} cannot read ${JSON.stringify(this.formatId)}.`,
        { details: { loaderFormatId: loader.formatId, formatId: this.formatId } },
      );
    }
    options.signal?.throwIfAborted();
    jsonValueLimit(options.maxJsonValues);
    const asset = await this.#asset(options);
    options.signal?.throwIfAborted();
    const loading = Promise.resolve().then(() => loader.load(new LoaderContext(asset, options)));
    const value = await waitForAbort(loading, options.signal);
    options.signal?.throwIfAborted();
    return value;
  }

  async mount(element: HTMLElement, options: ReadOptions = {}): Promise<MountedView> {
    const loader = this.#registeredLoader;
    if (loader?.mount === undefined) {
      throw new PublicationError(
        "loader_unavailable",
        `No mounting loader is registered for ${JSON.stringify(this.formatId)}.`,
        { details: { formatId: this.formatId } },
      );
    }
    options.signal?.throwIfAborted();
    jsonValueLimit(options.maxJsonValues);
    const asset = await this.#asset(options);
    options.signal?.throwIfAborted();
    const mounting = Promise.resolve().then(() =>
      loader.mount!(new LoaderContext(asset, options), element),
    );
    const mounted = await waitForAbort(mounting, options.signal, disposeLateMount);
    if (!isMountedView(mounted)) {
      options.signal?.throwIfAborted();
      throw new PublicationError(
        "loader_unavailable",
        `Loader ${JSON.stringify(loader.formatId)} returned an invalid mounted view.`,
      );
    }
    if (options.signal?.aborted === true) {
      try {
        void Promise.resolve(mounted.dispose()).catch(() => undefined);
      } catch {
        // Cancellation remains authoritative when mounted cleanup also fails.
      }
      options.signal.throwIfAborted();
    }
    return mounted;
  }

  async #asset(options: ReadOptions): Promise<DecodedBlobAsset> {
    const maxBytes = readLimit(options.maxBytes);
    const asset = await this.#reader.read(this.#manifest, options.signal);
    enforceLimit(asset.data.byteLength, maxBytes, this.#manifest.asset.key);
    return asset;
  }
}

async function waitForAbort<T>(
  task: Promise<T>,
  signal: AbortSignal | undefined,
  afterAbort?: (task: Promise<T>) => void,
): Promise<T> {
  if (signal === undefined) return task;
  signal.throwIfAborted();
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => {
      try {
        signal.throwIfAborted();
      } catch (error) {
        reject(error);
      }
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    const value = await Promise.race([task, aborted]);
    signal.throwIfAborted();
    return value;
  } catch (error) {
    if (signal.aborted) afterAbort?.(task);
    throw error;
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
  }
}

function disposeLateMount(mounting: Promise<unknown>): void {
  void mounting.then(
    async (mounted) => {
      try {
        if (!isMountedView(mounted)) return;
        await mounted.dispose();
      } catch (error) {
        console.error("Mounted view cleanup failed after cancellation.", error);
      }
    },
    () => undefined,
  );
}

function isMountedView(value: unknown): value is MountedView {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { readonly dispose?: unknown }).dispose === "function"
  );
}

class LoaderContext implements FormatLoaderContext {
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly filename: string | null;
  readonly size: number;
  readonly signal: AbortSignal | undefined;
  readonly #data: Uint8Array;
  readonly #maxJsonValues: number;

  constructor(asset: DecodedBlobAsset, options: ReadOptions) {
    this.formatId = asset.formatId;
    this.mediaType = asset.mediaType;
    this.metadata = asset.metadata;
    this.filename = asset.filename;
    this.size = asset.data.byteLength;
    this.signal = options.signal;
    this.#data = asset.data;
    this.#maxJsonValues = jsonValueLimit(options.maxJsonValues);
    Object.freeze(this);
  }

  async bytes(): Promise<Uint8Array> {
    this.signal?.throwIfAborted();
    return new Uint8Array(this.#data);
  }

  async text(): Promise<string> {
    try {
      return decodeText(await this.bytes(), this.mediaType);
    } catch (error) {
      this.signal?.throwIfAborted();
      throw new PublicationError(
        "decode_failed",
        "Projection data cannot be decoded using its media type charset.",
        { cause: error },
      );
    }
  }

  json(): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>): Promise<T>;
  async json<T>(decode?: JsonDecoder<T>): Promise<T | JsonValue> {
    let input: unknown;
    try {
      input = parseStrictJson(
        decodeUtf8(trimJsonWhitespace(await this.bytes())),
        this.#maxJsonValues,
      );
    } catch (error) {
      if (error instanceof PublicationError) throw error;
      throw new PublicationError("decode_failed", "Projection data is not valid JSON.", {
        cause: error,
      });
    }
    let value: JsonValue;
    try {
      value = parseJsonValue(input, "projection data");
    } catch (error) {
      throw new PublicationError(
        "decode_failed",
        "Projection data contains values outside the JSON contract.",
        { cause: error },
      );
    }
    return decode === undefined ? value : decode(value);
  }

  async blob(): Promise<Blob> {
    const bytes = await this.bytes();
    const buffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer;
    return new Blob([buffer], { type: this.mediaType });
  }
}

class AssetReader {
  readonly #source: PublicationSource;
  readonly #maxAssetBytes: number;
  readonly #pending = new Map<string, PendingAsset>();

  constructor(source: PublicationSource, maxAssetBytes: number) {
    this.#source = source;
    this.#maxAssetBytes = maxAssetBytes;
  }

  async read(format: ManifestFormat, signal?: AbortSignal): Promise<DecodedBlobAsset> {
    signal?.throwIfAborted();
    enforceLimit(format.asset.size, this.#maxAssetBytes, format.asset.key);
    const identity = assetIdentity(format.asset);
    let pending = this.#pending.get(identity);
    if (pending === undefined) {
      const controller = new AbortController();
      pending = {
        controller,
        consumers: 0,
        settled: false,
        promise: this.#read(format, controller.signal),
      };
      this.#pending.set(identity, pending);
      const settle = () => {
        pending!.settled = true;
        if (this.#pending.get(identity) === pending) this.#pending.delete(identity);
      };
      void pending.promise.then(settle, settle);
    }
    pending.consumers += 1;
    try {
      return cloneAsset(await waitForAbort(pending.promise, signal));
    } finally {
      pending.consumers -= 1;
      if (pending.consumers === 0 && !pending.settled) {
        if (this.#pending.get(identity) === pending) this.#pending.delete(identity);
        pending.controller.abort(signal?.reason);
      }
    }
  }

  async #read(format: ManifestFormat, signal?: AbortSignal): Promise<DecodedBlobAsset> {
    const ref = format.asset;
    const envelope = await this.#source.read(`cache/${ref.key}`, {
      ...(signal === undefined ? {} : { signal }),
      maxBytes: ref.size,
    });
    signal?.throwIfAborted();
    await verifyBytes(envelope, ref, `Cache asset ${JSON.stringify(ref.key)}`);
    signal?.throwIfAborted();
    return decodeBlobAsset(envelope, format);
  }
}

interface PendingAsset {
  readonly controller: AbortController;
  readonly promise: Promise<DecodedBlobAsset>;
  consumers: number;
  settled: boolean;
}

function loaderRegistry(loaders: readonly FormatLoader[]): ReadonlyMap<string, FormatLoader> {
  const registry = new Map<string, FormatLoader>();
  for (const loader of loaders) {
    if (
      typeof loader !== "object" ||
      loader === null ||
      !isFormatId(loader.formatId) ||
      typeof loader.load !== "function" ||
      (loader.mount !== undefined && typeof loader.mount !== "function")
    ) {
      throw new TypeError(
        "Each format loader must define a formatId, load function, and optional mount function.",
      );
    }
    if (registry.has(loader.formatId)) {
      throw new TypeError(`A loader is already registered for ${JSON.stringify(loader.formatId)}.`);
    }
    registry.set(
      loader.formatId,
      Object.freeze({
        formatId: loader.formatId,
        load: loader.load.bind(loader),
        ...(loader.mount === undefined ? {} : { mount: loader.mount.bind(loader) }),
      }),
    );
  }
  return registry;
}

function assetIdentity(ref: CacheAssetRef): string {
  return `${ref.key}\0${ref.sha256}\0${ref.size}`;
}

function cloneAsset(asset: DecodedBlobAsset): DecodedBlobAsset {
  return Object.freeze({ ...asset, data: new Uint8Array(asset.data) });
}

function sortedEntries<T>(value: Readonly<Record<string, T>>): [string, T][] {
  return Object.entries(value).sort(([left], [right]) => compareUnicodeScalarStrings(left, right));
}

function missing(kind: string, name: string, available: Iterable<string>): PublicationError {
  const requested = utf8Prefix(name, MAX_SELECTOR_UTF8_BYTES);
  const summary = summarizeAvailable(available);
  const renderedNames = summary.names.map(quoteSelectorName).join(", ");
  const omitted = summary.count - summary.names.length;
  const renderedAvailable =
    summary.count === 0
      ? "none"
      : `${renderedNames.length === 0 ? "..." : renderedNames}${
          omitted === 0 ? "" : `, ... (+${omitted} more)`
        }`;
  const message = truncateCodePoints(
    `${title(kind)} ${quoteSelectorName(requested.value)} is missing. Available ${kind}s: ${renderedAvailable}.`,
    MAX_SELECTOR_MESSAGE_CODE_POINTS,
  );
  return new PublicationError("not_found", message, {
    details: {
      kind,
      name: requested.value,
      name_truncated: requested.truncated,
      available: summary.names,
      available_count: summary.count,
      available_truncated: summary.names.length < summary.count,
    },
  });
}

function summarizeAvailable(available: Iterable<string>): {
  readonly names: readonly string[];
  readonly count: number;
} {
  const candidates: string[] = [];
  let count = 0;
  for (const name of available) {
    count += 1;
    candidates.push(name);
    candidates.sort(compareUnicodeScalarStrings);
    if (candidates.length > MAX_SELECTOR_NAMES) candidates.pop();
  }

  const names: string[] = [];
  let bytes = 0;
  for (const name of candidates) {
    const size = utf8ByteLength(name);
    if (bytes + size > MAX_SELECTOR_UTF8_BYTES) break;
    names.push(name);
    bytes += size;
  }
  return { names: Object.freeze(names), count };
}

function utf8Prefix(
  value: string,
  maxBytes: number,
): {
  readonly value: string;
  readonly truncated: boolean;
} {
  let prefix = "";
  let bytes = 0;
  let consumedCodeUnits = 0;
  for (const scalar of value) {
    const size = utf8ByteLength(scalar);
    if (bytes + size > maxBytes) break;
    prefix += scalar;
    bytes += size;
    consumedCodeUnits += scalar.length;
  }
  return { value: prefix, truncated: consumedCodeUnits < value.length };
}

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (const scalar of value) {
    const codePoint = scalar.codePointAt(0)!;
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

function quoteSelectorName(value: string): string {
  return JSON.stringify(value).replace(
    /[\u007f-\u009f]/gu,
    (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

function truncateCodePoints(value: string, maximum: number): string {
  let prefix = "";
  let count = 0;
  for (const scalar of value) {
    if (count >= maximum - 3) return `${prefix}...`;
    prefix += scalar;
    count += 1;
  }
  return prefix;
}

function title(value: string): string {
  return `${value[0]!.toUpperCase()}${value.slice(1)}`;
}

function decodeText(bytes: Uint8Array, mediaType: string): string {
  const encoding = mediaCharset(mediaType);
  if (encoding !== undefined && encoding.toLowerCase() !== "utf-8") {
    throw new TypeError("Portable publication text must use UTF-8.");
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function decodeUtf8(bytes: Uint8Array): string {
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
}

function mediaCharset(mediaType: string): string | undefined {
  for (const parameter of mediaType.split(";").slice(1)) {
    const separator = parameter.indexOf("=");
    if (separator < 0 || parameter.slice(0, separator).trim().toLowerCase() !== "charset") continue;
    const value = parameter.slice(separator + 1).trim();
    if (value.length === 0) throw new TypeError("The charset parameter must declare UTF-8.");
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      const unquoted = value.slice(1, -1);
      if (unquoted.length === 0) throw new TypeError("The charset parameter must declare UTF-8.");
      return unquoted;
    }
    return value;
  }
  return undefined;
}
