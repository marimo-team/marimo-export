# NumPy loader

Load an exported NumPy array as typed browser data:

```ts
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";

const array = await output.load(numpyLoader());
console.log(array.shape, array.dtype, array.data);
```

The loader supports numeric NPY v1, v2, and v3 arrays.
