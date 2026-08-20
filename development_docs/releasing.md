# Releasing marimo-export

One annotated `vX.Y.Z` tag publishes the Python package and both public npm
packages from the same source commit.

```console
make check
make package
./scripts/release.sh --dry-run
```

`make package` writes the release candidates under `dist/python` and
`dist/npm`. It installs both npm tarballs through npm and pnpm, installs the
direct and source-rebuilt Python wheels in isolated environments, and compares
the two wheel payloads.

## Coordinated version

These manifests carry the same version:

| Package                      | Version source                        |
| ---------------------------- | ------------------------------------- |
| `marimo-export`              | `packages/python/pyproject.toml`      |
| `@marimo-team/marimo-export` | `packages/browser/package.json`       |
| `@marimo-team/portable-json` | `packages/portable-json/package.json` |

The browser package keeps `workspace:*` while developing in the repository.
`pnpm pack` rewrites that dependency to the coordinated portable-json version
inside the release tarball. The artifact verifier rejects workspace, link,
file, and catalog dependency sources in published package metadata.

Update the three manifests, then refresh the locks:

```console
uv version --package marimo-export 0.1.0
pnpm --dir packages/browser version 0.1.0 --no-git-tag-version
pnpm --dir packages/portable-json version 0.1.0 --no-git-tag-version
uv lock
pnpm install --lockfile-only
```

Use the intended final version in place of `0.1.0` for later releases.

## Registry trust

The GitHub repository requires `npm` and `pypi` environments.

Configure the existing PyPI project to trust:

- owner `marimo-team`
- repository `marimo-export`
- workflow `publish.yml`
- environment `pypi`

Configure each npm package with the same owner, repository, and workflow plus
environment `npm`. Allow `npm publish`. The publish job runs on Node 24 with
`id-token: write` and uploads pnpm-produced tarballs.

The first publication of a new npm package must create its package settings.
Place one short-lived granular token in the protected `npm` environment as
`NPM_BOOTSTRAP_TOKEN` for that publication. After both packages verify:

1. Configure their trusted publishers.
2. Remove and revoke the bootstrap token.
3. Set publishing access to require two-factor authentication and disallow
   token publication.

Later releases leave `NPM_BOOTSTRAP_TOKEN` unset. npm uses the workflow's OIDC
identity and emits provenance for each package.

## Prepare the release commit

Start from a branch based on synchronized `main`. Update the coordinated
version and run:

```console
make bootstrap
make check
make package
```

Review these artifact facts before merging:

- the wheel, source archive, and source-rebuilt wheel report the same version
- the browser tarball depends on the exact portable-json version
- npm tarballs contain every declared export target
- Python wheel entry points include the CLI and Marimo kernel lifespan
- packed manifests point to the public repository

Merge after CI and documentation checks pass for the release commit.

## Tag the verified commit

Update local `main`, then run:

```console
git pull --ff-only origin main
./scripts/release.sh --dry-run
./scripts/release.sh
```

The preflight requires clean synchronized `main`, an unused final-version tag,
matching public package versions, and successful push-event CI for the exact
commit. The final command creates and pushes an annotated `vX.Y.Z` tag.

The publish workflow then:

1. Rechecks that the annotated tag, workflow commit, current `origin/main`, and
   successful push-event CI identify one commit.
2. Rebuilds and verifies every artifact.
3. Publishes portable-json to npm.
4. Publishes the browser package to npm.
5. Verifies both packages through fresh npm and pnpm consumers.
6. Publishes the Python wheel and source archive through PyPI trusted
   publishing.
7. Verifies a fresh public Python installation and CLI.
8. Creates the GitHub release notes.

## Recover a partial release

Registry versions are immutable. The npm publisher compares an existing
version's integrity with the tagged tarball. PyPI publication uses the simple
index to skip identical files.

Rerun a failed job when every existing registry artifact matches the tagged
artifact. Advance all three public packages to the next patch version when a
published artifact differs or needs a source correction.
