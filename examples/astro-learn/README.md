# Astro learn gallery

Astro 6.2 SSG example that builds a static gallery from a running marimo learn
workspace.

This example lists notebooks from a marimo server, reads source metadata for
card titles, groups notebooks by topic, and renders static HTML. It does not
execute cells, capture bundles, render notebook outputs, or load marimo
frontend components.

The gallery is scoped to the `altair`, `optimization`, and `tools` learn
topics.

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

Useful environment variables:

- `MARIMO_LEARN_SERVER_URL`: marimo server URL.
- `MARIMO_LEARN_SERVER_TOKEN`: token password used to start the server.
- `MARIMO_LEARN_LIMIT`: optional maximum number of notebooks to render.
