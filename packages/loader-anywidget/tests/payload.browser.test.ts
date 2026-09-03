import { describe, expect, test } from "vite-plus/test";

import { parseAnyWidgetPayload } from "../src/payload.js";
import { moduleUrl, notification, payload } from "./fixture.js";

const MIB = 1024 * 1024;

describe("AnyWidget payload in Chromium", () => {
  test("decodes an 8 MiB canonical base64 buffer", () => {
    const encoded = "A".repeat(8 * MIB);
    const snapshot = parseAnyWidgetPayload(
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

    const buffer = snapshot.models.get("model-0")!.state.binary;
    expect(buffer).toBeInstanceOf(DataView);
    if (!(buffer instanceof DataView)) throw new TypeError("Fixture buffer must be a DataView.");
    expect(buffer.byteLength).toBe(6 * MIB);
  });
});
