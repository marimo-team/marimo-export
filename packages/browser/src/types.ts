import { portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

export type { JsonObject, JsonPrimitive, JsonValue } from "@marimo-team/portable-json";

const NOTEBOOK_EXPORT_ERROR_BRAND = Symbol.for("@marimo-team/marimo-export.NotebookExportError.v1");
const NOTEBOOK_EXPORT_ERROR_CODES = [
  "abort",
  "asset_invalid",
  "decode_failed",
  "integrity_failed",
  "loader_ambiguous",
  "loader_invalid",
  "loader_unavailable",
  "output_not_found",
  "output_representation_changed",
  "export_invalid",
  "export_noncanonical",
  "read_failed",
  "read_limit_exceeded",
  "state_input_invalid",
  "state_not_found",
  "state_unavailable",
] as const;
const NOTEBOOK_EXPORT_ERROR_CODE_SET: ReadonlySet<string> = new Set(NOTEBOOK_EXPORT_ERROR_CODES);

export type ScalarValue = null | boolean | string | number | bigint;

export type NotebookExportErrorCode = (typeof NOTEBOOK_EXPORT_ERROR_CODES)[number];

export class NotebookExportError extends Error {
  readonly code: NotebookExportErrorCode;
  readonly details: JsonObject | undefined;
  override readonly cause: unknown;

  constructor(
    code: NotebookExportErrorCode,
    message: string,
    options: { readonly cause?: unknown; readonly details?: JsonObject } = {},
  ) {
    if (typeof code !== "string" || !NOTEBOOK_EXPORT_ERROR_CODE_SET.has(code)) {
      throw new TypeError("NotebookExportError code must be a known code.");
    }
    if (typeof message !== "string") {
      throw new TypeError("NotebookExportError message must be a string.");
    }
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    Object.defineProperty(this, NOTEBOOK_EXPORT_ERROR_BRAND, { value: true });
    this.name = "NotebookExportError";
    this.code = code;
    this.cause = options.cause;
    this.details = options.details === undefined ? undefined : portableJsonObject(options.details);
    Object.freeze(this);
  }
}

export function isNotebookExportError(value: unknown): value is NotebookExportError {
  if (value === null || typeof value !== "object") return false;
  try {
    const error = value as Readonly<Record<PropertyKey, unknown>>;
    const code = error.code;
    const details = error.details;
    if (
      error[NOTEBOOK_EXPORT_ERROR_BRAND] !== true ||
      error.name !== "NotebookExportError" ||
      typeof error.message !== "string" ||
      typeof code !== "string" ||
      !NOTEBOOK_EXPORT_ERROR_CODE_SET.has(code)
    ) {
      return false;
    }
    if (details !== undefined) portableJsonObject(details, "NotebookExportError.details");
    return true;
  } catch {
    return false;
  }
}

export interface NotebookProvenance {
  readonly filename: string | null;
  readonly documentSha256: string;
}

export interface ProducerProvenance {
  readonly marimo: string;
  readonly marimoExport: string;
  readonly implementationSha256: string;
}

export interface ControlIndexStep {
  readonly kind: "index";
  readonly value: number;
}

export interface ControlKeyStep {
  readonly kind: "key";
  readonly value: string;
}

export interface ControlElementStep {
  readonly kind: "element";
}

export type ControlPathStep = ControlIndexStep | ControlKeyStep | ControlElementStep;

export interface ControlBinding {
  readonly input: string;
  readonly path: readonly ControlPathStep[];
}

export interface Provenance {
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

export interface JsonDescriptor {
  readonly codec: "marimo.json.v1";
  readonly mediaType: "application/vnd.marimo.json.v1+json";
  readonly provenance: Provenance;
  readonly value: JsonValue;
}

export interface MarimoOutputDescriptor {
  readonly codec: "marimo.output.v1";
  readonly mediaType: "application/vnd.marimo.output.v1+json";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

export interface MarimoCellDescriptor {
  readonly codec: "marimo.cell.v1";
  readonly mediaType: "application/vnd.marimo.cell.v1+json";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
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
  | JsonDescriptor
  | MarimoOutputDescriptor
  | MarimoCellDescriptor
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
  readonly "marimo.json.v1": JsonValue;
  readonly "marimo.output.v1": Uint8Array;
  readonly "marimo.cell.v1": Uint8Array;
  readonly "numpy.npy.v1": Uint8Array;
  readonly "apache.arrow.file.v1": Uint8Array;
  readonly "marimo.blob-asset.msgpack.v1": BlobAsset;
}

export type OutputCodec = keyof OutputPayloadMap;

export type DescriptorFor<C extends OutputCodec> = C extends "marimo.scalar.v1"
  ? ScalarDescriptor
  : C extends "marimo.json.v1"
    ? JsonDescriptor
    : C extends "marimo.output.v1"
      ? MarimoOutputDescriptor
      : C extends "marimo.cell.v1"
        ? MarimoCellDescriptor
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
  readonly identity: string;
  readonly specSha256: string;
  readonly defaultState: ExportState;
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly inputNames: readonly string[];
  readonly controlBindings: Readonly<Record<string, ControlBinding>>;
  readonly outputNames: readonly string[];
  states(): readonly ExportState[];
  state(alias: string): ExportState;
  resolve(inputs: JsonObject): ExportState;
  verify(options?: VerifyOptions): Promise<VerificationResult>;
}

export interface ExportState {
  readonly notebookExport: NotebookExport;
  readonly fingerprint: string;
  readonly aliases: readonly string[];
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
