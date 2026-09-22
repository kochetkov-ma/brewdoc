# Source annotations and provenance

Each file `<document-id>.json` describes one unchanged corpus input. Find it
through `annotation_path` in [corpus.json](../corpus.json). An annotation is
source evidence for later output review. It is not expected Markdown, a receipt
snapshot, an OCR transcript or a conversion quality score.

## Manifest contract

Manifest `schema_version` is 2. Existing IDs, paths, licenses, feature groups,
formats and local hashes retain their meanings. Added fields are:

| Field | Meaning |
|---|---|
| `title`, `topics`, `language` | Document title, topic labels and language tag; `und` means undetermined or no meaningful language. These match the annotation. |
| `provenance_kind` | `public` for an input obtained from a public Internet source; `generated` for an input created by a recorded generator. |
| `source_url` | Original provenance URL, which can be a landing page or a direct file. |
| `download_url` | Observed direct download link. It does not claim to reconstruct an undocumented historical download. |
| `source_verification` | Dated HTTP method/status, effective URL, downloaded hash/size and `match`, `mismatch` or `unavailable` result. |
| `sha256`, `size_bytes` | Identity of the stored input bytes, independent of the current remote response. |
| `annotation_path` | Repository-relative path to an existing annotation bound to the same input hash. |

All 41 current inputs are `public`; none were generated for this corpus. The
20 Calamine examples and 10 document or presentation examples are public
upstream fixtures. Their `feature_probe` group is separate from provenance.
The `core` group holds 11 original documents. All inputs already appear in the
test suite, so neither group is held out.
Private company documents and private source-derived test shapes are prohibited.

A source match requires a completed HTTP 200 GET with both hash and size equal
to the stored input. HEAD accessibility alone is insufficient. A mismatch or
unavailable source never authorizes replacing an input. The USPTO endpoint
regenerates CreationDate: its remote SHA differs while a bounded comparison
shows all other bytes and decoded page images equal. That remains a byte
`mismatch`, with the narrower content observation recorded separately.

## Annotation contract

Annotation `schema_version` is 1. Required document fields are `document_id`,
`source_sha256`, `title`, `topics`, `language`, `annotation_scope`,
`review_status`, `limitations`, and exactly one of `pages`, `sheets`, `chapters`
or `slides`.
`document_id` and `source_sha256` must match the manifest. Enumerate every
page, sheet, main-story chapter or declared slide in source order, including
empty sheets, image-only pages, hidden slides and empty slides.

`annotation_scope` is `all_pages`, `all_sheets`, `main_story` or `all_slides`.
`review_status` is `complete` or `partial`; state what was inspected and what
remains uncertain.
Completeness refers to the declared annotation work. It does not imply successful
extraction or a full visual review unless the recorded review methods establish
that. Explicit uncertainty is preferable to an invented count or location.

PDF page records identify the one-based page number, dimensions, layout,
content types and localized tables/figures. Bounding boxes use PDF points in
`[x0, top, x1, bottom]` order, with the page coordinate basis stated when needed.
Keep a whole object distinct from a partial table candidate. Tables identify
structure such as grouped headers, ruled borders and merged regions. Figures
identify kind, labels or captions where visible. Review methods and uncertainties
state whether evidence came from visual inspection, text/geometry or both.

The PDF JSON types are consistent across documents:

| Field | Type and meaning |
|---|---|
| `pages` | Array of page objects in source order. |
| `number`, `text_char_count` | Integers; page numbers start at 1 and character counts at 0. |
| `width_pt`, `height_pt` | Positive JSON numbers in PDF points. |
| `layout` | Object with `columns` as an integer and `orientation` as a string. An optional string `description` explains insets or mixed layouts. |
| `content_types`, `review_method`, `uncertainties` | Arrays of strings, including an empty array when no uncertainty was recorded. |
| `tables`, `figures`, `other_features` | Arrays of objects, never strings. |
| Table or figure `id`, `label`, `notes` | Strings; IDs locate objects within their document/page. Notes can be empty. |
| `bbox_pt` | Four JSON numbers `[x0, top, x1, bottom]`, or null when not measured. Optional on other features. |
| Table `structure` | Object with a string `description`; optional numeric counts such as `columns`, `header_rows`, `body_rows` can be null when unknown. |
| Figure `kind` | String describing the object; optional `panels` array retains source-specific subregions. |
| Other feature | Object with a string `description`, or observed `kind`, `label`, `location`, `bbox_pt` and `notes` fields. |

Document identifiers, hashes, titles, language, annotation scope and review status
are strings; `schema_version` is an integer; `topics` and `limitations` are arrays
of strings. Descriptions retain source observations. Extra source-specific
properties do not change the types above or imply an exhaustive schema for every
PDF object or workbook format.

Workbook sheet records identify exact names and order, dimensions, visibility,
cell types and content. Locate tables, merges, charts and other structures with
one-based A1 ranges or explicit cell anchors when observed. Separate source
package evidence from values returned by a reader. A sheet named Chart is not
proof of a chart object; an empty value grid is not proof of an empty package.
Formula expressions, cached values and calculated results are different facts.

DOCX chapter records describe the WordprocessingML main story. Preserve direct
body block order and record paragraph styles, table dimensions and spans, nested
tables, related omitted parts and archive/XML resource measurements. A chapter
is an inspection unit for source facts. It is not authored expected Markdown or
a claim that style inheritance was reconstructed.

PPTX slide records follow the relationship order declared by presentation.xml,
not ZIP member names. Record hidden state, title candidates, shape-tree text and
table order, body speaker notes, pictures and unsupported objects. Keep notes
body text separate from slide image, header, footer, date and number furniture.
For pictures, distinguish embedded and external sources and retain exact package
byte counts when an embedded member exists. Do not infer captions from nearby
text or fetch external resources.

Use `null` and an explanation when a location, count or property is unknown.
An empty object array means no such object was found by the stated method; it
must not silently claim exhaustive visual absence. Preserve limitations such
as scanned text, unparsed chart parts, incomplete formula metadata and glyphs
that cannot be identified reliably. Source excerpts and cell examples are
locators for review, not authored expected conversion output.

## Future generated cases

Generated cases are permitted only when explicitly marked and reproducible.
Use `provenance_kind: generated`, null `source_url` and `download_url`, and a
`generator` object with repository-relative `path`, exact `version`, generator
`sha256`, a descriptive `case` and explicit `seed` (null for a deterministic
generator without randomness). Record generator arguments when they affect
bytes, and identify its public or synthetic inputs. Keep output size/hash,
format, features, license and source annotation as for public inputs.

A generator must not consume private documents or derive test shapes from them.
Do not label a downloaded upstream test fixture as locally generated. New files
need their own provenance and license record; no generated inputs are present
in the current manifest.
