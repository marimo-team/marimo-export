# NumPy loader workspace

This private workspace package owns verified NPY decoding and its typed browser
result.

Consumers import the public loader subpath from
`@marimo-team/marimo-export`:

```ts
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";

const array = await output.load(numpyLoader());
console.log(array.shape, array.dtype, array.data);
```

The parser accepts NPY v1, v2, and v3 numeric arrays. It supports booleans,
signed and unsigned integers, floating point values, and complex values.
Header, shape, allocation, endian, and payload-length checks run before the
typed view is returned.
