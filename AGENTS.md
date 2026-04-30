# Agent Notes

Before handoff, run the top-level QA commands from this workspace:

```bash
pnpm format
pnpm lint
pnpm typecheck
```

All three must pass. If `pnpm format` changes files, review the diff and rerun
`pnpm lint` and `pnpm typecheck` before handing off.
