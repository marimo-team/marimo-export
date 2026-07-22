import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { open } from "node:fs/promises";

import type { PayloadRef } from "../types.js";

const CHUNK_BYTES = 64 * 1024;
const READ_FLAGS = constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW;

export class LocalFileTooLargeError extends Error {
  readonly size: bigint;
  readonly maxBytes: number;

  constructor(size: bigint, maxBytes: number) {
    super(`Local file contains more than ${maxBytes} bytes.`);
    this.name = "LocalFileTooLargeError";
    this.size = size;
    this.maxBytes = maxBytes;
  }
}

export async function readRegularFile(
  path: string,
  maxBytes: number,
  signal?: AbortSignal,
): Promise<Uint8Array> {
  signal?.throwIfAborted();
  const handle = await open(path, READ_FLAGS);
  try {
    const status = await handle.stat({ bigint: true });
    if (!status.isFile()) throw new TypeError(`Local export object ${path} is not a regular file.`);
    if (status.size > BigInt(maxBytes)) throw new LocalFileTooLargeError(status.size, maxBytes);
    const size = Number(status.size);
    const bytes = new Uint8Array(size);
    let offset = 0;
    while (offset < size) {
      signal?.throwIfAborted();
      // oxlint-disable-next-line no-await-in-loop -- one opened file is read in bounded chunks.
      const result = await handle.read(bytes, offset, Math.min(CHUNK_BYTES, size - offset), offset);
      if (result.bytesRead === 0)
        throw new Error(`Local export object ${path} changed while reading.`);
      offset += result.bytesRead;
    }
    signal?.throwIfAborted();
    const overflow = new Uint8Array(1);
    const result = await handle.read(overflow, 0, 1, size);
    if (result.bytesRead > 0) throw new LocalFileTooLargeError(BigInt(size + 1), maxBytes);
    return bytes;
  } finally {
    await handle.close();
  }
}

export async function matchesRegularFile(
  path: string,
  ref: PayloadRef,
  signal?: AbortSignal,
): Promise<boolean> {
  signal?.throwIfAborted();
  const handle = await open(path, READ_FLAGS);
  try {
    const status = await handle.stat({ bigint: true });
    if (!status.isFile() || status.size !== BigInt(ref.size)) return false;
    const digest = createHash("sha256");
    const chunk = new Uint8Array(Math.min(CHUNK_BYTES, Math.max(ref.size, 1)));
    let offset = 0;
    while (offset < ref.size) {
      signal?.throwIfAborted();
      // oxlint-disable-next-line no-await-in-loop -- one opened file is hashed in bounded chunks.
      const result = await handle.read(
        chunk,
        0,
        Math.min(chunk.byteLength, ref.size - offset),
        offset,
      );
      if (result.bytesRead === 0) return false;
      digest.update(chunk.subarray(0, result.bytesRead));
      offset += result.bytesRead;
    }
    signal?.throwIfAborted();
    const overflow = await handle.read(new Uint8Array(1), 0, 1, ref.size);
    return overflow.bytesRead === 0 && digest.digest("hex") === ref.sha256;
  } finally {
    await handle.close();
  }
}
