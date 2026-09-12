# brewdoc

Document to Markdown for agent systems: PDF, Word and spreadsheets in, one Markdown file plus a
JSON receipt out. Small (~45 MB installed), fast (0.08 s import), no models, no OCR, no network,
deterministic (same file, same bytes). The receipt says what was rendered, what was dropped and
what the route structurally cannot carry, so an agent never mistakes a silent gap for "the
document does not say so".

## Install

NOT YET PUBLISHED: no PyPI project, no Homebrew formula, no release tag exists yet. The three
lines below are the intended install surface. Today the working install is from git:

```
uv tool install git+https://github.com/kochetkov-ma/brewdoc
uvx --from git+https://github.com/kochetkov-ma/brewdoc brewdoc file.pdf
```

| channel | command (intended, once published) |
|---|---|
| pip | `pip install brewdoc` |
| uvx | `uvx brewdoc file.pdf` |
| Homebrew | `brew install kochetkov-ma/brew/brewdoc` |
| Docker | `RUN pip install brewdoc==0.1.0` |

Requires Python >= 3.10. Runtime dependencies: `pdfplumber` (pulls pypdfium2, Pillow,
cryptography) and `python-calamine`.

## Usage

```
brewdoc file.pdf --out file.md
brewdoc file.xlsx                # no --out: receipt line, then the Markdown, on stdout
brewdoc --self-check             # renders synthetic fixtures twice, compares sha256, exit 0 when green
```

One JSON receipt line always goes to stdout first:

```json
{"file_ok": true, "route": "pdf", "pages": 130, "tables": 38, "text_regions": 338,
 "dropped": {"running_heads": 279, "page_numbers": 15},
 "not_carried": ["images, figures and the text drawn inside them",
                 "a table that spans a page break",
                 "text rotated out of the horizontal reading order"],
 "reason": "pdf rendered: 130 chapters, 38 tables, 338 text regions, 7 column splits",
 "out": "file.md"}
```

| key | meaning |
|---|---|
| `file_ok` | `false` when the file was refused (no text layer, unreadable, unsupported suffix); `reason` names why |
| `route` | which reader ran: `pdf`, `docx` or `sheet` |
| `pages`, `tables`, `text_regions` | what was rendered |
| `dropped` | sanitised items removed on purpose, counted by kind (running heads, page numbers, ...) |
| `not_carried` | what this route structurally cannot represent - read it before concluding a fact is absent |
| `out` | the Markdown path, or `null` when it went to stdout |

Paths resolve against the current working directory; `--out` creates parent directories.
Exit code is non-zero on refusal; the receipt line is still printed.

## Formats

| in | reader | notes |
|---|---|---|
| `.pdf` | pdfplumber | needs a text layer; route chosen per region, never per page |
| `.docx` | stdlib zip + XML | paragraphs and tables |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | one section per sheet, addressed by name; numbers as stored (`85.0`), dates as ISO days |

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

## Releasing

Tag-triggered, see `RELEASING.md`. Nothing is published yet.

## License

MIT, see `LICENSE`.
