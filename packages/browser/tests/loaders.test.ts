import { describe, expect, test } from "vite-plus/test";

import {
  defineBlobAssetLoader,
  defineOutputLoader,
  imageLoader,
  openExport,
  resolveOutputLoader,
  scalarLoader,
} from "../src/index.js";
import { exportFixture } from "./fixture.js";

describe("OutputLoader", () => {
  test("matches BlobAsset media types by lowercase essence", async () => {
    const fixture = await exportFixture({
      blobMediaType: 'Application/Vnd.Example.Fixture+Json; Charset="utf-8"',
    });
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const output = notebookExport.state("alpha").output("view");
    const loader = defineBlobAssetLoader({
      mediaTypes: "application/vnd.example.fixture+json",
      load: ({ mediaType }) => mediaType.parameters.get("charset"),
    });

    expect(output.mediaType.essence).toBe("application/vnd.example.fixture+json");
    expect(output.mediaType.parameters.get("charset")).toBe("utf-8");
    await expect(output.load(loader)).resolves.toBe("utf-8");
  });

  test("requires exactly one compatible loader", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const output = notebookExport.state("alpha").output("count");
    const declines = defineOutputLoader({
      codec: "marimo.scalar.v1",
      accepts: () => false,
      load: ({ payload }) => payload,
    });

    expect(() => resolveOutputLoader(output, [declines])).toThrowError(
      expect.objectContaining({ code: "loader_unavailable" }),
    );
    expect(() => resolveOutputLoader(output, [scalarLoader(), scalarLoader()])).toThrowError(
      expect.objectContaining({ code: "loader_ambiguous" }),
    );
  });

  test("wraps invalid accepts behavior", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const output = notebookExport.state("alpha").output("count");
    const loader = {
      codec: "marimo.scalar.v1" as const,
      accepts: () => {
        throw new Error("broken");
      },
      load: ({ payload }: { payload: unknown }) => payload,
    };

    expect(() => resolveOutputLoader(output, [loader])).toThrowError(
      expect.objectContaining({ code: "loader_invalid" }),
    );
  });

  test("provides an image loader without a rendering dependency", () => {
    const loader = imageLoader();
    expect(loader.codec).toBe("marimo.blob-asset.msgpack.v1");
    expect(
      loader.accepts(
        {
          asset: { sha256: "a".repeat(64), size: 1 },
          codec: "marimo.blob-asset.msgpack.v1",
          filename: "image.png",
          mediaType: "image/png",
          metadata: {},
          provenance: {
            pythonType: "BlobAsset",
          },
        },
        {
          raw: "image/png",
          essence: "image/png",
          type: "image",
          subtype: "png",
          parameters: new Map(),
        },
      ),
    ).toBe(true);
  });
});
