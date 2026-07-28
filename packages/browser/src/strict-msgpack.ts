const textDecoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const MAX_FORMAT_ID_BYTES = 255;
const MAX_MEDIA_TYPE_BYTES = 1_024;
const MAX_FILENAME_BYTES = 255;
const MAX_FORMAT_METADATA_JSON_BYTES = 256 * 1_024;

export interface BlobAssetWire {
  readonly data: Uint8Array;
  readonly mediaType: string;
  readonly filename: string | null;
  readonly formatId: string;
  readonly metadataJson: Uint8Array;
}

/** Decode the fixed, canonical MessagePack shape emitted for marimo BlobAsset. */
export function decodeBlobAssetWire(bytes: Uint8Array): BlobAssetWire {
  const reader = new CanonicalReader(bytes);
  reader.expectByte(0x84);
  reader.expectKey("data");
  const data = reader.binary();
  reader.expectKey("media_type");
  const mediaType = reader.string(MAX_MEDIA_TYPE_BYTES);
  reader.expectKey("filename");
  const filename = reader.nullableString(MAX_FILENAME_BYTES);
  reader.expectKey("metadata");
  reader.expectByte(0x82);
  reader.expectKey("format_id");
  const formatId = reader.string(MAX_FORMAT_ID_BYTES);
  reader.expectKey("metadata_json");
  const metadataJson = reader.binary(MAX_FORMAT_METADATA_JSON_BYTES);
  reader.expectEnd();
  return { data, mediaType, filename, formatId, metadataJson };
}

class CanonicalReader {
  readonly #bytes: Uint8Array;
  readonly #view: DataView;
  #offset = 0;

  constructor(bytes: Uint8Array) {
    this.#bytes = bytes;
    this.#view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }

  binary(maxBytes?: number): Uint8Array {
    const head = this.#u8();
    let length: number;
    if (head === 0xc4) {
      length = this.#u8();
    } else if (head === 0xc5) {
      length = this.#u16();
      if (length <= 0xff) throw nonCanonical();
    } else if (head === 0xc6) {
      length = this.#u32();
      if (length <= 0xffff) throw nonCanonical();
    } else {
      throw new RangeError("BlobAsset binary value has an invalid MessagePack token.");
    }
    if (maxBytes !== undefined && length > maxBytes) {
      throw new RangeError("BlobAsset binary value exceeds its wire size limit.");
    }
    return this.#payload(length);
  }

  string(maxBytes?: number): string {
    const head = this.#u8();
    let length: number;
    if (head >= 0xa0 && head <= 0xbf) {
      length = head & 0x1f;
    } else if (head === 0xd9) {
      length = this.#u8();
      if (length <= 0x1f) throw nonCanonical();
    } else if (head === 0xda) {
      length = this.#u16();
      if (length <= 0xff) throw nonCanonical();
    } else if (head === 0xdb) {
      length = this.#u32();
      if (length <= 0xffff) throw nonCanonical();
    } else {
      throw new RangeError("BlobAsset string has an invalid MessagePack token.");
    }
    if (maxBytes !== undefined && length > maxBytes) {
      throw new RangeError("BlobAsset string exceeds its wire size limit.");
    }
    return textDecoder.decode(this.#payload(length));
  }

  nullableString(maxBytes?: number): string | null {
    if (this.#peek() !== 0xc0) return this.string(maxBytes);
    this.#offset += 1;
    return null;
  }

  expectKey(expected: string): void {
    if (expected.length > 0x1f) throw new Error("BlobAsset key exceeds fixstr length.");
    this.expectByte(0xa0 | expected.length);
    for (let index = 0; index < expected.length; index += 1) {
      this.expectByte(expected.charCodeAt(index));
    }
  }

  expectByte(expected: number): void {
    if (this.#u8() !== expected) {
      throw new RangeError("BlobAsset does not use its canonical MessagePack shape.");
    }
  }

  expectEnd(): void {
    if (this.#offset !== this.#bytes.byteLength) {
      throw new RangeError("BlobAsset MessagePack contains trailing data.");
    }
  }

  #peek(): number {
    this.#ensure(1);
    return this.#view.getUint8(this.#offset);
  }

  #payload(length: number): Uint8Array {
    this.#ensure(length);
    const start = this.#offset;
    this.#offset += length;
    return this.#bytes.subarray(start, this.#offset);
  }

  #u8(): number {
    this.#ensure(1);
    return this.#view.getUint8(this.#offset++);
  }

  #u16(): number {
    this.#ensure(2);
    const value = this.#view.getUint16(this.#offset);
    this.#offset += 2;
    return value;
  }

  #u32(): number {
    this.#ensure(4);
    const value = this.#view.getUint32(this.#offset);
    this.#offset += 4;
    return value;
  }

  #ensure(length: number): void {
    if (length > this.#bytes.byteLength - this.#offset) {
      throw new RangeError("BlobAsset MessagePack is truncated.");
    }
  }
}

function nonCanonical(): RangeError {
  return new RangeError("BlobAsset must use minimal MessagePack length prefixes.");
}
