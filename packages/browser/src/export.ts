import { isAbortError } from "./abort.js";
import { decodeBlobAsset } from "./blob-asset.js";
import { sha256Hex, validateNativeFile, verifyBytes } from "./integrity.js";
import { resolveOutputLoader } from "./loader.js";
import { parseMediaType } from "./media-type.js";
import { parseStrictJson, portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";
import { canonicalJson, compareUnicodeScalarStrings, parseExportIndex } from "./schema.js";
import type { ParsedExportIndex, ParsedState } from "./schema.js";
import { fetchBytes, normalizeBase, resolveExportUrl } from "./transport.js";
import type {
  AnyOutputLoader,
  LoadOptions,
  OpenExportOptions,
  OutputCodec,
  OutputDescriptor,
  OutputLoader,
  OutputPayloadMap,
  NotebookExport,
  ExportOutput,
  ExportState,
  VerificationResult,
  VerifyOptions,
} from "./types.js";
import { isNotebookExportError, NotebookExportError } from "./types.js";
import { isCallableValue } from "./value-types.js";

const INDEX_MAX_BYTES = 16 * 1024 * 1024;
const INDEX_MAX_VALUES = 2_000_000;
const DEFAULT_ASSET_MAX_BYTES = 512 * 1024 * 1024;
const HARD_ASSET_MAX_BYTES = 2_147_483_647;
const DEFAULT_VERIFY_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

interface AssetReadOptions {
  readonly maxBytes: number;
  readonly signal?: AbortSignal;
}

interface MutableAssetReadOptions {
  maxBytes: number;
  signal?: AbortSignal;
}

interface SelectedLoadInput {
  descriptor: OutputDescriptor;
  mediaType: ReturnType<typeof parseMediaType>;
  payload: OutputPayloadMap[OutputCodec];
  signal?: AbortSignal;
}

interface SelectedLoader<Result> {
  load(input: SelectedLoadInput): Result | Promise<Result>;
}

interface FetchAssetOptions {
  cache: RequestCache;
  expectedBytes: number;
  maxBytes: number;
  signal?: AbortSignal;
}

export async function openExport(
  base: string | URL,
  options: OpenExportOptions = {},
): Promise<NotebookExport> {
  throwIfAborted(options.signal);
  const normalized = normalizeBase(base);
  const fetcher = options.fetch ?? globalThis.fetch;
  if (!isCallableValue(fetcher)) throw new TypeError("A fetch implementation is required.");
  const bytes = await fetchBytes(
    fetcher,
    resolveExportUrl(normalized, "index.json"),
    "index.json",
    assetReadOptions(INDEX_MAX_BYTES, options.signal),
  );
  const wire = decodeCanonicalIndex(bytes);
  const parsed = parseExportIndex(wire);
  const identity = await sha256Hex(bytes);
  await validateFingerprints(parsed, options.signal);
  return new NotebookExportValue(normalized, identity, parsed, fetcher);
}

function decodeCanonicalIndex(bytes: Uint8Array): JsonValue {
  let value: JsonValue;
  try {
    value = parseStrictJson(decoder.decode(bytes), INDEX_MAX_VALUES);
  } catch (error) {
    throw new NotebookExportError("export_invalid", "Export index must be strict UTF-8 JSON.", {
      cause: error,
    });
  }
  let canonical: Uint8Array;
  try {
    canonical = encoder.encode(canonicalJson(value));
  } catch (error) {
    if (isNotebookExportError(error)) throw error;
    throw new NotebookExportError("export_invalid", "Export index JSON is invalid.", {
      cause: error,
    });
  }
  if (!equalBytes(bytes, canonical)) {
    throw new NotebookExportError("export_noncanonical", "Export index is not canonical JSON.");
  }
  return value;
}

async function validateFingerprints(
  index: ParsedExportIndex,
  signal: AbortSignal | undefined,
): Promise<void> {
  for (const [fingerprint, state] of Object.entries(index.states)) {
    throwIfAborted(signal);
    // Keep hashing bounded when exports contain many states.
    // oxlint-disable-next-line no-await-in-loop
    const actual = await sha256Hex(encoder.encode(canonicalJson(state.inputs)));
    throwIfAborted(signal);
    if (actual !== fingerprint) {
      throw new NotebookExportError(
        "export_invalid",
        `State fingerprint ${fingerprint} does not match its inputs.`,
        { details: { fingerprint } },
      );
    }
  }
}

class NotebookExportValue implements NotebookExport {
  readonly #baseHref: string;
  readonly identity: string;
  readonly specSha256: string;
  readonly defaultState: ExportStateValue;
  readonly notebook: ParsedExportIndex["notebook"];
  readonly producer: ParsedExportIndex["producer"];
  readonly inputNames: readonly string[];
  readonly controlBindings: ParsedExportIndex["controlBindings"];
  readonly outputNames: readonly string[];
  readonly #states: readonly ExportStateValue[];
  readonly #statesByAlias: ReadonlyMap<string, ExportStateValue>;
  readonly #statesByInputs: ReadonlyMap<string, ExportStateValue>;
  readonly #reader: AssetReader;

  constructor(
    base: URL,
    identity: string,
    index: ParsedExportIndex,
    fetcher: typeof globalThis.fetch,
  ) {
    this.#baseHref = base.href;
    this.identity = identity;
    this.specSha256 = index.specSha256;
    this.notebook = index.notebook;
    this.producer = index.producer;
    this.inputNames = index.inputs;
    this.controlBindings = index.controlBindings;
    this.outputNames = index.outputs;
    this.#reader = new AssetReader(this.#baseHref, fetcher);
    const aliasesByFingerprint = new Map<string, string[]>(
      Object.keys(index.states).map((fingerprint) => [fingerprint, []]),
    );
    for (const [alias, fingerprint] of Object.entries(index.aliases).sort(([left], [right]) =>
      compareUnicodeScalarStrings(left, right),
    )) {
      aliasesByFingerprint.get(fingerprint)!.push(alias);
    }
    this.#states = Object.freeze(
      Object.entries(index.states)
        .sort(([left], [right]) => compareUnicodeScalarStrings(left, right))
        .map(
          ([fingerprint, state]) =>
            new ExportStateValue(
              this,
              fingerprint,
              Object.freeze(aliasesByFingerprint.get(fingerprint)!),
              state,
              this.#reader,
            ),
        ),
    );
    const statesByFingerprint = new Map(this.#states.map((state) => [state.fingerprint, state]));
    this.defaultState = statesByFingerprint.get(index.defaultState)!;
    this.#statesByAlias = new Map(
      Object.entries(index.aliases).map(([alias, fingerprint]) => [
        alias,
        statesByFingerprint.get(fingerprint)!,
      ]),
    );
    this.#statesByInputs = new Map(
      this.#states.map((state) => [canonicalJson(state.inputs), state]),
    );
    Object.freeze(this);
  }

  get base(): URL {
    return new URL(this.#baseHref);
  }

  states(): readonly ExportState[] {
    return this.#states;
  }

  state(alias: string): ExportState {
    const state = this.#statesByAlias.get(alias);
    if (state === undefined) {
      throw new NotebookExportError(
        "state_not_found",
        `State alias ${JSON.stringify(alias)} was not found.`,
        {
          details: {
            requested: String(alias).slice(0, 255),
            available: [...this.#statesByAlias.keys()].slice(0, 16),
          },
        },
      );
    }
    return state;
  }

  resolve(inputs: JsonObject): ExportState {
    const normalized = normalizeResolutionObject(inputs, "inputs");
    requireCompleteInputs(normalized, this.inputNames);
    return this.resolveNormalized(normalized);
  }

  resolveNormalized(inputs: JsonObject): ExportStateValue {
    const state = this.#statesByInputs.get(canonicalJson(inputs));
    if (state === undefined) {
      throw new NotebookExportError(
        "state_unavailable",
        "The requested input vector is absent from this export.",
        { details: { fingerprint: "unavailable" } },
      );
    }
    return state;
  }

  async verify(options: VerifyOptions = {}): Promise<VerificationResult> {
    const maxBytes = assetLimit(options.maxBytes);
    const maxTotalBytes = totalLimit(options.maxTotalBytes);
    const assets = uniqueAssets(this.#states);
    const total = assets.reduce((sum, item) => sum + item.descriptor.asset.size, 0);
    if (total > maxTotalBytes) {
      throw new NotebookExportError(
        "read_limit_exceeded",
        "Export assets exceed the verification byte limit.",
        { details: { declaredBytes: total, maxTotalBytes } },
      );
    }
    for (const item of assets) {
      throwIfAborted(options.signal);
      // Verification is sequential so one run does not retain every asset.
      // oxlint-disable-next-line no-await-in-loop
      await this.#reader.payload(item.descriptor, assetReadOptions(maxBytes, options.signal));
    }
    return Object.freeze({
      states: this.#states.length,
      outputs: this.#states.length * this.outputNames.length,
      assets: assets.length,
      bytesVerified: total,
    });
  }
}

class ExportStateValue implements ExportState {
  readonly notebookExport: NotebookExportValue;
  readonly fingerprint: string;
  readonly aliases: readonly string[];
  readonly inputs: JsonObject;
  readonly #outputs: readonly ExportOutputValue[];
  readonly #outputsByName: ReadonlyMap<string, ExportOutputValue>;

  constructor(
    notebookExport: NotebookExportValue,
    fingerprint: string,
    aliases: readonly string[],
    state: ParsedState,
    reader: AssetReader,
  ) {
    this.notebookExport = notebookExport;
    this.fingerprint = fingerprint;
    this.aliases = aliases;
    this.inputs = state.inputs;
    this.#outputs = Object.freeze(
      notebookExport.outputNames.map(
        (outputName) => new ExportOutputValue(this, outputName, state.outputs[outputName]!, reader),
      ),
    );
    this.#outputsByName = new Map(this.#outputs.map((output) => [output.name, output]));
    Object.freeze(this);
  }

  outputs(): readonly ExportOutput[] {
    return this.#outputs;
  }

  output(name: string): ExportOutput {
    const output = this.#outputsByName.get(name);
    if (output === undefined) {
      throw new NotebookExportError(
        "output_not_found",
        `Output ${JSON.stringify(name)} was not found.`,
        {
          details: {
            requested: String(name).slice(0, 255),
            available: this.#outputs.slice(0, 16).map((item) => item.name),
          },
        },
      );
    }
    return output;
  }

  resolve(patch: JsonObject): ExportState {
    const normalized = normalizeResolutionObject(patch, "patch");
    const keys = Object.keys(normalized);
    if (keys.length === 0) return this;
    const allowed = new Set(this.notebookExport.inputNames);
    if (keys.some((key) => !allowed.has(key))) {
      throw new NotebookExportError(
        "state_input_invalid",
        "State patch contains an unknown input name.",
        { details: { keys: keys.slice(0, 16) } },
      );
    }
    const merged = Object.freeze(
      Object.fromEntries(
        this.notebookExport.inputNames.map((name) => [
          name,
          Object.hasOwn(normalized, name) ? normalized[name]! : this.inputs[name]!,
        ]),
      ),
    );
    return this.notebookExport.resolveNormalized(merged);
  }
}

class ExportOutputValue implements ExportOutput {
  readonly state: ExportStateValue;
  readonly name: string;
  readonly codec: OutputCodec;
  readonly mediaType: ReturnType<typeof parseMediaType>;
  readonly descriptor: OutputDescriptor;
  readonly #reader: AssetReader;

  constructor(
    state: ExportStateValue,
    name: string,
    descriptor: OutputDescriptor,
    reader: AssetReader,
  ) {
    this.state = state;
    this.name = name;
    this.codec = descriptor.codec;
    this.mediaType = parseMediaType(descriptor.mediaType);
    this.descriptor = descriptor;
    this.#reader = reader;
    Object.freeze(this);
  }

  async load<C extends OutputCodec, T>(
    loader: OutputLoader<C, T>,
    options: LoadOptions = {},
  ): Promise<T> {
    const selected = resolveOutputLoader(this, [anyOutputLoader(loader)]);
    const payload = await this.#reader.payload(
      this.descriptor,
      assetReadOptions(assetLimit(options.maxBytes), options.signal),
    );
    throwIfAborted(options.signal);
    const call = selectedLoader<T>(selected);
    try {
      const input: SelectedLoadInput = {
        descriptor: this.descriptor,
        mediaType: this.mediaType,
        payload,
      };
      if (options.signal !== undefined) input.signal = options.signal;
      return await waitForLoader(Promise.resolve(call.load(input)), options.signal);
    } catch (error) {
      throwLoaderError(error, options.signal, this);
    }
  }
}

type AssetOutputDescriptor = Exclude<
  OutputDescriptor,
  { readonly codec: "marimo.scalar.v1" | "marimo.json.v1" }
>;

class AssetReader {
  readonly #baseHref: string;
  readonly #fetch: typeof globalThis.fetch;

  constructor(baseHref: string, fetcher: typeof globalThis.fetch) {
    this.#baseHref = baseHref;
    this.#fetch = fetcher;
  }

  async payload(
    descriptor: OutputDescriptor,
    options: AssetReadOptions,
  ): Promise<OutputPayloadMap[OutputCodec]> {
    throwIfAborted(options.signal);
    if (descriptor.codec === "marimo.scalar.v1") return descriptor.value;
    if (descriptor.codec === "marimo.json.v1") return descriptor.value;
    if (descriptor.asset.size > options.maxBytes) {
      throw new NotebookExportError(
        "read_limit_exceeded",
        "Export asset exceeds the caller byte limit.",
        {
          details: {
            declaredBytes: descriptor.asset.size,
            maxBytes: options.maxBytes,
          },
        },
      );
    }
    const path = assetPath(descriptor.codec, descriptor.asset.sha256);
    const fetchOptions: FetchAssetOptions = {
      cache: "force-cache",
      maxBytes: options.maxBytes,
      expectedBytes: descriptor.asset.size,
    };
    if (options.signal !== undefined) fetchOptions.signal = options.signal;
    const bytes = await fetchBytes(
      this.#fetch,
      resolveExportUrl(this.#baseHref, path),
      path,
      fetchOptions,
    );
    throwIfAborted(options.signal);
    await verifyBytes(bytes, descriptor.asset);
    throwIfAborted(options.signal);
    validateNativeFile(descriptor.codec, bytes);
    if (descriptor.codec === "marimo.blob-asset.msgpack.v1") {
      return decodeBlobAsset(bytes, descriptor);
    }
    return bytes;
  }
}

function assetPath(codec: AssetOutputDescriptor["codec"], digest: string): string {
  const extension = (() => {
    if (codec === "marimo.output.v1") return "output.json";
    if (codec === "marimo.cell.v1") return "cell.json";
    if (codec === "numpy.npy.v1") return "npy";
    if (codec === "apache.arrow.file.v1") return "arrow";
    return "bin";
  })();
  return `assets/${digest}.${extension}`;
}

function uniqueAssets(
  states: readonly ExportStateValue[],
): readonly { readonly descriptor: AssetOutputDescriptor }[] {
  const values = new Map<string, { readonly descriptor: AssetOutputDescriptor }>();
  for (const state of states) {
    for (const output of state.outputs()) {
      if (
        output.descriptor.codec === "marimo.scalar.v1" ||
        output.descriptor.codec === "marimo.json.v1"
      ) {
        continue;
      }
      const descriptor = output.descriptor;
      values.set(`${descriptor.codec}\0${descriptor.asset.sha256}`, { descriptor });
    }
  }
  return Object.freeze([...values.values()]);
}

function normalizeResolutionObject<Input>(input: Input, label: string): JsonObject {
  try {
    return portableJsonObject(input, label);
  } catch (error) {
    throw new NotebookExportError("state_input_invalid", `State ${label} is invalid.`, {
      cause: error,
    });
  }
}

function requireCompleteInputs(inputs: JsonObject, names: readonly string[]): void {
  const expected = new Set(names);
  const keys = Object.keys(inputs);
  if (
    keys.length !== names.length ||
    keys.some((key) => !expected.has(key)) ||
    names.some((name) => !Object.hasOwn(inputs, name))
  ) {
    throw new NotebookExportError(
      "state_input_invalid",
      "State inputs must equal the export input name set.",
      { details: { expected: names.slice(0, 16), actual: keys.slice(0, 16) } },
    );
  }
}

function assetLimit(value: number | undefined): number {
  if (value === undefined) return DEFAULT_ASSET_MAX_BYTES;
  if (!Number.isSafeInteger(value) || value <= 0 || value > HARD_ASSET_MAX_BYTES) {
    throw new TypeError(`maxBytes must be an integer from 1 through ${HARD_ASSET_MAX_BYTES}.`);
  }
  return value;
}

function totalLimit(value: number | undefined): number {
  if (value === undefined) return DEFAULT_VERIFY_MAX_BYTES;
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new TypeError("maxTotalBytes must be a non-negative safe integer.");
  }
  return value;
}

async function waitForLoader<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (signal === undefined) return promise;
  throwIfAborted(signal);
  let abort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () =>
      reject(
        new NotebookExportError("abort", "Export output loading was aborted.", {
          cause: signal.reason,
        }),
      );
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([promise, aborted]);
  } finally {
    if (abort !== undefined) signal.removeEventListener("abort", abort);
  }
}

function throwLoaderError(
  cause: unknown,
  signal: AbortSignal | undefined,
  output: ExportOutputValue,
): never {
  if (isNotebookExportError(cause)) throw cause;
  if (signal?.aborted || isAbortError(cause)) {
    throw new NotebookExportError("abort", "Export output loading was aborted.", {
      cause: signal?.aborted ? signal.reason : cause,
    });
  }
  throw new NotebookExportError("decode_failed", "OutputLoader decoding failed.", {
    cause,
    details: {
      output: output.name,
      codec: output.codec,
      mediaType: output.mediaType.raw,
    },
  });
}

function assetReadOptions(maxBytes: number, signal: AbortSignal | undefined): AssetReadOptions {
  const options: MutableAssetReadOptions = { maxBytes };
  if (signal !== undefined) options.signal = signal;
  return options;
}

function selectedLoader<Result>(loader: AnyOutputLoader): SelectedLoader<Result> {
  // SAFETY: resolveOutputLoader matched the loader codec to the output descriptor and payload.
  return loader as SelectedLoader<Result>;
}

function anyOutputLoader<Codec extends OutputCodec, Result>(
  loader: OutputLoader<Codec, Result>,
): AnyOutputLoader {
  // SAFETY: The mapped AnyOutputLoader union contains every OutputCodec specialization.
  return loader as AnyOutputLoader;
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new NotebookExportError("abort", "Export operation was aborted.", {
      cause: signal.reason,
    });
  }
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((byte, index) => byte === right[index]);
}
