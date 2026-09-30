# Releasing

A push of a `v*.*.*` tag runs `release.yml`: build, publish to PyPI through trusted publishing,
GitHub Release with the sdist and wheel. Checklist, in order.

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
| 1 | Discover existing tags and select the release version. On a `chore/release-X.Y.Z` branch, bump `project.version` and `brewdoc.__version__`, refresh `uv.lock` and pin installation examples. For a version-only release, use `uv lock` without upgrade flags and require every third-party lock entry and dependency constraint to remain unchanged. Validate the new wheel and sdist, including normal installed-wheel checks, open a PR and squash merge it into `main`. |
| 2 | When publication is authorized, verify the clean release checkout is the merged `main` commit. Tag and push: `git tag vX.Y.Z && git push origin refs/tags/vX.Y.Z` (the tag must equal `v` + `project.version`; `release.yml` refuses a mismatch). |
| 3 | Watch `release.yml`: build (sdist + wheel), publish to PyPI, GitHub Release with `dist/*` attached |
| 4 | Verify published version, wheel/sdist hashes and a fresh normal installation, then run `uvx brewdoc==X.Y.Z --self-check`. Record failures without claiming publication or closing the task. |
