# @marimo-team/export-loader-parquet

Loader for `dataframe.parquet.v1` formats.

This package converts exported Parquet bytes into browser-usable row and
metadata handles with `hyparquet`.

## Installation

```bash
npm install @marimo-team/export-loader-parquet @marimo-team/export-reader
```

## Usage

```ts
import { parquetLoader } from "@marimo-team/export-loader-parquet";
import { readExport } from "@marimo-team/export-reader";

const parquetLoaderForExport = parquetLoader();
const exp = await readExport({ root: "/export/" });

const handle = exp.get({
  scenario: "default",
  value: "prices",
  format: "parquet",
});

const parquet = await handle.load(parquetLoaderForExport);

const metadata = await parquet.readMetadata();
const rows = await parquet.readRows({ columns: ["Date", "Close"] });
```

## Contract

- Supports `dataframe.parquet.v1`.
- Reads verified format bytes through the reader context before passing them
  to `hyparquet`.
- Exposes `.readMetadata()` and `.readRows()`.
- Supports column and row-range reads.
