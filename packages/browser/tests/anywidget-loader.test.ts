import { encode } from "@msgpack/msgpack";
import { describe, expect, expectTypeOf, test } from "vite-plus/test";
import producerPayload from "../../python/tests/fixtures/anywidget-v1.json" with { type: "json" };

import { anyWidgetLoader, type LoadedAnyWidget } from "../src/loader/anywidget.js";
import { openExport } from "../src/index.js";
import type { BlobAssetLoader } from "../src/types.js";
import { exportFixture } from "./fixture.js";

const encoder = new TextEncoder();
const mediaType = "application/vnd.marimo-export.anywidget.v1+json";

describe("AnyWidget loader adapter", () => {
  test("binds the AnyWidget decoder to the BlobAsset loader contract", async () => {
    interface MapState {
      child: string;
      binary: { view: DataView };
    }
    const loader = anyWidgetLoader<MapState>();
    expectTypeOf(loader).toEqualTypeOf<BlobAssetLoader<LoadedAnyWidget<MapState>>>();
    expect(loader.codec).toBe("marimo.blob-asset.msgpack.v1");
    const fixture = await exportFixture({
      blobFilename: null,
      blobMetadata: {},
      blobMediaType: mediaType,
      envelope: envelope(producerPayload),
    });
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    const loaded = await notebookExport.state("alpha").output("view").load(loader);

    expect(loaded.initialState.child).toBe("anywidget:model-1");
    expect([...new Uint8Array(loaded.initialState.binary.view.buffer)]).toEqual([1, 2, 3]);
  });

  test("rejects incompatible media types before decoding", async () => {
    const fixture = await exportFixture({
      blobFilename: null,
      blobMetadata: {},
      blobMediaType: "application/json",
      envelope: envelope(producerPayload, "application/json"),
    });
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(
      notebookExport.state("alpha").output("view").load(anyWidgetLoader()),
    ).rejects.toMatchObject({ code: "loader_unavailable" });
  });

  test("adds output context to AnyWidget decode failures", async () => {
    const fixture = await exportFixture({
      blobFilename: null,
      blobMetadata: {},
      blobMediaType: mediaType,
      envelope: envelope({ schema: "invalid" }),
    });
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(
      notebookExport.state("alpha").output("view").load(anyWidgetLoader()),
    ).rejects.toMatchObject({
      code: "decode_failed",
      details: {
        codec: "marimo.blob-asset.msgpack.v1",
        mediaType,
        output: "view",
      },
    });
  });
});

const envelope = (payload: unknown, valueMediaType = mediaType): Uint8Array =>
  encode({
    data: encoder.encode(JSON.stringify(payload)),
    media_type: valueMediaType,
    filename: null,
    metadata: {},
  });
