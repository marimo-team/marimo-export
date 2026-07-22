import { sha256Hex, verifyBytes } from "./hash.js";
import type { JsonDecoder, OutputLoader, OutputLoaderContext } from "./loader.js";
import { canonicalJson, parseExportManifest, parseExportRef, parseJsonObject } from "./schema.js";
import type {
  ExportRef,
  ExportSource,
  JsonObject,
  JsonValue,
  NotebookProvenance,
  PayloadRef,
  ProducerInfo,
  ReadOptions,
} from "./types.js";
import { MarimoExportError } from "./types.js";
import type { ExportManifest, ManifestProjection, ManifestScenario } from "./schema.js";

export interface OpenExportOptions extends ReadOptions {
  readonly ref?: ExportRef;
}

export interface NotebookExport {
  readonly ref: ExportRef;
  readonly notebook: NotebookProvenance;
  readonly planSha256: string;
  readonly producer: ProducerInfo;
  scenarios(): readonly ExportScenario[];
  scenario(id: string): ExportScenario;
  resolve(inputs: JsonObject): ExportScenario;
}

export interface ExportScenario {
  readonly id: string;
  readonly inputs: JsonObject;
  outputs(): readonly ExportOutput[];
  output(name: string, formatName?: string): ExportOutput;
}

export interface ExportOutput {
  readonly name: string;
  readonly formatName: string;
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly ref: PayloadRef;
  bytes(options?: ReadOptions): Promise<Uint8Array>;
  text(options?: ReadOptions): Promise<string>;
  json(options?: ReadOptions): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>, options?: ReadOptions): Promise<T>;
  blob(options?: ReadOptions): Promise<Blob>;
  load<T>(loader: OutputLoader<T>, options?: ReadOptions): Promise<T>;
}

export interface ExportSnapshot {
  readonly indexBytes: Uint8Array;
  readonly payloads: readonly PayloadRef[];
}

interface StoredSnapshot {
  readonly indexBytes: Uint8Array;
  readonly payloads: readonly PayloadRef[];
}

const snapshots = new WeakMap<NotebookExport, StoredSnapshot>();
const DEFAULT_INDEX_MAX_BYTES = 16 * 1024 * 1024;

export async function openExport(
  source: ExportSource,
  options: OpenExportOptions = {},
): Promise<NotebookExport> {
  options.signal?.throwIfAborted();
  const ref = options.ref === undefined ? undefined : parseExportRef(options.ref);
  const requestedLimit = readLimit(options.maxBytes);
  if (ref !== undefined && requestedLimit !== undefined && ref.size > requestedLimit) {
    throw tooLarge("index.json", ref.size, requestedLimit);
  }
  const indexLimit = ref?.size ?? requestedLimit ?? DEFAULT_INDEX_MAX_BYTES;
  const indexBytes = new Uint8Array(
    await source.read("index.json", {
      ...(options.signal === undefined ? {} : { signal: options.signal }),
      maxBytes: indexLimit,
    }),
  );
  options.signal?.throwIfAborted();
  if (indexBytes.byteLength > indexLimit) {
    throw tooLarge("index.json", indexBytes.byteLength, indexLimit);
  }
  if (ref !== undefined) await verifyBytes(indexBytes, ref, "Export index");
  options.signal?.throwIfAborted();
  const digest = await sha256Hex(indexBytes);
  options.signal?.throwIfAborted();

  let input: unknown;
  try {
    input = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(indexBytes));
  } catch (error) {
    throw new MarimoExportError("invalid_index", "Export index is not valid UTF-8 JSON.", {
      cause: error,
    });
  }
  const manifest = parseExportManifest(input);
  const reader = new PayloadReader(source);
  const published = new NotebookExportValue(
    Object.freeze({
      key: `marimo-export/indexes/${digest}.json`,
      sha256: digest,
      size: indexBytes.byteLength,
    }),
    manifest,
    reader,
  );
  const payloads = collectPayloads(manifest);
  snapshots.set(published, {
    indexBytes: new Uint8Array(indexBytes),
    payloads,
  });
  return published;
}

// Node transfer reads verified index bytes through this internal module boundary.
export function snapshotExport(published: NotebookExport): ExportSnapshot {
  const snapshot = snapshots.get(published);
  if (snapshot === undefined) {
    throw new TypeError("snapshotExport requires a NotebookExport returned by openExport.");
  }
  return Object.freeze({
    indexBytes: new Uint8Array(snapshot.indexBytes),
    payloads: snapshot.payloads,
  });
}

class NotebookExportValue implements NotebookExport {
  readonly ref: ExportRef;
  readonly notebook: NotebookProvenance;
  readonly planSha256: string;
  readonly producer: ProducerInfo;
  readonly #scenarios: readonly ExportScenarioValue[];
  readonly #byId: ReadonlyMap<string, ExportScenarioValue>;
  readonly #byInputs: ReadonlyMap<string, ExportScenarioValue>;

  constructor(ref: ExportRef, manifest: ExportManifest, reader: PayloadReader) {
    this.ref = ref;
    this.notebook = Object.freeze({
      name: manifest.notebook.name,
      sourceSha256: manifest.notebook.source_sha256,
    });
    this.planSha256 = manifest.plan_sha256;
    this.producer = Object.freeze({
      marimoVersion: manifest.producer.marimo_version,
      marimoExportVersion: manifest.producer.marimo_export_version,
    });
    this.#scenarios = Object.freeze(
      manifest.scenarios.map((scenario) => new ExportScenarioValue(scenario, reader)),
    );
    this.#byId = new Map(this.#scenarios.map((scenario) => [scenario.id, scenario]));
    this.#byInputs = new Map(
      this.#scenarios.map((scenario) => [canonicalJson(scenario.inputs), scenario]),
    );
    Object.freeze(this);
  }

  scenarios(): readonly ExportScenario[] {
    return this.#scenarios;
  }

  scenario(id: string): ExportScenario {
    const scenario = this.#byId.get(id);
    if (scenario === undefined) {
      const available = [...this.#byId.keys()];
      throw new MarimoExportError(
        "missing_scenario",
        `Scenario ${JSON.stringify(id)} is missing. Available scenarios: ${joinQuoted(available)}.`,
        { details: { scenario: id, available } },
      );
    }
    return scenario;
  }

  resolve(inputs: JsonObject): ExportScenario {
    let parsed: JsonObject;
    try {
      parsed = parseJsonObject(inputs, "inputs");
    } catch (error) {
      if (error instanceof MarimoExportError) {
        throw new MarimoExportError("invalid_request", error.message, { cause: error });
      }
      throw error;
    }
    const scenario = this.#byInputs.get(canonicalJson(parsed));
    if (scenario === undefined) {
      throw new MarimoExportError(
        "missing_scenario",
        `No scenario matches inputs ${canonicalJson(parsed)}.`,
        { details: { inputs: parsed, available: this.#scenarios.map((item) => item.id) } },
      );
    }
    return scenario;
  }
}

class ExportScenarioValue implements ExportScenario {
  readonly id: string;
  readonly inputs: JsonObject;
  readonly #outputs: readonly ExportOutputValue[];
  readonly #byName: ReadonlyMap<string, readonly ExportOutputValue[]>;

  constructor(scenario: ManifestScenario, reader: PayloadReader) {
    this.id = scenario.id;
    this.inputs = scenario.inputs;
    const outputs: ExportOutputValue[] = [];
    const byName = new Map<string, ExportOutputValue[]>();
    for (const [name, formats] of Object.entries(scenario.outputs)) {
      const entries = Object.entries(formats).map(
        ([format, projection]) => new ExportOutputValue(name, format, projection, reader),
      );
      outputs.push(...entries);
      byName.set(name, entries);
    }
    this.#outputs = Object.freeze(outputs);
    this.#byName = new Map([...byName].map(([name, entries]) => [name, Object.freeze(entries)]));
    Object.freeze(this);
  }

  outputs(): readonly ExportOutput[] {
    return this.#outputs;
  }

  output(name: string, formatName?: string): ExportOutput {
    const formats = this.#byName.get(name);
    if (formats === undefined) {
      const available = [...this.#byName.keys()];
      throw new MarimoExportError(
        "missing_output",
        `Output ${JSON.stringify(name)} is missing from scenario ${JSON.stringify(this.id)}. Available outputs: ${joinQuoted(available)}.`,
        { details: { scenario: this.id, output: name, available } },
      );
    }
    if (formatName === undefined) {
      if (formats.length === 1) return formats[0]!;
      const available = formats.map((item) => item.formatName);
      throw new MarimoExportError(
        "ambiguous_format",
        `Output ${JSON.stringify(name)} has multiple formats. Select one of: ${joinQuoted(available)}.`,
        { details: { scenario: this.id, output: name, available } },
      );
    }
    const output = formats.find((candidate) => candidate.formatName === formatName);
    if (output === undefined) {
      const available = formats.map((item) => item.formatName);
      throw new MarimoExportError(
        "missing_format",
        `Format ${JSON.stringify(formatName)} is missing from output ${JSON.stringify(name)}. Available formats: ${joinQuoted(available)}.`,
        { details: { scenario: this.id, output: name, format: formatName, available } },
      );
    }
    return output;
  }
}

class ExportOutputValue implements ExportOutput {
  readonly name: string;
  readonly formatName: string;
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly ref: PayloadRef;
  readonly #reader: PayloadReader;
  readonly #loaderContext: OutputLoaderContext;

  constructor(
    name: string,
    formatName: string,
    projection: ManifestProjection,
    reader: PayloadReader,
  ) {
    this.name = name;
    this.formatName = formatName;
    this.formatId = projection.format_id;
    this.mediaType = projection.media_type;
    this.metadata = projection.metadata;
    this.ref = projection.payload;
    this.#reader = reader;
    this.#loaderContext = new LoaderContext(this);
    Object.freeze(this);
  }

  bytes(options: ReadOptions = {}): Promise<Uint8Array> {
    return this.#reader.read(this.ref, options);
  }

  async text(options: ReadOptions = {}): Promise<string> {
    const bytes = await this.bytes(options);
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (error) {
      if (options.signal?.aborted === true) throw error;
      throw new MarimoExportError(
        "decode_failed",
        `Output ${JSON.stringify(this.name)} is not valid UTF-8 text.`,
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
    const options: ReadOptions =
      typeof decodeOrOptions === "function" ? decoderOptions : decodeOrOptions;
    const text = await this.text(options);
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch (error) {
      if (options.signal?.aborted === true) throw error;
      throw new MarimoExportError(
        "decode_failed",
        `Output ${JSON.stringify(this.name)} is not valid JSON.`,
        { cause: error },
      );
    }
    return typeof decodeOrOptions === "function" ? decodeOrOptions(value) : (value as JsonValue);
  }

  async blob(options: ReadOptions = {}): Promise<Blob> {
    const bytes = await this.bytes(options);
    const buffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer;
    return new Blob([buffer], { type: this.mediaType });
  }

  async load<T>(loader: OutputLoader<T>, options: ReadOptions = {}): Promise<T> {
    if (loader.formatId !== this.formatId) {
      throw new MarimoExportError(
        "unsupported_format",
        `Loader ${JSON.stringify(loader.formatId)} cannot read ${JSON.stringify(this.formatId)}.`,
      );
    }
    options.signal?.throwIfAborted();
    const context =
      options.signal === undefined && options.maxBytes === undefined
        ? this.#loaderContext
        : new LoaderContext(this, options);
    const value = await loader.load(context);
    options.signal?.throwIfAborted();
    return value;
  }
}

class LoaderContext implements OutputLoaderContext {
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly size: number;
  readonly signal: AbortSignal | undefined;
  readonly #output: ExportOutputValue;
  readonly #defaults: ReadOptions;

  constructor(output: ExportOutputValue, defaults: ReadOptions = {}) {
    this.#output = output;
    this.#defaults = Object.freeze({ ...defaults });
    this.formatId = output.formatId;
    this.mediaType = output.mediaType;
    this.metadata = output.metadata;
    this.size = output.ref.size;
    this.signal = defaults.signal;
    Object.freeze(this);
  }

  bytes(): Promise<Uint8Array> {
    return this.#output.bytes(this.#defaults);
  }

  text(): Promise<string> {
    return this.#output.text(this.#defaults);
  }

  json(): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>): Promise<T>;
  json<T>(decode?: JsonDecoder<T>): Promise<T | JsonValue> {
    return decode === undefined
      ? this.#output.json(this.#defaults)
      : this.#output.json(decode, this.#defaults);
  }
}

class PayloadReader {
  readonly #source: ExportSource;
  readonly #pending = new Map<string, Promise<Uint8Array>>();

  constructor(source: ExportSource) {
    this.#source = source;
  }

  async read(ref: PayloadRef, options: ReadOptions): Promise<Uint8Array> {
    options.signal?.throwIfAborted();
    const requestedLimit = readLimit(options.maxBytes);
    if (requestedLimit !== undefined && ref.size > requestedLimit) {
      throw tooLarge(ref.key, ref.size, requestedLimit);
    }
    if (options.signal !== undefined) {
      return new Uint8Array(await this.#readVerified(ref, options));
    }
    const identity = `${ref.key}\0${ref.sha256}\0${ref.size}`;
    let pending = this.#pending.get(identity);
    if (pending === undefined) {
      pending = this.#readVerified(ref, {});
      this.#pending.set(identity, pending);
      const evict = () => {
        if (this.#pending.get(identity) === pending) this.#pending.delete(identity);
      };
      void pending.then(evict, evict);
    }
    const bytes = await pending;
    return new Uint8Array(bytes);
  }

  async #readVerified(ref: PayloadRef, options: ReadOptions): Promise<Uint8Array> {
    const bytes = new Uint8Array(
      await this.#source.read(`cache/${ref.key}`, { ...options, maxBytes: ref.size }),
    );
    options.signal?.throwIfAborted();
    await verifyBytes(bytes, ref, `Payload ${JSON.stringify(ref.key)}`);
    options.signal?.throwIfAborted();
    return new Uint8Array(bytes);
  }
}

function collectPayloads(manifest: ExportManifest): readonly PayloadRef[] {
  const byKey = new Map<string, PayloadRef>();
  for (const scenario of manifest.scenarios) {
    for (const formats of Object.values(scenario.outputs)) {
      for (const projection of Object.values(formats)) {
        byKey.set(projection.payload.key, projection.payload);
      }
    }
  }
  return Object.freeze([...byKey.values()]);
}

function readLimit(input: number | undefined): number | undefined {
  if (input === undefined) return undefined;
  if (!Number.isSafeInteger(input) || input < 0) {
    throw new TypeError("maxBytes must be a non-negative safe integer.");
  }
  return input;
}

function tooLarge(path: string, size: number, maxBytes: number): MarimoExportError {
  return new MarimoExportError(
    "output_too_large",
    `Export object ${JSON.stringify(path)} declares ${size} bytes, above the ${maxBytes} byte read limit.`,
    { details: { path, maxBytes, declaredBytes: size } },
  );
}

function joinQuoted(values: Iterable<string>): string {
  return [...values].map((value) => JSON.stringify(value)).join(", ");
}
