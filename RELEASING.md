# Releasing

Link version-specific INSTALL.md for practical lifecycle instructions; historical
0.2.0/0.3.0/0.3.1 tags lack that file and link their own README instead. Never change
release tags/package bytes merely to revise notes.
Current installation examples select 0.6.0. List a channel as available only after
its publication and consumer acceptance.

## Release format

Use this shared Codex and Claude contract for 0.5.0 and every future release.
Leave historical release bodies, tags and packages unchanged. The existing
admission, publication and consumer gates below still apply.

The first line is exactly:

```markdown
[Release notes](#release-notes) | [Installation](#installation) | [Downloads](#downloads) | [Documentation](#documentation)
```

Emit these explicit anchors and level-two headings once, in this order:

```markdown
<a name="release-notes"></a>
## Release notes

<a name="installation"></a>
## Installation

<a name="downloads"></a>
## Downloads

<a name="documentation"></a>
## Documentation
```

Under Release notes, use short Added and Fixed groups for major user-visible
changes. Omit empty groups, minor cleanups, internal work and repeated product
descriptions. Preserve material compatibility and limitation notes. Put a useful
minimal example or new command beside the relevant feature when it needs one;
use commands supported by the release source. Link a real relevant PR only when
known; otherwise omit it. Do not invent PR numbers or add live/latest badges.

Put one `---` between Release notes and Installation. Every release body keeps
concise, complete installation, downloads and documentation sections:

- Installation states the Python prerequisite and all three exact-version choices:
  `python -m pip install "brewdoc==0.6.0"` in an active virtual environment,
  `uv tool install --python 3.12 "brewdoc==0.6.0"` and
  `pipx install --python 3.12 "brewdoc==0.6.0"`. Replace 0.6.0 with the release
  version and state its declared Python compatibility. Include
  `brewdoc --self-check` and `brewdoc file.pdf --out file.md`.
- Public npm/npx commands require admission and consumer acceptance on Linux
  x64/glibc and macOS ARM64. Use `npm install --global brewdoc@0.6.0` and
  `npx --yes --package brewdoc@0.6.0 brewdoc --self-check` with the release version.
  Staged notes keep their conditional-publication statement and omit npm commands.
  Homebrew stays pending until public formula readback and both-host acceptance;
  support metadata alone does not establish availability.
- Downloads link every admitted native archive and SHA256SUMS for that exact
  release. Include Linux x64/glibc, Windows x64 and macOS ARM64 when admitted.
  State that bundles include Python: extract and keep the complete directory,
  then run its `brewdoc` executable (`brewdoc.exe` on Windows). Add no unbound links.
- Documentation links the same immutable tag's README.md, INSTALL.md and
  FORMATS.md. INSTALL owns full lifecycle details; release notes retain usable
  installation commands. Do not substitute branch or latest-version links.

Generate deterministic notes from admitted metadata and artifacts. Before
publication, review the body, versions, availability, commands and link targets.
After publication, read back the exact public body and verify the rendered
navigation, documentation links and downloads. Static checks alone do not prove
rendered anchor behavior or consumer availability.

## Channels and publisher setup

| Channel | Targets | Required public proof |
|---|---|---|
| PyPI | Declared Python compatibility | Wheel/sdist bytes, fresh registry installs, API/CLI/self-check |
| GitHub native | Linux x64/glibc, Windows x64, macOS ARM64 | Three exact public archives/checksums and native consumers |
| npm | Linux x64/glibc, macOS ARM64 | Platform packages before main, exact bytes/integrity and npm/npx consumers on both hosts |
| Homebrew | Linux x64/system glibc 2.35+, macOS 15+ ARM64 | Public formula commits and installed lifecycle/bundle parity on both hosts |

Do not publish Windows npm. Windows uses GitHub native or Python. Homebrew is
unavailable until its public formula and consumer acceptance pass.

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
Full lifecycle details belong in [INSTALL.md](INSTALL.md); retain usable installation
commands in release notes. Format contracts belong in [FORMATS.md](FORMATS.md).

Native verification retains three external HTTPS probes. Static acquisition and
JavaScript capture use `https://example.org/`; invalid-certificate refusal uses
`https://self-signed.badssl.com/`. A local HTTPS fixture cannot exercise these frozen
CLI paths: URL acquisition refuses loopback/private DNS destinations and connected
peers, and admits only standard HTTP(S) ports. Keep that policy intact.

The two `example.org` probes remain because they verify valid HTTPS and JavaScript
capture on supported native targets. Unsupported targets require a prerequisites
refusal before HTTP. DNS, connection, proxy,
deadline or external-service failures can fail these probes. The invalid-certificate
probe still requires exit 1 and refusal at `tls`; its failure logs only the observed
exit code and a known refusal stage. A failure at `dns`, `connect` or `timeout`
does not establish a certificate-validation defect. Existing acquisition budgets
remain 30 seconds for static/TLS and 5 seconds for JavaScript, with process ceilings
of 35 and 10 seconds. Issue #53 measured no stable external-host lag to justify a probe retry.
Acquisition itself never re-sends a request; it only tries the next validated address after a
TCP connect failure.

Draft staging retains the release ID returned by GitHub creation. The `stage-publish`
controller carries it through final staging, publication and public-byte verification,
so an absent draft in the release list requires no wait. Tag/source, list ambiguity,
list completeness, release identity, asset sizes and downloaded bytes still have
their existing refusal checks. Issue #53 gives no measured draft-list delay from
which to choose a polling bound.

## Homebrew delivery

Use the existing `kochetkov-ma/homebrew-brew` tap. Render its formula from admitted public
native archives with exact versioned URLs and downloaded SHA-256 values. Install the complete
bundle in libexec, including runtime files, licenses, manifest and internal links; link its
existing executable. Do not rebuild from source or add a release asset.

Publish two real tap versions in order: accepted public 0.4.1, then the current release after
its native release passes admission. A fabricated old formula or relabelled archive is not an
upgrade baseline. Both versions belong to the existing tap; no second Brewdoc PR is needed.

After both formula commits are public, dispatch the two-host Homebrew consumer workflow
on Ubuntu 22.04 x64 and macOS 15 ARM64 with exact tag/source/tap identities. Install 0.4.1,
perform a real upgrade to the current release, and compare installed file hashes, sizes, modes
and internal links against admitted bundle inventories. Verify conversion, self-check, refusal preserving
output, supported JS/child cleanup, unlink/relink and removal with an external user-file sentinel.
Cleaning/rpath precautions alone do not prove byte parity. Mismatches stop acceptance.

Run the reviewed consumer tools from the dispatch commit. Check out the immutable release
tag separately and pass that directory as --checkout. Keep its tag/source identity checks;
validate and record the executing tool_source_sha separately from the release source_sha.

Initial notes state that Homebrew awaits tap and consumer verification. After public formula
readback and both-host acceptance, replace the pending statement with a short availability
line and accepted install command through a notes-only update. Preserve the release-format
anchors and section order, then verify exact public readback. Never leave contradictory
pending and available statements. Do not pass edited notes to the unchanged exact-body
release finalizer.
See [INSTALL.md](INSTALL.md#homebrew) for the managed lifecycle.

## Historical releases

0.2.0/0.3.0 are Python wheel/sdist releases. Their notes describe their original
features; do not retrofit later assets or EPUB/URL features into 0.2.0.
0.3.1 retains source 7c923c99f38f5f8146a64cb4f2eb7784cab4b0d7, its fixed tag and
13 original assets. Python/GitHub/native are available; npm/npx registry installation
remains unavailable. Existing native evidence and reviewed notes may be retained;
package/seal/provenance identities are not rewritten. The fixed resume-v031 and
verify-v031-binaries controller operations are retired. Notes-only revisions require
exact public readback, without a new version, package rebuild or PyPI republish.

Original 0.2.0 recovery succeeded through its original workflow run. The later guarded
recovery controller and dispatch-only jobs are retired; issue #41 is closed as not planned.
Keep `v0.2.0` fixed at `dc1f0c7fdedeec024840127ed989e1f58416762a` and retain its accepted
artifacts and installation evidence. Current source must never be published as 0.2.0.
Original 0.2.0 includes neither EPUB nor URL acquisition.
