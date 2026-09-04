# Releasing marimo-export

One annotated `vX.Y.Z` tag publishes
[`marimo-export`](https://pypi.org/project/marimo-export/) to PyPI and
[`@marimo-team/marimo-export`](https://www.npmjs.com/package/@marimo-team/marimo-export)
to npm from the same source commit.

```console
make check
make package
./scripts/release.sh --dry-run
```

`make package` writes the release candidates under `dist/python` and
`dist/npm`. It installs the npm tarball through pnpm, installs the direct and
source-rebuilt Python wheels in isolated environments, and compares the two
wheel payloads.

## Coordinated version

These manifests carry the same version:

| Package                      | Version source                   |
| ---------------------------- | -------------------------------- |
| `marimo-export`              | `packages/python/pyproject.toml` |
| `@marimo-team/marimo-export` | `packages/browser/package.json`  |

The portable JSON workspace owns the shared TypeScript value contract. Vite+
bundles that implementation into the browser package, so consumers install one
npm package. The artifact verifier rejects workspace, link, file, and catalog
dependency sources in published package metadata.

Update the two public manifests, then refresh the locks:

```console
VERSION=x.y.z
uv version --package marimo-export "$VERSION"
pnpm --dir packages/browser pkg set "version=$VERSION"
uv lock
pnpm install --lockfile-only
```

## Registry trust

The GitHub repository requires existing `npm` and `pypi` environments. The
release preflight checks both names before creating or accepting a release tag.

Make the GitHub repository public before tagging. The release preflight checks
repository visibility because npm provenance links each public package to its
public source repository.

Configure the existing PyPI project to trust:

- owner `marimo-team`
- repository `marimo-export`
- workflow `publish.yml`
- environment `pypi`

Configure the npm package with the same owner, repository, and workflow plus
environment `npm`. Allow `npm publish`. The publish job runs on Node 24 with
`id-token: write` and publishes the verified pnpm-produced tarballs. npm uses
the workflow's
[OpenID Connect](https://openid.net/developers/how-connect-works/) identity and
records provenance for each package.

Repository installs, packing, version changes, registry reads, and consumer
checks use pnpm. The final registry write uses npm CLI because npm trusted
publishing performs its OpenID Connect exchange inside `npm publish`.

npm requires a package to exist before it can accept a trusted-publisher
configuration. Reserve the package name through an authenticated maintainer
publication, then configure its trusted publisher before the first coordinated
release. The release preflight checks that the package exists.

The release preflight checks that the `npm` and `pypi` environments exist.
Required reviewers remain an optional repository policy. Apply a repository
ruleset to `v*` tags when the release process needs maintainer approval before
publication.

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
- the browser tarball contains every declared export target and its bundled
  portable JSON implementation
- Python wheel entry points include the CLI and Marimo kernel lifespan
- packed manifests point to the public repository

Merge after CI and documentation checks pass for the release commit.

## Tag the verified commit

Fetch `main` and tags from a clean checkout, then run:

```console
git fetch origin main --tags
./scripts/release.sh --dry-run
./scripts/release.sh
```

The preflight requires `HEAD` to equal `origin/main`. A branch checkout and a
detached checkout at that commit are both accepted. It also requires a clean
tree, an unused final-version tag, matching public package versions, both
publishing environments, and successful push-event CI for the exact commit. The
final command creates and pushes an annotated `vX.Y.Z` tag.

The publish workflow then:

1. Rechecks that the annotated tag resolves to the workflow commit, the commit
   is on the fetched `origin/main` history, and push-event CI passed that exact
   commit.
2. Rebuilds and verifies every artifact.
3. Writes SHA-256 checksums and records GitHub build provenance.
4. Publishes the browser package to npm.
5. Verifies the npm archive by integrity and through a fresh pnpm consumer.
6. Publishes the Python wheel and source archive through PyPI trusted
   publishing.
7. Verifies the PyPI file hashes, a fresh public Python installation, and the
   CLI.
8. Creates a GitHub Release with generated notes, distributions, and the
   checksum manifest.

## Recover a partial release

Registry versions are immutable. The npm publisher compares an existing
version's integrity with the tagged tarball. PyPI publication compares existing
files through the simple index and skips files whose hashes match.

Before either registry accepts a package, rerun the failed job and its dependent
jobs:

```console
gh run rerun RUN_ID --failed
```

After one registry accepts a package, download the workflow artifacts and
compare the published bytes with the tagged artifacts:

```console
ARTIFACTS="$(mktemp -d)"
trap 'rm -rf "$ARTIFACTS"' EXIT
gh run download RUN_ID --name release-artifacts --dir "$ARTIFACTS"
```

When npm accepted the browser package, verify its integrity:

```console
./scripts/publish-npm.sh --verify-only \
  "$ARTIFACTS/npm/marimo-team-marimo-export-$VERSION.tgz"
```

When PyPI accepted the Python distributions, verify their SHA-256 hashes:

```console
uv run --no-project --isolated python scripts/verify_pypi_artifacts.py \
  "$VERSION" --dist "$ARTIFACTS/python"
```

After the applicable verifier passes, rerun the failed job and its dependent
jobs:

```console
gh run rerun RUN_ID --failed
```

Advance both public packages to the next patch version when published bytes
differ or the source needs a correction.

## Verify a published release

Verify the public tag, workflow runs, release assets, attestations, registry
bytes, metadata, and fresh consumer jobs with one command:

```console
./scripts/verify-release.sh "$VERSION"
./scripts/verify-release.sh "$VERSION" --json
```

The command downloads release assets into a temporary directory and leaves the
working tree unchanged. It uses the release workflow's pnpm and Python consumer
jobs as fresh-install evidence, so local package age policy stays active.
