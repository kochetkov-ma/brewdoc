# brewdoc

Document to Markdown for agent systems: PDF, Word and spreadsheets in, one Markdown file plus a
JSON receipt out. Small (~45 MB installed), fast (0.08 s import), no models, no OCR, no network,
deterministic (same file, same bytes). The receipt says what was rendered, what was dropped and
what the route structurally cannot carry, so an agent never mistakes a silent gap for "the
document does not say so".

## Install

| channel | command |
|---|---|
| pip | `pip install brewdoc` |
| uvx | `uvx brewdoc file.pdf` |
| uv tool | `uv tool install brewdoc` |
| Homebrew | `brew install kochetkov-ma/brew/brewdoc` |
| Docker | `RUN pip install brewdoc==0.1.0` |

Alternative, straight from git (no PyPI round-trip):

```
uvx --from git+https://github.com/kochetkov-ma/brewdoc brewdoc file.pdf
```

Requires Python >= 3.12. Runtime dependencies: `pdfplumber` (pulls pypdfium2, Pillow,
cryptography) and `python-calamine`.

## Usage

```
brewdoc file.pdf --out file.md
brewdoc file.xlsx                # no --out: receipt line, then the Markdown, on stdout
brewdoc file.xlsx --sheet Calc --sheet Inputs
brewdoc file.xlsm --artifact formula/sheet/000002=formulas.json
brewdoc file.xlsm --artifact vba/project/000001=vbaProject.bin
brewdoc --self-check             # renders synthetic fixtures twice, compares sha256, exit 0 when green
```

`--sheet` is repeatable. Names are exact and case-sensitive. Output follows caller order while
keeping each sheet's full-workbook ordinal. `--artifact` is also repeatable. Use keys listed in the
Markdown artifact table. brewdoc validates every request and output collision before writing.

Every successful document uses `brewdoc.markdown/2`. A quoted source title is followed by Metadata,
Artifacts, Known omissions, Contents, then anchored content units. PDF keys are `page/000001`,
workbook keys are `sheet/000001`, and DOCX keys are `chapter/000001`. Metadata contains deterministic
source identity, selection, content hashes, conversion tallies, and artifact capabilities. It excludes
paths, timestamps, permissions, host data, and unsupported author metadata.

One JSON receipt line always goes to stdout first (wrapped here, `unit_keys` omitted):

```json
{"artifacts": [], "broken_ligature_words": 0, "columns_split": 1,
 "dropped": {"cid_survivors": 0, "control_chars": 0, "ligatures": 0, "nbsp": 0,
             "non_ascii_replaced": 0, "page_numbers": 3, "pua_glyphs": 0,
             "running_heads": 0, "soft_hyphens": 0},
 "file_ok": true, "markdown_schema": "brewdoc.markdown/2",
 "not_carried": ["images, figures and the text drawn inside them",
                 "a table that spans a page break",
                 "text rotated out of the horizontal reading order"],
 "out": "file.md", "pages": 7,
 "reason": "pdf rendered: 7 pages, 1 tables, 26 text regions, 1 column splits",
 "route": "pdf", "sheets": 0, "source": "nist-sp800-145.pdf", "tables": 1,
 "text_regions": 26}
```

| key | meaning |
|---|---|
| `file_ok`, `reason` | `false` when the file was refused (no text layer, unreadable, unsupported suffix); `reason` names why, or summarises what was rendered |
| `route` | which reader ran: `pdf`, `doc` or `sheet` |
| `source` | the input file name, without its directory |
| `pages`, `sheets`, `tables`, `text_regions`, `columns_split` | what was rendered |
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
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | selected sheets keep full-source ordinals; numbers as stored (`85.0`), dates as ISO days |

`.xlsx` and `.xlsm` expose original OOXML formulas as one deterministic JSON artifact per
formula-bearing selected sheet. Markdown still contains cached values. `.xlsm` and `.xlsb` can expose
one complete related `vbaProject.bin` as opaque bytes. brewdoc does not execute macros, decompile VBA,
report source-module counts, evaluate formulas, or infer dependency graphs.

The public Python API keeps the existing render tuples and adds generic artifact access:

```python
refs = brewdoc.list_book_artifacts("book.xlsm", sheets=("Calc",))
payload = brewdoc.read_book_artifact("book.xlsm", refs[0].key, sheets=("Calc",))
code, receipt, markdown = brewdoc.run(
    "book.xlsm", out="book.md", sheets=("Calc",),
    artifact_outputs={refs[0].key: "formulas.json"},
)
```

`artifact_outputs` maps artifact keys to paths. The CLI builds it from repeated `--artifact KEY=PATH`.

Planned formats (all stdlib, zero new dependencies), deferred ones and the never-list: `FORMATS.md`.

PDF regions, in the order tried:

| region looks like | rendered as |
|---|---|
| ruled on all sides | Markdown table from the drawn ruling |
| ruling unfinished (cells never closed) | re-read on the grid the ruling implies, kept only when it carries more text than the drawn ruling did |
| ruled across only (head and foot rules) | column grid from the rule segment ends |
| unruled, tabular band | layout-preserved text |
| unruled, captioned `Table N` | the band is bounded first (caption to next blank band), then read as a table |
| two-column prose | split at the gutter (relative density on the character x-histogram), left then right |
| plain prose | lines |

## What it does not do

| gap | behaviour |
|---|---|
| OCR | a PDF with no text layer is refused by name, never rendered empty |
| images, figures, text drawn inside them | not carried; listed in `not_carried` |
| a table spanning a page break | columns recovered, wrapped rows stay separate rows |
| text rotated out of horizontal reading order | not carried |
| sub/superscripts | folded back into their line (`NH4H2PO4`, not `NHHPO` over `4 2 4`) |
| broken font CMaps (ligatures mapped to wrong code points) | counted (`broken_ligature_words`), folded to ASCII, never guessed back |
| formula calculation | cached values in Markdown; passive source formulas only for `.xlsx` and `.xlsm` |
| readable VBA source | unavailable; `.xlsm` and `.xlsb` preserve a related project only as opaque bytes |

## Determinism

No timestamps, no set iteration, no dict ordering. Two renders of one file have the same sha256;
`--self-check` pins it. Output is ASCII.

## Size and speed

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

## License

MIT, see `LICENSE`.
