# brewdoc

[![PyPI](https://img.shields.io/pypi/v/brewdoc)](https://pypi.org/project/brewdoc/)
[![CI](https://github.com/kochetkov-ma/brewdoc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kochetkov-ma/brewdoc/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kochetkov-ma/brewdoc)](https://github.com/kochetkov-ma/brewdoc/releases/latest)
[![MIT](https://img.shields.io/github/license/kochetkov-ma/brewdoc)](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/LICENSE)

Fast, deterministic document-to-Markdown conversion for agent systems. Easy to install. No model weights, OCR or external services.

Local conversion runs offline after installation, with no runtime component downloads.
Supported documents become one Markdown file and a JSON receipt describing omissions.
See [formats and limits](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md).
Version 0.4.1 added [selected ZIP/XML reader budgets](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md#zipxml-reader-limits)
for DOCX/PPTX and workbook metadata/artifacts. Excessive work refuses before output
writes; native workbook values and total process memory remain outside those bounds.

Version 0.5.0 adds ODT/ODP text and tables, selected MIME email bodies and exact UTF-8
Markdown/text pass-through. It preserves workbook Unicode and exposed datetime precision,
and selects one DOCX compatibility fallback.
It also adds textless PDF page facts and selected caption, table and math-spacing fixes.
See the [format contracts](FORMATS.md).

## Install

These examples select 0.5.0. Use a channel listed as available in [that release](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.5.0). Choose one method:

Article mode is introduced in 0.4.0. Channel availability is listed in the release.

```sh
python -m pip install "brewdoc==0.5.0"
uv tool install --python 3.12 "brewdoc==0.5.0"
pipx install --python 3.12 "brewdoc==0.5.0"
```

Use a virtual environment for pip. Python 3.12+ is required; HTML/URL need CPython 3.12-3.14.
[INSTALL.md](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/INSTALL.md) covers prerequisites, Windows, uvx, PATH, update and removal.
[Latest release](https://github.com/kochetkov-ma/brewdoc/releases/latest) lists actual assets and channel availability.

### Standalone downloads

Use downloads listed as available in the release. Python and dependencies are bundled. Verify [SHA256SUMS](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.5.0/SHA256SUMS) and keep the entire extracted directory.

[![Linux x64 download](https://img.shields.io/badge/Linux_x64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.5.0/brewdoc-0.5.0-linux-x64.tar.gz)
[![Windows x64 download](https://img.shields.io/badge/Windows_x64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.5.0/brewdoc-0.5.0-windows-x64.zip)
[![macOS ARM64 download](https://img.shields.io/badge/macOS_ARM64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.5.0/brewdoc-0.5.0-macos-arm64.tar.gz)

| Native target | Verification environment | JavaScript |
|---|---|---|
| Linux x64/glibc | Ubuntu 22.04, glibc 2.35 | Bundled |
| Windows x64 | Windows Server 2025 | Refused before requests |
| macOS ARM64 | macOS 15, Apple Silicon | Bundled |

Older OSes, Linux musl/ARM64 and macOS Intel are outside this binary set.
Windows is unsigned; macOS has an ad hoc signature without Developer ID/notarization.
Linux archives have checksums, without a detached publisher signature. [Download and launch](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/INSTALL.md#standalone).

### npm

[![npm](https://img.shields.io/npm/v/brewdoc)](https://www.npmjs.com/package/brewdoc)

npm targets Linux x64/glibc and macOS ARM64. Windows uses the GitHub native download or Python package.
Use npm/npx only when listed as available in [the release](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.5.0).
[INSTALL.md](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/INSTALL.md#npm-and-npx) contains exact-version installation, update and removal instructions.

### Homebrew

Use `kochetkov-ma/brew/brewdoc` only when the release lists it as available.
It installs the complete native bundle on macOS 15+ ARM64 or Linux x64 with system
glibc 2.35+. See [installation and lifecycle](INSTALL.md#homebrew).

## Usage

```sh
brewdoc file.pdf --out file.md
brewdoc book.epub --out book.md
brewdoc file.xlsx --sheet Calc --sheet Inputs --out book.md
brewdoc file.xlsm --artifact formula/sheet/000002=formulas.json
brewdoc file.xlsm --artifact vba/project/000001=vbaProject.bin
brewdoc --self-check
```

`--sheet` and `--artifact` are repeatable and workbook-only. Sheet names are exact and case-sensitive;
caller order retains full-workbook ordinals. Artifact keys come from the Markdown artifact table.
Requests and output collisions are validated before writes. Paths resolve against the caller's directory;
`--out` creates parents. Refusal preserves existing output. Self-check is offline and has its own output.

## Markdown and receipts

Parsed document output uses `brewdoc.markdown/2`: quoted source title, Metadata, Artifacts,
Known omissions, Contents, then anchored units. PDF uses `page/000001`, workbooks `sheet/000001`,
PPTX/ODP `slide/000001`, and DOCX/EPUB/HTML/ODT/EML `chapter/000001`.
Empty declared units remain navigable.
Metadata holds source/selection/content hashes, conversion tallies and artifact capabilities;
it excludes paths, timestamps, permissions and host data. EPUB retains declared publication metadata.

UTF-8 `.md` and `.txt` use `brewdoc.markdown/passthrough/1`: exact source bytes with
no generated envelope, contents or anchors. BOM, whitespace, newline variants and absent
final newline survive; empty input succeeds. The receipt reports route `text`, kind `none`
and zero units. Without `--out`, the JSON receipt precedes the unchanged body bytes.

Conversion prints one JSON receipt line first. With `--out`, Markdown goes to the file;
without it, Markdown follows on stdout. Local receipts use `brewdoc.receipt/1`; URL receipts use `brewdoc.receipt/2`.

| Receipt fields | Meaning |
|---|---|
| `file_ok`, `reason` | Conversion success or named refusal |
| `receipt_schema` | Local brewdoc.receipt/1 or URL brewdoc.receipt/2 |
| `route`, `source`, `unit_kind`, `units` | Reader, local filename/redacted URL, content kind/count |
| `tables`, `text_regions`, `columns_split` | Conversion tallies |
| `dropped`, `broken_ligature_words` | Counted normalization; malformed font words are folded, not guessed |
| `not_carried` | Structural omissions; read before concluding a fact is absent |
| `out` | Markdown path, or null for stdout |
| `markdown_schema`, `unit_keys`, `artifacts` | Success-only schema, ordered source keys and available/requested artifacts |
| `selected_sheets` | Caller-ordered names, only when selected |
| `acquisition` | URL provenance, frozen snapshot and capture status |

Exit 0 means conversion, 1 refusal and 2 invalid usage. Success does not prove content fidelity or complete JavaScript.
PDF/DOCX/PPTX emit ASCII; workbooks/HTML/EPUB/ODT/ODP/EML emit UTF-8 without BOM, with LF newlines.
Text pass-through retains the source UTF-8 encoding and newlines. JSON keeps ASCII escaping.
The same local file or frozen snapshot produces identical bytes; live pages can change.

## Python API

```python
import brewdoc

code, receipt, markdown = brewdoc.run("book.epub", out="book.md")
markdown, tally = brewdoc.render_html("page.html")
refs = brewdoc.list_book_artifacts("book.xlsm", sheets=("Calc",))
payload = brewdoc.read_book_artifact("book.xlsm", refs[0].key, sheets=("Calc",))
```

`run` accepts `sheets` and an `artifact_outputs` key-to-path mapping. Renderers include
`render_pdf`, `render_doc`, `render_presentation`, `render_epub`, `render_html` and `render_book`.
`read_formulas` provides typed formulas; `self_check` verifies installed routes. Local APIs never acquire URLs.

## Explicit URL acquisition

`--url` deliberately acquires a public HTTP(S) page before local conversion.

```sh
brewdoc --url https://example.com/article --out article.md
brewdoc --url https://example.com/article --render-js --timeout 30 --out article.md
```

`--timeout` is one positive finite waiting budget, default 30 seconds; cleanup/conversion can add time.
Public destinations, DNS, redirects, TLS, requests and bytes are validated. Ordinary requests use GET;
JS admits only the narrow credential-free HN Algolia XHR POST exception. Local HTML never fetches or runs scripts.
Optional JS needs installed/bundled prerequisites, refuses unsupported runtimes before HTTP and downloads no engine.
Timing-only expiry can preserve valid partial content; hard failures refuse output. [Full URL/JS limits](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md#url-acquisition).

## Article mode

Introduced in 0.4.0; use a channel listed as available in the release. For Python JS capture, install
`brewdoc[render-js]==0.5.0`; supported standalone bundles include its prerequisites.

```sh
brewdoc page.html --article --out article.md
brewdoc --url https://example.com/article --article --out article.md
brewdoc --url https://example.com/article --render-js --article --out article.md
```

```python
code, receipt, markdown = brewdoc.run("page.html", article=True)
markdown, tally = brewdoc.render_html("page.html", article=True)
```

Article mode selects one explicit article or a qualified main subtree. It suits single articles
and documentation pages with those literal HTML elements. Feeds with peer articles, ambiguous
main regions and pages without a qualified subtree refuse. A visible form disqualifies main
fallback; an explicit article can retain forms. There is no full-page fallback or text scoring.
Selection uses the recovered static HTML or frozen JS snapshot,
without layout or inferred author/date metadata. The source title is unchanged.

The receipt's `not_carried` and successful Markdown's Known omissions declare:
`body content outside the selected article or main subtree; complete page coverage is not claimed`.
The [article contract](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md#article-selection)
defines selection, refusals and limits.

[Earlier source-package CI](https://github.com/kochetkov-ma/brewdoc/actions/runs/38431645006) passed on
Ubuntu 22.04 x64/glibc with CPython 3.13.15, and macOS 15 ARM64/Windows Server 2025 x64
with CPython 3.14.7. Each passed four independent wheel/sdist base/optional installs;
optional JS executed on Unix and refused before requests on Windows. These are selected
tested patches, not a claim about newer versions. These results predate the 0.5.0 changes.
CPython 3.12.14 retains the local macOS full-suite baseline and existing CI smoke checks.
Package evidence is separate from standalone binaries; other OS/minor combinations, free-threaded CPython and other
architectures remain untested here.
See [package compatibility](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md#python-package-compatibility).

[Format contracts](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/FORMATS.md) | [Corpus](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/benchmarks/CATALOG.md) | [Measurements](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/benchmarks/METHODOLOGY.md) | [Results](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/benchmarks/results/README.md)
[Releasing](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/RELEASING.md) | [Agent entry point](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/llms.txt) | [MIT license](https://github.com/kochetkov-ma/brewdoc/blob/v0.5.0/LICENSE)

## Related projects

- [BrewPage](https://brewpage.app): free hosting for HTML, Markdown, JSON and files; each upload returns a shareable HTTPS link.
- [BrewPage MCP server](https://github.com/kochetkov-ma/brewpage-openapi/tree/main/mcp-server): npm package `brewpage-mcp` for publishing to BrewPage from MCP clients.
- [claude-brewcode](https://github.com/kochetkov-ma/claude-brewcode): plugin suite for Claude Code, with [documentation](https://doc-claude.brewcode.app). Its brewdoc plugin is a different tool from this brewdoc CLI.
- [brewcode.app](https://brewcode.app): home site of the Brew projects.
