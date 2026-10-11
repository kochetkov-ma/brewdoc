# Releasing

A push of a `v*.*.*` tag runs `release.yml`: build and independent installed-package checks,
publish to PyPI through trusted publishing, then GitHub Release with the sdist and wheel.
Manual dispatch also supports guarded recovery of the immutable original 0.2.0 release.

## One-time setup (manual)

| # | step | where |
|---|---|---|
| 1 | Create the PyPI project `brewdoc` or use the pending-publisher form | pypi.org |
| 2 | Add a trusted publisher: owner `kochetkov-ma`, repository `brewdoc`, workflow `release.yml`, environment `pypi` | pypi.org -> project -> Publishing |
| 3 | Create the GitHub environment `pypi` (optionally with required reviewers) | github.com/kochetkov-ma/brewdoc -> Settings -> Environments |

Publication requires a configured trusted publisher. A missing PyPI project can have a pending
publisher; its public 404 response does not establish setup status. The actual publish job
reports a missing or mismatched publisher binding.

Homebrew is optional after the first PyPI release. The tap `kochetkov-ma/homebrew-brew`
needs a `brewdoc` formula pointing at the published sdist before installation is advertised.

The workflow uses no stored PyPI API token: `pypa/gh-action-pypi-publish` exchanges its OIDC token
(`id-token: write`) for a short-lived PyPI token.

## Per release

| # | step |
|---|---|
| 1 | Accept the intended source and corpus changes on the working branch, discover tags/releases/PyPI versions, then select an unused version. Update `project.version`, `brewdoc.__version__`, `uv.lock` and pinned examples on that same branch. Use `uv lock` without upgrade flags; third-party lock entries and dependency constraints must stay unchanged. Complete final-version wheel/sdist checks outside the checkout before the consolidated PR is squash merged into `main`. |
| 2 | Verify required CI at the exact merged-main commit, publisher readiness and the clean release checkout. Recheck version availability. Tag and push: `git tag vX.Y.Z && git push origin refs/tags/vX.Y.Z` (the tag must equal `v` + `project.version`; `release.yml` refuses a mismatch). |
| 3 | Watch `release.yml`: build (sdist + wheel), publish to PyPI, GitHub Release with `dist/*` attached |
| 4 | Download wheel and sdist from PyPI and GitHub and match their sizes/hashes to accepted artifacts. Verify separate fresh normal registry wheel and forced-sdist installations outside the checkout, including origin/version, API, self-check, receipt-first CLI and `--out`. Record failures without claiming publication or closing the task. |

## Prepared 0.3.0 iteration

Version 0.3.0 adds offline EPUB 2/3 conversion in spine order, Unicode publication metadata
and navigation. It also includes the delivered URL acquisition and optional finite JavaScript
capture, reviewed artifact Action patches and a two-job CI matrix cap. Python remains `>=3.12`;
EPUB adds no runtime dependency. Installation examples remain conditional until publication.

Issues #18, #19, #20 and #12 use one branch and one consolidated PR. Source/corpus acceptance
unlocks version preparation; final-version packages precede merge, and exact-main CI precedes
the new tag. Before merge, original recovery must be complete or its reviewed fallback must be
included. Original 0.2.0 publication and public installations must pass before tagging 0.3.0.
Publication-dependent issues stay open after source merge until their own public checks pass.

## Original 0.2.0 recovery

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
before rerunning. If this path cannot finish safely, use the reviewed dispatch operations
from `release.yml` on merged `main`. [Manual dispatch requires the workflow on the default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow):

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
