# Formats

What brewdoc reads today, what is planned, what is deferred and what it will never do. Runtime
dependencies stay at two (`pdfplumber`, `python-calamine`): every planned format is stdlib only.

## Supported

| in | reader | mechanism |
|---|---|---|
| `.pdf` | pdfplumber | text layer required; anchored page units; each region routed by its own ruling |
| `.docx` | stdlib `zipfile` + `xml.etree` | paragraphs and tables in reading order; anchored chapters retain duplicate and empty headings |
| `.pptx` | stdlib `zipfile` + `xml.etree` | slides in declared order; text, tables, speaker notes and picture metadata placeholders |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | exact sheet selection in caller order with full-source ordinal keys; cached values in Markdown |

All supported routes emit `brewdoc.markdown/2`: quoted source title, deterministic metadata,
artifact inventory, known omissions, linked contents, then anchored content units. Empty physical
PDF pages, selected empty sheets, heading-created empty DOCX chapters, and declared empty or hidden
PPTX slides remain navigable.

Every receipt line uses `brewdoc.receipt/1`, which names the route (`pdf`, `doc`, `presentation`,
`sheet`), its unit kind (`page`, `chapter`, `slide`, `sheet`) and how many units were rendered. The
unit kind decides the content keys: `page/000001`, `chapter/000001`, `slide/000001`,
`sheet/000001`. A suffix brewdoc does not read is refused with route `none` and unit kind `none`;
every other refusal keeps its route's own name and unit kind, with zero units.

## Container documents

DOCX accepts Transitional and Strict WordprocessingML. Main-story paragraphs and tables stay in
document order. `Title` and `Heading1` through `Heading9` start chapters. Tabs become spaces and
line breaks are retained. Nested table text is flattened into its parent cell, and invalid or
non-positive table spans fall back to one column. Images, headers, footers, footnotes, comments,
tracked-change reconstruction, list formatting, style inheritance and vertical merge reconstruction
are omitted.

PPTX follows the presentation relationships and declared slide list instead of ZIP member names.
It reads every declared slide, including hidden and empty slides. Title placeholders label units;
slides without one use `Untitled`. Headings are `## Slide N: "<title>"`, with ordered keys such as
`slide/000001`. The public `render_presentation` tally reports `slides`; there is no Slides metadata
row. Shape and grouped-shape XML order controls reading order. Text keeps paragraph and run order,
DrawingML tables keep row and cell order, and merge continuation cells stay empty. Body speaker
notes follow slide content. Other notes placeholders are omitted.

PPTX pictures use this exact metadata placeholder and never include pixels:

```text
Image: name="<name>"; size=<cx>x<cy> EMU; alt="<descr|none>"; caption="<title|none>"; source=<embedded|external>; bytes=<n|unknown>
```

Embedded picture byte counts come from the related package member. External pictures are never
fetched. Charts, SmartArt, audio, video, embedded objects, animations, transitions,
layout/master-only text, styling and visual positioning are omitted. Required package, presentation
and slide relationships or XML must resolve inside the archive; malformed or missing required parts
refuse the document. A slide may have no picture or notes relationship, but a referenced part must
be valid. PPTX is the only supported container that promises image placeholders. DOCX images
remain omitted.

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
| 1 | `.csv` `.tsv` | `csv` | one Markdown table |
| 2 | `.html` | `html.parser` | headings, lists, tables |
| 3 | `.odt` `.odp` | `zipfile` + `content.xml` | paragraphs, tables; one chapter per slide for `.odp` |
| 4 | `.epub` | `zipfile` + XHTML chapters through the html path | one chapter per spine item |
| 5 | `.md` `.txt` | pass-through | bytes unchanged, receipt still emitted |
| 6 | `.eml` | `email` | headers, text body, attachment names |

Image behavior for planned containers is not accepted yet. Never OCR.

Nine planned suffixes share six adapter modules: `.csv` with `.tsv`, `.odt` with `.odp`, and
`.md` with `.txt` each pair into one module, the other three rows take one each. Ten source
modules today, sixteen at all planned formats. Each format is its own task and follows the ordered
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
