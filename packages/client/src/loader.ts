import type { JsonObject, JsonValue } from "./types.js";

export type JsonDecoder<T> = (value: unknown) => T;

export interface OutputLoaderContext {
  readonly formatId: string;
  readonly mediaType: string;
  readonly metadata: JsonObject;
  readonly size: number;
  readonly signal: AbortSignal | undefined;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  json(): Promise<JsonValue>;
  json<T>(decode: JsonDecoder<T>): Promise<T>;
}

export interface OutputLoader<T> {
  readonly formatId: string;
  load(output: OutputLoaderContext): T | Promise<T>;
}
