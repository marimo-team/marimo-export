# marimo-export NumPy loader

`numpyLoader()` decodes verified `numpy.npy.v1` outputs into a typed numeric
array, shape, dtype, and memory-order record.

```ts
import { numpyLoader } from "@marimo-team/marimo-export-loader-numpy";

const array = await output.load(numpyLoader());
```
