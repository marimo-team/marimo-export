# Arrow loader

Load an exported Arrow table with Flechette:

```ts
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";

const table = await output.load(arrowTableLoader());
console.log(table.numRows);
```

Install `@uwdata/flechette` and `lz4js` beside
`@marimo-team/marimo-export`.
