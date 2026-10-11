# Releasing

A push of a `v*.*.*` tag runs `release.yml`: admit the successful CI artifacts
from that exact `main` commit, then publish their unchanged accepted bytes.
The tag workflow does not rebuild Python packages, native bundles or npm packages.
PyPI receives only the wheel and sdist. The GitHub release contains their accepted
bytes, the three native archives, manifests, checksums and a complete installation guide.
Selected npm platform packages publish before the main brewdoc package.
Manual dispatch contains the guarded original 0.2.0 recovery path described below.

## One-time setup (manual)

| # | step | where |
|---|---|---|
| 1 | Create the PyPI project `brewdoc` or use the pending-publisher form | pypi.org |
| 2 | Add a trusted publisher: owner `kochetkov-ma`, repository `brewdoc`, workflow `release.yml`, environment `pypi` | pypi.org -> project -> Publishing |
| 3 | Create the GitHub environment `pypi` (optionally with required reviewers) | github.com/kochetkov-ma/brewdoc -> Settings -> Environments |

Publication requires a configured trusted publisher. A missing PyPI project can have a pending
publisher; its public 404 response does not establish setup status. The actual publish job
reports a missing or mismatched publisher binding.

Homebrew remains a separate unpublished channel. Do not advertise a tap or install
command until its formula and actual consumer checks are accepted.

The workflow uses no stored PyPI API token: `pypa/gh-action-pypi-publish` exchanges its OIDC token
(`id-token: write`) for a short-lived PyPI token.

The first npm publication can bootstrap the four new package names with an
owner-authorized granular token stored as the Brewdoc repository's `NPM_TOKEN`
secret. Verify creation/write permission for the selected packages and current
2FA policy. The token value must never appear in source, logs or artifacts.
This initial token route does not establish npm trusted publishing.

After the packages exist, their owner can configure a separate
[trusted publisher](https://docs.npmjs.com/trusted-publishers/) for each package:
GitHub user `kochetkov-ma`, repository `brewdoc`, workflow filename `release.yml`.
Leave environment empty for the current npm job, and permit `npm publish` if
using direct publication. This is a separate owner action; it is not a gate on
the initial token publication or a claim that configuration is complete.
Verify a successful OIDC publish before removing the bootstrap token route.

The token bootstrap is time-limited by npm policy: direct publication with a
granular access token is scheduled for removal in January 2027. Recheck
[current token policy](https://docs.npmjs.com/about-access-tokens/) before future
releases; do not treat token-based direct publishing as an indefinite workflow.

## Per release

| # | step |
|---|---|
| 1 | Accept the intended source changes on the working branch, discover tags/releases/PyPI/npm versions, then select an unused version before the first new three-OS native matrix. Update `project.version`, `brewdoc.__version__`, `uv.lock` and pinned examples on that same branch. Use `uv lock` without upgrade flags; third-party lock entries and dependency constraints must stay unchanged. Complete the final local checks below before finishing the consolidated PR. |
| 2 | Accept the final-version PR smoke/native checks, squash merge into `main`, then review the successful push CI at that exact merged commit and its complete producer artifacts. Verify publisher readiness, the clean release checkout and version availability. Tag and push: `git tag vX.Y.Z && git push origin refs/tags/vX.Y.Z` (the tag must equal `v` + `project.version`; `release.yml` refuses a mismatch). |
| 3 | Watch `release.yml`: admit the exact-main producer, artifact integrity and explicit smoke evidence; reuse its wheel/sdist, native and npm bytes without rebuilding; generate staged notes; publish PyPI and the platform npm packages before the main package; verify the draft's complete assets/body; then expose the GitHub release. No blanket skip-existing, unverified overwrite or partial-success claim. |
| 4 | Download wheel and sdist from PyPI and GitHub and match their sizes/hashes to accepted artifacts. Verify separate fresh normal registry wheel and forced-sdist installations outside the checkout, including origin/version, API, self-check, receipt-first CLI and `--out`. Record failures without claiming publication or closing the task. |

## Local and CI acceptance

After the final source and version are set, run the full Python suite, including
the corpus, and the full Node launcher suite locally before finishing the PR:

```sh
uv sync --locked --group dev --extra render-js
uv run --locked --extra render-js pytest -q
node --test npm/test/launcher.test.cjs
```

Use Python 3.12 and the exact Node/npm versions in `.github/distribution.json`.
Record source identity, interpreter/tool versions, commands, results and explicit
platform skips. Verify the final wheel and sdist outside the source checkout and
exercise the installed API, CLI and self-check. Source tests alone do not prove
installability. Material fixes require fresh affected checks and final local
acceptance; unchanged heavy checks need no speculative repeats.

CI runs a tiny API/CLI contract selection, independent wheel/sdist installation
smoke, native launch/conversion smoke and npm/npx consumer smoke. Windows also
runs exactly four source DNS lifecycle cases: isolated entry, hostile Python
paths, timeout cleanup and owner-exit cleanup. The full Python suite, corpus and
Node launcher suite stay local. Native build/runtime evidence remains required
on Linux x64/glibc, Windows x64 and macOS ARM64. At most two heavy jobs run at once.

The release producer must be a completed successful `ci.yml` push run from this
repository on `main` at the exact tagged commit. Admission checks tracked source
and tool identities, the complete unexpired artifact set, transport digests,
package/archive inventories and explicit smoke evidence for all three targets.
Missing, stale, foreign or mismatched evidence stops publication; there is no
rebuild fallback. Release publication preserves the admitted package/archive bytes.

For a failed run, use GitHub's built-in **Re-run failed jobs** so successful
producer jobs and their admitted artifacts remain available. Check the completed
attempt and evidence again before reuse. Dependency caches save downloads; they
do not prove success or authorize an arbitrary unchanged-binary cache. A new
source, version or tool identity requires a matching successful producer.

## Native, npm and guide gates

The reviewed target facts are maintained in `.github/distribution.json`. Current
version comes from `pyproject.toml`; API and every accepted artifact must match it.
The initial native set is Linux x86_64/glibc, Windows x86_64 and macOS ARM64.
Build each on its matching native host, using the exact embedded Python and locked
dependencies. Verify outside the checkout with Python absent from consumer PATH.
Keep package installation, native runtime proof and public downloads distinct.

Native aggregate admission requires all three matching PASS records and exact
archive bytes. `binaries.json` and `SHA256SUMS` bind archive names/hashes; shipped
brewdoc/version.json binds version/source/Python/target. Keep the whole one-folder
bundle, its native libraries, JS data and licenses intact. Signing descriptions
must reflect the actual artifacts; checksums do not replace publisher signatures.

The npm packages are brewdoc, brewdoc-linux-x64-glibc, brewdoc-win32-x64 and
brewdoc-darwin-arm64, all at the exact same version. The platform tarballs contain
the admitted complete native bundles. Verify packed inventories and SHA-512
integrities, then fresh local installs and cancellation behavior on all three
hosts before publication. No lifecycle install hook or runtime download is used.
Authentication/publication requires specifically authorized npm access. Keep
credentials out of source, packages, notes and evidence.

Render the staged body from the accepted aggregate directories:

```sh
python .github/workflows/release_guide.py check --checkout .
python .github/workflows/release_guide.py render --checkout . --phase staged --native-manifest release-assets/binaries.json --npm-manifest npm-assets/npm-manifest.json --output staged-body.md
```

The staged body marks its registry/install/download commands conditional. The
release workflow's separate asset helper handles draft admission and publication;
the guide helper only checks facts and writes deterministic text.

After publication, download actual GitHub/PyPI/npm bytes and verify their admitted
identities. Run downloaded native archives and fresh public npm/npx installs on
each matching host. Required proof includes receipt-first stdout, --out, self-check,
conversion, runtime prerequisites and cancellation cleanup. Missing native/npm
consumer proof prevents claiming those channels accepted. Existing Python release
availability remains independently true if another channel fails.

The public admission helper aggregates matching byte and consumer records into
public-acceptance.json. The guide helper refuses missing or changed version/source,
archive/package identities, or any required native/npm/npx PASS record:

```sh
python .github/workflows/release_guide.py render --checkout . --phase public --native-manifest release-assets/binaries.json --npm-manifest release-assets/npm-manifest.json --availability public-acceptance.json --output public-body.md
```

The public finalizer regenerates and compares the exact body before updating it.
README's dynamic version badges reflect published registries/releases. Its direct
versioned links remain conditional before first publication; they become usable
without another source commit when that exact release passes the public gates.
Only advertise tested target/runtime/signing facts. Root llms.txt gives concise
public entry links and does not promise client discovery.

Historical 0.2.0/0.3.0 releases are public Python wheel/sdist releases. Preserve
their tags and bytes. Amend guide text only from each release's original features
and available channels; never add later EPUB, URL, native or npm features to 0.2.0,
or native/npm assets to 0.3.0.

## Original 0.2.0 recovery

Original recovery succeeded through the original workflow run. The separate
guarded dispatch preparation failed before producing artifacts and remains
unaccepted; [issue #41](https://github.com/kochetkov-ma/brewdoc/issues/41) tracks
that repair outside this release. The dispatch reference below describes its
intended contract, not a verified recovery path. Normal new releases do not use it.
Keep `v0.2.0` fixed at `dc1f0c7fdedeec024840127ed989e1f58416762a`. Current source must never
be published under 0.2.0. Preserve separate accepted artifacts and installation evidence for
the original and new release identities. Original 0.2.0 does not include EPUB or URL acquisition.

Before retry, the PyPI account owner must confirm the matching `release.yml`/`pypi` publisher
configuration changed after the prior `invalid-publisher` failure. Recheck the original tag,
artifact availability and actual package bytes, then compare any existing public files.
Matching files may be completed; mismatches or unavailable preflight stop publication.
Never move the tag, retry blindly after a partial upload or use blanket `skip-existing`.

The default path reruns failed jobs of original run `36746089723` after artifact acceptance.
Use it only when the public-file preflight permits uploading the original pair:

```sh
gh run rerun 36746089723 --repo kochetkov-ma/brewdoc --failed
gh run watch 36746089723 --repo kochetkov-ma/brewdoc --exit-status
```

GitHub permits [reruns within 30 days](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
of the original run. Artifact expiry does not extend that window; check current eligibility
before rerunning. If this path cannot finish safely, stop until the guarded
dispatch repair and preparation have their own acceptance.
[Manual dispatch requires the workflow on the default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow):

| Operation | Required inputs | Behavior |
|---|---|---|
| `prepare-v020` | Other inputs empty | Validate fixed original source/tag; use the accepted retained artifact when available; rebuild only if absent or expired; refuse identity mismatches; verify original wheel/sdist installations and upload `recovery-v020` with its identity manifest |
| `publish-v020` | Positive decimal `prepared_run_id`; independently approved wheel and sdist SHA-256 values, each 64 lowercase hexadecimal characters | Admit the successful same-commit preparation and exact artifact bytes, then publish only missing accepted files |

The hash fields are `approved_wheel_sha256` and `approved_sdist_sha256`. No arbitrary version,
tag, source SHA, URL or path input is accepted. Preparation has no PyPI environment, OIDC or
release-write permission. A rebuilt archive needs independent acceptance of its new hashes.
Publication requires one unexpired expected artifact from this repository's successful
`release.yml` preparation on `main`, matching workflow commit, manifest, original tag and
approved package bytes. A moved main commit requires fresh preparation.

The publisher preflight checks actual public files: upload both if absent, only the missing
file if one matches, or skip upload if both match. Unexpected files, altered bytes and unavailable
metadata refuse. GitHub completion verifies PyPI first, targets `v0.2.0`, adds only missing
accepted assets and does not promote recovery to latest. Verify public bytes and fresh normal
registry installations before declaring original recovery complete.
