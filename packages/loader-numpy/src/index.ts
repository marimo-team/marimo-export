import { defineOutputLoader } from "@marimo-team/marimo-export";
import type { OutputLoader } from "@marimo-team/marimo-export";

const MAGIC = [0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59] as const;
const MAX_HEADER_BYTES = 1_048_576;
const MAX_TYPED_ARRAY_LENGTH = 0xffff_ffff;
const decoderLatin1 = new TextDecoder("latin1", { fatal: true });
const decoderUtf8 = new TextDecoder("utf-8", { fatal: true });
const hostLittleEndian = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

export type NumpyDTypeKind =
  | "boolean"
  | "signed-integer"
  | "unsigned-integer"
  | "floating-point"
  | "complex-floating-point";

export interface NumpyDType {
  readonly descriptor: string;
  readonly kind: NumpyDTypeKind;
  readonly itemSize: number;
  readonly byteOrder: "little" | "big" | "not-applicable";
}

export interface NumpyArray {
  readonly data: ArrayBufferView;
  readonly shape: readonly number[];
  readonly dtype: NumpyDType;
  readonly fortranOrder: boolean;
}

/** Decode a verified portable NPY asset. */
export function numpyLoader(): OutputLoader<"numpy.npy.v1", NumpyArray> {
  return defineOutputLoader({
    codec: "numpy.npy.v1",
    accepts: (_descriptor, mediaType) => mediaType.essence === "application/x-npy",
    load({ payload, signal }) {
      signal?.throwIfAborted();
      const result = decodeNpy(payload);
      signal?.throwIfAborted();
      return result;
    },
  });
}

function decodeNpy(bytes: Uint8Array): NumpyArray {
  if (bytes.byteLength < 10 || MAGIC.some((value, index) => bytes[index] !== value)) {
    throw new TypeError("NPY payload has invalid magic.");
  }
  const major = bytes[6]!;
  const minor = bytes[7]!;
  if (minor !== 0 || (major !== 1 && major !== 2 && major !== 3)) {
    throw new TypeError(`NPY version ${major}.${minor} is unsupported.`);
  }
  const lengthBytes = major === 1 ? 2 : 4;
  const headerStart = 8 + lengthBytes;
  if (bytes.byteLength < headerStart) throw new TypeError("NPY header is truncated.");
  const headerLength = lengthBytes === 2 ? readUint16(bytes, 8) : readUint32(bytes, 8);
  if (headerLength === 0 || headerLength > MAX_HEADER_BYTES) {
    throw new TypeError("NPY header length is outside the supported range.");
  }
  const headerEnd = headerStart + headerLength;
  if (!Number.isSafeInteger(headerEnd) || headerEnd > bytes.byteLength) {
    throw new TypeError("NPY header is truncated.");
  }
  const headerBytes = bytes.subarray(headerStart, headerEnd);
  const header = (major === 3 ? decoderUtf8 : decoderLatin1).decode(headerBytes).trim();
  const parsed = parseHeader(header);
  const dtype = parseDtype(parsed.descriptor);
  const count = elementCount(parsed.shape);
  const expectedBytes = safeProduct(count, dtype.itemSize, "NPY payload size");
  if (headerEnd + expectedBytes !== bytes.byteLength) {
    throw new TypeError("NPY payload length does not match its shape and dtype.");
  }
  const data = decodePayload(bytes.subarray(headerEnd), count, dtype);
  return Object.freeze({
    data,
    shape: Object.freeze(parsed.shape),
    dtype: Object.freeze(dtype),
    fortranOrder: parsed.fortranOrder,
  });
}

function parseHeader(header: string): {
  readonly descriptor: string;
  readonly fortranOrder: boolean;
  readonly shape: number[];
} {
  const match =
    /^\{\s*['"]descr['"]\s*:\s*(['"])([^'"]+)\1\s*,\s*['"]fortran_order['"]\s*:\s*(True|False)\s*,\s*['"]shape['"]\s*:\s*\(([^)]*)\)\s*,?\s*\}$/u.exec(
      header,
    );
  if (match === null) throw new TypeError("NPY header literal is invalid.");
  const dimensions = match[4]!.trim();
  const shape =
    dimensions.length === 0
      ? []
      : dimensions
          .split(",")
          .map((value) => value.trim())
          .filter((value) => value.length > 0)
          .map((value) => {
            if (!/^\d+$/u.test(value)) throw new TypeError("NPY shape is invalid.");
            const dimension = Number(value);
            if (!Number.isSafeInteger(dimension)) throw new TypeError("NPY shape is unsafe.");
            return dimension;
          });
  return {
    descriptor: match[2]!,
    fortranOrder: match[3] === "True",
    shape,
  };
}

function parseDtype(descriptor: string): NumpyDType {
  const match = /^([<>=|])([?biufc])(\d*)$/u.exec(descriptor);
  if (match === null)
    throw new TypeError(`NPY dtype ${JSON.stringify(descriptor)} is unsupported.`);
  const marker = match[1]!;
  const code = match[2]!;
  const itemSize = Number(match[3] || "1");
  if (!Number.isSafeInteger(itemSize) || itemSize <= 0) {
    throw new TypeError("NPY dtype has an invalid item size.");
  }
  const kind = dtypeKind(code);
  const supported =
    (code === "?" && itemSize === 1) ||
    (code === "b" && itemSize === 1) ||
    ((code === "i" || code === "u") && [1, 2, 4, 8].includes(itemSize)) ||
    (code === "f" && [2, 4, 8].includes(itemSize)) ||
    (code === "c" && [8, 16].includes(itemSize));
  if (!supported) throw new TypeError(`NPY dtype ${JSON.stringify(descriptor)} is unsupported.`);
  if (marker === "|" && itemSize !== 1) {
    throw new TypeError("NPY no-endian dtype must use one-byte items.");
  }
  return {
    descriptor,
    kind,
    itemSize,
    byteOrder:
      itemSize === 1
        ? "not-applicable"
        : marker === ">"
          ? "big"
          : marker === "<"
            ? "little"
            : hostLittleEndian
              ? "little"
              : "big",
  };
}

function dtypeKind(code: string): NumpyDTypeKind {
  if (code === "?" || code === "b") return "boolean";
  if (code === "i") return "signed-integer";
  if (code === "u") return "unsigned-integer";
  if (code === "f") return "floating-point";
  return "complex-floating-point";
}

function elementCount(shape: readonly number[]): number {
  let count = 1;
  for (const dimension of shape) count = safeProduct(count, dimension, "NPY shape");
  return count;
}

function safeProduct(left: number, right: number, label: string): number {
  const result = left * right;
  if (!Number.isSafeInteger(result) || result < 0) throw new TypeError(`${label} is unsafe.`);
  return result;
}

function decodePayload(bytes: Uint8Array, count: number, dtype: NumpyDType): ArrayBufferView {
  const code = dtype.descriptor[1]!;
  if (count > MAX_TYPED_ARRAY_LENGTH) throw new TypeError("NPY array is too large.");
  if (dtype.itemSize === 1) {
    const copy = bytes.slice();
    if (code === "i") return new Int8Array(copy.buffer);
    return copy;
  }
  const littleEndian = dtype.byteOrder === "little";
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (code === "i") {
    if (dtype.itemSize === 2)
      return fillNumbers(Int16Array, count, (offset) => view.getInt16(offset, littleEndian));
    if (dtype.itemSize === 4)
      return fillNumbers(Int32Array, count, (offset) => view.getInt32(offset, littleEndian));
    return fillBigInts(BigInt64Array, count, (offset) => view.getBigInt64(offset, littleEndian));
  }
  if (code === "u") {
    if (dtype.itemSize === 2)
      return fillNumbers(Uint16Array, count, (offset) => view.getUint16(offset, littleEndian));
    if (dtype.itemSize === 4)
      return fillNumbers(Uint32Array, count, (offset) => view.getUint32(offset, littleEndian));
    return fillBigInts(BigUint64Array, count, (offset) => view.getBigUint64(offset, littleEndian));
  }
  if (code === "f") {
    if (dtype.itemSize === 2) {
      return fillNumbers(
        Float32Array,
        count,
        (offset) => decodeFloat16(view.getUint16(offset, littleEndian)),
        2,
      );
    }
    if (dtype.itemSize === 4)
      return fillNumbers(Float32Array, count, (offset) => view.getFloat32(offset, littleEndian));
    return fillNumbers(Float64Array, count, (offset) => view.getFloat64(offset, littleEndian));
  }
  const components = safeProduct(count, 2, "NPY complex component count");
  if (components > MAX_TYPED_ARRAY_LENGTH) throw new TypeError("NPY array is too large.");
  if (dtype.itemSize === 8) {
    return fillNumbers(Float32Array, components, (offset) => view.getFloat32(offset, littleEndian));
  }
  return fillNumbers(Float64Array, components, (offset) => view.getFloat64(offset, littleEndian));
}

interface NumberArrayConstructor<T extends ArrayBufferView> {
  new (length: number): T;
  readonly BYTES_PER_ELEMENT: number;
}

function fillNumbers<T extends ArrayBufferView>(
  Constructor: NumberArrayConstructor<T>,
  length: number,
  read: (offset: number) => number,
  sourceStride = Constructor.BYTES_PER_ELEMENT,
): T {
  const result = new Constructor(length);
  for (let index = 0; index < length; index += 1) {
    (result as unknown as { [key: number]: number })[index] = read(index * sourceStride);
  }
  return result;
}

interface BigIntArrayConstructor<T extends BigInt64Array | BigUint64Array> {
  new (length: number): T;
  readonly BYTES_PER_ELEMENT: number;
}

function fillBigInts<T extends BigInt64Array | BigUint64Array>(
  Constructor: BigIntArrayConstructor<T>,
  length: number,
  read: (offset: number) => bigint,
): T {
  const result = new Constructor(length);
  for (let index = 0; index < length; index += 1) {
    result[index] = read(index * Constructor.BYTES_PER_ELEMENT);
  }
  return result;
}

function decodeFloat16(value: number): number {
  const sign = (value & 0x8000) === 0 ? 1 : -1;
  const exponent = (value >>> 10) & 0x1f;
  const fraction = value & 0x03ff;
  if (exponent === 0) return sign * 2 ** -14 * (fraction / 1024);
  if (exponent === 0x1f) return fraction === 0 ? sign * Infinity : Number.NaN;
  return sign * 2 ** (exponent - 15) * (1 + fraction / 1024);
}

function readUint16(bytes: Uint8Array, offset: number): number {
  return bytes[offset]! | (bytes[offset + 1]! << 8);
}

function readUint32(bytes: Uint8Array, offset: number): number {
  return (
    bytes[offset]! +
    bytes[offset + 1]! * 0x100 +
    bytes[offset + 2]! * 0x1_0000 +
    bytes[offset + 3]! * 0x100_0000
  );
}
