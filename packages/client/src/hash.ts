import { MarimoExportError } from "./types.js";

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) {
    throw new MarimoExportError(
      "integrity_failed",
      "SHA-256 verification requires the Web Crypto API.",
    );
  }

  const digest = await subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function verifyBytes(
  bytes: Uint8Array,
  expected: { sha256: string; size: number },
  label: string,
): Promise<void> {
  if (bytes.byteLength !== expected.size) {
    throw new MarimoExportError(
      "integrity_failed",
      `${label} has ${bytes.byteLength} bytes, expected ${expected.size}.`,
    );
  }

  const actual = await sha256Hex(bytes);
  if (actual !== expected.sha256) {
    throw new MarimoExportError(
      "integrity_failed",
      `${label} has SHA-256 ${actual}, expected ${expected.sha256}.`,
    );
  }
}
