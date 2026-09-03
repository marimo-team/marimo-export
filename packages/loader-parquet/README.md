# Parquet loader workspace

`@marimo-export/internal-loader-parquet` owns [Parquet](https://parquet.apache.org/docs/) row decoding for the public
[`@marimo-team/marimo-export/loader/parquet`](../browser/src/loader/parquet.ts)
facade. The workspace package is private and the public browser package carries
its compiled implementation.

The loader passes column and row selection to
[Hyparquet](https://github.com/hyparam/hyparquet), reads verified BlobAsset bytes,
and returns a frozen outer row array. Individual row objects and nested values
retain Hyparquet's runtime shapes. Cancellation rejects the loader call and
removes its result authority, while an in-progress Hyparquet decode can still
settle afterward.

Run focused checks from the repository root:

```bash
pnpm --filter @marimo-export/internal-loader-parquet test
pnpm --filter @marimo-export/internal-loader-parquet typecheck
```

Public consumers install `@marimo-team/marimo-export` and follow the
[Parquet representation and peer-runtime contract](../../docs/reference/representations.md).
