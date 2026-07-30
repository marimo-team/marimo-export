import type { AssetDescriptor, OutputCodec } from "./types.js";
import { NotebookExportError } from "./types.js";

const NPY_MAGIC = new Uint8Array([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59]);
const ARROW_MAGIC = new TextEncoder().encode("ARROW1");

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) {
    throw new NotebookExportError(
      "integrity_failed",
      "Export verification requires Web Crypto SHA-256.",
    );
  }
  try {
    const digest = await subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(
      "",
    );
  } catch (error) {
    throw new NotebookExportError("integrity_failed", "SHA-256 verification failed.", {
      cause: error,
    });
  }
}

export async function verifyBytes(bytes: Uint8Array, expected: AssetDescriptor): Promise<void> {
  if (bytes.byteLength !== expected.size) {
    throw new NotebookExportError("integrity_failed", "Export asset size does not match.", {
      details: {
        expectedSize: expected.size,
        observedSize: bytes.byteLength,
      },
    });
  }
  const actual = await sha256Hex(bytes);
  if (actual !== expected.sha256) {
    throw new NotebookExportError("integrity_failed", "Export asset SHA-256 does not match.", {
      details: {
        expectedSha256: expected.sha256,
        observedSha256: actual,
      },
    });
  }
}

export function validateNativeFile(codec: OutputCodec, bytes: Uint8Array): void {
  if (codec === "numpy.npy.v1" && !hasBytes(bytes, NPY_MAGIC, 0)) {
    throw new NotebookExportError("decode_failed", "NumPy asset has an invalid NPY header.");
  }
  if (
    codec === "apache.arrow.file.v1" &&
    (bytes.byteLength < 10 ||
      !hasBytes(bytes, ARROW_MAGIC, 0) ||
      !hasBytes(bytes, ARROW_MAGIC, bytes.byteLength - ARROW_MAGIC.byteLength))
  ) {
    throw new NotebookExportError("decode_failed", "Arrow asset has an invalid file signature.");
  }
}

function hasBytes(value: Uint8Array, expected: Uint8Array, offset: number): boolean {
  if (offset < 0 || offset + expected.byteLength > value.byteLength) return false;
  return expected.every((byte, index) => value[offset + index] === byte);
}
