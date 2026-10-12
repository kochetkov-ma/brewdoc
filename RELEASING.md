# Releasing

Release notes stay short: product advantage, concrete changes, exact install choices,
actual asset links and channel availability. Use this product description:

Fast, deterministic document-to-Markdown conversion for agent systems. Easy to install. No model weights, OCR or external services.

Link version-specific INSTALL.md for practical lifecycle instructions; historical
0.2.0/0.3.0/0.3.1 tags lack that file and link their own README instead. Never change
release tags/package bytes merely to revise notes.
Current installation examples select 0.3.2; each channel needs publication and consumer acceptance.

## Channels and publisher setup

| Channel | Targets | Required public proof |
|---|---|---|
| PyPI | Declared Python compatibility | Wheel/sdist bytes, fresh registry installs, API/CLI/self-check |
| GitHub native | Linux x64/glibc, Windows x64, macOS ARM64 | Three exact public archives/checksums and native consumers |
| npm | Linux x64/glibc, macOS ARM64 | Platform packages before main, exact bytes/integrity and npm/npx consumers on both hosts |

Do not publish Windows npm. Windows uses GitHub native or Python. Homebrew remains
separate and unavailable until its own formula/consumer acceptance.

PyPI uses OIDC with trusted publisher owner kochetkov-ma, repository brewdoc,
workflow release.yml and environment pypi. npm requires authorized creation/write
rights for brewdoc, brewdoc-linux-x64-glibc and brewdoc-darwin-arm64. Retain existing
owner-authorized bootstrap access or separately configured per-package trusted
publishers; configuration alone does not prove publication. Recheck
[npm token policy](https://docs.npmjs.com/about-access-tokens/) before using that route.
Keep credential values out of source, packages, logs and evidence.

## Delivery

1. Accept scoped source, discover public/tag versions and select an unused version
   before the new native matrix. Update project/API/lock/docs together on the task
   branch. Plain uv lock must preserve third-party pins unless separately changed.
2. Complete final local checks, reviewed PR smoke/native proof, squash merge and
   exact merged-main CI. Review its complete successful producer artifacts and
   publisher readiness before tagging. The tag equals v plus project.version.
3. The tag workflow admits that exact main push producer, complete unexpired
   artifacts, source/tool identities, transport digests and package/consumer scope.
   Reuse its wheel/sdist/native/npm bytes without rebuilding. Missing, foreign,
   stale or differing evidence refuses; dependency cache hits are not acceptance.
4. Publish only admitted channels. Inspect public state first: existing matching
   bytes may be reused, missing files completed, differing/unknown state refused.
   Platform npm packages precede main. No blanket skip-existing or overwrite.
5. Read back actual public bytes and notes, then execute fresh consumers on each
   advertised target. Keep source, local checks, CI, publication and runtime proof
   separate. Channels can become available at different times; pending npm must
   be explicit in notes and overall acceptance stays open until its required proof.

Use built-in failed-job reruns only when their dependent jobs and public-state
admission are safe. Recheck run/artifact identity; do not retry generic registry
refusals blindly. No source/version changes are implied by an operational retry.

## Validation

After final source/version, run the full Python suite including corpus and full
Node launcher suite locally before finishing the PR:

```sh
uv sync --locked --group dev --extra render-js
uv run --locked --extra render-js pytest -q
node --test npm/test/launcher.test.cjs
```

Use development Python 3.12 and exact maintained tool pins. Verify final wheel and
sdist installations outside the source checkout; source tests do not prove installability.
CI runs small API/CLI/install/native/npm smoke checks plus four actual Windows
source DNS lifecycle cases. Full Python/corpus/Node suites remain local. Native
proof covers three hosts; npm proof covers Linux/macOS. At most two heavy jobs run.

The guide helper validates version, targets, asset hashes and availability before
rendering concise notes. Keep public byte/consumer gates when shortening text.
Full commands belong in [INSTALL.md](INSTALL.md); format contracts in [FORMATS.md](FORMATS.md).

## Historical releases

0.2.0/0.3.0 are Python wheel/sdist releases. Their notes describe their original
features; do not retrofit later assets or EPUB/URL features into 0.2.0.
0.3.1 retains source 7c923c99f38f5f8146a64cb4f2eb7784cab4b0d7, its fixed tag and
13 original assets. Python/GitHub/native are available; npm/npx registry installation
remains unavailable. Existing native evidence and reviewed notes may be retained;
package/seal/provenance identities are not rewritten. The fixed resume-v031 and
verify-v031-binaries controller operations are retired. Notes-only revisions require
exact public readback, without a new version, package rebuild or PyPI republish.

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
