import { describe, expect, test } from "vite-plus/test";

import { numpyLoader } from "../src/index.js";

const descriptor = {
  asset: { sha256: "a".repeat(64), size: 0 },
  codec: "numpy.npy.v1",
  mediaType: "application/x-npy",
  provenance: { pythonType: "numpy.ndarray" },
} as const;
const mediaType = {
  raw: "application/x-npy",
  essence: "application/x-npy",
  type: "application",
  subtype: "x-npy",
  parameters: new Map(),
} as const;

describe("numpyLoader", () => {
  test("decodes little-endian C-order float64 arrays", async () => {
    const payload = npy("<f8", [2, 2], false, (view, offset, index) =>
      view.setFloat64(offset, index + 0.5, true),
    );

    const result = await numpyLoader().load({ descriptor, mediaType, payload });

    expect(result.shape).toEqual([2, 2]);
    expect(result.dtype).toEqual({
      descriptor: "<f8",
      kind: "floating-point",
      itemSize: 8,
      byteOrder: "little",
    });
    expect([...new Float64Array(result.data.buffer)]).toEqual([0.5, 1.5, 2.5, 3.5]);
    expect(result.fortranOrder).toBe(false);
  });

  test("normalizes big-endian Fortran-order integers", async () => {
    const payload = npy(">i2", [3], true, (view, offset, index) =>
      view.setInt16(offset, (index + 1) * -7, false),
    );

    const result = await numpyLoader().load({ descriptor, mediaType, payload });

    expect([...new Int16Array(result.data.buffer)]).toEqual([-7, -14, -21]);
    expect(result.fortranOrder).toBe(true);
    expect(result.dtype.byteOrder).toBe("big");
  });

  test("preserves complex values as interleaved components", async () => {
    const payload = npy("<c8", [2], false, (view, offset, index) =>
      view.setFloat32(offset, index + 0.25, true),
    );

    const result = await numpyLoader().load({ descriptor, mediaType, payload });

    expect([...new Float32Array(result.data.buffer)]).toEqual([0.25, 1.25, 2.25, 3.25]);
  });

  test("accepts scalar and empty arrays", async () => {
    const scalar = npy("|u1", [], false, (view) => view.setUint8(0, 9));
    const empty = npy("<f4", [0, 3], false, () => undefined);

    expect([
      ...((await numpyLoader().load({ descriptor, mediaType, payload: scalar }))
        .data as Uint8Array),
    ]).toEqual([9]);
    expect(
      (await numpyLoader().load({ descriptor, mediaType, payload: empty })).data.byteLength,
    ).toBe(0);
  });

  test.each(["|O8", "|S4", "<U4", "[('x','<i4')]"])("rejects unsupported dtype %s", (dtype) => {
    const payload = npyBytes(dtype, [1], false, new Uint8Array(8));
    expect(() => numpyLoader().load({ descriptor, mediaType, payload })).toThrow();
  });

  test("rejects mismatched payload lengths and aborts", () => {
    const malformed = npyBytes("<i4", [2], false, new Uint8Array(4));
    expect(() => numpyLoader().load({ descriptor, mediaType, payload: malformed })).toThrow(
      "does not match",
    );

    const controller = new AbortController();
    controller.abort();
    expect(() =>
      numpyLoader().load({
        descriptor,
        mediaType,
        payload: npyBytes("|u1", [1], false, new Uint8Array([1])),
        signal: controller.signal,
      }),
    ).toThrow(expect.objectContaining({ name: "AbortError" }));
  });
});

function npy(
  dtype: string,
  shape: readonly number[],
  fortranOrder: boolean,
  write: (view: DataView, offset: number, index: number) => void,
): Uint8Array {
  const itemSize = Number(/\d+$/u.exec(dtype)?.[0] ?? "1");
  const components = dtype.includes("c") ? 2 : 1;
  const count = shape.reduce((value, dimension) => value * dimension, 1) * components;
  const payload = new Uint8Array(count * (itemSize / components));
  const view = new DataView(payload.buffer);
  for (let index = 0; index < count; index += 1) {
    write(view, index * (itemSize / components), index);
  }
  return npyBytes(dtype, shape, fortranOrder, payload);
}

function npyBytes(
  dtype: string,
  shape: readonly number[],
  fortranOrder: boolean,
  payload: Uint8Array,
): Uint8Array {
  const shapeLiteral =
    shape.length === 0 ? "" : `${shape.join(", ")}${shape.length === 1 ? "," : ""}`;
  const base = `{'descr': '${dtype}', 'fortran_order': ${fortranOrder ? "True" : "False"}, 'shape': (${shapeLiteral}), }`;
  const prefixLength = 10;
  const padding = 64 - ((prefixLength + base.length + 1) % 64);
  const header = `${base}${" ".repeat(padding)}\n`;
  const headerBytes = new TextEncoder().encode(header);
  const result = new Uint8Array(prefixLength + headerBytes.byteLength + payload.byteLength);
  result.set([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 1, 0], 0);
  result[8] = headerBytes.byteLength & 0xff;
  result[9] = headerBytes.byteLength >>> 8;
  result.set(headerBytes, prefixLength);
  result.set(payload, prefixLength + headerBytes.byteLength);
  return result;
}
