import { expect, test } from "vite-plus/test";

import { defineBlobAssetLoader, openPublication } from "../src/index.js";
import { hexBytes, publicationFixture } from "./fixture.js";

test("decodes the exact native four-field msgspec envelope with direct metadata", async () => {
  const envelope = hexBytes(
    "84a464617461c40178aa6d656469615f74797065b06170706c69636174696f6e2f74657374a866696c656e616d65c0a86d6574616461746181a176cb3ff0000000000000",
  );
  const fixture = await publicationFixture({
    envelope,
    blobMediaType: "application/test",
    blobFilename: null,
    blobMetadata: { v: 1 },
  });
  const publication = await openPublication("https://example.test/stocks", {
    fetch: fixture.fetch,
  });
  const loader = defineBlobAssetLoader({
    mediaTypes: "application/test",
    load: ({ payload }) => payload,
  });

  const value = await publication.state("alpha").output("view").load(loader);
  expect(value.metadata).toEqual({ v: 1 });
  expect(new TextDecoder().decode(value.data)).toBe("x");
});

test("rejects a non-native envelope field order", async () => {
  const envelope = hexBytes(
    "84aa6d656469615f74797065b06170706c69636174696f6e2f74657374a464617461c40178a866696c656e616d65c0a86d6574616461746181a17601",
  );
  const fixture = await publicationFixture({
    envelope,
    blobMediaType: "application/test",
    blobFilename: null,
    blobMetadata: { v: 1 },
  });
  const publication = await openPublication("https://example.test/stocks", {
    fetch: fixture.fetch,
  });
  const loader = defineBlobAssetLoader({
    mediaTypes: "application/test",
    load: ({ payload }) => payload,
  });

  await expect(publication.state("alpha").output("view").load(loader)).rejects.toMatchObject({
    code: "asset_invalid",
  });
});
