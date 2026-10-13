# Formats

Supported extensions cover the profiles below, not every possible file. PDF needs a text layer;
unreadable, malformed and unsupported input is refused by name. Base dependencies are pdfplumber
and python-calamine; static HTML adds its conditional native parser. Other routes use those or stdlib.

## Supported

| Input | Reader | Content |
|---|---|---|
| `.pdf` | pdfplumber | Anchored pages; each region routes by its own ruling; text layer required |
| `.docx` | stdlib ZIP/XML | Ordered paragraphs/tables; duplicate and empty heading-created chapters |
| `.pptx` | stdlib ZIP/XML | Declared slides, text/tables/notes and picture metadata; hidden/empty slides retained |
| `.epub` | stdlib ZIP/XML | EPUB 2/3 static XHTML; every declared spine item, including empty/non-linear content |
| `.xlsx` `.xlsm` `.xls` `.xlsb` `.ods` | python-calamine | Cached values; exact selected sheets in caller order with full-source ordinals |
| `.html` | selectolax Lexbor | Local static body/fragment in DOM order; one chapter; CPython 3.12-3.14 |

All routes emit brewdoc.markdown/2: quoted title, deterministic metadata, artifact inventory,
known omissions, linked contents and anchored units. Empty physical pages/sheets/chapters/slides
stay navigable. Local receipts use brewdoc.receipt/1 with route, unit kind and count:
routes are pdf, doc, presentation, sheet, epub and html.
PDF page/000001, DOCX/EPUB/HTML chapter/000001, PPTX slide/000001, workbook sheet/000001.
Unsupported suffixes use route/kind none; other refusals retain their route/kind and zero units.
HTML/EPUB preserve Unicode as UTF-8 without BOM and LF newlines; other routes emit ASCII.
MiB means 1,048,576 bytes. Local conversion never acquires external resources.

## PDF

Region order: fully ruled table; unfinished ruling recovered from baselines on its structural
grid; horizontal head/foot rules; unruled tabular band as layout-preserved text; captioned
Table N cropped to the next blank band; two-column prose split at the density gutter; plain lines.
Images/figures and their drawn text, page-spanning tables and rotated text are not carried.
For tables crossing pages, columns recover but wrapped rows stay separate.
Sub/superscripts are folded into the line. Broken font ligatures are counted/folded, not guessed.

## Static HTML

Requires CPython 3.12-3.14 and this exact conditional dependency:

```text
selectolax==0.4.13; python_version < '3.15' and platform_python_implementation == 'CPython'
```

Overall Python support stays >=3.12. Other runtimes retain the HTML API/route but refuse with
html/chapter, zero units, empty Markdown and no writes; self-check verifies that and other routes.
A missing/broken parser on supported CPython fails HTML and self-check. No alternate parser.

By default, read the complete recovered body/fragment, including navigation/sidebars/footer. First nonempty
head title labels chapter/000001, otherwise Untitled. Only .html and strict UTF-8 with optional
BOM are accepted. Charset declarations in the first 1,024 bytes after BOM must be utf-8/utf8;
malformed, unknown, conflicting/non-UTF-8 declarations or invalid bytes refuse. No HTML artifacts.

Prose/headings/lists/emphasis retain recovered DOM order. Pre and inline code outside tables
become literal fences longer than any source backtick run; angle brackets, ampersands, whitespace,
LF/tabs and repeated spaces are retained. Inline layout is omitted. Cell code uses
`Code: text="..."` with escaped literals; LF/tab/CR become &#10;/&#9;/&#13; after source ampersand
escaping. Emphasis and safe links balance around each nonempty segment across fences;
code-only emphasis produces no delimiter paragraph.

Safe anchors enclosing only code/images retain one `[Link target](destination)` after them;
ordinary text/cell links do not duplicate it. Anchors consumed inside code/pre retain ordered
`[Link target](destination); text="..."` records, in paragraphs or their original cell.
Labels retain Unicode, decoded characters, repeated spaces and escaped quotes. Retained whitespace
is data; empty, hidden/excluded, unsafe or missing-target anchors add no record. Separate anchors
with identical URLs stay distinct; nested code/pre does not duplicate them. Body target paragraphs
count once in text_regions; cell fragments add no regions/tables. Each consumed anchor adds one record.

Quotes use `Quote (level N):` / `End quote (level N).` boundaries. Plain list items emit their
marker once, then blank lines/continuation indentation. Items containing fences, tables/captions,
controls, images or quotes use `List item (level N, marker "-"):` / `End list item (level N).`
boundaries; affected ancestors use the same scopes. Ordered markers retain numbers. Plain nested
items indent relative to labelled scopes; unaffected lists keep Markdown layout. Emitted paragraphs,
literal blocks, controls and link targets count once; boundary/empty/delimiter-only fragments do not.

Tables retain captions and recovered head/body/foot row/cell order. Spans expand to rectangular
grids with origin content once and empty continuations. Nested tables remain in their cell as
labelled row/column records with origin coordinates/spans. Controls use fixed-order source-declared
fields, values and boolean presence. Forms/labels/fieldsets/buttons/inputs, textarea/output,
all select options/datalist suggestions, progress/meter and details/summary stay in source order.
Closed details, unselected options and disabled controls remain. Missing attributes are unspecified;
empty strings stay empty. No inferred defaults/current user values. Password/file values are
redacted; hidden inputs are omitted explicitly.

Only normalized HTTP/HTTPS/mailto/relative/fragment targets become links. Other targets are inert;
form actions/overrides are always metadata. Images keep supplied alt/title/source category without
pixels/fetching; data/unsupported/missing targets are omitted. Source Markdown/HTML is escaped;
source IDs never become output anchors.

Comments/head/scripts/styles/templates/active embeds are excluded. Direct hidden,
aria-hidden=true and effective inline display:none/visibility:hidden omit entire subtrees.
Inline declarations respect supported values/order/!important; stylesheets are not evaluated.
Unknown nonexcluded elements retain children. SVG title/description/text, canvas fallback and
body noscript remain; graphical SVG, canvas pixels, MathML, active media and dynamic content are
omitted. This is static conversion, not browser-complete extraction.

Inclusive limits, exceeded before output writes: raw input 8 MiB; depth 256 with root at 1;
200,000 recovered elements including excluded subtrees; 1 MiB UTF-8 per decoded attribute;
32 MiB included decoded text before whitespace reduction; 100,000 expanded table positions
including nested tables; final UTF-8 Markdown/envelope 64 MiB. Postparse admission is not a
native-parser memory-safety guarantee.

### Article selection (unreleased source)

This opt-in mode is available in the source checkout, not the published 0.3.3 package or binaries.
Use `run(path, article=True)`, `render_html(path, article=True)`, local `--article`, or combine
`--article` with `--url` and optionally `--render-js`. Other document formats refuse with
exit 1; combining it with self-check is a usage error with exit 2. Local APIs stay offline;
URL selection uses the frozen snapshot.

Only literal `main` and `article` elements select roots; `role=main`, `role=article` and class
names do not. Existing `role=button` controls exclude candidates and fallback prose.
Hidden/excluded ancestors remove candidates. Candidates inside `nav`, `aside`, `header`, `footer`
or form-control contexts are ignored. One eligible main defines the scope, otherwise the body does;
multiple eligible mains refuse. A main inside an article does not replace its outer article.
In that scope, one outer article selects its complete subtree,
including nested articles. Peer articles refuse, even when one is empty.

Without an eligible article, one main can qualify only with exactly one nonempty h1, prose
outside navigation, tables and controls, and no visible form. A body without an article cannot
qualify. Selection with no rendered content refuses. An explicit article containing only a
renderable table or image is valid. The existing head-title/Untitled rule remains; headings
do not become a replacement title and no author, date or other metadata is inferred.

Ambiguity and missing/empty candidates produce a named HTML refusal, empty Markdown and no writes.
There is no score, generic readability extraction or fallback to the complete page. Selected
content retains the static HTML rendering rules and limits above. Complete-tree and complete-body
table budgets are checked before selection, including content outside the selected subtree.
HTML article receipts include the following exact `not_carried` entry on success and refusal.
Successful Markdown carries the identical entry in Known omissions:

```text
body content outside the selected article or main subtree; complete page coverage is not claimed
```

Single-article pages and semantically marked documentation are suitable. Multi-article feeds
and pages relying on visual layout or unmarked containers are unsuitable. A visible form
disqualifies main fallback; forms inside an eligible explicit article remain rendered content.
The corpus includes MDN document structure for article/navigation separation, MDN form refusal,
MDN flexbox for peer-article refusal, and unmarked Apache HTTPD documentation for refusal.
These fixtures establish bounded behavior, not general website coverage.

## Python package compatibility

The development and existing CI baseline remains CPython 3.12. Declared Python >=3.12 support
and the CPython 3.12-3.14 HTML parser constraint are not proof of every OS/minor combination.
The following package-validation targets are pending CI evidence:

| Target | CPython | Package cases | Optional JS expectation |
|---|---|---|---|
| Ubuntu 22.04, x64, glibc | 3.13.15 | Wheel and sdist, base and render-js extra | Execute |
| macOS 15, ARM64 | 3.14.7 | Wheel and sdist, base and render-js extra | Execute |
| Windows Server 2025, x64 | 3.14.7 | Wheel and sdist, base and render-js extra | Explicit refusal before requests |

No passing result is claimed until those exact jobs complete. This matrix concerns Python
packages; the published standalone binaries retain their separate bundled-runtime targets.
Other OS/minor combinations, free-threaded CPython and other architectures remain untested here.

## EPUB

Local .epub uses stdlib ZIP/XML without an HTML parser/native runtime. Accepts package 2.0/3.0,
one rootfile/manifest and a nonempty spine. Qualified XHTML needs exactly one direct body.
XML is strict UTF-8/UTF-16 with consistent BOM/declaration. Internal DTD subsets, entity declarations,
XInclude and XHTML base/xml:base refuse; bare HTML/NCX doctypes are never fetched.

Spine order includes linear=no as Auxiliary content. Repeated references refuse; entries sharing
an XHTML fallback retain separate ordinals. Required unsupported media needs a declared local XHTML
fallback. EPUB3 requires declared TOC navigation; EPUB2 its spine-declared NCX. Nested/repeated
navigation stays in source preorder after Contents. A navigation item in the spine represents its
consumed TOC once while retaining other content/chapter; TOC-only chapters link to Publication navigation.

Labels choose first matching navigation label, XHTML title, included heading, then Chapter N.
Headings remain; chapter/000001 keys and generated fragment anchors replace source IDs. Duplicate
IDs refuse. Required navigation resolves to available spine content; unresolved ordinary body links
become inert records. HTTP(S)/mailto links remain without fetching.

Paragraphs/headings/emphasis/lists/quotes/tables/tails retain XML order. h1-h4 map to levels 3-6;
deeper headings are labelled bold text. Ordered lists keep starts/reversed order/explicit values.
Complex/anchored items and nested quotes use labelled boundaries. Body/heading code uses safe
fences retaining XML-normalized literals and balanced emphasis/links. Cell code keeps escaped
Code records and numeric LF/tab/CR entities without monospace layout. Tables keep group order,
captions, spans and empty continuations; nested tables remain coordinate records in their origin cell.

Images use `Image: alt="..."; title="..."; src="..."` in source position; missing attributes are
unquoted unspecified, supplied empty values remain "". SVG preserves title/description/image-reference
metadata. Pixels, graphical SVG/unsupported MathML/SVG semantics, CSS/layout/pagination/fonts, scripts,
forms, active media and navigation beyond TOC are omitted. hidden/aria-hidden=true omit subtrees;
CSS is not evaluated. No archive extraction, network, JS, OCR or DRM decryption occurs.

Titles/creators/languages/identifiers follow existing metadata in that order, retaining declaration
order, duplicates, empty values and Unicode. A declared unique identifier must resolve. Source/body
hash meanings remain. No EPUB artifacts; --sheet/--artifact remain workbook-only.
Malformed/missing required content, unsafe paths, ambiguous names, duplicate members/IDs, encrypted
reading text/unsupported encryption, fixed layout and multiple rootfiles refuse the entire book.
Recognized IDPF/Adobe font obfuscation is accepted only for existing declared fonts, never read/decoded.
Hard refusals return zero units, empty Markdown and no writes.

| Inclusive budget | Maximum |
|---|---:|
| Compressed source; declared expanded total; actual bytes read | 32 MiB each |
| ZIP members including directories | 1,024 |
| Declared/observed bytes per consumed member | 8 MiB |
| XML elements per parse; across parses | 65,536; 262,144 |
| XML depth, root at 1; spine items | 64; 512 |
| Table columns; slots/table; slots/publication | 256; 65,536; 262,144 |
| Complete UTF-8 Markdown | 8 MiB |

Every physical reread/logical parse and nested grid consumes its aggregate budget. Expat checks
XML depth/elements before tree construction; ZIP central-directory allocation precedes member admission. Intermediate
strings/output rollback also consume memory. These are policy limits, not capacity or memory caps.

## URL acquisition

--url explicitly acquires a static HTTP(S) page, then converts a frozen UTF-8 snapshot. Local HTML
and APIs remain offline. Relative links/images resolve against final URL/valid base without fetching
images/linked pages. The default complete recovered body retains source order. Source-only
`--article` selects a subtree under the [article contract](#article-selection-unreleased-source).
Local files and static capture do not execute scripts.

Only public destinations/default ports 80/443 pass. All DNS answers, peers and redirects are
validated. TLS verifies certificates/hostnames. GET excludes ambient credentials/cookies/proxy
environment. At most five redirects; downgrades, unsupported schemes/private/special-use addresses
refuse. Transferred/decompressed HTML is limited to 8 MiB. Accept text/html with absent or UTF-8/UTF8
charset, identity or bounded gzip encoding; invalid UTF-8 bytes/conflicting declarations refuse.

--timeout is a positive finite waiting budget within clock range, default 30 seconds. One budget
covers prerequisites/DNS/connections/TLS/redirects/body/scripts/resources/JS waiting. Ready content
returns immediately; no separate five-second network or twelve-second JS ceiling. Recovery is
bounded to one second, cleanup to another 1.1 seconds. Local conversion/output follow; total CLI
duration need not equal the wait budget.

--render-js requires installed brewdoc[render-js]: QuickJS-ng 0.17.0.1 with bundled LinkeDOM 0.18.13.
No Node/browser/runtime downloads. Native ABI/resource prerequisites are checked before HTTP.
JS requires macOS ARM64 or 64-bit glibc Linux x86_64/aarch64; Windows, macOS Intel, musl, 32-bit and
unsupported ABIs refuse. Execution is a separate process without filesystem/shell APIs or reused
browser state; native limits are not an OS sandbox against engine vulnerabilities.

The finite subset supports DOM mutations, classic scripts, relative/dynamic module imports,
Promise jobs, function timers, GET fetch and async GET XHR. Only async XHR admits the HN Algolia
search POST: https://hn.algolia.com documents may query
https://uj5wyc0l7x-dsn.algolia.net/1/indexes/Item_dev/query with original public application/key
query, string JSON body and application/x-www-form-urlencoded header. Other POST/fetch POST,
cookies and user credentials are unsupported. No redirects/retries/body rewriting. Search UTF-8
body is limited to 16,384 bytes, JSON depth four and 256 total array entries. Unsupported options keep
partial content without sending; security/resource overflow is a hard refusal.

Read-only history and HTTP(S) anchor components provide limited compatibility. innerWidth starts
at 1024 CSS pixels, is writable through window/global and resets next capture. Inline style reads
mark partial without CSS/layout computation. Temporary localStorage starts empty, is discarded
without storage events and marks partial on any access. Its 200-key/65,536 UTF-8-byte key/value
quota is hard. Host requests validate destinations; cross-origin API bodies require accepted CORS.
No authenticated fetch, arbitrary request headers or cookie model. Scripts follow parsing;
async/defer/event order is approximate. Layout/IntersectionObserver/iframes/workers/WebSockets/
media/browser CSP enforcement are outside the subset.

| JS budget | Maximum |
|---|---:|
| Host requests | 100 |
| Per response/snapshot | 8 MiB |
| Aggregate decoded responses/outgoing search bodies | 32 MiB |
| Native heap; OS-thread stack | 64 MiB; 1 MiB |
| Native invocation; Promise jobs; fired timers | 250 ms; 2,000; 200 |
| Child CPU | 45 seconds |

Charge search bodies once before sending. Increased timeout does not enlarge these budgets.
Timing-only expiry stops scripts/network after complete validated HTML exists. Cooperative recovery
returns current DOM partial with capture_timeout/pending work. Parent-cancelled unresponsive workers
without hard failures use acquired original HTML as initial_html_timeout_fallback, without refetch;
JS completion/unobserved work is unknown. Incomplete main responses, security/heap/native faults,
tampering/actual quotas refuse without output and take precedence. Ordinary script/API errors can
produce useful partial DOM.

URL receipts use brewdoc.receipt/2 with acquisition: redacted requested/final/base URLs, response/
snapshot identities, mode, capture status, effective timeout_seconds and explicit omissions.
JS snapshot kind is javascript_dom or initial_html_timeout_fallback; the latter is original HTML.
New soft_window_seconds equals configured timeout, soft_window_origin is request; historical
capture-relative receipts retain their meaning. Query values/nonempty fragments are redacted.
Snapshot names are url-<first 16 URL SHA-256 hex>.html; Markdown identity names those snapshot bytes.
Local receipts remain brewdoc.receipt/1. Exit 0 partial means valid conversion, not complete JS or
primary-content recovery. Frozen conversion is deterministic; live acquisition is mutable. No 90%
coverage or general browser compatibility is claimed.

--url with a document/self-check, --render-js without --url, invalid timeout or explicit timeout
without --url produce exit 2 before acquisition. URL with --sheet/--artifact produces HTML refusal
exit 1 before HTTP. Hard refusal preserves existing destinations. HTML structure/Markdown limits apply.

## Container documents

DOCX accepts Transitional/Strict WordprocessingML. Main-story paragraphs/tables retain order;
Title/Heading1-Heading9 start chapters. Tabs become spaces; line breaks remain. Nested tables flatten
into parent cells; invalid/nonpositive spans use one column. Images/headers/footers/footnotes/comments,
tracked-change reconstruction, list layout, style inheritance and vertical merge reconstruction are omitted.

PPTX follows relationships/declared slide order, including hidden/empty slides. Title placeholders
label units, otherwise Untitled; headings are ## Slide N: "<title>" with slide/000001 keys.
render_presentation tallies slides without a Slides metadata row. Grouped shape XML, paragraphs/runs
and table rows/cells retain order; merge continuations remain empty. Body notes follow content;
other notes placeholders are omitted. Pictures have no pixels and use this exact placeholder:

```text
Image: name="<name>"; size=<cx>x<cy> EMU; alt="<descr|none>"; caption="<title|none>"; source=<embedded|external>; bytes=<n|unknown>
```

Embedded counts come from package members; external pictures are never fetched. Charts/SmartArt/
audio/video/objects/animations/transitions, master-only text, styling/positioning are omitted.
Required package/presentation/slide relationships and XML must resolve inside the archive; malformed,
missing or invalid referenced picture/notes parts refuse. Optional absent relationships are accepted.
EPUB image records preserve declarations; DOCX images remain omitted.

## Workbook artifacts

| Input | Formulas | VBA | Constraint |
|---|---|---|---|
| .xlsx | brewdoc.formulas/1 JSON per selected formula-bearing sheet | Unavailable | No evaluation, rewriting or inferred dependencies |
| .xlsm | Same as .xlsx | Related vbaProject.bin when present | Exact opaque bytes |
| .xlsb | Unavailable through pinned binding | Related vbaProject.bin when present | Exact opaque bytes |
| .xls/.ods | Unavailable through pinned binding | Unavailable | Cached values still render |

Readable VBA modules and module/script counts are unavailable/unknown; projects never execute.
Stable explicit keys select artifacts; typed formula APIs enumerate selected formula sheets.
Artifact requests do not change Markdown bytes. Numbers stay as stored (for example 85.0);
dates use ISO days. Formula Markdown contains cached values, not recalculated results.

## Planned

Planned inputs are refused today and each requires its own acceptance task; no new parser dependency.
Order follows value for agents:

| Order | Input | Mechanism/output |
|---|---|---|
| 1 | .odt/.odp | stdlib ZIP/content.xml, paragraphs/tables; one chapter per ODP slide |
| 2 | .md/.txt | Pass-through bytes with receipt |
| 3 | .eml | stdlib email: headers, text body, attachment names |

Image handling is not accepted yet; no OCR. Five suffixes map to three modules: ODT/ODP together,
MD/TXT together, EML separate. Each new module requires its own acceptance task
and explicit approval.

## Excluded

CSV/TSV are intentionally unsupported/unplanned; agent systems can read their text tables directly.

## Deferred

Not currently planned: .doc/.ppt binary OLE2 (including Word 97 piece tables) needs olefile or a CFB reader (text only, lossy tables);
.rtf needs a parser.

## Never

OCR or ML layout models. Local conversion never uses networking; deliberate --url acquisition
is the exception. Missing PDF text layers are named refusals, not guessed content.
