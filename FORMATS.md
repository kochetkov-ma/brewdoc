# Formats

What brewdoc reads today, what is planned, what is excluded, what is deferred and what it will never do.
Base runtime dependencies are `pdfplumber` and `python-calamine`. Static HTML adds one conditional native parser;
other formats retain the two-dependency policy.

## Supported

| in | reader | mechanism |
|---|---|---|
| `.pdf` | pdfplumber | text layer required; anchored page units; each region routed by its own ruling |
| `.docx` | stdlib `zipfile` + `xml.etree` | paragraphs and tables in reading order; anchored chapters retain duplicate and empty headings |
| `.pptx` | stdlib `zipfile` + `xml.etree` | slides in declared order; text, tables, speaker notes and picture metadata placeholders |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | exact sheet selection in caller order with full-source ordinal keys; cached values in Markdown |
| `.html` | selectolax Lexbor | local static body or fragment in DOM order; one anchored chapter; CPython 3.12-3.14 only |

All supported routes emit `brewdoc.markdown/2`: quoted source title, deterministic metadata,
artifact inventory, known omissions, linked contents, then anchored content units. Empty physical
PDF pages, selected empty sheets, heading-created empty DOCX chapters, and declared empty or hidden
PPTX slides remain navigable.

Every receipt line uses `brewdoc.receipt/1`, which names the route (`pdf`, `doc`, `presentation`,
`sheet`, `html`), its unit kind (`page`, `chapter`, `slide`, `sheet`) and how many units were rendered. The
unit kind decides the content keys: `page/000001`, `chapter/000001`, `slide/000001`,
`sheet/000001`. A suffix brewdoc does not read is refused with route `none` and unit kind `none`;
every other refusal keeps its route's own name and unit kind, with zero units.

## Static HTML

HTML requires CPython 3.12-3.14 and this exact marked dependency:

```text
selectolax==0.4.13; python_version < '3.15' and platform_python_implementation == 'CPython'
```

Brewdoc's overall Python requirement remains `>=3.12`. The `.html` suffix and `render_html`
API remain registered on other runtimes and explicitly refuse with route `html`, kind `chapter`,
zero units, empty Markdown and no output writes. Self-check verifies this refusal and all old
formats. A missing or broken native parser on supported CPython is an environment failure:
HTML refuses and self-check exits 1. There is no alternate parser.

Read the full recovered body or fragment, including navigation, main content, sidebars and footer.
The first nonempty recovered head title labels `chapter/000001`; otherwise use `Untitled`.
Only `.html` is accepted. Input must be strict UTF-8, optionally prefixed by a UTF-8 BOM.
Charset declarations in the first 1,024 bytes after the BOM must name `utf-8` or `utf8`;
malformed, unknown, conflicting or non-UTF-8 declarations refuse. Invalid bytes are not repaired.
HTML preserves meaningful Unicode and emits UTF-8 without BOM, with LF newlines. Other routes
retain ASCII output. HTML has no artifacts.

Headings, prose, lists and emphasis retain recovered DOM order. Preformatted code preserves
literal angle brackets, ampersands, newlines, tabs and repeated spaces in a fence longer than
any source backtick run. Inline code outside tables uses the same fenced-block fallback at its
reading-order position, omitting inline layout. Table-cell code uses `Code: text="..."` with
escaped literal data, omitting monospace layout. Literal LF, tab and CR in cell code and quoted
control metadata become `&#10;`, `&#9;` and `&#13;` after escaping source ampersands.
Emphasis and safe links balance separately around each nonempty text segment across a fence.
Code-only emphasis adds no delimiter paragraphs. A safe anchor enclosing only literal code or
image placeholders retains its destination once as `[Link target](destination)` after that content.
Ordinary text links and existing cell links around code/images get no duplicate fallback.
Safe anchors consumed inside code/pre retain labelled records after the literal representation:
`[Link target](destination); text="..."`. Records keep recovered source order in body paragraphs
or the same table cell. Labels preserve Unicode, repeated spaces and decoded characters with
the quoted literal escaping above, including escaped source quotes. Retained literal whitespace
is data; an anchor without retained characters is empty. Hidden/excluded, unsafe/missing-target
or empty anchors add no record.
Each body target paragraph counts once in `text_regions`; cell target fragments add no regions
or tables. Each consumed source anchor adds one record; separate anchors with identical URLs
stay distinct. Nested code/pre do not duplicate those records.
Quotes use matching `Quote (level N):` and `End quote (level N).` paragraphs around their
children, preserving nested scopes and order without Markdown blockquote layout.

Plain list items emit their marker once; later paragraphs use blank lines and continuation
indentation. Items containing a standalone fence, table/caption, control record, image placeholder
or quote use `List item (level N, marker "-"):` and `End list item (level N).` boundaries.
Ordered markers retain their number. Affected ancestor items use the same scopes so all child
blocks stay enclosed in DOM order. Plain nested items inside a labelled scope use indentation
relative to that scope; unaffected lists keep normal Markdown layout. Each emitted paragraph,
literal block, control or link-target record counts once, including list continuations.
List/quote boundary labels and empty or delimiter-only fragments add no text regions.

Tables keep captions and recovered row/cell order across head, body and foot sections. Spans
expand into a rectangular grid with origin content once and empty continuations. Nested tables
stay inside their parent cell as labelled row/column records with origin coordinates and spans.
Controls preserve source-declared fields, values and boolean attribute presence in fixed-order
records. Forms, labels, fieldsets, buttons, inputs, textarea/output, every select option and
datalist suggestion, progress/meter and details/summary stay in source order. Closed details,
unselected options and disabled controls remain present. Missing attributes are `unspecified`;
supplied empty strings remain empty. Browser defaults and current user values are not inferred.
Supplied password/file input values are redacted; hidden inputs are omitted explicitly.

Only HTTP, HTTPS, mailto, relative and fragment link targets become links after normalized scheme
checks. Other targets are inert text; form actions and overrides always remain inert metadata.
Images preserve supplied alt/title and source category in a placeholder without pixels or fetching.
Data, unsupported and missing image targets are omitted. Source Markdown/HTML is escaped;
source IDs never become output anchors.

Comments, head content, scripts, styles, templates and active embeds are excluded. Direct `hidden`,
`aria-hidden="true"`, effective inline `display:none` or `visibility:hidden` exclude whole
subtrees. Inline declarations respect supported values, order and `!important`; external and
embedded stylesheets are not evaluated. Unknown nonexcluded elements retain useful children.
SVG title/description/text, canvas fallback and body noscript text remain available. Omissions
name graphical SVG, canvas pixels, MathML semantics, active media and dynamic browser content.
This is static source conversion, not browser-complete extraction.

Limits are inclusive; exceeding one refuses before writing output. MiB is 1,048,576 bytes:
8 MiB raw input, depth 256 (recovered root at 1), 200,000 recovered elements including excluded
subtrees, 1 MiB UTF-8 per decoded attribute, 32 MiB included decoded text before whitespace
reduction, 100,000 total span-expanded table positions including nested tables, and 64 MiB final
UTF-8 Markdown including the envelope. Postparse checks do not guarantee native-parser memory safety.

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
No further parser dependency is planned. Each format requires its own acceptance task.
Order = implementation order, by value for agents.

| # | in | mechanism | output |
|---|---|---|---|
| 1 | `.odt` `.odp` | `zipfile` + `content.xml` | paragraphs, tables; one chapter per slide for `.odp` |
| 2 | `.epub` | `zipfile` + XHTML chapters; parser reuse requires separate acceptance | one chapter per spine item |
| 3 | `.md` `.txt` | pass-through | bytes unchanged, receipt still emitted |
| 4 | `.eml` | `email` | headers, text body, attachment names |

Image behavior for planned containers is not accepted yet. Never OCR.

Six planned suffixes share four adapter modules: `.odt` with `.odp` and `.md` with `.txt`
each pair into one module; the other two rows take one each. Eleven source modules including
HTML, fifteen at all planned formats. Each format is its own task and follows the ordered
checklist in [`docs/architecture.md`](docs/architecture.md); a new source module needs explicit
user approval.

## Excluded

CSV and TSV are intentionally unsupported and are not planned. LLMs can read their text tables
directly, so brewdoc does not convert them to Markdown.

## Deferred

Not in any current plan.

| in | reason |
|---|---|
| `.doc` `.ppt` (legacy binary) | OLE2 container, Word 97 piece table; needs `olefile` or an own CFB reader; text only, tables lossy |
| `.rtf` | own parser required |

## Never

OCR, ML layout models, network. A page without a text layer is reported in the receipt, not guessed.
