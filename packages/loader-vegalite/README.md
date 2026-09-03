# Vega-Lite loader workspace

`@marimo-export/internal-loader-vegalite` owns
[Vega-Lite](https://vega.github.io/vega-lite/) decoding and mount lifecycle for the public
[`@marimo-team/marimo-export/loader/vegalite`](../browser/src/loader/vegalite.ts)
facade. The workspace package is private and the public browser package carries
its compiled implementation.

The loader validates the saved chart specification, imports
[Vega-Embed](https://github.com/vega/vega-embed) when the chart mounts, applies
caller options, connects cancellation, and finalizes the mounted view during
disposal.

Run focused checks from the repository root:

```bash
pnpm --filter @marimo-export/internal-loader-vegalite test
pnpm --filter @marimo-export/internal-loader-vegalite typecheck
```

Public consumers install `@marimo-team/marimo-export` and follow the
[Vega-Lite representation and peer-runtime contract](../../docs/reference/representations.md).
