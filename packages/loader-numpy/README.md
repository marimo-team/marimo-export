# NumPy loader workspace

`@marimo-export/internal-loader-numpy` owns decoding for
[NumPy's NPY array-file format](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.html) through the public
[`@marimo-team/marimo-export/loader/numpy`](../browser/src/loader/numpy.ts)
facade. The workspace package is private and the public browser package carries
its compiled implementation.

The decoder accepts numeric NPY version 1, 2, and 3 arrays, validates the header,
shape, dtype, byte order, and payload length, then returns typed browser data.

Run focused checks from the repository root:

```bash
pnpm --filter @marimo-export/internal-loader-numpy test
pnpm --filter @marimo-export/internal-loader-numpy typecheck
```

Public consumers install `@marimo-team/marimo-export` and follow the
[NumPy representation contract](../../docs/reference/representations.md).
