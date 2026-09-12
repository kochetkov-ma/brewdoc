# Formats

What brewdoc reads today, what is planned, what is deferred and what it will never do. Runtime
dependencies stay at two (`pdfplumber`, `python-calamine`): every planned format is stdlib only.

## Supported

| in | reader | mechanism |
|---|---|---|
| `.pdf` | pdfplumber | text layer required; each region routed by its own ruling (drawn table, implied grid, rule segments, tabular band, captioned band, two-column prose, prose) |
| `.docx` | stdlib `zipfile` + `xml.etree` | `word/document.xml`, `w:p` paragraphs and `w:tbl` tables in reading order; one `## <heading>` chapter per Word heading |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | one section per sheet, addressed by name; numbers as stored, dates as ISO days |

## Planned

Zero new dependencies, all stdlib. Order = implementation order, by value for agents.

| # | in | mechanism | output |
|---|---|---|---|
| 1 | `.pptx` | `zipfile` + `ppt/slides/slideN.xml`; `a:p` paragraphs, `a:tbl` tables, notes | one `## Slide N` chapter per slide |
| 2 | `.csv` `.tsv` | `csv` | one Markdown table |
| 3 | `.html` | `html.parser` | headings, lists, tables |
| 4 | `.odt` `.odp` | `zipfile` + `content.xml` | paragraphs, tables; one chapter per slide for `.odp` |
| 5 | `.epub` | `zipfile` + XHTML chapters through the html path | one chapter per spine item |
| 6 | `.md` `.txt` | pass-through | bytes unchanged, receipt still emitted |
| 7 | `.eml` | `email` | headers, text body, attachment names |

Images inside any container (pptx, docx, odt, epub, html, eml) become placeholders carrying size,
alt text and caption. Never OCR.

## Deferred

Not in any current plan.

| in | reason |
|---|---|
| `.doc` `.ppt` (legacy binary) | OLE2 container, Word 97 piece table; needs `olefile` or an own CFB reader; text only, tables lossy |
| `.rtf` | own parser required |

## Never

OCR, ML layout models, network. A page without a text layer is reported in the receipt, not guessed.
