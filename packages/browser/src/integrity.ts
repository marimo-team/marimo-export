import { PublicationError } from "./types.js";

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) {
    throw new PublicationError(
      "integrity_failed",
      "Publication verification requires the Web Crypto API.",
    );
  }
  try {
    const digest = await subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(
      "",
    );
  } catch (error) {
    throw new PublicationError("integrity_failed", "SHA-256 verification failed.", {
      cause: error,
    });
  }
}

export async function verifyBytes(
  bytes: Uint8Array,
  expected: { readonly sha256: string; readonly size: number },
  label: string,
): Promise<void> {
  if (bytes.byteLength !== expected.size) {
    throw new PublicationError(
      "integrity_failed",
      `${label} has ${bytes.byteLength} bytes, expected ${expected.size}.`,
      {
        details: {
          expectedSize: expected.size,
          observedSize: bytes.byteLength,
        },
      },
    );
  }
  const actual = await sha256Hex(bytes);
  if (actual !== expected.sha256) {
    throw new PublicationError(
      "integrity_failed",
      `${label} has SHA-256 ${actual}, expected ${expected.sha256}.`,
      { details: { expectedSha256: expected.sha256, observedSha256: actual } },
    );
  }
}
