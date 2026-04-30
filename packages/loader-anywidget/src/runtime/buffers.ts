export function decodeBase64Buffer(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

export function restoreBuffers<T extends object>(
  state: T,
  bufferPaths: Array<Array<string | number>>,
  base64Buffers: string[],
): T {
  return restoreBufferBytes(state, bufferPaths, base64Buffers.map(decodeBase64Buffer));
}

export function restoreBufferBytes<T extends object>(
  state: T,
  bufferPaths: Array<Array<string | number>>,
  buffers: Uint8Array[],
): T {
  for (const [index, bufferPath] of bufferPaths.entries()) {
    let target = state as Record<string | number, unknown>;
    for (const key of bufferPath.slice(0, -1)) {
      target = target[key] as Record<string | number, unknown>;
    }
    target[bufferPath[bufferPath.length - 1] as string | number] = buffers[index];
  }
  return state;
}

export function normalizeOutgoingBuffers(
  buffers?: ArrayBuffer[] | ArrayBufferView[] | Uint8Array[],
): Uint8Array[] {
  if (!buffers) {
    return [];
  }

  return buffers.map((buffer) => {
    if (buffer instanceof Uint8Array) {
      return buffer;
    }
    if (ArrayBuffer.isView(buffer)) {
      return new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    }
    return new Uint8Array(buffer);
  });
}

export function toDataViews(buffers: Uint8Array[]): DataView[] {
  return buffers.map((buffer) => new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength));
}
