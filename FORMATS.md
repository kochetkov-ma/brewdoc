# Formats

What brewdoc reads today, what is planned, what is deferred and what it will never do. Runtime
dependencies stay at two (`pdfplumber`, `python-calamine`): every planned format is stdlib only.

## Supported

| in | reader | mechanism |
|---|---|---|
| `.pdf` | pdfplumber | text layer required; anchored page units; each region routed by its own ruling |
| `.docx` | stdlib `zipfile` + `xml.etree` | paragraphs and tables in reading order; anchored chapters retain duplicate and empty headings |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | exact sheet selection in caller order with full-source ordinal keys; cached values in Markdown |

All supported routes emit `brewdoc.markdown/2`: quoted source title, deterministic metadata,
artifact inventory, known omissions, linked contents, then anchored content units. Empty physical
PDF pages, selected empty sheets, and heading-created empty DOCX chapters remain navigable.

Every receipt line uses `brewdoc.receipt/1`, which names the route (`pdf`, `doc`, `sheet`), its
unit kind (`page`, `chapter`, `sheet`) and how many units were rendered. The unit kind decides the
content keys: `page/000001`, `chapter/000001`, `sheet/000001`. A suffix brewdoc does not read is
refused with route `none` and unit kind `none`; every other refusal keeps its route's own name and
unit kind, with zero units.

## Workbook artifacts

| input | formulas | VBA project | limit |
|---|---|---|---|
| `.xlsx` | `brewdoc.formulas/1` JSON per selected formula-bearing sheet | unavailable | formulas are copied without evaluation, rewriting, or dependency inference |
| `.xlsm` | same as `.xlsx` | one related `vbaProject.bin`, when present | exact opaque bytes only |
| `.xlsb` | unavailable through the pinned Python binding | one related `vbaProject.bin`, when present | exact opaque bytes only |
| `.xls` `.ods` | unavailable through the pinned Python binding | unavailable | cached values still render |

Readable VBA source modules are unavailable for every format. Module and script counts are unknown,
and brewdoc never executes a project. Generic formula and VBA artifact output uses explicit stable
keys; the typed Python formula API can also enumerate selected formula sheets. Artifact requests do
not change Markdown bytes.

## Planned

Planned means not readable today: every suffix below is refused by name, and the receipt says so.
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

Ten planned suffixes share seven adapter modules: `.csv` with `.tsv`, `.odt` with `.odp`, and
`.md` with `.txt` each pair into one module, the other four rows take one each. Nine source
modules today, sixteen at all ten formats. Each format is its own task and follows the ordered
checklist in [`docs/architecture.md`](docs/architecture.md); a new source module needs explicit
user approval.

## Deferred

Not in any current plan.

| in | reason |
|---|---|
| `.doc` `.ppt` (legacy binary) | OLE2 container, Word 97 piece table; needs `olefile` or an own CFB reader; text only, tables lossy |
| `.rtf` | own parser required |

## Never

OCR, ML layout models, network. A page without a text layer is reported in the receipt, not guessed.
