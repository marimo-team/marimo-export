import type { JsonObject, JsonValue } from "./types.js";

export type JsonDecoder<T> = (value: unknown) => T;

export interface MountedView {
  dispose(): void | Promise<void>;
}

export interface FormatLoaderContext {
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly filename: string | null;
  /** Decoded projection size in bytes. */
  readonly size: number;
  readonly signal: AbortSignal | undefined;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json(): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>): Promise<T>;
  blob(): Promise<Blob>;
}

export interface FormatLoader<T = unknown> {
  readonly formatId: string;
  load(format: FormatLoaderContext): T | Promise<T>;
  mount?(format: FormatLoaderContext, element: HTMLElement): MountedView | Promise<MountedView>;
}
