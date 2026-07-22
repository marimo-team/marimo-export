import { constants } from "node:fs";
import { open } from "node:fs/promises";

import { CliUsageError } from "./cli-args.js";

const DOCUMENT_MAX_BYTES = 16 * 1024 * 1024;
const READ_FLAGS = constants.O_RDONLY | constants.O_NONBLOCK;

export type StdinReader = (signal?: AbortSignal) => Promise<string>;

export async function readDocument(
  path: string,
  stdin: StdinReader | undefined,
  signal?: AbortSignal,
): Promise<string> {
  signal?.throwIfAborted();
  if (path !== "-") return readDocumentFile(path, signal);
  if (stdin !== undefined) {
    const text = await withAbort(stdin(signal), signal);
    assertDocumentSize(Buffer.byteLength(text, "utf8"), "standard input");
    signal?.throwIfAborted();
    return text;
  }
  return readDocumentStdin(signal);
}

async function readDocumentFile(path: string, signal?: AbortSignal): Promise<string> {
  const handle = await open(path, READ_FLAGS);
  try {
    const status = await handle.stat({ bigint: true });
    if (!status.isFile()) throw new CliUsageError(`Input ${JSON.stringify(path)} must be a file.`);
    if (status.size > BigInt(DOCUMENT_MAX_BYTES)) tooLarge(JSON.stringify(path));
    const size = Number(status.size);
    const bytes = new Uint8Array(size);
    let offset = 0;
    while (offset < size) {
      signal?.throwIfAborted();
      // oxlint-disable-next-line no-await-in-loop -- one bounded document is read in file order.
      const result = await handle.read(bytes, offset, size - offset, offset);
      if (result.bytesRead === 0) {
        throw new Error(`Input ${JSON.stringify(path)} changed while reading.`);
      }
      offset += result.bytesRead;
    }
    signal?.throwIfAborted();
    const overflow = await handle.read(new Uint8Array(1), 0, 1, size);
    if (overflow.bytesRead > 0) tooLarge(JSON.stringify(path));
    return decodeDocument(bytes, JSON.stringify(path));
  } finally {
    await handle.close();
  }
}

async function readDocumentStdin(signal?: AbortSignal): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    const cleanup = () => {
      process.stdin.removeListener("data", onData);
      process.stdin.removeListener("end", onEnd);
      process.stdin.removeListener("error", onError);
      signal?.removeEventListener("abort", onAbort);
    };
    const fail = (error: unknown) => {
      cleanup();
      process.stdin.pause();
      reject(error);
    };
    const onData = (input: Buffer | string) => {
      const chunk = Buffer.isBuffer(input) ? input : Buffer.from(input);
      size += chunk.byteLength;
      if (size > DOCUMENT_MAX_BYTES) {
        fail(documentTooLarge("standard input"));
        return;
      }
      chunks.push(chunk);
    };
    const onEnd = () => {
      cleanup();
      try {
        resolve(decodeDocument(Buffer.concat(chunks, size), "standard input"));
      } catch (error) {
        reject(error);
      }
    };
    const onError = (error: Error) => fail(error);
    const onAbort = () => fail(signal?.reason);
    process.stdin.on("data", onData);
    process.stdin.once("end", onEnd);
    process.stdin.once("error", onError);
    signal?.addEventListener("abort", onAbort, { once: true });
    process.stdin.resume();
  });
}

function decodeDocument(bytes: Uint8Array, label: string): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new CliUsageError(`Input ${label} must be valid UTF-8.`, error);
  }
}

function assertDocumentSize(size: number, label: string): void {
  if (size > DOCUMENT_MAX_BYTES) tooLarge(label);
}

function tooLarge(label: string): never {
  throw documentTooLarge(label);
}

function documentTooLarge(label: string): CliUsageError {
  return new CliUsageError(`Input ${label} exceeds ${DOCUMENT_MAX_BYTES} bytes.`);
}

async function withAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (signal === undefined) return promise;
  signal.throwIfAborted();
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(signal.reason);
    signal.addEventListener("abort", aborted, { once: true });
    void promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", aborted));
  });
}
