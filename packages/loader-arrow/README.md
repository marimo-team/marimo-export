# marimo-export Arrow loader

`arrowTableLoader()` decodes verified `apache.arrow.file.v1` outputs as
Flechette tables. The loader registers Arrow LZ4 frame decoding itself.

```ts
import { arrowTableLoader } from "@marimo-team/marimo-export-loader-arrow";

const table = await output.load(arrowTableLoader());
```
