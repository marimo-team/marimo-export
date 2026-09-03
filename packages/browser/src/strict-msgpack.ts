const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const MAX_DEPTH = 256;
const MAX_VALUES = 100_000;

type MessagePackValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | Uint8Array
  | readonly MessagePackValue[]
  | ReadonlyMap<string, MessagePackValue>;

export function validateCanonicalMessagePack(bytes: Uint8Array): void {
  const scanner = new MessagePackScanner(bytes);
  scanner.value(0);
  scanner.end();
}

class MessagePackScanner {
  readonly #bytes: Uint8Array;
  readonly #view: DataView;
  #offset = 0;
  #values = 0;

  constructor(bytes: Uint8Array) {
    this.#bytes = bytes;
    this.#view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }

  value(depth: number): MessagePackValue {
    this.#values += 1;
    if (this.#values > MAX_VALUES) throw new RangeError("MessagePack exceeds its value limit.");
    if (depth > MAX_DEPTH) throw new RangeError("MessagePack exceeds its nesting limit.");
    const head = this.u8();
    if (head <= 0x7f) return head;
    if (head >= 0xe0) return head - 0x100;
    if (head >= 0xa0 && head <= 0xbf) return this.stringPayload(head & 0x1f);
    if (head >= 0x90 && head <= 0x9f) return this.array(head & 0x0f, depth);
    if (head >= 0x80 && head <= 0x8f) return this.map(head & 0x0f, depth);
    switch (head) {
      case 0xc0:
        return null;
      case 0xc2:
        return false;
      case 0xc3:
        return true;
      case 0xc4:
        return this.binary(this.u8());
      case 0xc5: {
        const length = this.u16();
        if (length <= 0xff) throw nonCanonical();
        return this.binary(length);
      }
      case 0xc6: {
        const length = this.u32();
        if (length <= 0xffff) throw nonCanonical();
        return this.binary(length);
      }
      case 0xca:
        throw new RangeError("Canonical BlobAsset metadata uses float64.");
      case 0xcb: {
        const value = this.f64();
        if (!Number.isFinite(value)) throw new RangeError("MessagePack float must be finite.");
        return value;
      }
      case 0xcc: {
        const value = this.u8();
        if (value <= 0x7f) throw nonCanonical();
        return value;
      }
      case 0xcd: {
        const value = this.u16();
        if (value <= 0xff) throw nonCanonical();
        return value;
      }
      case 0xce: {
        const value = this.u32();
        if (value <= 0xffff) throw nonCanonical();
        return value;
      }
      case 0xcf: {
        const value = this.u64();
        if (value <= 0xffffffffn) throw nonCanonical();
        return value;
      }
      case 0xd0: {
        const value = this.i8();
        if (value >= -32) throw nonCanonical();
        return value;
      }
      case 0xd1: {
        const value = this.i16();
        if (value >= -128) throw nonCanonical();
        return value;
      }
      case 0xd2: {
        const value = this.i32();
        if (value >= -32768) throw nonCanonical();
        return value;
      }
      case 0xd3: {
        const value = this.i64();
        if (value >= -2147483648n) throw nonCanonical();
        return value;
      }
      case 0xd9: {
        const length = this.u8();
        if (length <= 0x1f) throw nonCanonical();
        return this.stringPayload(length);
      }
      case 0xda: {
        const length = this.u16();
        if (length <= 0xff) throw nonCanonical();
        return this.stringPayload(length);
      }
      case 0xdb: {
        const length = this.u32();
        if (length <= 0xffff) throw nonCanonical();
        return this.stringPayload(length);
      }
      case 0xdc: {
        const length = this.u16();
        if (length <= 0x0f) throw nonCanonical();
        return this.array(length, depth);
      }
      case 0xdd: {
        const length = this.u32();
        if (length <= 0xffff) throw nonCanonical();
        return this.array(length, depth);
      }
      case 0xde: {
        const length = this.u16();
        if (length <= 0x0f) throw nonCanonical();
        return this.map(length, depth);
      }
      case 0xdf: {
        const length = this.u32();
        if (length <= 0xffff) throw nonCanonical();
        return this.map(length, depth);
      }
      default:
        throw new RangeError("BlobAsset contains an unsupported MessagePack token.");
    }
  }

  array(length: number, depth: number): readonly MessagePackValue[] {
    const values: MessagePackValue[] = [];
    for (let index = 0; index < length; index += 1) values.push(this.value(depth + 1));
    return values;
  }

  map(length: number, depth: number): ReadonlyMap<string, MessagePackValue> {
    const values = new Map<string, MessagePackValue>();
    for (let index = 0; index < length; index += 1) {
      const key = this.value(depth + 1);
      if (!isMessagePackString(key)) throw new RangeError("BlobAsset map keys must be strings.");
      if (values.has(key)) throw new RangeError("BlobAsset map keys must be unique.");
      values.set(key, this.value(depth + 1));
    }
    return values;
  }

  binary(length: number): Uint8Array {
    return this.payload(length);
  }

  stringPayload(length: number): string {
    return decoder.decode(this.payload(length));
  }

  end(): void {
    if (this.#offset !== this.#bytes.byteLength) {
      throw new RangeError("BlobAsset MessagePack contains trailing data.");
    }
  }

  payload(length: number): Uint8Array {
    this.ensure(length);
    const start = this.#offset;
    this.#offset += length;
    return this.#bytes.subarray(start, this.#offset);
  }

  u8(): number {
    this.ensure(1);
    return this.#view.getUint8(this.#offset++);
  }

  i8(): number {
    this.ensure(1);
    return this.#view.getInt8(this.#offset++);
  }

  u16(): number {
    this.ensure(2);
    const value = this.#view.getUint16(this.#offset);
    this.#offset += 2;
    return value;
  }

  i16(): number {
    this.ensure(2);
    const value = this.#view.getInt16(this.#offset);
    this.#offset += 2;
    return value;
  }

  u32(): number {
    this.ensure(4);
    const value = this.#view.getUint32(this.#offset);
    this.#offset += 4;
    return value;
  }

  i32(): number {
    this.ensure(4);
    const value = this.#view.getInt32(this.#offset);
    this.#offset += 4;
    return value;
  }

  u64(): bigint {
    this.ensure(8);
    const value = this.#view.getBigUint64(this.#offset);
    this.#offset += 8;
    return value;
  }

  i64(): bigint {
    this.ensure(8);
    const value = this.#view.getBigInt64(this.#offset);
    this.#offset += 8;
    return value;
  }

  f64(): number {
    this.ensure(8);
    const value = this.#view.getFloat64(this.#offset);
    this.#offset += 8;
    return value;
  }

  ensure(length: number): void {
    if (length > this.#bytes.byteLength - this.#offset) {
      throw new RangeError("BlobAsset MessagePack is truncated.");
    }
  }
}

function isMessagePackString(value: MessagePackValue): value is string {
  return Object.prototype.toString.call(value) === "[object String]";
}

function nonCanonical(): RangeError {
  return new RangeError("BlobAsset MessagePack is not canonical.");
}
