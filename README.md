# brewdoc

[![PyPI](https://img.shields.io/pypi/v/brewdoc)](https://pypi.org/project/brewdoc/)
[![CI](https://github.com/kochetkov-ma/brewdoc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kochetkov-ma/brewdoc/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kochetkov-ma/brewdoc)](https://github.com/kochetkov-ma/brewdoc/releases/latest)
[![MIT](https://img.shields.io/github/license/kochetkov-ma/brewdoc)](LICENSE)

Document to Markdown for agent systems: PDF, Word, PPTX presentations, spreadsheets, EPUB and static
HTML in, one Markdown file plus a JSON receipt out. No models or OCR. Local conversion
is offline and deterministic (same file, same bytes). Explicit `--url` acquires a page.
The receipt says what was rendered,
what was dropped and what the route structurally cannot carry.

## Install

Choose one method. [Latest release](https://github.com/kochetkov-ma/brewdoc/releases/latest)
contains its complete installation guide. Python releases 0.2.0 and 0.3.0 are public.
These commands select 0.3.1 and apply when that version is published; its
[release guide](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.3.1)
records accepted channels. Badges show published versions and main CI, not universal OS support.

| channel | command |
|---|---|
| pip | `python -m pip install "brewdoc==0.3.1"` in a dedicated virtual environment |
| uvx, temporary | `uvx --python 3.12 --from "brewdoc==0.3.1" brewdoc file.pdf --out file.md` |
| uv tool, installed | `uv tool install --python 3.12 "brewdoc==0.3.1"` |
| pipx, installed | `pipx install --python 3.12 "brewdoc==0.3.1"` |
| Container recipe (pip) | `RUN pip install brewdoc==0.3.1` |

pip needs an installed Python 3.12. Use explicit environment paths without activation.
Linux/macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install "brewdoc==0.3.1"
.venv/bin/python -c "from importlib.metadata import version; print(version('brewdoc'))"
.venv/bin/brewdoc --self-check
.venv/bin/brewdoc file.pdf --out file.md
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "brewdoc==0.3.1"
.\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('brewdoc'))"
.\.venv\Scripts\brewdoc.exe --self-check
.\.venv\Scripts\brewdoc.exe file.pdf --out file.md
```

uv and pipx must already be installed. After installation, run `uv tool update-shell`
or `pipx ensurepath`, open a new terminal, then run `brewdoc --self-check` and
`brewdoc file.pdf --out file.md`. `uv tool list` or `pipx list` shows the managed version.
Check PATH selection with `command -v brewdoc` on Unix or `Get-Command brewdoc` in PowerShell.

For an exact published version X.Y.Z, update with the environment's Python and
`-m pip install --upgrade "brewdoc==X.Y.Z"`,
`uv tool install --force --python 3.12 "brewdoc==X.Y.Z"` or
`pipx install --force --python 3.12 "brewdoc==X.Y.Z"`. Replace X.Y.Z before running.
Remove with the same Python's `-m pip uninstall brewdoc`, `uv tool uninstall brewdoc`
or `pipx uninstall brewdoc`. pip leaves dependencies; remove the dedicated virtual
environment when no longer needed. uvx uses temporary cached environments, adds no
persistent PATH command and needs no tool uninstall. Choose another exact version to update.

### Standalone and npm

The links and commands below work when the named release is published and its
native/public consumer checks pass. Check the
[release guide](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.3.1)
for that version's accepted channels and targets. Download badges select assets;
they do not establish runtime acceptance. The npm badge shows the registry version.
Existing 0.2.0/0.3.0 releases contain Python wheel/sdist archives only.

[![npm](https://img.shields.io/npm/v/brewdoc)](https://www.npmjs.com/package/brewdoc)
[![Linux x64 download](https://img.shields.io/badge/Linux_x64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-linux-x64.tar.gz)
[![Windows x64 download](https://img.shields.io/badge/Windows_x64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-windows-x64.zip)
[![macOS ARM64 download](https://img.shields.io/badge/macOS_ARM64-download-blue)](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-macos-arm64.tar.gz)

| Native target | Version-specific download | Verification target | JavaScript |
|---|---|---|---|
| Linux x86_64, glibc | [Linux archive](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-linux-x64.tar.gz) | Ubuntu 22.04, glibc 2.35 | Bundled; requires target acceptance |
| Windows x86_64 | [Windows archive](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-windows-x64.zip) | Windows Server 2025 | Refused before requests |
| macOS ARM64 | [macOS archive](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/brewdoc-0.3.1-macos-arm64.tar.gz) | macOS 15, Apple Silicon | Bundled; requires target acceptance |

Download [SHA256SUMS](https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.1/SHA256SUMS)
beside the archive. Compare its SHA-256 before extraction with `sha256sum ARCHIVE`
on Linux, `shasum -a 256 ARCHIVE` on macOS or `Get-FileHash ARCHIVE -Algorithm SHA256`
in PowerShell. Keep the complete brewdoc directory, including _internal and licenses.
Read brewdoc/version.json for version/source/target, then run
`./brewdoc/brewdoc --self-check` or `.\brewdoc\brewdoc.exe --self-check` and use that
executable with `file.pdf --out file.md`. Add its absolute directory to your shell
profile or Windows user PATH for persistent use. Update by verifying and extracting
a fresh complete release directory, self-checking it, then repointing PATH. Remove
the dedicated directory and PATH entry to uninstall. Full download/extraction commands
are in the generated guide attached to the published release.

Verification targets do not establish older OS support. Linux musl/ARM64 and macOS
Intel are outside this initial binary set. Windows is unsigned; macOS uses an ad hoc
build signature without Developer ID/notarization. Linux uses checksums without a
detached publisher signature. OS policy may prevent launch.

npm requires Node 24.21.0/npm 12.2.0 for the tested flow; Python is bundled on admitted
targets. Once this exact npm release is publicly verified:

```sh
npm install --global brewdoc@0.3.1
npm list --global brewdoc --depth=0
brewdoc --self-check
brewdoc file.pdf --out file.md
```

Temporary cached invocation:

```sh
npx --yes --package brewdoc@0.3.1 brewdoc --self-check
npx --yes --package brewdoc@0.3.1 brewdoc file.pdf --out file.md
```

Update with `npm install --global brewdoc@X.Y.Z` for an exact published version;
remove with `npm uninstall --global brewdoc`. npx installs no persistent global tool;
choose another exact version to update. Keep optional platform packages enabled.
Missing packages or unsupported platforms refuse. The launcher adds no runtime downloads.

Python package installations require Python >= 3.12. Base runtime dependencies: `pdfplumber` (pulls pypdfium2, Pillow,
cryptography) and `python-calamine`. HTML requires CPython 3.12-3.14 and the native Lexbor
parser from this exact conditional dependency:

```text
selectolax==0.4.13; python_version < '3.15' and platform_python_implementation == 'CPython'
```

Other runtimes retain the `.html` route and Python API but explicitly refuse HTML. On supported
CPython, a missing or broken parser is an environment failure and `--self-check` exits 1.
On an unsupported HTML runtime, self-check verifies HTML refusal and checks all other formats.

Version 0.3.0 includes URL acquisition and optional JavaScript capture.
Install the extra with `python -m pip install 'brewdoc[render-js]==0.3.1'`. It uses QuickJS-ng 0.17.0.1
and bundled LinkeDOM 0.18.13. From this source checkout, install the locked extra
with `uv sync --locked --extra render-js`. Run it with
`uv run --locked --extra render-js brewdoc --url URL --render-js`.
Python and standalone invocations need no Node or browser engine. npm/npx use Node
as a launcher; no invocation downloads runtime components.

## Usage

```
brewdoc file.pdf --out file.md
brewdoc slides.pptx --out slides.md
brewdoc book.epub --out book.md
brewdoc page.html --out page.md
brewdoc --url https://example.com/article --out article.md
brewdoc --url https://example.com/article --render-js --timeout 30 --out article.md
brewdoc file.xlsx                # no --out: receipt line, then the Markdown, on stdout
brewdoc file.xlsx --sheet Calc --sheet Inputs
brewdoc file.xlsm --artifact formula/sheet/000002=formulas.json
brewdoc file.xlsm --artifact vba/project/000001=vbaProject.bin
brewdoc --self-check             # renders synthetic fixtures twice, compares sha256, exit 0 when green
```

`--sheet` is repeatable. Names are exact and case-sensitive. Output follows caller order while
keeping each sheet's full-workbook ordinal. `--artifact` is also repeatable. Use keys listed in the
Markdown artifact table. Both options are workbook-only. brewdoc validates every request and
output collision before writing.

URL mode accepts one public HTTP(S) HTML page with strict UTF-8 decoding. Initial
page acquisition uses GET, verified TLS and default ports only. Private destinations, URL credentials
and HTTPS downgrades are refused; proxy environment settings are ignored. Each redirect and
resource is validated. Acquisition allows at most five redirects and an 8 MiB
body limit. `--timeout SECONDS` sets one maximum waiting budget for loading and
optional JS, default 30 seconds. It accepts positive finite seconds within the
supported clock range and returns earlier when ready. Increase it for pages
that need longer loading or JS waiting.
Bounded cleanup and local conversion can add time after waiting ends.
No login, cookie profile or bot bypass is offered.
Local files and default static URL capture do not execute JavaScript.

`--render-js` explicitly executes the selected page's scripts locally in a separate
process with bounded APIs and resources. No filesystem or shell APIs are exposed,
and captures do not reuse browser state. This is not a general operating-system
security sandbox. It supports DOM mutations, bounded jobs/timers and guarded GET
requests. Async XHR also supports the public Algolia search endpoint used by
hn.algolia.com, with its original string body and no cookies or user credentials.
Other POST requests remain unsupported; search redirects are refused without replay.
Supported module loading uses the native engine. Temporary `localStorage`
starts empty for each capture; accessing it marks the result partial.
Each capture starts with a writable `innerWidth` of 1024 CSS pixels.
Width changes stay within that capture.
It does not provide browser layout, iframe execution,
workers, WebSockets, media or browser-wide compatibility. Native JS requires a
verified QuickJS public ABI on macOS ARM64 or 64-bit glibc Linux (x86_64/aarch64).
Windows, macOS Intel, musl Linux and 32-bit systems refuse JS. Missing components or unsupported
runtimes refuse before fetching.
See [URL acquisition limits](FORMATS.md#url-acquisition).

URL receipts use `brewdoc.receipt/2` with redacted acquisition provenance and the
frozen snapshot identity. Timing-only expiry preserves available valid content
with `acquisition.capture_status=partial` and a diagnostic.
The receipt distinguishes the current DOM from an explicitly marked fallback to
the already acquired original HTML, where JS completion is unknown. No HTML is
returned from an incomplete response. Hard network, security, heap, native fault,
tamper or resource failures exit 1 and preserve an existing output file.
Exit 0 confirms conversion, not script completion or article fidelity.
Live acquisition can change; conversion of the same frozen snapshot remains
deterministic. No 90% coverage or
global JavaScript compatibility is claimed.

Combining `--url` with a local path or `--self-check`, or using `--render-js` without
`--url`, exits 2 before acquisition. Invalid timeout values and an explicit
`--timeout` without `--url` also exit 2. URL requests with `--sheet` or `--artifact`
exit 1 with an HTML refusal receipt before HTTP.

Every successful document uses `brewdoc.markdown/2`. A quoted source title is followed by Metadata,
Artifacts, Known omissions, Contents, then anchored content units. PDF keys are `page/000001`,
workbook keys are `sheet/000001`, DOCX keys are `chapter/000001`, PPTX keys are `slide/000001`,
EPUB keys follow declared spine ordinals as `chapter/000001`, and HTML has one `chapter/000001`.
Metadata contains deterministic source identity, selection, content hashes, conversion tallies, and
artifact capabilities. It excludes paths, timestamps, permissions, host data, and unsupported author
metadata. EPUB adds source-declared publication titles, creators, languages and identifiers.

For a conversion request, one JSON receipt line goes to stdout first (wrapped here, `unit_keys` omitted):

```json
{"artifacts": [], "broken_ligature_words": 0, "columns_split": 1,
 "dropped": {"cid_survivors": 0, "control_chars": 0, "ligatures": 0, "nbsp": 0,
             "non_ascii_replaced": 0, "page_numbers": 3, "pua_glyphs": 0,
             "running_heads": 0, "soft_hyphens": 0},
 "file_ok": true, "markdown_schema": "brewdoc.markdown/2",
 "not_carried": ["images, figures and the text drawn inside them",
                 "a table that spans a page break",
                 "text rotated out of the horizontal reading order"],
 "out": "file.md",
 "reason": "pdf rendered: 7 pages, 1 tables, 26 text regions, 1 column splits",
 "receipt_schema": "brewdoc.receipt/1",
 "route": "pdf", "source": "nist-sp800-145.pdf", "tables": 1,
 "text_regions": 26, "unit_kind": "page", "units": 7}
```

| key | meaning |
|---|---|
| `file_ok`, `reason` | `false` when the file was refused (no text layer, unreadable, unsupported suffix); `reason` names why, or summarises what was rendered |
| `route` | which reader ran: `pdf`, `doc`, `presentation`, `sheet`, `epub` or `html` |
| `source` | the local filename, or the redacted requested URL in URL mode |
| `unit_kind`, `units` | the route's content unit and how many were rendered; one pair for every format. `page` for PDF, `chapter` for DOCX, EPUB and HTML, `slide` for PPTX, `sheet` for a workbook, and `none` on a suffix brewdoc does not read |
| `tables`, `text_regions`, `columns_split` | what else was rendered |
| `receipt_schema` | the receipt key contract; `brewdoc.receipt/1` replaced the per-format `pages` and `sheets` counters |
| `broken_ligature_words` | words with a ligature the font mapped to a stray code point, folded to ASCII, not repaired |
| `dropped` | sanitised items removed on purpose, counted by kind (running heads, page numbers, ...) |
| `not_carried` | what this route structurally cannot represent - read it before concluding a fact is absent |
| `out` | the Markdown path, or `null` when it went to stdout |
| `markdown_schema`, `unit_keys` | the contract and ordered full-source content keys; success only |
| `artifacts` | available keyed formula or opaque VBA outputs and requested output paths; success only |
| `selected_sheets` | the `--sheet` names in caller order; present only when sheets were selected |

Paths resolve against the current working directory; `--out` creates parent directories.
Exit code is non-zero on refusal; the receipt line is still printed.

## Formats

| in | reader | notes |
|---|---|---|
| `.pdf` | pdfplumber | needs a text layer; route chosen per region, never per page |
| `.docx` | stdlib zip + XML | paragraphs and tables |
| `.pptx` | stdlib zip + XML | slides in declared order; text, tables, notes and picture metadata |
| `.epub` | stdlib zip + XML | EPUB 2/3 static XHTML in declared spine order; Unicode metadata and navigation |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | selected sheets keep full-source ordinals; numbers as stored (`85.0`), dates as ISO days |
| `.html` | selectolax Lexbor | full static body or fragment, complete tables and source-declared controls; CPython 3.12-3.14 |

`.xlsx` and `.xlsm` expose original OOXML formulas as one deterministic JSON artifact per
formula-bearing selected sheet. Markdown still contains cached values. `.xlsm` and `.xlsb` can expose
one complete related `vbaProject.bin` as opaque bytes. brewdoc does not execute macros, decompile VBA,
report source-module counts, evaluate formulas, or infer dependency graphs.

HTML reads local strict UTF-8, with an optional BOM. It preserves meaningful Unicode, navigation,
main content, sidebars and footers in recovered DOM order. It never fetches links or resources,
executes scripts, submits forms or infers interactive values. Links use a restricted scheme policy;
form actions remain inert metadata. Inline code outside tables becomes a literal fenced block;
surrounding emphasis and links remain balanced. Safe links in literal code and around code/images
retain destination records without changing the source literals. Table-cell code uses labelled
escaped text. Quotes and list items containing standalone child blocks use explicit nesting
boundaries in place of visual Markdown
containers. Plain list paragraphs retain their item marker and continuation indentation.
See [HTML scope and limits](FORMATS.md#static-html).

EPUB reads one package with static XHTML in EPUB 2 or 3. Each declared spine item becomes
a chapter, including empty and non-linear items, front matter and back matter. Publication
navigation retains source hierarchy and links to generated chapter and fragment anchors.
Declared metadata and meaningful Unicode are preserved. Images remain inert alt/title/target
records; no pixels or external resources are read. EPUB adds no parser dependency.
Malformed required content, unsafe archives, unavailable navigation targets, encrypted reading
text, declared fixed layout and unsupported required media without an XHTML fallback refuse
the whole book before output writes. Multiple rootfiles are outside this supported profile.
See [EPUB scope and limits](FORMATS.md#epub).

The public Python API exposes EPUB, HTML and presentation rendering and generic workbook artifact access:

```python
import brewdoc

markdown, tally = brewdoc.render_epub("book.epub")
code, receipt, markdown = brewdoc.run("book.epub", out="book.md")
markdown, tally = brewdoc.render_html("page.html")
code, receipt, markdown = brewdoc.run("page.html", out="page.md")
markdown, tally = brewdoc.render_presentation("slides.pptx")
refs = brewdoc.list_book_artifacts("book.xlsm", sheets=("Calc",))
payload = brewdoc.read_book_artifact("book.xlsm", refs[0].key, sheets=("Calc",))
code, receipt, markdown = brewdoc.run(
    "book.xlsm", out="book.md", sheets=("Calc",),
    artifact_outputs={refs[0].key: "formulas.json"},
)
```

`artifact_outputs` maps artifact keys to paths. The CLI builds it from repeated `--artifact KEY=PATH`.

CSV and TSV are intentionally unsupported and are not planned. LLMs can read their text tables
directly, so brewdoc does not convert them to Markdown.

Planned formats, excluded ones, deferred ones and the never-list: `FORMATS.md`.
A planned format is refused by name today. The adapter contract and the ordered checklist for
adding one are in [`docs/architecture.md`](docs/architecture.md).

PDF regions, in the order tried:

| region looks like | rendered as |
|---|---|
| ruled on all sides | Markdown table from the drawn ruling |
| ruling unfinished (cells never closed) | source baselines recovered as logical rows on the inferred structural grid |
| ruled across only (head and foot rules) | column grid from the rule segment ends |
| unruled, tabular band | layout-preserved text |
| unruled, captioned `Table N` | the band is bounded first (caption to next blank band), then read as a table |
| two-column prose | split at the gutter (relative density on the character x-histogram), left then right |
| plain prose | lines |

## What it does not do

| gap | behaviour |
|---|---|
| OCR | a PDF with no text layer is refused by name, never rendered empty |
| PDF and DOCX images, figures, text drawn inside them | not carried; listed in `not_carried` |
| PPTX pictures | represented by deterministic name, size, alt text, caption, source and byte-count placeholders; pixels are never emitted |
| EPUB layout, scripts, media and graphics | CSS, pagination, font rendering, scripts, active media, image pixels and unsupported SVG/MathML semantics are omitted |
| PPTX charts, SmartArt, audio, video and embedded objects | not carried; listed in `not_carried` |
| a table spanning a page break | columns recovered, wrapped rows stay separate rows |
| text rotated out of horizontal reading order | not carried |
| sub/superscripts | folded back into their line (`NH4H2PO4`, not `NHHPO` over `4 2 4`) |
| broken font CMaps (ligatures mapped to wrong code points) | counted (`broken_ligature_words`), folded to ASCII, never guessed back |
| formula calculation | cached values in Markdown; passive source formulas only for `.xlsx` and `.xlsm` |
| readable VBA source | unavailable; `.xlsm` and `.xlsb` preserve a related project only as opaque bytes |

## Determinism

No timestamps or unordered iteration. Two renders of one file have the same sha256;
`--self-check` pins it. PDF, DOCX, PPTX and workbook output remains ASCII. HTML and EPUB Markdown use
UTF-8 without BOM and LF newlines, with the same bytes in the API, file output and CLI stdout.
Receipt JSON keeps ASCII escaping.

## Size and speed

These measurements cover the existing PDF/workbook baseline before the HTML parser addition.
They do not measure HTML or EPUB conversion or the new installed footprint.

| measure | value |
|---|---|
| installed footprint | ~45 MB (pdfplumber, pypdfium2, Pillow, cryptography, python-calamine) |
| import | 0.08 s |
| 130-page PDF | ~5.5 s |
| 14-sheet xlsx | ~0.9 s |

## Benchmarks

Browse the [corpus catalog](benchmarks/CATALOG.md), follow the
[measurement method](benchmarks/METHODOLOGY.md), and inspect the
[full-run results](benchmarks/results/README.md). Completed runs include actual
outputs and known failures; capture completion does not mean content quality passed.

## Releasing

Tag-triggered, see `RELEASING.md`.
Coding agents can start with the compact [llms.txt](llms.txt) guide. It does not imply
automatic discovery by any client.

## License

MIT, see `LICENSE`.
