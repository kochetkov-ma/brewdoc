# Releasing

A push of a `v*.*.*` tag runs `release.yml`: build, publish to PyPI through trusted publishing,
GitHub Release with the sdist and wheel. Checklist, in order.

## One-time setup (manual)

| # | step | where |
|---|---|---|
| 1 | Create the PyPI project `brewdoc` or use the pending-publisher form | pypi.org |
| 2 | Add a trusted publisher: owner `kochetkov-ma`, repository `brewdoc`, workflow `release.yml`, environment `pypi` | pypi.org -> project -> Publishing |
| 3 | Create the GitHub environment `pypi` (optionally with required reviewers) | github.com/kochetkov-ma/brewdoc -> Settings -> Environments |
| 4 | Homebrew tap `kochetkov-ma/homebrew-brew` with a `brewdoc` formula pointing at the PyPI sdist | separate repo, after the first PyPI release |

No API token is stored anywhere: `pypa/gh-action-pypi-publish` exchanges the workflow's OIDC token
(`id-token: write`) for a short-lived PyPI token.

## Per release

| # | step |
|---|---|
| 1 | Discover existing tags and select the release version. On a `chore/release-X.Y.Z` branch, bump `project.version` and refresh `uv.lock` (`uv lock`). Validate, open a PR and squash merge it into `main`. |
| 2 | When publication is authorized, verify the clean release checkout is the merged `main` commit. Tag and push: `git tag vX.Y.Z && git push origin refs/tags/vX.Y.Z` (the tag must equal `v` + `project.version`; `release.yml` refuses a mismatch). |
| 3 | Watch `release.yml`: build (sdist + wheel), publish to PyPI, GitHub Release with `dist/*` attached |
| 4 | Verify: `uvx brewdoc==X.Y.Z --self-check` |
