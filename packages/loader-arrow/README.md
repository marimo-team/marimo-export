# Arrow loader workspace

`@marimo-export/internal-loader-arrow` owns [Apache Arrow](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc)
interprocess communication file decoding for the public
[`@marimo-team/marimo-export/loader/arrow`](../browser/src/loader/arrow.ts)
facade. The workspace package is private and the public browser package carries
its compiled implementation.

The loader validates the Arrow media type, registers bounded
[LZ4](https://github.com/Benzinga/lz4js) decompression, and returns a
[Flechette](https://github.com/uwdata/flechette) table from verified export bytes.

Run focused checks from the repository root:

```bash
pnpm --filter @marimo-export/internal-loader-arrow test
pnpm --filter @marimo-export/internal-loader-arrow typecheck
```

Public consumers install `@marimo-team/marimo-export` and follow the
[Arrow representation and peer-runtime contract](../../docs/reference/representations.md).
