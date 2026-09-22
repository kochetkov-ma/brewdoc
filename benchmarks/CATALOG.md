# Corpus catalog

The corpus contains **41 public Internet inputs and 0 generated inputs**:
9 PDFs, 6 DOCX documents, 4 PPTX presentations and 22 workbooks, totaling
4,658,579 bytes. Original files remain in
[tests/fixtures](../tests/fixtures). Each input below links to its source file,
public download and detailed annotation. [corpus.json](corpus.json) holds the
searchable metadata, license links and immutable local hashes.

`core` contains 11 original government/research documents; `feature_probe`
contains 20 public upstream Calamine examples and 10 public office-format
examples. Both groups already appear in tests. Public provenance is separate
from the feature-probe selection label. Forty downloaded files match the stored
bytes exactly. The patent endpoint
regenerates CreationDate; its explicit mismatch is confined to metadata in a
bounded comparison, with all page images unchanged.

## PDF documents

All 105 pages have source annotation records and recorded visual review. Object
counts below describe source objects, not what a converter managed to extract.

| Original file and title | Format / bytes | Pages | Topic and contents | Public source / annotation |
|---|---|---:|---|---|
| [arxiv-2302.13971v1.pdf](../tests/fixtures/pdf/arxiv-2302.13971v1.pdf)<br>LLaMA: Open and Efficient Foundation Language Models | PDF / 726,566 | 27 | language model pretraining; training datasets. Two-column research paper; 16 tables, 3 numbered figures and displayed equations. | [public download](https://arxiv.org/pdf/2302.13971v1) / [annotation](annotations/pdf-arxiv-2302.13971v1.json) |
| [arxiv-2305.18290v3.pdf](../tests/fixtures/pdf/arxiv-2305.18290v3.pdf)<br>Direct Preference Optimization: Your Language Model is Secretly a Reward Model | PDF / 1,299,212 | 27 | preference optimization; reinforcement learning from human feedback. Single-column research paper; 10 tables, 5 numbered figures and displayed equations. | [public download](https://arxiv.org/pdf/2305.18290v3) / [annotation](annotations/pdf-arxiv-2305.18290v3.json) |
| [arxiv-2402.00838v4.pdf](../tests/fixtures/pdf/arxiv-2402.00838v4.pdf)<br>OLMo: Accelerating the Science of Language Models | PDF / 562,224 | 21 | open language models; training data. Two-column research paper; 8 tables, 4 numbered composite chart figures and a logo. | [public download](https://arxiv.org/pdf/2402.00838v4) / [annotation](annotations/pdf-arxiv-2402.00838v4.json) |
| [eia-steo-table1.pdf](../tests/fixtures/pdf/eia-steo-table1.pdf)<br>Table 1. U.S. Energy Markets Summary, September 2026 | PDF / 167,515 | 1 | energy production; energy consumption. Energy forecast table; grouped year/quarter headers, dot leaders and unruled body rows. | [public download](https://www.eia.gov/outlooks/steo/tables/pdf/1tab.pdf) / [annotation](annotations/pdf-eia-steo-table1.json) |
| [fbi-nics-background-checks-2015-11.pdf](../tests/fixtures/pdf/fbi-nics-background-checks-2015-11.pdf)<br>NICS Firearm Background Checks, November 2015 | PDF / 90,468 | 1 | firearm background checks; state and territory statistics. Landscape 25-column grid; grouped headers, geography rows and totals. | [public download](https://raw.githubusercontent.com/jsvine/pdfplumber/stable/tests/pdfs/nics-background-checks-2015-11.pdf) / [annotation](annotations/pdf-fbi-nics-background-checks-2015-11.json) |
| [federal-register-2020-17221.pdf](../tests/fixtures/pdf/federal-register-2020-17221.pdf)<br>Federal Register: Airworthiness Directives; The Boeing Company Airplanes | PDF / 713,992 | 15 | aviation regulation; Boeing 737 MAX. Three-column legal notice; one table in 2 segments, 10 numbered text-bearing figures and a logo. | [public download](https://raw.githubusercontent.com/jsvine/pdfplumber/stable/tests/pdfs/federal-register-2020-17221.pdf) / [annotation](annotations/pdf-federal-register-2020-17221.json) |
| [nist-sp800-145.pdf](../tests/fixtures/pdf/nist-sp800-145.pdf)<br>The NIST Definition of Cloud Computing | PDF / 85,781 | 7 | cloud computing; service models. Cloud definitions, indented lists, footnote, errata table and cover graphics. | [public download](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-145.pdf) / [annotation](annotations/pdf-nist-sp800-145.json) |
| [us-patent-223898-scan.pdf](../tests/fixtures/pdf/us-patent-223898-scan.pdf)<br>US Patent 223,898: Electric-Lamp, with correction documents | PDF / 227,921 | 5 | electric lamp; carbon filament. Five full-page scanned images; lamp drawings, patent prose and correction documents; no text layer. | [public download](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/0223898) / [annotation](annotations/pdf-us-patent-223898-scan.json) |
| [us-senate-expenditures.pdf](../tests/fixtures/pdf/us-senate-expenditures.pdf)<br>United States Senate expenditures, page B-1191 | PDF / 53,481 | 1 | government expenditure; staff compensation. Landscape payment table; grouped date header, names, amounts and body rows without separators. | [public download](https://raw.githubusercontent.com/jsvine/pdfplumber/stable/tests/pdfs/senate-expenditures.pdf) / [annotation](annotations/pdf-us-senate-expenditures.json) |

## Word documents

All six DOCX main stories have source annotations. The records preserve direct
body block order, styles, tables and spans, nested tables, related omitted parts
and archive/XML resource measurements. They do not treat headers, footers,
footnotes or inherited styles as main-story content.

| Original file and title | Format / bytes | Main stories | Topic and contents | Public source / annotation |
|---|---|---:|---|---|
| [libreoffice-negative-cell-margin-twips.docx](../tests/fixtures/docx/libreoffice-negative-cell-margin-twips.docx)<br>Horizontal table span in a compact Word package | DOCX / 5,902 | 1 | document parser features; horizontal table span. Three-row table with one `gridSpan=2` cell. | [public download](https://raw.githubusercontent.com/LibreOffice/core/master/sw/qa/extras/ooxmlexport/data/negative-cell-margin-twips.docx) / [annotation](annotations/docx-libreoffice-negative-cell-margin-twips.json) |
| [poi-ThreeColHead.docx](../tests/fixtures/docx/poi-ThreeColHead.docx)<br>Two-page Word body with a Heading1 boundary | DOCX / 12,508 | 1 | document parser features; heading boundary. Ten main-story paragraphs, a page break and a related three-column header. | [public download](https://raw.githubusercontent.com/apache/poi/trunk/test-data/document/ThreeColHead.docx) / [annotation](annotations/docx-poi-three-col-head.json) |
| [poi-deep-table-cell.docx](../tests/fixtures/docx/poi-deep-table-cell.docx)<br>Five thousand nested Word tables | DOCX / 17,198 | 1 | document parser features; nested tables. A 1.2 MB XML main part contains 5,000 table levels. | [public download](https://raw.githubusercontent.com/apache/poi/trunk/test-data/document/deep-table-cell.docx) / [annotation](annotations/docx-poi-deep-table-cell.json) |
| [poi-table-alignment.docx](../tests/fixtures/docx/poi-table-alignment.docx)<br>Six table alignment values | DOCX / 15,519 | 1 | document parser features; table alignment values. Six repeated 3 x 3 tables use absent, left, start, center, right and end alignment. | [public download](https://raw.githubusercontent.com/apache/poi/trunk/test-data/document/table-alignment.docx) / [annotation](annotations/docx-poi-table-alignment.json) |
| [tika-testWORD.docx](../tests/fixtures/docx/tika-testWORD.docx)<br>Word title, headings, nested table and links | DOCX / 13,436 | 1 | document parser features; title and heading styles. Title, heading styles, a nested table, bookmarks, hyperlinks and related header/footer parts. | [public download](https://raw.githubusercontent.com/apache/tika/main/tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/tika-parser-microsoft-module/src/test/resources/test-documents/testWORD.docx) / [annotation](annotations/docx-tika-testword.json) |
| [tika-testWORD_various.docx](../tests/fixtures/docx/tika-testWORD_various.docx)<br>Word lists, text boxes, multilingual text and related parts | DOCX / 14,470 | 1 | document parser features; lists and text boxes. List paragraphs, two text-box containers, a 2 x 3 table, Japanese and Gothic text, and a related footnote. | [public download](https://raw.githubusercontent.com/apache/tika/main/tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/tika-parser-microsoft-module/src/test/resources/test-documents/testWORD_various.docx) / [annotation](annotations/docx-tika-testword-various.json) |

## Presentations

The four PPTX inputs contain 15 declared slides. Source annotations preserve
relationship order, hidden state, title and shape order, tables, notes,
pictures and unsupported objects. One slide is hidden. The richer Tika fixture
contains all three notes parts and both picture shapes, including an
`AlternateContent` fallback picture.

| Original file and title | Format / bytes | Slides | Topic and contents | Public source / annotation |
|---|---|---:|---|---|
| [poi-table_test.pptx](../tests/fixtures/pptx/poi-table_test.pptx)<br>Empty six-by-three DrawingML table | PPTX / 28,935 | 1 | presentation parser features; empty table cells. One slide with an empty 6 x 3 DrawingML table. | [public download](https://raw.githubusercontent.com/apache/poi/trunk/test-data/slideshow/table_test.pptx) / [annotation](annotations/pptx-poi-table-test.json) |
| [poi-testPPT.pptx](../tests/fixtures/pptx/poi-testPPT.pptx)<br>Three-slide attachment test presentation | PPTX / 36,518 | 3 | presentation parser features; title and subtitle placeholders. Title, subtitle and body text across three slides; no notes parts. | [public download](https://raw.githubusercontent.com/apache/poi/trunk/test-data/slideshow/testPPT.pptx) / [annotation](annotations/pptx-poi-testppt.json) |
| [python-pptx-test.pptx](../tests/fixtures/pptx/python-pptx-test.pptx)<br>Presentation Title Text | PPTX / 37,859 | 1 | presentation parser features; centered title placeholder. One title slide with centered title and subtitle. | [public download](https://raw.githubusercontent.com/scanny/python-pptx/master/tests/test_files/test.pptx) / [annotation](annotations/pptx-python-pptx-test.json) |
| [tika-testPPT_various2.pptx](../tests/fixtures/pptx/tika-testPPT_various2.pptx)<br>Presentation feature collection with notes, media and a hidden slide | PPTX / 248,937 | 10 | presentation parser features; speaker notes. Notes, embedded pictures, table, chart, SmartArt, OLE objects, hyperlinks and one hidden slide. | [pinned public source](https://github.com/apache/tika/blob/cc1eaf5317d99588b9ee90468723e407d0c72d28/tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/tika-parser-microsoft-module/src/test/resources/test-documents/testPPT_various2.pptx) / [annotation](annotations/pptx-tika-testppt-various2.json) |

## Workbooks

All 54 sheets have complete annotations within the declared structural scope,
including empty sheets. XML and binary inspections cover value grids, formula
counts/caches, merges and table/chart/image presence. XLSB formula expressions
remain undecompiled; no workbook was visually rendered or recalculated, and no
VBA was executed. Unknown properties, including styled blank extents, remain explicit. See each
annotation's `limitations` and the [annotation contract](annotations/README.md).

The two Census workbooks belong to `core`; all Calamine workbooks belong to
`feature_probe`. No workbook was generated for this task.

| Original file and title | Format / bytes | Sheets | Topic and contents | Public source / annotation |
|---|---|---:|---|---|
| [calamine-any_sheets.ods](../tests/fixtures/ods/calamine-any_sheets.ods)<br>Worksheet order, visibility and an embedded chart | ODS / 5,813 | 4 | spreadsheet parser features; sheet-order. Four worksheets, two hidden; blank row and embedded chart with English/Russian content. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/any_sheets.ods) / [annotation](annotations/ods-calamine-any-sheets.json) |
| [calamine-date.ods](../tests/fixtures/ods/calamine-date.ods)<br>ODF date, datetime and time values | ODS / 11,886 | 1 | spreadsheet parser features; date-cell. Date, datetime and two time values, including fractional seconds; numeric companion column. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/date.ods) / [annotation](annotations/ods-calamine-date.json) |
| [calamine-issues.ods](../tests/fixtures/ods/calamine-issues.ods)<br>ODF mixed cells and stored formula results | ODS / 4,095 | 6 | spreadsheet parser features; six-sheets. Mixed cells and Unicode; leading blank row; five formulas with stored string, boolean or numeric results. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/issues.ods) / [annotation](annotations/ods-calamine-issues.json) |
| [calamine-merged_cells.ods](../tests/fixtures/ods/calamine-merged_cells.ods)<br>ODF vertical merge and covered cells | ODS / 8,650 | 1 | spreadsheet parser features; vertical-merge. A2:A3 vertical merge and covered cell; 3 x 3 value grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/merged_cells.ods) / [annotation](annotations/ods-calamine-merged-cells.json) |
| [calamine-special_cells.ods](../tests/fixtures/ods/calamine-special_cells.ods)<br>ODF line breaks and whitespace | ODS / 12,051 | 1 | spreadsheet parser features; string-cells. Five strings with a newline, repeated interior spaces and leading/trailing spaces. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/special_cells.ods) / [annotation](annotations/ods-calamine-special-cells.json) |
| [calamine-any_sheets.xls](../tests/fixtures/xls/calamine-any_sheets.xls)<br>Legacy workbook sheet order and chart sheet | XLS / 38,400 | 4 | spreadsheet parser features; sheet-order. Visible, Hidden, VeryHidden and Chart; empty sheets, blank row, two-series column chart and VBA streams. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/any_sheets.xls) / [annotation](annotations/xls-calamine-any-sheets.json) |
| [calamine-date.xls](../tests/fixtures/xls/calamine-date.xls)<br>Legacy workbook dates, duration and integers | XLS / 5,632 | 1 | spreadsheet parser features; date-cells. Two dates, one duration and integers 15/16/17 in a 3 x 2 grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/date.xls) / [annotation](annotations/xls-calamine-date.json) |
| [calamine-merge_cells.xls](../tests/fixtures/xls/calamine-merge_cells.xls)<br>Legacy workbook horizontal, vertical and block merges | XLS / 19,456 | 1 | spreadsheet parser features; horizontal-merge. Horizontal, vertical and block merges; merge extents exceed the 2 x 3 anchor-value grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/merge_cells.xls) / [annotation](annotations/xls-calamine-merge-cells.json) |
| [calamine-xls_formula.xls](../tests/fixtures/xls/calamine-xls_formula.xls)<br>Legacy workbook same-sheet and cross-sheet formulas | XLS / 25,600 | 2 | spreadsheet parser features; cached-numeric-formula-values. Same-sheet and cross-sheet formula examples with stored numeric results across two sheets. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/xls_formula.xls) / [annotation](annotations/xls-calamine-xls-formula.json) |
| [census-st-est00int-01.xls](../tests/fixtures/xls/census-st-est00int-01.xls)<br>Table 1. Intercensal Estimates of the Resident Population for the United States, Regions, States, and Puerto Rico: April 1, 2000 to July 1, 2010 | XLS / 35,328 | 1 | United States population; states and regions. Population counts in a 70 x 14 value grid; title/header rows, 15 merges, footnotes and a blank row. | [public download](https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/state/st-est00int-01.xls) / [annotation](annotations/xls-census-st-est00int-01.json) |
| [calamine-date.xlsb](../tests/fixtures/xlsb/calamine-date.xlsb)<br>Binary workbook dates and duration | XLSB / 7,711 | 1 | spreadsheet parser features; date-cells. Two dates, one duration and float cells in a 3 x 2 binary value grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/date.xlsb) / [annotation](annotations/xlsb-calamine-date.json) |
| [calamine-issue127.xlsb](../tests/fixtures/xlsb/calamine-issue127.xlsb)<br>Binary workbook with eight empty sheets | XLSB / 13,868 | 8 | spreadsheet parser features; eight-empty-sheets. Eight empty worksheet value grids in source order. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/issue127.xlsb) / [annotation](annotations/xlsb-calamine-issue127.json) |
| [calamine-issues.xlsb](../tests/fixtures/xlsb/calamine-issues.xlsb)<br>Binary workbook mixed cells and Unicode | XLSB / 19,226 | 6 | spreadsheet parser features; six-sheets. Mixed cells and Unicode; leading blank row, five formula records with stored results, and a VBA project. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/issues.xlsb) / [annotation](annotations/xlsb-calamine-issues.json) |
| [calamine-issue221.xlsm](../tests/fixtures/xlsm/calamine-issue221.xlsm)<br>Macro-enabled container with a text grid | XLSM / 7,660 | 1 | spreadsheet parser features; 2-by-2-text-grid. A 2 x 2 text grid in a macro-enabled container; no VBA project. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/issue221.xlsm) / [annotation](annotations/xlsm-calamine-issue221.json) |
| [calamine-issue3.xlsm](../tests/fixtures/xlsm/calamine-issue3.xlsm)<br>Macro-enabled container with one data row and empty sheets | XLSM / 8,266 | 3 | spreadsheet parser features; sheet-order. One float/text data row followed by two empty sheets; no VBA project. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/issue3.xlsm) / [annotation](annotations/xlsm-calamine-issue3.json) |
| [calamine-vba.xlsm](../tests/fixtures/xlsm/calamine-vba.xlsm)<br>VBA project with three empty worksheets | XLSM / 12,752 | 3 | spreadsheet parser features; three-empty-sheets. Three empty worksheet value grids with a stored VBA project. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/vba.xlsm) / [annotation](annotations/xlsm-calamine-vba.json) |
| [calamine-any_sheets.xlsx](../tests/fixtures/xlsx/calamine-any_sheets.xlsx)<br>OOXML sheet order, visibility and chart sheet | XLSX / 14,804 | 4 | spreadsheet parser features; sheet-order. Visible, Hidden, VeryHidden and Chart; real chart part with empty chart value grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/any_sheets.xlsx) / [annotation](annotations/xlsx-calamine-any-sheets.json) |
| [calamine-date.xlsx](../tests/fixtures/xlsx/calamine-date.xlsx)<br>OOXML dates and duration | XLSX / 4,659 | 1 | spreadsheet parser features; date-cells. Two dates, one duration and floats 15/16/17 in a 3 x 2 grid. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/date.xlsx) / [annotation](annotations/xlsx-calamine-date.json) |
| [calamine-inventory-table.xlsx](../tests/fixtures/xlsx/calamine-inventory-table.xlsx)<br>Inventory table of items, types and quantities | XLSX / 8,347 | 1 | spreadsheet parser features; text-cells. Item/type/quantity cells; text and numeric values; one named Excel table. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/inventory-table.xlsx) / [annotation](annotations/xlsx-calamine-inventory-table.json) |
| [calamine-merge_cells.xlsx](../tests/fixtures/xlsx/calamine-merge_cells.xlsx)<br>OOXML horizontal, vertical and block merges | XLSX / 8,948 | 1 | spreadsheet parser features; horizontal-merge. XML merges A1:B1, A2:A4 and B2:D4; anchor cells preserve distinct labels. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/merge_cells.xlsx) / [annotation](annotations/xlsx-calamine-merge-cells.json) |
| [calamine-temperature-table.xlsx](../tests/fixtures/xlsx/calamine-temperature-table.xlsx)<br>Temperature and auxiliary named tables | XLSX / 11,478 | 2 | spreadsheet parser features; two-sheets. Two named Excel tables; text and fractional numeric cells across two sheets. | [public download](https://raw.githubusercontent.com/tafia/calamine/master/tests/temperature-table.xlsx) / [annotation](annotations/xlsx-calamine-temperature-table.json) |
| [census-NST-EST2024-POP.xlsx](../tests/fixtures/xlsx/census-NST-EST2024-POP.xlsx)<br>Annual Estimates of the Resident Population for the United States, Regions, States, District of Columbia, and Puerto Rico: April 1, 2020 to July 1, 2024 | XLSX / 15,507 | 1 | United States population; states and regions. Population counts in a 68 x 7 value grid; title/header rows, 11 merges, footnotes and a blank row. | [public download](https://www2.census.gov/programs-surveys/popest/tables/2020-2024/state/totals/NST-EST2024-POP.xlsx) / [annotation](annotations/xlsx-census-nst-est2024-pop.json) |

## Scope and reuse

Tables, figures, charts, cell ranges, main-story blocks, slide shapes, source
order and review methods are detailed in the annotations. Unknown locations and
undecompiled XLSB formula expressions stay explicit. [Manifest gaps](corpus.json)
list missing source coverage. Complete
annotation records do not establish correct conversion, formula calculation,
chart extraction or OCR. Compare actual outputs with the source evidence using
[the measurement method](METHODOLOGY.md).

Keep each file's [provenance](../tests/fixtures/SOURCES.md), license and
[attribution](README.md#attribution) with redistributed copies. The corpus uses
public documents and public upstream fixtures; private company inputs and
private source-derived cases are excluded. Future generated cases must follow
the [generator provenance contract](annotations/README.md#future-generated-cases).
