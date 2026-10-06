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

Every local receipt line uses `brewdoc.receipt/1`, which names the route (`pdf`, `doc`, `presentation`,
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

## URL acquisition

`--url URL` explicitly acquires a static HTTP(S) page, then passes a frozen UTF-8
snapshot through the existing HTML converter. Local `.html` and Python APIs keep
their offline contracts. Relative links and image targets resolve against the final
URL and valid source base; this resolution does not fetch images or linked pages.
The full recovered body remains in source order, without automatic article selection.
Local files and static URL capture do not execute JavaScript.

Only public destinations and default ports 80/443 are accepted. Every DNS answer,
connection peer and redirect destination must pass the destination policy. TLS keeps
certificate and hostname verification. GET requests omit ambient credentials,
cookies and proxy environment settings. At most five redirects are followed;
HTTPS-to-HTTP redirects, unsupported schemes and private/special-use addresses
refuse. Static acquisition has 8 MiB transferred/decompressed HTML limits. Only
`text/html` with absent or UTF-8/UTF8 charset is accepted; invalid UTF-8 and
conflicting declarations refuse. Identity and bounded gzip are accepted encodings.

`--timeout SECONDS` sets the maximum loading/waiting budget for one `--url` call,
default 30 seconds. It accepts positive finite seconds within the supported
clock range. One budget covers prerequisites, DNS, connections, TLS, redirects,
the main body, scripts, resources and optional JS waiting. Ready captures return
immediately. For example, `--timeout 60` allows longer acquisition and JS waiting;
there is no separate five-second network or twelve-second JS waiting ceiling.
After waiting stops, trusted recovery is bounded to one second and worker cleanup
to another 1.1 seconds. Local conversion and output of the accepted snapshot follow;
the option does not guarantee an exact overall CLI duration.

`--render-js` additionally requires the installed `brewdoc[render-js]` extra:
QuickJS-ng 0.17.0.1 with bundled LinkeDOM 0.18.13. No Node, browser or runtime
component download is used. The native public API and resource prerequisites are
checked before HTTP. JS requires macOS ARM64 or 64-bit glibc Linux (x86_64/aarch64)
with proved native prerequisites. Windows, macOS Intel, musl Linux, 32-bit systems and
unsupported ABIs explicitly refuse JS.

`--render-js` explicitly executes the selected page's scripts locally in a separate
process. No filesystem or shell APIs are exposed, and browser state is not reused.
The finite subset includes DOM mutations, classic scripts, relative imported
modules and dynamic imports, Promise jobs, function timers, GET fetch and async
GET XHR. Async XHR admits one public search POST profile: documents at
https://hn.algolia.com may query the public Item_dev endpoint at
https://uj5wyc0l7x-dsn.algolia.net/1/indexes/Item_dev/query using the original
public application/key query, string JSON body and application/x-www-form-urlencoded
header. Other POST, fetch POST, cookies and user credentials remain unsupported.
Search acquisition never follows redirects, automatically retries, or rewrites the body. Its strict
UTF-8 body is limited to 16,384 bytes, JSON depth four and 256 total array entries.
Unsupported search options preserve partial content without sending the request;
resource overflow and security failures remain hard refusals.
Read-only history and HTTP(S) anchor components provide limited page
compatibility. Each JS capture starts with `innerWidth` set to 1024 CSS pixels.
The page can change it through `window` or the global; later captures start at
1024 again. Inline style reads mark the capture partial; they do not compute
external CSS or layout. Temporary `localStorage` starts empty, is discarded after
capture and has no storage events. Any access marks the result partial. It accepts
at most 200 keys and 65,536 aggregate UTF-8 bytes across keys and values; exceeding
either quota causes hard refusal. Host requests remain destination-validated and
bounded. Cross-origin API bodies require accepted CORS headers; there is no authenticated fetch, arbitrary
request-header or browser cookie model. Documents are parsed before scripts;
async/defer and event ordering are approximate. Layout, IntersectionObserver,
iframe execution, workers, WebSockets, media and browser CSP enforcement are
outside this subset. A child process and native limits bound execution; they do
not establish an operating-system sandbox against native engine vulnerabilities.

JS acquisition allows at most 100 host requests, an 8 MiB
limit per response/snapshot and 32 MiB aggregate decoded response and outgoing
search-body bytes. An admitted search body is charged once before sending. The
native heap is limited to 64 MiB. Execution uses a checked, guarded OS thread with
at most 1 MiB of stack, 250 ms per native invocation, 2,000 Promise jobs and 200
fired timer callbacks. The child also has a 45 CPU-second limit. A larger timeout
does not increase these limits.

Once complete validated HTML exists, timing-only expiry stops scripts and further
network activity while preserving valid content. Cooperative recovery returns the
current DOM as partial with `capture_timeout` and observed pending work. If only
parent cancellation can stop an unresponsive worker and no hard failure is observed,
recovery uses the already acquired original HTML with
`initial_html_timeout_fallback`. It does not fetch the page again. This fallback
reports JS completion and unobserved pending work as unknown. Incomplete main
responses, security violations, heap exhaustion, native faults, tampering and actual
resource quotas still refuse without output. Hard failures take precedence over
timing recovery. Ordinary script/API errors can also produce a useful partial DOM.

URL receipts use `brewdoc.receipt/2`: existing conversion fields plus `acquisition`
with redacted requested/final/base URLs, response and snapshot identities, mode,
capture status, effective `timeout_seconds` and explicit capture omissions.
For JS requests, `acquisition.snapshot.kind` identifies `javascript_dom` or
`initial_html_timeout_fallback`; the latter is original HTML, not recovered JS DOM.
New JS receipts use `soft_window_seconds` equal to the configured timeout and
`soft_window_origin: request`; historical capture-relative receipts retain their
original meaning. Query values and nonempty URL fragments are redacted in
receipt display URLs.
Frozen snapshot names use `url-<first 16 URL SHA-256 hex>.html`; Markdown source
identity describes those snapshot bytes. Local receipts remain `brewdoc.receipt/1`.
An exit-0 partial capture means valid snapshot conversion, not complete JS or
primary-content recovery. Live acquisition is mutable; frozen conversion remains
deterministic. No 90% corpus acceptance or global JS compatibility is claimed.

`--url` with a positional document or `--self-check`, and `--render-js` without
`--url`, produce usage exit 2 before acquisition. Invalid timeout values and an
explicit `--timeout` without `--url` also produce exit 2. `--sheet` and `--artifact` with
a URL produce HTML refusal exit 1 before HTTP. Hard refusal preserves existing
destinations. The existing HTML structure and Markdown output limits still apply.

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

OCR and ML layout models. Local conversion never uses the network; explicit URL
acquisition is the exception. A page without a text layer is reported in the receipt, not guessed.
