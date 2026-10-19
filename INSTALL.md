# Installation

These examples select 0.7.0. Use a channel listed as available in
[that release](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.7.0).
X.Y.Z marks an exact published update version to replace.
After installation, local conversion and self-check need no network or runtime downloads.

Article mode is introduced in 0.4.0. Use local `brewdoc page.html --article`, or combine
`--article` with `--url` and optional `--render-js`. See the [article contract](FORMATS.md#article-selection).
Version 0.6.0 adds `--body-only` and document URLs, for example
`brewdoc --url https://arxiv.org/pdf/1706.03762 --body-only --out attention.md`.
Version 0.7.0 adds `--render-js auto` and HTML `--no-links`; see the [URL policy](FORMATS.md#url-acquisition).

## Python prerequisites

Python packages require Python 3.12+. Local and URL HTML require CPython 3.12-3.14;
other `--url` documents do not.
Base dependencies are pdfplumber and python-calamine. HTML uses the conditional parser:

```text
selectolax==0.4.13; python_version < '3.15' and platform_python_implementation == 'CPython'
```

Other runtimes retain the HTML API/route and refuse explicitly. On supported CPython,
a missing/broken parser makes HTML conversion and self-check fail. On unsupported
HTML runtimes, self-check verifies that refusal and the other formats.

### pip

Use a dedicated virtual environment; these paths need no activation or PATH change.
Linux/macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install "brewdoc==0.7.0"
.venv/bin/python -c "from importlib.metadata import version; print(version('brewdoc'))"
.venv/bin/brewdoc --self-check
.venv/bin/brewdoc file.pdf --out file.md
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "brewdoc==0.7.0"
.\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('brewdoc'))"
.\.venv\Scripts\brewdoc.exe --self-check
.\.venv\Scripts\brewdoc.exe file.pdf --out file.md
```

Update with the same environment's Python and an exact published version:
`-m pip install --upgrade "brewdoc==X.Y.Z"`, then self-check. Replace X.Y.Z first.
Remove with `.venv/bin/python -m pip uninstall brewdoc` on Unix or
`.\.venv\Scripts\python.exe -m pip uninstall brewdoc` in PowerShell. Dependencies
remain; remove the dedicated environment when no longer needed. Keep inputs/output outside it.
For a container recipe using installed Python: `RUN pip install brewdoc==0.7.0`.

### uv

Requires installed [uv](https://docs.astral.sh/uv/getting-started/installation/);
uv can obtain Python when needed. Temporary invocations:

```sh
uvx --python 3.12 --from "brewdoc==0.7.0" brewdoc --self-check
uvx --python 3.12 --from "brewdoc==0.7.0" brewdoc file.pdf --out file.md
```

uvx uses a cached environment, creates no persistent PATH command and needs no
uninstall. Choose another exact version on the next invocation to update.
`uv cache clean brewdoc` removes cached package data, not a managed tool.

For a persistent tool:

```sh
uv tool install --python 3.12 "brewdoc==0.7.0"
uv tool update-shell
```

Open a new terminal, then run `uv tool list`, `brewdoc --self-check` and
`brewdoc file.pdf --out file.md`. Update with
`uv tool install --force --python 3.12 "brewdoc==X.Y.Z"`, then self-check.
Replace X.Y.Z first. Remove with `uv tool uninstall brewdoc`.

### pipx

Requires installed [pipx](https://pipx.pypa.io/stable/how-to/install-pipx.html) and Python 3.12.

```sh
pipx install --python 3.12 "brewdoc==0.7.0"
pipx ensurepath
```

Open a new terminal, then run `pipx list`, `brewdoc --self-check` and
`brewdoc file.pdf --out file.md`. Update with
`pipx install --force --python 3.12 "brewdoc==X.Y.Z"`, then self-check.
Replace X.Y.Z first. Remove with `pipx uninstall brewdoc`.

For managed tools, check the selected executable with `command -v brewdoc` on Unix
or `Get-Command brewdoc` in PowerShell. `uv tool list` and `pipx list` show versions.
For pip, use installed metadata as above; brewdoc has no version flag.

## Standalone

Download the archive matching your target; Python/dependencies are bundled.
The native verification matrix is Linux x64/glibc on Ubuntu 22.04/glibc 2.35, Windows x64
on Windows Server 2025 and macOS ARM64 on macOS 15/Apple Silicon. This does not
establish older OS support. Linux musl/ARM64 and macOS Intel are outside this set.

Linux x64/glibc:

```sh
curl -fLO "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/brewdoc-0.7.0-linux-x64.tar.gz"
curl -fLO "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/SHA256SUMS"
sha256sum brewdoc-0.7.0-linux-x64.tar.gz
awk '$2 == "brewdoc-0.7.0-linux-x64.tar.gz" { print }' SHA256SUMS
```

Continue only when the two SHA-256 values match:

```sh
mkdir brewdoc-0.7.0-linux-x64
tar -xzf brewdoc-0.7.0-linux-x64.tar.gz -C brewdoc-0.7.0-linux-x64
cd brewdoc-0.7.0-linux-x64
cat brewdoc/version.json
./brewdoc/brewdoc --self-check
./brewdoc/brewdoc /absolute/path/file.pdf --out file.md
export PATH="$PWD/brewdoc:$PATH"
```

macOS ARM64:

```sh
curl -fLO "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/brewdoc-0.7.0-macos-arm64.tar.gz"
curl -fLO "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/SHA256SUMS"
shasum -a 256 brewdoc-0.7.0-macos-arm64.tar.gz
awk '$2 == "brewdoc-0.7.0-macos-arm64.tar.gz" { print }' SHA256SUMS
```

Continue only when the two SHA-256 values match:

```sh
mkdir brewdoc-0.7.0-macos-arm64
tar -xzf brewdoc-0.7.0-macos-arm64.tar.gz -C brewdoc-0.7.0-macos-arm64
cd brewdoc-0.7.0-macos-arm64
cat brewdoc/version.json
./brewdoc/brewdoc --self-check
./brewdoc/brewdoc /absolute/path/file.pdf --out file.md
export PATH="$PWD/brewdoc:$PATH"
```

Windows PowerShell:

```powershell
Invoke-WebRequest "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/brewdoc-0.7.0-windows-x64.zip" -OutFile "brewdoc-0.7.0-windows-x64.zip"
Invoke-WebRequest "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.7.0/SHA256SUMS" -OutFile SHA256SUMS
(Get-FileHash "brewdoc-0.7.0-windows-x64.zip" -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -SimpleMatch "  brewdoc-0.7.0-windows-x64.zip"
```

Continue only when the SHA-256 values match:

```powershell
Expand-Archive "brewdoc-0.7.0-windows-x64.zip" -DestinationPath "brewdoc-0.7.0-windows-x64"
Set-Location "brewdoc-0.7.0-windows-x64"
Get-Content .\brewdoc\version.json
.\brewdoc\brewdoc.exe --self-check
.\brewdoc\brewdoc.exe "C:\path\file.pdf" --out file.md
$env:Path = "$PWD\brewdoc;$env:Path"
```

The PATH changes above last for this terminal. For persistence, add the absolute
brewdoc directory to your shell profile or Windows user PATH and open a new terminal.
Check command selection as above. Keep the complete directory, _internal and licenses.
The manifest reports version/source/Python/target. Do not merge bundle versions.

Update by verifying a new exact release, extracting to a fresh directory, checking
its manifest and self-check, then repointing PATH. Remove the old directory after
verification. Uninstall by removing the dedicated directory and its PATH entry.
Windows is unsigned; macOS has an ad hoc signature without Developer ID/notarization.
Linux archives have checksums without a detached publisher signature. OS policy may prevent launch.

## Homebrew

Use these commands only when the exact release notes list Homebrew as available.
The formula installs the complete native bundle without Python dependencies.
It requires macOS 15+ ARM64 or Linux
x64 with system glibc 2.35+; Homebrew's separate glibc does not satisfy that floor.

```sh
brew tap kochetkov-ma/brew
brew install kochetkov-ma/brew/brewdoc
brew list --versions brewdoc
command -v brewdoc
cat "$(brew --prefix brewdoc)/libexec/version.json"
brewdoc --self-check
brewdoc file.pdf --out file.md
```

The tap supplies its published formula version; check the installed manifest before use.
Update with `brew update` then `brew upgrade kochetkov-ma/brew/brewdoc`, and repeat
the manifest/self-check checks. `brew unlink brewdoc` removes the command link;
`brew link brewdoc` restores it. Remove with `brew uninstall brewdoc`; optionally remove
the dedicated tap with `brew untap kochetkov-ma/brew`. Keep inputs and outputs outside
the managed keg. Other installations on PATH can still provide a `brewdoc` command.

## Optional JavaScript capture

Python installs use `brewdoc[render-js]==0.7.0` instead of the base package in the
chosen install command. It includes QuickJS-ng 0.17.0.1 and bundled LinkeDOM 0.18.13.
JS needs CPython 3.12-3.14 on macOS ARM64 or 64-bit glibc Linux x86_64/aarch64.
Windows, macOS Intel, musl and 32-bit systems refuse before requests.
Standalone bundles include admitted JS components on Linux x64 and macOS ARM64.

```sh
brewdoc --url https://example.com --render-js --timeout 30 --out page.md
brewdoc --url https://example.com --render-js auto --out page.md
```

Bare `--render-js` means `always`; `auto` runs JS only when the static page is a JavaScript shell.
Prefer `auto` for unknown pages: `always` runs JS on every page and can hit an engine
quota on a page that static mode reads.
No Node/browser/driver/engine downloads occur during conversion. Explicit --url
fetches the requested page or document and page resources under the [URL policy](FORMATS.md#url-acquisition).
From source, use `uv sync --locked --extra render-js` and
`uv run --locked --extra render-js brewdoc --url URL --render-js`.

## npm and npx

The npm channel targets Linux x64/glibc and macOS ARM64 only. Windows uses the
GitHub native archive or Python installation above; there is no Windows npm package.

Use npm/npx only when listed as available in [the release](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.7.0).
Tested launcher tooling is Node 24.21.0/npm 12.2.0.
Keep optional platform packages enabled. Missing packages/unsupported targets refuse.

```sh
npm install --global brewdoc@0.7.0
npm list --global brewdoc --depth=0
brewdoc --self-check
brewdoc file.pdf --out file.md
```

Check PATH selection as above. Update with `npm install --global brewdoc@X.Y.Z`
for another exact published version; uninstall with `npm uninstall --global brewdoc`.
Temporary cached invocation:

```sh
npx --yes --package brewdoc@0.7.0 brewdoc --self-check
npx --yes --package brewdoc@0.7.0 brewdoc file.pdf --out file.md
```

npx creates no persistent global tool and needs no tool uninstall. Choose another
exact version on the next invocation to update. Neither launcher nor conversion
downloads runtime components during invocation. Avoid clearing unrelated npm cache.
