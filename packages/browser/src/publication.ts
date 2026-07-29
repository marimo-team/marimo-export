import { decodeBlobAsset } from "./blob-asset.js";
import { sha256Hex, validateNativeFile, verifyBytes } from "./integrity.js";
import { resolveOutputLoader } from "./loader.js";
import { parseMediaType } from "./media-type.js";
import {
  canonicalJson,
  compareUnicodeScalarStrings,
  parsePublicationIndex,
  portableJsonObject,
} from "./schema.js";
import type { ParsedPublicationIndex, ParsedState } from "./schema.js";
import { parseStrictJson } from "./strict-json.js";
import { fetchBytes, normalizeBase } from "./transport.js";
import type {
  AnyOutputLoader,
  JsonObject,
  JsonValue,
  LoadOptions,
  OpenPublicationOptions,
  OutputCodec,
  OutputDescriptor,
  OutputLoader,
  OutputPayloadMap,
  Publication,
  PublishedOutput,
  PublishedState,
  VerificationResult,
  VerifyOptions,
} from "./types.js";
import { PublicationError } from "./types.js";

const INDEX_MAX_BYTES = 16 * 1024 * 1024;
const INDEX_MAX_VALUES = 2_000_000;
const DEFAULT_ASSET_MAX_BYTES = 512 * 1024 * 1024;
const HARD_ASSET_MAX_BYTES = 2_147_483_647;
const DEFAULT_VERIFY_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

export async function openPublication(
  base: string | URL,
  options: OpenPublicationOptions = {},
): Promise<Publication> {
  throwIfAborted(options.signal);
  const normalized = normalizeBase(base);
  const fetcher = options.fetch ?? globalThis.fetch;
  if (typeof fetcher !== "function") throw new TypeError("A fetch implementation is required.");
  const bytes = await fetchBytes(fetcher, new URL("index.json", normalized), "index.json", {
    maxBytes: INDEX_MAX_BYTES,
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  const wire = decodeCanonicalIndex(bytes);
  const parsed = parsePublicationIndex(wire);
  await validateFingerprints(parsed, options.signal);
  return new PublicationValue(normalized, parsed, fetcher);
}

function decodeCanonicalIndex(bytes: Uint8Array): unknown {
  let value: unknown;
  try {
    value = parseStrictJson(decoder.decode(bytes), INDEX_MAX_VALUES);
  } catch (error) {
    throw new PublicationError(
      "publication_invalid",
      "Publication index must be strict UTF-8 JSON.",
      {
        cause: error,
      },
    );
  }
  let canonical: Uint8Array;
  try {
    canonical = encoder.encode(canonicalJson(value as JsonValue));
  } catch (error) {
    if (error instanceof PublicationError) throw error;
    throw new PublicationError("publication_invalid", "Publication index JSON is invalid.", {
      cause: error,
    });
  }
  if (!equalBytes(bytes, canonical)) {
    throw new PublicationError(
      "publication_noncanonical",
      "Publication index is not canonical JSON.",
    );
  }
  return value;
}

async function validateFingerprints(
  index: ParsedPublicationIndex,
  signal: AbortSignal | undefined,
): Promise<void> {
  for (const [name, state] of Object.entries(index.states)) {
    throwIfAborted(signal);
    // Keep hashing bounded when publications contain many states.
    // oxlint-disable-next-line no-await-in-loop
    const actual = await sha256Hex(encoder.encode(canonicalJson(state.inputs)));
    throwIfAborted(signal);
    if (actual !== state.fingerprint) {
      throw new PublicationError(
        "publication_invalid",
        `State ${JSON.stringify(name)} fingerprint does not match its inputs.`,
        { details: { state: name } },
      );
    }
  }
}

class PublicationValue implements Publication {
  readonly #baseHref: string;
  readonly notebook: ParsedPublicationIndex["notebook"];
  readonly producer: ParsedPublicationIndex["producer"];
  readonly inputNames: readonly string[];
  readonly outputNames: readonly string[];
  readonly #states: readonly PublishedStateValue[];
  readonly #statesByName: ReadonlyMap<string, PublishedStateValue>;
  readonly #statesByInputs: ReadonlyMap<string, PublishedStateValue>;
  readonly #reader: AssetReader;

  constructor(base: URL, index: ParsedPublicationIndex, fetcher: typeof globalThis.fetch) {
    this.#baseHref = base.href;
    this.notebook = index.notebook;
    this.producer = index.producer;
    this.inputNames = index.inputs;
    this.outputNames = index.outputs;
    this.#reader = new AssetReader(this.#baseHref, fetcher);
    this.#states = Object.freeze(
      Object.entries(index.states)
        .sort(([left], [right]) => compareUnicodeScalarStrings(left, right))
        .map(([name, state]) => new PublishedStateValue(this, name, state, this.#reader)),
    );
    this.#statesByName = new Map(this.#states.map((state) => [state.name, state]));
    this.#statesByInputs = new Map(
      this.#states.map((state) => [canonicalJson(state.inputs), state]),
    );
    Object.freeze(this);
  }

  get base(): URL {
    return new URL(this.#baseHref);
  }

  states(): readonly PublishedState[] {
    return this.#states;
  }

  state(name: string): PublishedState {
    const state = this.#statesByName.get(name);
    if (state === undefined) {
      throw new PublicationError(
        "state_not_found",
        `State ${JSON.stringify(name)} was not found.`,
        {
          details: {
            requested: String(name).slice(0, 255),
            available: this.#states.slice(0, 16).map((item) => item.name),
          },
        },
      );
    }
    return state;
  }

  resolve(inputs: JsonObject): PublishedState {
    const normalized = normalizeResolutionObject(inputs, "inputs");
    requireCompleteInputs(normalized, this.inputNames);
    return this.resolveNormalized(normalized);
  }

  resolveNormalized(inputs: JsonObject): PublishedStateValue {
    const state = this.#statesByInputs.get(canonicalJson(inputs));
    if (state === undefined) {
      throw new PublicationError(
        "state_unavailable",
        "The requested input vector is absent from this publication.",
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
      throw new PublicationError(
        "read_limit_exceeded",
        "Publication assets exceed the verification byte limit.",
        { details: { declaredBytes: total, maxTotalBytes } },
      );
    }
    for (const item of assets) {
      throwIfAborted(options.signal);
      // Verification is sequential so one run does not retain every asset.
      // oxlint-disable-next-line no-await-in-loop
      await this.#reader.payload(item.descriptor, {
        maxBytes,
        ...(options.signal === undefined ? {} : { signal: options.signal }),
      });
    }
    return Object.freeze({
      states: this.#states.length,
      outputs: this.#states.length * this.outputNames.length,
      assets: assets.length,
      bytesVerified: total,
    });
  }
}

class PublishedStateValue implements PublishedState {
  readonly publication: PublicationValue;
  readonly name: string;
  readonly fingerprint: string;
  readonly inputs: JsonObject;
  readonly #outputs: readonly PublishedOutputValue[];
  readonly #outputsByName: ReadonlyMap<string, PublishedOutputValue>;

  constructor(
    publication: PublicationValue,
    name: string,
    state: ParsedState,
    reader: AssetReader,
  ) {
    this.publication = publication;
    this.name = name;
    this.fingerprint = state.fingerprint;
    this.inputs = state.inputs;
    this.#outputs = Object.freeze(
      publication.outputNames.map(
        (outputName) =>
          new PublishedOutputValue(this, outputName, state.outputs[outputName]!, reader),
      ),
    );
    this.#outputsByName = new Map(this.#outputs.map((output) => [output.name, output]));
    Object.freeze(this);
  }

  outputs(): readonly PublishedOutput[] {
    return this.#outputs;
  }

  output(name: string): PublishedOutput {
    const output = this.#outputsByName.get(name);
    if (output === undefined) {
      throw new PublicationError(
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

  resolve(patch: JsonObject): PublishedState {
    const normalized = normalizeResolutionObject(patch, "patch");
    const keys = Object.keys(normalized);
    if (keys.length === 0) return this;
    const allowed = new Set(this.publication.inputNames);
    if (keys.some((key) => !allowed.has(key))) {
      throw new PublicationError(
        "state_input_invalid",
        "State patch contains an unknown input name.",
        { details: { keys: keys.slice(0, 16) } },
      );
    }
    const merged = Object.freeze(
      Object.fromEntries(
        this.publication.inputNames.map((name) => [
          name,
          Object.hasOwn(normalized, name) ? normalized[name]! : this.inputs[name]!,
        ]),
      ),
    ) as JsonObject;
    return this.publication.resolveNormalized(merged);
  }
}

class PublishedOutputValue implements PublishedOutput {
  readonly state: PublishedStateValue;
  readonly name: string;
  readonly codec: OutputCodec;
  readonly mediaType: ReturnType<typeof parseMediaType>;
  readonly descriptor: OutputDescriptor;
  readonly #reader: AssetReader;

  constructor(
    state: PublishedStateValue,
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
    const selected = resolveOutputLoader(this, [loader as AnyOutputLoader]);
    const payload = await this.#reader.payload(this.descriptor, {
      maxBytes: assetLimit(options.maxBytes),
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    });
    throwIfAborted(options.signal);
    const call = selected as unknown as {
      load(input: {
        readonly descriptor: OutputDescriptor;
        readonly mediaType: ReturnType<typeof parseMediaType>;
        readonly payload: OutputPayloadMap[OutputCodec];
        readonly signal?: AbortSignal;
      }): unknown;
    };
    const result = Promise.resolve(
      call.load({
        descriptor: this.descriptor,
        mediaType: this.mediaType,
        payload,
        ...(options.signal === undefined ? {} : { signal: options.signal }),
      }),
    );
    return (await waitForLoader(result, options.signal)) as T;
  }
}

type AssetOutputDescriptor = Exclude<OutputDescriptor, { readonly codec: "marimo.scalar.v1" }>;

class AssetReader {
  readonly #baseHref: string;
  readonly #fetch: typeof globalThis.fetch;

  constructor(baseHref: string, fetcher: typeof globalThis.fetch) {
    this.#baseHref = baseHref;
    this.#fetch = fetcher;
  }

  async payload(
    descriptor: OutputDescriptor,
    options: { readonly maxBytes: number; readonly signal?: AbortSignal },
  ): Promise<OutputPayloadMap[OutputCodec]> {
    throwIfAborted(options.signal);
    if (descriptor.codec === "marimo.scalar.v1") return descriptor.value;
    if (descriptor.asset.size > options.maxBytes) {
      throw new PublicationError(
        "read_limit_exceeded",
        "Publication asset exceeds the caller byte limit.",
        {
          details: {
            declaredBytes: descriptor.asset.size,
            maxBytes: options.maxBytes,
          },
        },
      );
    }
    const path = assetPath(descriptor.codec, descriptor.asset.sha256);
    const bytes = await fetchBytes(this.#fetch, new URL(path, this.#baseHref), path, {
      maxBytes: options.maxBytes,
      expectedBytes: descriptor.asset.size,
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    });
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
  const extension =
    codec === "numpy.npy.v1" ? "npy" : codec === "apache.arrow.file.v1" ? "arrow" : "bin";
  return `assets/${digest}.${extension}`;
}

function uniqueAssets(
  states: readonly PublishedStateValue[],
): readonly { readonly descriptor: AssetOutputDescriptor }[] {
  const values = new Map<string, { readonly descriptor: AssetOutputDescriptor }>();
  for (const state of states) {
    for (const output of state.outputs()) {
      if (output.descriptor.codec === "marimo.scalar.v1") continue;
      const descriptor = output.descriptor as AssetOutputDescriptor;
      values.set(`${descriptor.codec}\0${descriptor.asset.sha256}`, { descriptor });
    }
  }
  return Object.freeze([...values.values()]);
}

function normalizeResolutionObject(input: unknown, label: string): JsonObject {
  try {
    return portableJsonObject(input, label);
  } catch (error) {
    throw new PublicationError("state_input_invalid", `State ${label} is invalid.`, {
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
    throw new PublicationError(
      "state_input_invalid",
      "State inputs must equal the publication input name set.",
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
        new PublicationError("abort", "Publication output loading was aborted.", {
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

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new PublicationError("abort", "Publication operation was aborted.", {
      cause: signal.reason,
    });
  }
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((byte, index) => byte === right[index]);
}
