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
| `.md` `.txt` | stdlib UTF-8 decoding | Exact source bytes; no generated envelope or units |
| `.odt` `.odp` | stdlib ZIP/XML | ODT heading chapters; declared ODP slides, text and tables |
| `.eml` | stdlib email; existing HTML renderer for HTML body | Selected headers, one MIME body and attachment names |

Parsed document routes emit brewdoc.markdown/2: quoted title, deterministic metadata, artifact inventory,
known omissions, linked contents and anchored units. Empty physical pages/sheets/chapters/slides
stay navigable. Local receipts use brewdoc.receipt/1 with route, unit kind and count:
routes are pdf, doc, presentation, sheet, epub, html, odt, odp and eml. Text uses route text, kind none,
zero units and brewdoc.markdown/passthrough/1.
PDF page/000001, DOCX/EPUB/HTML/ODT/EML chapter/000001, PPTX/ODP slide/000001,
workbook sheet/000001.
Unsupported suffixes use route/kind none; other refusals retain their route/kind and zero units.
Workbooks/HTML/EPUB/ODT/ODP/EML preserve Unicode as UTF-8 without BOM and LF newlines;
PDF/DOCX/PPTX emit ASCII. Text preserves its source UTF-8 bytes and newlines.
MiB means 1,048,576 bytes. Local conversion never acquires external resources.

## Markdown escaping

Since 0.6.0 the schema stays brewdoc.markdown/2 with minimal escaping. HTML, EPUB and EML
escape source text inline: `\`, backtick, `*`, `[` and `]` get a backslash; `<` becomes `&lt;`;
control characters become numeric entities; `_` is escaped unless a letter or digit sits on
both sides; `&` becomes `&amp;` only before an entity-shaped run (`#?\w+;`). Other punctuation,
including `>`, `#`, `-`, `.`, `!`, `~~` and `|` outside tables, stays literal, except that a `!`
directly before a generated link becomes `\!`, so the link cannot become an image. On these routes a
source line also loses leading blanks and escapes a block opener at its start: `#`, `>`, `+`,
`-`, `|`, a setext `=` run, `~~~`, an ordered marker such as `1.` and a table delimiter row.
A trailing `#` run in a heading is escaped. Table cells escape `|` on every route.
Link and image targets also write `&` as `&amp;` before an entity-shaped run.

HTML and EPUB emphasis uses `*` markers, or `_` when `*` cannot open or close under CommonMark
flanking rules; a run where neither can is written without markers. Adjacent runs of the same
kind merge. Edge spaces of emphasis and link labels move outside the markers and brackets.
A safe link whose label is empty or only whitespace is written as that plain text.

Quoted envelope fields (title, source name, unit labels) also write `"` as `&quot;` and turn
CR, LF and TAB into spaces. PDF, DOCX, PPTX, workbook, ODT and ODP bodies are raw: `<`
becomes `&lt;`, `&` becomes `&amp;` before an entity-shaped run, and a `` ``` `` or `~~~` run
after at most three leading spaces gets a backslash, so source text cannot open a code block
that swallows later units. They get no other line-start escaping, so source text such as `## X`
or `- item` can render as Markdown structure.
Generated unit anchors and keys still cannot be forged by source text.

## Body-only output

`--body-only` or `run(path, body_only=True)` keeps only unit content. It removes the quoted
title, Metadata, Artifacts, Known omissions, Contents and unit anchors. Unit headings such as
`## Page 1` stay only when the document has more than one unit; zero units give empty Markdown.
Lines inside fences are never removed. EPUB fragment anchors stay, so chapter links and the
Publication navigation link can dangle. Text pass-through is unchanged. Receipt, exit codes,
refusals and limits are those of the full rendering; `--out` writes the body in the route
encoding. It composes with `--article` and `--url`; with `--self-check` it is a usage error (exit 2).

## UTF-8 text

`.md` and `.txt` decode strict UTF-8 and return identical re-encoded bytes, including BOM,
CRLF/bare CR/LF, whitespace, syntax and final-newline presence. Empty input succeeds.
No normalization, parser, generated metadata, anchors, text/table counting or size cap is added.
Invalid UTF-8 and UTF-16/32 BOM input refuse before writes. Workbook/article options refuse.
The local receipt remains brewdoc.receipt/1 with empty unit keys, artifacts and omissions.
`--out` contains only source bytes; stdout places the JSON receipt before those bytes.

## PDF

Region order: fully ruled table; unfinished ruling recovered from baselines on its structural
grid; horizontal head/foot rules; unruled tabular band as layout-preserved text; captioned
Table N cropped to the next blank band; two-column prose split at the density gutter; plain lines.
Images/figures and their drawn text and page-spanning tables are not carried.
Prose, layout and caption text split words at glyph gaps above 0.15 of the font size
(pdfplumber `x_tolerance_ratio=0.15`), so tightly kerned text such as LaTeX output keeps its spaces.
Characters that are not upright (rotated stamps, margin labels such as arXiv identifiers) are
dropped from that text and from its tallies. Rotated column headers inside detected tables stay
in their cells and keep their reading order.
For tables crossing pages, columns recover but wrapped rows stay separate.
Sub/superscripts are folded into the line. Broken font ligatures are counted/folded, not guessed.

In a mixed document, textless pages retain their anchors. Markdown Known omissions names
each original page with no extractable text and whether image/vector drawing objects are
present. These facts do not identify scans, blank pages or lost text; no OCR is performed.
They do not change the receipt's static omissions. A nonempty wholly textless PDF still refuses.

Selected recovery keeps unique adjacent table captions beside independent prose, repeated
centered text fields beside numeric records, and one ruled group label in its first covered
record with empty continuations. Physically contiguous unchanged ASCII letters retain one
display token in math blocks. Ambiguous geometry retains the existing route; these fixes
do not establish general table, reading-order or mathematical fidelity.

## Static HTML

Requires CPython 3.12-3.14 and this exact conditional dependency:

```text
selectolax==0.4.13; python_version < '3.15' and platform_python_implementation == 'CPython'
```

Overall Python support stays >=3.12. Other runtimes retain the HTML API/route but refuse with
html/chapter, zero units, empty Markdown and no writes; self-check verifies that and other routes.
A missing/broken parser on supported CPython fails HTML and self-check. No alternate parser.

By default, HTML renders the recovered body/fragment without page chrome. `header` and `footer` elements
are dropped unless they sit inside `article`, `aside`, `main`, `nav`, `section` or role main.
`nav` and `aside` elements and roles navigation, banner, contentinfo and complementary on other
elements are dropped unless they sit inside `main`, `article` or role main. A landmark that
contains a main is kept. A `header` or role banner that contains an `h1` is kept, since it
carries the page title; footers have no such exception. Only the first `role` token that names
an ARIA 1.2 role counts: `role="note navigation"` is a note and stays. Any role other than the
four landmark roles keeps the element. First nonempty head title labels chapter/000001,
with entities decoded and Unicode kept, otherwise Untitled. Only .html is accepted. No HTML artifacts.

Charset precedence: a BOM (UTF-8, UTF-16BE, UTF-16LE), then the HTTP header label for `--url`,
then the first declaring meta (`charset`, or http-equiv content-type) in the first 1,024 bytes,
then UTF-8. Labels follow the WHATWG Encoding Standard, after trimming whitespace and quotes,
ASCII case-insensitive. A meta label for UTF-16 means UTF-8. GBK and gb18030 labels decode like
the WHATWG gb18030 decoder: byte 0x80 is U+20AC anywhere, including at the end of input, the 20
two-byte codes Python maps to Private Use characters get the WHATWG characters, and `A8 BC` and
`81 35 F4 37` give U+1E3F and U+E7C7. Other sequences use Python's gb18030 tables.
Deviations: unknown, empty or malformed labels and the replacement and x-user-defined encodings
refuse. The meta prescan also differs from WHATWG: a meta inside `title`, `script`, `style`,
`textarea` or another raw-text element is not seen; http-equiv content needs `;` before
`charset`; an unquoted content label followed by other text refuses; a meta whose `charset`
names an unknown label refuses instead of being skipped; x-user-defined refuses instead of
meaning windows-1252. In single-byte encodings a byte Python leaves undefined decodes to
U+0080+n for 0x80-0x9F (written as numeric entities) and to U+FFFD for 0xA0 and above.
That matches WHATWG except windows-1255 0xCA (U+FFFD, not U+05BA); KOI8-U 0xAE/0xBE and
x-mac-cyrillic follow Python's tables. Multi-byte decoding is strict: invalid bytes refuse.

Since 0.7.0, local and URL HTML fall back when the chosen charset does not fit the bytes.
A single-byte label from the header or a meta gives way to UTF-8 when the body is valid UTF-8
with non-ASCII bytes. When UTF-8 fails (a UTF-8 label or the default), UTF-8 stays, with U+FFFD
for each invalid sequence, when valid non-ASCII characters outnumber invalid sequences. Otherwise
the first meta within 16 KiB naming a charset other than UTF-8 or UTF-16 applies. Otherwise byte
statistics on the first 64 KiB choose windows-1252 when at most half of the high bytes sit next
to another high byte. Else, when at least 80% of the high bytes are Cyrillic letters 0xC0-0xFF,
windows-1251 applies if bytes 0xE0-0xFF are at least as frequent as 0xC0-0xDF, else KOI8-R.
Other bodies, such as CJK text, still refuse as in 0.6.0. A BOM, or a multi-byte label other
than UTF-8, with invalid bytes still refuses. When the U+FFFD, UTF-8 or statistics rule decides,
a local brewdoc.receipt/1 adds `charset_fallback` with the charset name, such as
`"windows-1251"`; a late meta is not a fallback. URL receipts report `charset` and
`charset_source` instead.

Prose/headings/lists/emphasis retain recovered DOM order. Pre and inline code outside tables
become literal fences longer than any source backtick run; angle brackets, ampersands, whitespace,
LF/tabs and repeated spaces are retained. Inline layout is omitted. Cell code uses
`Code: text="..."` with escaped literals; LF/tab/CR become &#10;/&#9;/&#13;.
Emphasis and safe links balance around each nonempty segment across fences;
code-only emphasis produces no delimiter paragraph.

Since 0.7.0, the `[Link target]` records of 0.6.0 are standard Markdown links. A safe anchor
whose content starts with an image wraps that image, `[![alt](src)](destination)`, inline in
prose; a second image in the same anchor, or an image after link text, stays a separate image
block. A safe anchor enclosing only code, or consumed inside code/pre, adds one
`[text](destination)` after the fence, in DOM order, in paragraphs or its original cell. The
label is the anchor's literal text with whitespace runs collapsed to one space; whitespace-only
text uses the destination as its label. Empty, hidden/excluded, unsafe or missing-target anchors
and anchors holding only images without alt text add no link. Separate anchors with identical
URLs stay distinct; nested code/pre does not duplicate them. Each link line after a fence counts
once in text_regions; cell links add no regions/tables. EPUB records are unchanged.

`--no-links`, `run(path, links=False)` or `render_html(path, links=False)` keeps anchor text and
drops every `href` after the limits are checked: linked images stay plain images and code
anchors add no link line. The
receipt `not_carried` and Markdown Known omissions add `link destinations: omitted by --no-links`
after any article entry, also on HTML refusals and article fallback. Other routes refuse it with
exit 1 and `--no-links is an HTML-only option`, including a `--url` that returns a document (stage
`options`). With `--self-check` it is a usage error (exit 2).

Lists are plain nested Markdown with `- ` or `N. ` markers; nested items indent by the parent
marker width and tight items stay tight. A list right after a sibling list of the same type
switches to `* ` or `N) ` markers, so CommonMark keeps the two lists apart. Ordered items count
from `start`, down under `reversed` (default: visible item count); an item `value` renumbers it
and later items. `start` and `value` read only optionally signed ASCII digits; other values keep
the default. Each rendered number is clamped to 0..999999999, the CommonMark marker range.
Later blocks of an item (fences, tables, quotes) indent by the marker width; a first block that
cannot share the marker line follows a bare marker line. A nested list that cannot interrupt its
parent paragraph gets one blank line before it. Blockquotes prefix each line with `> `; nesting stacks `> > `.
No list or quote records are emitted. Emitted paragraphs, literal blocks and link targets count
once in text_regions; bare marker lines and empty fragments do not.

Tables retain captions and recovered head/body/foot row/cell order. Spans expand to rectangular
grids with origin content once and empty continuations. Nested tables remain in their cell as
labelled row/column records with origin coordinates/spans. Cells apply the same page-chrome drop.
A table with no `th` cell and no caption, and with a cell holding a paragraph, `div`, heading,
list, table, blockquote or `pre`, is a layout table: its cells render as ordinary blocks in source
order, so tables nested in it render as tables. Its positions still count toward the table limit.
Form controls are dropped with their subtrees and values: button, input (every type), select,
option, optgroup, datalist, textarea, output, progress and meter. Form, fieldset, legend, label,
details and summary text stays as prose, including closed details; role=button content is ordinary prose.

Only normalized HTTP/HTTPS/mailto/relative/fragment targets become links. Other targets are inert.
An image with nonempty alt text becomes `![alt](target)` in source position, inline in table
cells; data, unsupported or missing targets give `![alt]()`. Images without alt text are dropped;
image title attributes are not carried. Brewdoc never fetches image or link targets, though a
Markdown viewer may load external images. Source IDs never become output anchors.

HTML receipts list these `not_carried` entries in order: dynamic browser content, scripts and
stylesheets; graphical SVG content, canvas pixels and MathML semantics; active media and embeds;
inline code layout outside tables: rendered as fenced blocks; inline code monospace inside table
cells; form controls and their values; images without alt text; navigation, header, footer and
aside landmarks outside main or article content.

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

### Article selection

Introduced in 0.4.0. Use a channel listed as available in
[that release](https://github.com/kochetkov-ma/brewdoc/releases/tag/v0.4.0).
Since 0.6.0, selection falls back to the whole page instead of refusing.
Use `run(path, article=True)`, `render_html(path, article=True)`, local `--article`, or combine
`--article` with `--url` and optionally `--render-js`. Other document formats refuse with
exit 1, including a `--url` that returns a document (stage `options`); combining it with
self-check is a usage error with exit 2. Local APIs stay offline; URL selection uses the frozen snapshot.

Selection walks the body in DOM order and skips hidden subtrees and the page-chrome landmark
subtrees defined above. Candidates are `main` elements or role main that are not inside an
article or another main, and outer `article` elements outside any main; `role=article` and class
names do not select. When mains exist, the mains are the roots and render whole, including their
articles; articles outside every main are not selected. Without a main, the outer articles are
the roots. Several roots, such as peer articles or multiple mains, render in DOM order; nested
articles stay inside their outer article. Forms, controls and role=button no longer exclude
candidates.

When no root exists or the roots add no text region or table (for example only an empty quote,
an empty list item or hidden content), the whole page renders with the default clean
profile and the success receipt adds `article_fallback: true`; that is never a refusal. A hidden
`html` or `body` element renders nothing. Selected content keeps the static HTML rendering rules
and limits above; complete-tree and complete-body table budgets are checked first and still
refuse. The head-title/Untitled rule remains; headings do not become a replacement title and no
author, date or other metadata is inferred. There is no score or generic readability extraction.

When roots are selected, the receipt `not_carried` and the Markdown Known omissions add this exact
entry; a fallback omits it. Article-mode refusal receipts on the HTML route also carry it; a refusal
under another route, such as a local PDF or a `--url` PDF response, does not:

```text
body content outside the selected article or main subtree; complete page coverage is not claimed
```

Single-article pages and semantically marked documentation suit selection best. The corpus
includes MDN document structure for article/navigation separation, MDN flexbox with three peer
articles rendered in order, and an MDN form page and unmarked Apache HTTPD documentation that
fall back. These fixtures establish bounded behavior, not general website coverage.

## Python package compatibility

The following compatibility evidence predates the 0.5.0 changes.
CPython 3.12.14 retains the local macOS full-suite baseline and existing CI smoke checks.
Declared Python >=3.12 support and the CPython 3.12-3.14 HTML parser constraint are not proof
of every OS/minor combination. The following source-package cells passed in
[CI run 38431645006](https://github.com/kochetkov-ma/brewdoc/actions/runs/38431645006) for
[PR #57](https://github.com/kochetkov-ma/brewdoc/pull/57):

| Observed target | CPython | Passed package cases | Optional JS result |
|---|---|---|---|
| Ubuntu 22.04, x64, glibc 2.35 | 3.13.15 | Wheel and sdist, base and render-js extra | Module/DOM executed |
| macOS 15.7.9, ARM64 | 3.14.7 | Wheel and sdist, base and render-js extra | Module/DOM executed |
| Windows Server 2025 Datacenter, x64 | 3.14.7 | Wheel and sdist, base and render-js extra | Explicit refusal before requests |

All three cells used the same verified wheel/sdist pair in four independent installations each.
Each installation converted 11 original documents and passed self-check 38 and article API/CLI
checks. These are selected tested patches, not a claim about newer Python versions. This matrix
concerns source-built Python packages; standalone binaries have separate bundled-runtime
targets. CI evidence alone does not establish channel publication.
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
`start`, `value` and spans read optionally signed ASCII digits. Item numbers clamp to
0..999999999 and a negative span counts as 1, so a huge number does not refuse by itself; spans
still count toward the table budgets.
Lists and quotes use the plain nested Markdown and `> ` prefixes described for static HTML.
Header, footer and aside content stays: EPUB has no landmark drop. Body/heading code uses safe
fences retaining XML-normalized literals and balanced emphasis/links. Cell code keeps escaped
Code records and numeric LF/tab/CR entities without monospace layout. Tables keep group order,
captions, spans and empty continuations; nested tables remain coordinate records in their origin cell.

An image with nonempty alt text becomes `![alt](src)` in source position; images without alt text
are dropped. SVG preserves title/description/image-reference metadata. Form controls are dropped;
label and other form text stays as prose. Pixels, graphical SVG/unsupported MathML/SVG semantics,
CSS/layout/pagination/fonts, scripts, active media and navigation beyond TOC are omitted.
hidden/aria-hidden=true omit subtrees; CSS is not evaluated. No archive extraction, network, JS,
OCR or DRM decryption occurs.

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

--url explicitly acquires a static HTTP(S) page or supported document, then converts a frozen
snapshot: UTF-8 HTML, UTF-8 text or the unchanged document bytes. Local HTML and APIs remain
offline. Relative links/images resolve against final URL/valid base without fetching
images/linked pages. A base that does not parse is ignored; a base with another scheme,
credentials or a non-default port refuses. The default clean body retains source order.
`--article` selects content under the [article contract](#article-selection).
Local files and static capture do not execute scripts.

Only public destinations/default ports 80/443 pass. All DNS answers, peers and redirects are
validated. TLS verifies certificates/hostnames against the Python runtime's default trust store,
with no bundled CA list: a store that lacks the issuing root refuses at `tls`. HTTPS offers ALPN
`http/1.1`, the protocol actually spoken; since 0.7.0 this lets WordPress.com edges that answered
403 to a ClientHello without ALPN serve the page. GET excludes ambient credentials/cookies/proxy
environment. At most five redirects; downgrades, unsupported schemes/private/special-use addresses
refuse. Location values have tabs and line breaks removed and spaces, controls and other
whitespace percent-encoded as UTF-8; raw non-UTF-8 bytes become `%XX`; a lone `%` or `\` refuses.
The main request sends `Accept: text/html,application/xhtml+xml,*/*;q=0.8` and every request
sends `Accept-Encoding: gzip`; identity and bounded gzip responses are accepted.

No request is re-sent. A host's validated addresses are tried in alternating families, starting
with the first answer's, at most three attempts. Only a TCP connect failure moves to the next
address: earlier attempts get at most 3 seconds each, the last one the rest of the budget.
Nothing moves on after TLS starts, so request bytes, including the search POST, go out once.

The response Content-Type picks the route; magic bytes must match:

| Media type | Route suffix | Magic |
|---|---|---|
| text/html | .html | none; HTML charset rules |
| application/pdf | .pdf | starts `%PDF-` |
| OOXML word, presentation and spreadsheet types; ms-excel macro-enabled and binary sheets | .docx .pptx .xlsx .xlsm .xlsb | ZIP with `[Content_Types].xml` |
| application/vnd.ms-excel | .xls | OLE header |
| OpenDocument spreadsheet, text, presentation; application/epub+zip | .ods .odt .odp .epub | ZIP with first member `mimetype` |
| message/rfc822; text/plain; text/markdown | .eml .txt .md | none |
| application/octet-stream | Content-Disposition filename suffix, else the final URL path suffix | that suffix's magic |

octet-stream admits only .pdf, .docx, .pptx, .xlsx, .xlsm, .xlsb, .xls, .ods, .odt, .odp and
.epub. A missing or duplicate Content-Type, a duplicate Content-Disposition, any other type
(including application/xhtml+xml, application/x-pdf and application/zip), an unknown suffix or a
magic mismatch refuses at `media`. So does a malformed octet-stream Content-Disposition,
including one with a NUL character. text/plain and text/markdown bodies are never sniffed: HTML
served as text/plain converts as text, so its markup appears verbatim.
Text bodies decode with the header charset (BOM first), without the HTML fallback, and freeze as
UTF-8. HTML is limited to
8 MiB transferred/decompressed; other main responses to 32 MiB. Adapters validate the snapshot
fully. Runtimes without the HTML parser still acquire documents; an HTML response refuses at
`prerequisites` after its request.

Since 0.7.0, decoded URL HTML is checked before conversion. Captcha, bot challenge and login
pages refuse at stage `challenge` with
`URL captcha or bot challenge refused: <name> (HTTP <status>)` or `URL login page refused`;
receipt `challenge` names cloudflare, ddos-guard, yandex-smartcaptcha, hcaptcha, recaptcha,
datadome, qrator, generic or login. Evidence that decides alone: a `cf-mitigated: challenge`
header, or a Cloudflare challenge script with a title holding "Just a moment" or "Attention
required"; a final path `/showcaptcha` or a form posting to `/checkcaptcha`
(yandex-smartcaptcha); a password field in a form with the exact title Login, Log in or Sign in,
in any case. DataDome and QRATOR count only on a non-2xx answer: an `x-datadome` header or a
captcha-delivery.com reference (datadome); HTTP 439 or a `Server: QRATOR` header (qrator). Weak
evidence counts only when the visible body text, without script, style, noscript and template,
is under 1,000 characters: `Server: ddos-guard`, `/.well-known/ddos-guard/`, hCaptcha, reCAPTCHA
or Turnstile hosts, and phrases such as "checking your browser", "please enable cookies" or
"Проверяем браузер" (generic).

A page with that little text and at least one script is a JavaScript shell when it holds an
enable-JavaScript message, or an empty `#root`, `#app`, `#__next` or `#__nuxt` element and under
200 visible characters. Shell evidence beats the hCaptcha, reCAPTCHA and Turnstile hosts: such a
page is a shell, not a challenge; other weak evidence still names a challenge. Static acquisition
refuses it at `javascript_required` with `URL page needs JavaScript; retry with --render-js auto`.
After JS capture the same check runs on the captured DOM, and a remaining shell refuses with
`URL page content was not produced by the bounded JavaScript engine`. A non-2xx HTML answer with
recognized challenge evidence refuses at `challenge`; otherwise, including a shell or an
undecodable error page, it keeps `URL HTTP status refused: <status>` at `status`. The non-2xx
check needs the HTML parser; outside CPython 3.12-3.14 every such answer refuses at `status`.
0.6.0 recognized only titled Cloudflare and login pages; other captcha and shell pages answered
with 200 converted as near-empty successes. These are fixed markers, not a general bot detector;
brewdoc never solves a challenge.

--timeout is a positive finite waiting budget within clock range, default 30 seconds. One budget
covers prerequisites/DNS/connections/TLS/redirects/body/scripts/resources/JS waiting. Ready content
returns immediately; there is no separate five-second network ceiling. JS capture also stops at a
soft window of min(12, timeout/2) seconds from request start. Recovery is bounded to one second,
cleanup to another 1.1 seconds. Local conversion/output follow; total CLI duration need not equal
the wait budget.

--render-js requires installed brewdoc[render-js]: QuickJS-ng 0.17.0.1 with bundled LinkeDOM 0.18.13.
No Node/browser/runtime downloads. Native ABI/resource prerequisites are checked before HTTP.
JS requires macOS ARM64 or 64-bit glibc Linux x86_64/aarch64; Windows, macOS Intel, musl, 32-bit and
unsupported ABIs refuse. Execution is a separate process without filesystem/shell APIs or reused
browser state; native limits are not an OS sandbox against engine vulnerabilities. Documents are
never passed to JS: their capture status is `not_applied` with `always` and `static` with `auto`.

Bare `--render-js` means `--render-js always`: JS runs on every HTML page. Since 0.7.0,
`--render-js auto` acquires statically and runs JS on the already decoded page, with no second
request, only when the page check finds a JavaScript shell; otherwise the static snapshot
converts. Both modes check JS prerequisites before HTTP, and challenge or login pages refuse
before any script runs. Receipt `render_js` is `auto`, `always` or null; `mode` names what ran.
Prefer `auto` for unknown pages: `always` runs scripts even on pages that convert statically,
and such a page can hit an engine quota and refuse.

The finite subset supports DOM mutations, classic scripts, relative/dynamic module imports,
Promise jobs, function timers, animation frames, GET fetch and async GET XHR. Only async XHR
admits the HN Algolia search POST: https://hn.algolia.com documents may query
https://uj5wyc0l7x-dsn.algolia.net/1/indexes/Item_dev/query with original public application/key
query, string JSON body and application/x-www-form-urlencoded header. Other POST/fetch POST,
cookies and user credentials are unsupported. Search POSTs are never redirected, re-sent or
rewritten. Search UTF-8 body is limited to 16,384 bytes, JSON depth four and 256 total array
entries. Unsupported options keep partial content without sending; security/resource overflow is
a hard refusal.

Capture finishes when the page is quiet: no host request in flight or queued, no newly added
script, no queued Promise job and no one-shot timer due within 500 ms. Repeating timers, animation
frames, later timers, unresolved promises and pending modules do not hold capture open; such a
capture ends partial. At the soft window, requests not yet served stay pending. Since 0.7.0 no
initial or page-inserted script source fetch and no module import starts after the soft window:
the unfetched sources and imports count as pending requests, later scripts of that batch do not
run, and an import failed this way adds no `script_execution_failed`. Animation frames
run on each pass and do not count toward the 200 fired-timer budget. At most 200 timers may be
pending at once; creating a 201st is a hard quota refusal. A sub-request refused by
destination policy or a resource quota refuses the capture, and one that hits the deadline stops
it. Any other sub-request failure, such as DNS, connect, TLS, transport, a refused media type (a
script served as text/plain) or encoding, adds `ancillary_transport_failed` with its redacted
resource_url and keeps the capture partial; a main-document TLS failure still refuses at `tls`.
A sub-request with an invalid URL, such as `fetch('http://[')` or one over 8,192 UTF-8 bytes,
rejects inside the page like a browser TypeError, adds `ancillary_transport_failed` and capture
continues. Since 0.7.0 so does a fetch, XHR, script or import whose URL is not HTTP(S), such as
`data:`, `mailto:` or `ftp:`, without sending a request; a script with such a source fails alone.
Inside the engine an unparseable or non-HTTP(S) `<base href>` is ignored like a browser; the
snapshot base rule above still applies.

Script and module responses and `<script type>` values accept every WHATWG JavaScript MIME type
essence, such as `text/javascript`, `application/x-javascript` or `text/jscript`, in any ASCII
case. Other `type` values stay inert; other response types are a refused media type as above.

Read-only history and HTTP(S) anchor components provide limited compatibility. innerWidth starts
at 1024 CSS pixels, is writable through window/global and resets next capture. Inline style reads
mark partial without CSS/layout computation. Temporary localStorage starts empty, is discarded
without storage events and marks partial on any access. Its 200-key/65,536 UTF-8-byte key/value
quota is hard. Host requests validate destinations; cross-origin API bodies require accepted CORS.
No authenticated fetch, arbitrary request headers or cookie model. Scripts follow parsing;
async/defer/event order is approximate. Layout/iframes/workers/WebSockets/media/browser CSP
enforcement are outside the subset.

Shims: `self` is window; `document.currentScript` is the running classic script or null;
`document.cookie` reads "" and ignores writes; `console` log/info/warn/error/debug do nothing.
`URL` and `URLSearchParams` accept absolute http(s) URLs or relative ones against an http(s)
base; other schemes throw TypeError, and only href, pathname, search and hash are writable.
There is no crypto, MessageChannel, IntersectionObserver or matchMedia. 0.7.0 adds no browser
API shims: on the 2026-10 bench they recovered no page. Known limits: crates.io calls
`new URL('a://')`, so with `auto` or `always` it refuses at `javascript_required` with
`URL page content was not produced by the bounded JavaScript engine`; bundlephobia needs crypto
and keeps its server-rendered content. A shell page whose content needs more than the bounded
engine provides refuses instead of converting. A fallback below still converts a page whose
static HTML holds the content: in one run, Wikipedia's World War II page exhausted the native
heap with `always` and converted from its original HTML as initial_html_heap_fallback.
Since 0.7.0 a script, style, title or textarea element created with an upper-case name, such as
`createElement('STYLE')`, converts like its lower-case form.

| JS budget | Maximum |
|---|---:|
| Host requests | 100 |
| Per response/snapshot | 8 MiB |
| Main document (non-HTML) response | 32 MiB |
| Aggregate decoded responses/outgoing search bodies | 32 MiB |
| Native heap; child process memory; OS-thread stack | 64 MiB; 256 MiB; 1 MiB |
| Native invocation; Promise jobs; pending timers | 250 ms thread CPU per call; 2,000; 200 |
| Fired timer callbacks, then original HTML fallback | 200 |
| Child CPU | 45 seconds |

Charge search bodies once before sending. Increased timeout does not enlarge these budgets.
Timing-only expiry stops scripts/network after complete validated HTML exists. Cooperative recovery
returns current DOM partial with capture_timeout/pending work; a soft-window finish adds no
capture_timeout. Parent-cancelled unresponsive workers without hard failures use acquired
original HTML as initial_html_timeout_fallback, without refetch; JS completion/unobserved work is
unknown. Since 0.7.0 a worker that reports native heap exhaustion, a refused DOM snapshot
inspection (for example, page code replacing serializer methods) or more than 200 fired timer
callbacks (animation frames not counted) and then exits cleanly also uses the original HTML, as
initial_html_heap_fallback, initial_html_dom_fallback or initial_html_timer_fallback, instead of
refusing. Incomplete main responses, security/native faults, child crashes (including a Linux
address-space limit hit before the heap signal), a worker whose stop cannot be proved
(`JS child cancellation unproved`), the child memory cap and other quotas, including the pending
timer count, refuse without output and take precedence. Ordinary script/API errors can produce
useful partial DOM.

URL receipts use brewdoc.receipt/2 with acquisition: redacted requested/final/base URLs, response/
snapshot identities, mode, capture status, effective timeout_seconds and explicit omissions.
JS snapshot kind is javascript_dom, initial_html_timeout_fallback, initial_html_heap_fallback,
initial_html_dom_fallback or initial_html_timer_fallback; the fallbacks are original HTML, capture
status partial, and add their own name as an error category.

Since 0.7.0, acquisition also always has these keys:

| Key | Value |
|---|---|
| `http_status` | Final main-document HTTP status, including 200; on a refusal the last received status; null without a response |
| `redirect_count` | Main-document redirects followed, 0-5 |
| `challenge` | cloudflare, ddos-guard, yandex-smartcaptcha, hcaptcha, recaptcha, datadome, qrator, generic or login on a `challenge` refusal; else null |
| `charset`, `charset_source` | HTML decoding charset (WHATWG name) and its source: bom, header, meta, default or fallback; null for documents and before decoding |
| `render_js` | `auto` or `always` as requested; null for static |

Sub-request failures never change `http_status` or `redirect_count`. refusal_stage adds
`challenge` and `javascript_required`. Refusals at `status`, `redirect`, `challenge` and
`javascript_required` keep their fixed reason, such as `URL HTTP status refused: 403` or
`URL redirect limit exceeded after 5 hops (last status 302)`; other network stages report
`URL acquisition refused during <stage>`.
Document snapshots have kind `document`, capture status `static` or `not_applied`, no base URL and
the detected route. JS soft_window_seconds is min(12, timeout_seconds/2) with soft_window_origin
request; before 0.6.0 it equalled timeout_seconds. Query values/nonempty fragments are
redacted. Snapshot names are url-<first 16 URL SHA-256 hex><suffix>; Markdown identity names
those snapshot bytes. Every refusal names its real stage; its error record carries the redacted
URL that failed, or the final URL once fetched, and null before network stages and for every
`options` refusal, including `--article` after a document fetch. Local receipts
remain brewdoc.receipt/1. Exit 0 partial means valid conversion, not complete JS or
primary-content recovery. Frozen conversion is deterministic; live acquisition is mutable. No 90%
coverage or general browser compatibility is claimed.

--url with a document/self-check, --render-js without --url or with a value other than `auto` or
`always`, invalid timeout or explicit timeout without --url produce exit 2 before acquisition.
--url with --sheet/--artifact refuses at `options` with exit 1 before HTTP. --article with a
document response refuses at `options` with the document route. The internal URL entry refuses
a `render_js` value other than None, False, True, `"auto"` or `"always"` at `options` with exit 1
and `render_js must be None, False, True, "auto" or "always"`. Hard refusal preserves existing
destinations. Route structure/Markdown limits apply.

## Container documents

DOCX accepts Transitional/Strict WordprocessingML. Main-story paragraphs/tables retain order;
Title/Heading1-Heading9 start chapters. Tabs become spaces; line breaks remain. Nested tables flatten
into parent cells; invalid/nonpositive spans use one column. Images/headers/footers/footnotes/comments,
tracked-change reconstruction, list layout, style inheritance and vertical merge reconstruction are omitted.

For namespace-qualified `mc:AlternateContent`, only the first direct qualified `mc:Fallback`
is read. Choices are skipped; an alternative without a fallback contributes no text.
Nested fallbacks retain source order. This is not a `Requires` resolver or full MCE support.

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
EPUB images with alt text become Markdown image links; DOCX images remain omitted.

### ZIP/XML reader limits

Since 0.4.1, these inclusive limits apply to Brewdoc-owned DOCX/PPTX reads and
workbook relationship, formula and VBA reads. PPTX embedded picture reads are
included. Native python-calamine value parsing is outside this envelope.

| Budget | Maximum |
|---|---:|
| ZIP members per archive, including directories, duplicates and ignored members | 4,096 |
| Declared/observed bytes per consumed member | 32 MiB |
| Actual decompressed bytes returned across one public operation | 256 MiB |
| XML elements per member parse; across the operation | 2,097,152; 4,194,304 |
| XML depth, root at 1 | 16,384 |
| Expanded text and attribute-value characters per member parse; across the operation | 33,554,432; 67,108,864 |

Characters are Unicode code points in expanded character data, including tails,
and parser-reported attribute values. Attribute names, namespace declarations and
empty-valued attribute counts have no separate character budget.
Every physical reread and logical XML parse counts
again, including archive reopenings and workbook inventory/artifact retrieval
within one `run` or artifact API call. Preflight and tree construction count as
one logical parse. Separate public calls start fresh budgets.

Consumed members must use ZIP_STORED or ZIP_DEFLATED. Previously readable selected
BZIP2/LZMA members and documents exceeding any budget now refuse; ignored members
do not receive the compression or member-byte check. The existing 16 MiB VBA
project limit remains stricter. No source-file, Markdown or formula-JSON size cap
is added by this envelope. EPUB retains its separate limits above.

Returned bytes are checked before Brewdoc retains an oversized chunk. XML depth,
elements and expanded characters are checked before ElementTree construction.
Bounded inert doctypes/internal entities retain OOXML behavior; EPUB's stricter
entity and encoding rules do not apply here. No external entities are fetched.
Limit/compression failures name the member and budget where applicable. `run`
returns exit 1, zero units, empty Markdown and no writes; raising reader APIs
raise `BrewdocError`. Complete successful output is preserved, with no truncation.

These are selected-reader policy limits, not a process memory cap or universal
security guarantee. ZipFile allocates its central directory before member
admission. Codec buffers, Expat token/entity expansion and callback dictionaries
or names allocated before counters, native calamine buffers/cell grids, raw source
hashing, formula serialization, Markdown assembly and output staging/rollback
are outside the guarantee. Source identity and concurrent file mutation handling
are unchanged. A file below these limits can still require substantial memory/time.

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
dates use ISO days and exposed datetime values retain their full `isoformat()` precision.
No timezone or unexposed time is inferred from source formatting. Workbook cells and labels
preserve ordinary Unicode, with the existing named control/ligature cleanup and whitespace handling.
Formula Markdown contains cached values, not recalculated results.

## OpenDocument text and presentations

ODT/ODP require unique `mimetype`, `META-INF/manifest.xml` and `content.xml` members,
matching qualified media types and the expected office body. Missing, duplicate, malformed
or consumed encrypted content refuses. Optional styles/settings/metadata are ignored.
No extraction, external resource fetching, decryption or macro execution occurs.

ODT keeps ordered paragraphs, heading chapters, section/list descendants and tables.
Inline spans, hyperlink labels, tails, `text:s` space counts, tabs and line breaks retain
text; list numbering, notes, comments and tracked-change history are omitted. ODT drawing text,
bibliography containers and fields outside that inline vocabulary are not read.
ODP keeps direct page order, including hidden/empty slides, grouped text and frame tables.
The first nonempty title-class frame labels a slide, otherwise its declared name or Untitled.
Masters and automatic headers/footers are omitted. Images, charts, equations, embedded objects,
styles, positioning and layout are not carried. This is qualified text extraction, not full
ODF conformance validation or fidelity.

Tables expand explicit row/cell repeats and covered cells, retaining merged owner text once.
Malformed/nonpositive counts and contradictory merged grids refuse. The shared
[ZIP/XML reader limits](#zipxml-reader-limits) apply to all selected ODF reads and parses.
Additional inclusive expansion limits are 256 columns, 65,536 slots per table, 262,144 slots
per operation and 33,554,432 expanded characters per operation. Empty slots and
every repeated text copy count. Space counts and repeats are checked before materialization.
No source-size or final-Markdown cap is added; these limits do not bound process memory.

## MIME email

EML emits one chapter. Its first decoded Subject labels it, otherwise Untitled.
From, To, Cc, Date, Subject and Message-ID appear in that fixed order; repeated values retain
source order within each name. Encoded words decode strictly and headers unfold without
address/date normalization. Names and headers are literal data, never active Markdown or paths.

Stdlib body selection prefers plain over HTML, even when HTML occurs first. An empty plain
body stays empty. Related-root selection follows stdlib; attachment dispositions are excluded,
while a filename alone does not exclude an inline text body. Only the selected body decodes,
with its declared charset or ASCII when absent. Invalid selected text, unknown charset/transfer
encoding, consumed structure defects and base64 defects refuse. Literal quoted-printable
recovery follows stdlib. Plain bodies retain literal text with CRLF/bare CR normalized to LF.
Missing supported body is stated explicitly.

Selected HTML uses the existing offline HTML renderer, its clean profile including the landmark
drop, and its 8 MiB UTF-8 input and structural limits; it needs the same supported parser
runtime. No article selection is applied.
Named inline parts and attachment names appear in MIME order, with unnamed attachment markers.
Attached-message inventory stops at `message/rfc822`. Attachment payloads, nested-message bodies,
unselected alternatives and non-body inline resources are omitted, with no retrievable artifacts.
Other headers, including Bcc, routing and signature fields, are omitted. No decryption or
signature verification occurs.

The parser admits at most 1,024 retained MIME nodes before constructing the next node.
Root depth one is checked against a maximum of 64 before body selection. Parser recursion
failure is a named refusal. The stdlib parser can recurse and retain encoded payloads before
that depth check. There is no EML raw-size, attachment-size, decoded-plain-body or final-output
cap; node/depth checks do not guarantee streaming, constant memory or process memory safety.

## Excluded

CSV/TSV are intentionally unsupported/unplanned; agent systems can read their text tables directly.

## Deferred

Not currently planned: .doc/.ppt binary OLE2 (including Word 97 piece tables) needs olefile or a CFB reader (text only, lossy tables);
.rtf needs a parser.

## Never

OCR or ML layout models. Local conversion never uses networking; deliberate --url acquisition
is the exception. Missing PDF text layers are named refusals, not guessed content.
