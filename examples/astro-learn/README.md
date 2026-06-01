# Astro learn gallery

Astro 6.2 SSG example that builds a static gallery from marimo learn notebook
metadata.

The default build uses a committed catalog fixture, groups notebooks by topic,
and renders static HTML. It does not execute cells, capture bundles,
materialize display outputs, or load marimo frontend components.

Run the static build:

```bash
pnpm --filter @marimo-team/export-example-astro-learn build
```

The gallery is scoped to the `altair`, `optimization`, and `tools` learn topics.

## Live Catalog

Start the learn repository separately:

```bash
uvx marimo edit . --sandbox --port 7676 --token-password learn --no-skew-protection
```

Build the gallery:

```bash
MARIMO_LEARN_SERVER_URL=http://localhost:7676 \
MARIMO_LEARN_SERVER_TOKEN=learn \
pnpm --filter @marimo-team/export-example-astro-learn build
```

For local development:

```bash
MARIMO_LEARN_SERVER_URL=http://localhost:7676 \
MARIMO_LEARN_SERVER_TOKEN=learn \
pnpm --filter @marimo-team/export-example-astro-learn dev
```

Environment variables:

- `MARIMO_LEARN_SERVER_URL`: marimo server URL. When omitted, the build uses
  the committed catalog fixture.
- `MARIMO_LEARN_SERVER_TOKEN`: token password used to start the server.
- `MARIMO_LEARN_LIMIT`: optional maximum number of notebooks to render.
