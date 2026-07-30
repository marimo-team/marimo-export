export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export type ScalarValue = null | boolean | string | number | bigint;

export type NotebookExportErrorCode =
  | "abort"
  | "asset_invalid"
  | "decode_failed"
  | "integrity_failed"
  | "loader_ambiguous"
  | "loader_invalid"
  | "loader_unavailable"
  | "output_not_found"
  | "output_representation_changed"
  | "export_invalid"
  | "export_noncanonical"
  | "read_failed"
  | "read_limit_exceeded"
  | "state_input_invalid"
  | "state_not_found"
  | "state_unavailable";

export class NotebookExportError extends Error {
  readonly code: NotebookExportErrorCode;
  readonly details: JsonObject | undefined;
  override readonly cause: unknown;

  constructor(
    code: NotebookExportErrorCode,
    message: string,
    options: { readonly cause?: unknown; readonly details?: JsonObject } = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "NotebookExportError";
    this.code = code;
    this.cause = options.cause;
    this.details = options.details === undefined ? undefined : freezeJsonObject(options.details);
  }
}

export interface NotebookProvenance {
  readonly filename: string | null;
  readonly documentSha256: string;
}

export interface ProducerProvenance {
  readonly marimo: string;
  readonly marimoExport: string;
}

export interface Provenance {
  readonly cacheKey: string;
  readonly returnReference: string | null;
  readonly pythonType: string;
}

export interface AssetDescriptor {
  readonly sha256: string;
  readonly size: number;
}

export interface ScalarDescriptor {
  readonly codec: "marimo.scalar.v1";
  readonly mediaType: "application/vnd.marimo.scalar.v1+json";
  readonly provenance: Provenance;
  readonly value: ScalarValue;
}

export interface NumpyDescriptor {
  readonly codec: "numpy.npy.v1";
  readonly mediaType: "application/x-npy";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

export interface ArrowDescriptor {
  readonly codec: "apache.arrow.file.v1";
  readonly mediaType: "application/vnd.apache.arrow.file";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

export interface BlobAssetDescriptor {
  readonly codec: "marimo.blob-asset.msgpack.v1";
  readonly mediaType: string;
  readonly filename: string | null;
  readonly metadata: JsonObject;
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

export type OutputDescriptor =
  | ScalarDescriptor
  | NumpyDescriptor
  | ArrowDescriptor
  | BlobAssetDescriptor;

export interface MediaType {
  readonly raw: string;
  readonly essence: string;
  readonly type: string;
  readonly subtype: string;
  readonly parameters: ReadonlyMap<string, string>;
}

export interface BlobAsset {
  readonly data: Uint8Array;
  readonly mediaType: MediaType;
  readonly filename: string | null;
  readonly metadata: JsonObject;
}

export interface OutputPayloadMap {
  readonly "marimo.scalar.v1": ScalarValue;
  readonly "numpy.npy.v1": Uint8Array;
  readonly "apache.arrow.file.v1": Uint8Array;
  readonly "marimo.blob-asset.msgpack.v1": BlobAsset;
}

export type OutputCodec = keyof OutputPayloadMap;

export type DescriptorFor<C extends OutputCodec> = C extends "marimo.scalar.v1"
  ? ScalarDescriptor
  : C extends "numpy.npy.v1"
    ? NumpyDescriptor
    : C extends "apache.arrow.file.v1"
      ? ArrowDescriptor
      : C extends "marimo.blob-asset.msgpack.v1"
        ? BlobAssetDescriptor
        : never;

export interface OutputLoader<C extends OutputCodec, T> {
  readonly codec: C;
  accepts(descriptor: DescriptorFor<C>, mediaType: MediaType): boolean;
  load(input: {
    readonly descriptor: DescriptorFor<C>;
    readonly mediaType: MediaType;
    readonly payload: OutputPayloadMap[C];
    readonly signal?: AbortSignal;
  }): T | Promise<T>;
}

export type AnyOutputLoader = {
  [C in OutputCodec]: OutputLoader<C, unknown>;
}[OutputCodec];

export type BlobAssetLoader<T> = OutputLoader<"marimo.blob-asset.msgpack.v1", T>;

export interface BlobAssetLoadInput {
  readonly descriptor: BlobAssetDescriptor;
  readonly mediaType: MediaType;
  readonly payload: BlobAsset;
  readonly signal?: AbortSignal;
}

export interface OpenExportOptions {
  readonly fetch?: typeof globalThis.fetch;
  readonly signal?: AbortSignal;
}

export interface LoadOptions {
  readonly signal?: AbortSignal;
  readonly maxBytes?: number;
}

export interface VerifyOptions extends LoadOptions {
  readonly maxTotalBytes?: number;
}

export interface VerificationResult {
  readonly states: number;
  readonly outputs: number;
  readonly assets: number;
  readonly bytesVerified: number;
}

export interface NotebookExport {
  readonly base: URL;
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly inputNames: readonly string[];
  readonly outputNames: readonly string[];
  states(): readonly ExportState[];
  state(name: string): ExportState;
  resolve(inputs: JsonObject): ExportState;
  verify(options?: VerifyOptions): Promise<VerificationResult>;
}

export interface ExportState {
  readonly notebookExport: NotebookExport;
  readonly name: string;
  readonly fingerprint: string;
  readonly inputs: JsonObject;
  outputs(): readonly ExportOutput[];
  output(name: string): ExportOutput;
  resolve(patch: JsonObject): ExportState;
}

export interface ExportOutput {
  readonly state: ExportState;
  readonly name: string;
  readonly codec: OutputCodec;
  readonly mediaType: MediaType;
  readonly descriptor: OutputDescriptor;
  load<C extends OutputCodec, T>(loader: OutputLoader<C, T>, options?: LoadOptions): Promise<T>;
}

export interface MountedView {
  dispose(): void | Promise<void>;
}

export interface MountableValue {
  mount(
    element: HTMLElement,
    options?: {
      readonly signal?: AbortSignal;
    },
  ): Promise<MountedView>;
}

export function freezeJsonObject(value: JsonObject): JsonObject {
  return Object.freeze(
    Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeJsonValue(item)])),
  );
}

export function freezeJsonValue(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return Object.freeze(value.map(freezeJsonValue));
  if (typeof value === "object" && value !== null) return freezeJsonObject(value as JsonObject);
  return value;
}
