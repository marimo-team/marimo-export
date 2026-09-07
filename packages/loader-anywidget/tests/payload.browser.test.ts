import { describe, expect, test } from "vite-plus/test";

import { loadPayload, moduleUrl, notification, payload } from "./fixture.js";

const MIB = 1024 * 1024;

describe("AnyWidget payload in Chromium", () => {
  test("decodes an 8 MiB canonical base64 buffer", async () => {
    const encoded = "A".repeat(8 * MIB);
    const loaded = await loadPayload<{ binary: DataView }>(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { binary: null },
            moduleUrl: moduleUrl("export default {}"),
            bufferPaths: [["binary"]],
            buffers: [encoded],
          }),
        ],
      }),
    );

    const buffer = loaded.initialState.binary;
    expect(buffer).toBeInstanceOf(DataView);
    expect(buffer.byteLength).toBe(6 * MIB);
  });
});
