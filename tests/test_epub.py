"""Synthetic EPUB contracts stay separate from licensed publication acceptance."""

import codecs
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import zipfile

import pytest

from inspection_contract import (rendering_bytes, rendering_result, rendering_stdout)

import brewdoc
from test_contract import NOT_APPLICABLE, SCHEMA_TWO, zero_tally


CONTAINER = ('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
             '<rootfiles><rootfile full-path="OPS/book.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles></container>')
METADATA = ('<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>café 東京</dc:title><dc:creator>First</dc:creator>'
            '<dc:creator>First</dc:creator><dc:language>fr</dc:language>'
            '<dc:identifier id="book-id">urn:synthetic</dc:identifier></metadata>')
ITEMS = ('<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
         '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>')
SPINE = '<itemref idref="b"/><itemref idref="a" linear="no"/>'
NAV = ('<html xmlns="http://www.w3.org/1999/xhtml" '
       'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>TOC</title></head><body>'
       '<nav epub:type="toc"><ol><li><a href="a.xhtml">Alpha</a></li>'
       '<li><a href="b.xhtml">βeta</a></li></ol></nav></body></html>')
NCX = ('<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
       '<head/><docTitle><text>Publication</text></docTitle><navMap>'
       '<navPoint id="nav-a" playOrder="9"><navLabel><text>Alpha</text></navLabel>'
       '<content src="a.xhtml"/></navPoint>'
       '<navPoint id="nav-b" playOrder="1"><navLabel><text>βeta</text></navLabel>'
       '<content src="b.xhtml"/></navPoint></navMap></ncx>')
EPUB_OMISSIONS = [
    "CSS, layout, pagination and font rendering", "scripts, forms and active media",
    "image pixels, images without alt text and graphical SVG content",
    "unsupported SVG and MathML semantics",
    "navigation semantics beyond declared TOC hierarchy",
]
PUBLICATION_ROWS = ('| Publication title 1 | "café 東京" |\n'
                    '| Creator 1 | "First" |\n| Creator 2 | "First" |\n'
                    '| Language 1 | "fr" |\n| Identifier 1 | "urn:synthetic" |\n'
                    '| Unique identifier | "urn:synthetic" |\n')
PUBLICATION_NAV = ('<a id="brewdoc-publication-navigation"></a>\n'
                   '### Publication navigation\n\n'
                   '- [Alpha](#brewdoc-chapter-000002)\n'
                   '- [βeta](#brewdoc-chapter-000001)\n')
TWO_CHAPTER_BODY = ('<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "βeta"\n\n'
                    'café 東京\n\n<a id="brewdoc-chapter-000002"></a>\n'
                    '## Chapter 2: "Alpha"\n\nAuxiliary content.\n\n'
                    'First e\u0301\u00a0word\u00ad.\n')
# Rows shared with test_html.py: identical source and Markdown for both adapters.
INLINE_BOUNDARY_WHITESPACE = [
    pytest.param('<p>see <span> label</span></p>', 'see label\n', 1, 0, id="span-leading-space-after-space"),
    pytest.param('<p>see <span>label </span> next</p>', 'see label next\n', 1, 0,
                 id="span-trailing-space-before-space"),
    pytest.param('<p>a <em> </em> b</p>', 'a b\n', 1, 0, id="whitespace-only-emphasis-between-spaces"),
    pytest.param('<p>see <span> <span> label</span></span> end</p>', 'see label end\n', 1, 0,
                 id="nested-span-spaces"),
    pytest.param('<p>see <a href="https://x.test/"> <em> x</em></a></p>', 'see [*x*](https://x.test/)\n', 1, 0,
                 id="link-edge-space-before-emphasis"),
    pytest.param('<table><tr><td>see <span> label</span></td></tr></table>', '| see label |\n| --- |\n', 0, 1,
                 id="cell-span-leading-space-after-space"),
    pytest.param('<table><tr><td>a <em> </em> b</td></tr></table>', '| a b |\n| --- |\n', 0, 1,
                 id="cell-whitespace-only-emphasis-between-spaces"),
    pytest.param('<p><b>a </b> b</p>', '**a** b\n', 1, 0, id="strong-trailing-space-before-space"),
    pytest.param('<p>see <a href="https://x.test/"> x</a></p>', 'see [x](https://x.test/)\n', 1, 0,
                 id="link-label-leading-space-after-space"),
    pytest.param('<pre>a  <span> b</span></pre>', '```\na   b\n```\n', 1, 0, id="pre-keeps-boundary-spaces"),
    pytest.param('<table><tr><td>x <code>a  <span> b</span></code></td></tr></table>',
                 '| x Code: text="a   b" |\n| --- |\n', 0, 1, id="cell-code-keeps-boundary-spaces"),
    pytest.param('<blockquote><p>Spring, <span> </span><br/> <span> Or</span> the bees</p></blockquote>',
                 '> Spring,\n> Or the bees\n', 1, 0, id="verse-lines-drop-boundary-spaces"),
]
# Rows shared with test_html.py: text `!` before a generated link opener is escaped.
BANG_BEFORE_LINK = [
    pytest.param('<p>Hi!<a href="https://x.test/t">x</a></p>', 'Hi\\![x](https://x.test/t)\n', 1, 0,
                 id="bang-before-link-escaped"),
    pytest.param('<table><tr><td>Hi!<a href="https://x.test/t">x</a></td></tr></table>',
                 '| Hi\\![x](https://x.test/t) |\n| --- |\n', 0, 1, id="cell-bang-before-link-escaped"),
    pytest.param('<p>Wow!<b><a href="https://x.test/t">pic</a></b>z</p>', 'Wow\\![pic](https://x.test/t)z\n',
                 1, 0, id="bang-before-link-after-dropped-strong-escaped"),
    pytest.param('<p>a\\!<a href="https://x.test/t">x</a></p>', 'a\\\\\\![x](https://x.test/t)\n', 1, 0,
                 id="bang-after-literal-backslash-before-link-escaped"),
]
# Rows shared with test_html.py: EPUB matches the HTML code-split image and blank link.
CODE_SPLIT_IMAGE_AND_BLANK_LINK = [
    pytest.param('<p><span>a<img alt="I" src="p.png"/><code>c</code></span></p>',
                 'a\n\n![I](p.png)\n\n```\nc\n```\n', 3, 0, id="code-split-span-keeps-image"),
    pytest.param('<p><a href="https://x.test/">a<img alt="I" src="p.png"/><code>c</code></a></p>',
                 '[a](https://x.test/)\n\n![I](p.png)\n\n```\nc\n```\n', 3, 0, id="code-split-link-keeps-image"),
    pytest.param('<p>a<a href="https://x.test/"> </a>b</p>', 'a b\n', 1, 0, id="blank-link-label-plain-text"),
    pytest.param('<p>a<a href="https://x.test/"></a>b</p>', 'ab\n', 1, 0, id="empty-link-label-plain-text"),
    pytest.param('<table><tr><td>a<a href="https://x.test/"> </a>b</td></tr></table>',
                 '| a b |\n| --- |\n', 0, 1, id="cell-blank-link-label-plain-text"),
    pytest.param('<table><tr><td>a<a href="https://x.test/"></a>b</td></tr></table>',
                 '| ab |\n| --- |\n', 0, 1, id="cell-empty-link-label-plain-text"),
]


def xhtml(body, title="Fallback"):
    """Declare a qualified static XHTML body with literal synthetic content."""
    return ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title></head>'
            '<body>%s</body></html>' % (title, body))


def epub3_members():
    """Declare EPUB 3 with spine order distinct from ZIP and navigation order."""
    return {
        "mimetype": "application/epub+zip", "META-INF/container.xml": CONTAINER,
        "OPS/book.opf": ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                         'unique-identifier="book-id">%s<manifest>%s'
                         '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                         'properties="nav"/></manifest><spine>%s</spine></package>'
                         % (METADATA, ITEMS, SPINE)),
        "OPS/a.xhtml": xhtml("<p>First e&#x301;\u00a0word\u00ad.</p>"),
        "OPS/b.xhtml": xhtml("<p>café 東京</p>"), "OPS/nav.xhtml": NAV,
    }


def epub2_members():
    """Declare real EPUB 2 OPF/NCX instead of relabelling an EPUB 3 package."""
    return {
        "mimetype": "application/epub+zip", "META-INF/container.xml": CONTAINER,
        "OPS/book.opf": ('<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
                         'unique-identifier="book-id">%s<manifest>%s'
                         '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                         '</manifest><spine toc="toc">%s</spine></package>'
                         % (METADATA, ITEMS, SPINE)),
        "OPS/a.xhtml": xhtml("<p>First e&#x301;\u00a0word\u00ad.</p>"),
        "OPS/b.xhtml": xhtml("<p>café 東京</p>"), "OPS/toc.ncx": NCX,
    }


def write_epub(path, members):
    """Write deterministic synthetic members without extracting an archive."""
    return write_epub_bytes(path, {name: text.encode("utf-8") for name, text in members.items()})


def write_epub_bytes(path, members):
    """Retain source byte encodings in deterministic regular ZIP members."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            archive.writestr(info, payload)
    return path


def expected_receipt(path, *, units=2, regions=2, tables=0):
    """Pin every public receipt field, including zero loss counters and empty artifacts."""
    return {
        "file_ok": True, "route": "epub", "reason":
        "epub rendered: %d chapters, %d tables, %d text regions, 0 column splits"
        % (units, tables, regions), "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1",
        "unit_kind": "chapter", "units": units, "tables": tables, "text_regions": regions,
        "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": EPUB_OMISSIONS, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": ["chapter/%06d" % number for number in range(1, units + 1)],
    }


def expected_refusal(path, reason):
    """Pin the complete zero-unit refusal independently of successful-output fields."""
    return {
        "file_ok": False, "route": "epub", "reason": reason, "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter",
        "units": 0, "tables": 0, "text_regions": 0, "columns_split": 0,
        "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": EPUB_OMISSIONS,
    }


def expected_document(path, body=TWO_CHAPTER_BODY, *, regions=2, tables=0,
                      labels=("βeta", "Alpha"), navigation=PUBLICATION_NAV):
    """Construct the public envelope from literal expected content and independent hashes."""
    source, encoded = path.read_bytes(), body.encode("utf-8")
    document = SCHEMA_TWO.format(
        **NOT_APPLICABLE, name="sample.epub", suffix=".epub", route="epub", unit="chapter",
        count=len(labels), keys=", ".join("chapter/%06d" % ordinal
                                        for ordinal in range(1, len(labels) + 1)),
        pages=0, sheets=0, chapters=len(labels),
        tables=tables, text_regions=regions, body=body,
        omissions="\n".join("- " + value for value in EPUB_OMISSIONS),
        contents="\n".join('- [Chapter %d: "%s"](#brewdoc-chapter-%06d)'
                           % (ordinal, label, ordinal) for ordinal, label in enumerate(labels, 1))
                 + "\n\n" + navigation.rstrip("\n"),
        source_bytes=len(source), source_sha256=hashlib.sha256(source).hexdigest(),
        body_bytes=len(encoded), body_sha256=hashlib.sha256(encoded).hexdigest())
    return document.replace("\n## Artifacts", PUBLICATION_ROWS + "\n## Artifacts", 1)


@pytest.mark.parametrize("members_factory", [epub2_members, epub3_members], ids=["epub2", "epub3"])
def test_valid_epub_publications_render_through_the_existing_public_api(tmp_path, members_factory):
    # GIVEN a real synthetic EPUB package with different navigation and spine order
    path = write_epub(tmp_path / "sample.epub", members_factory())
    assert zipfile.is_zipfile(path) is True, "the RED input must be a valid ZIP publication"
    # WHEN the existing public service converts the declared publication
    rc, receipt, markdown = rendering_result(brewdoc.run(path))
    # THEN valid EPUB 2 and EPUB 3 are successful chapter routes
    assert (rc, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], receipt.get("unit_keys")) == (
        0, True, "epub", "chapter", 2, ["chapter/000001", "chapter/000002"],
    ), "valid EPUB must render both declared spine items through brewdoc.run"
    assert (receipt, markdown) == (expected_receipt(path), expected_document(path)), (
        "spine order, duplicate metadata, Unicode and navigation must match the complete contract")


@pytest.mark.parametrize("content,expected,regions,tables", [
    pytest.param('<p>Before<script>secret</script>After<span hidden="hidden">hidden</span>Tail</p>',
                 "BeforeAfterTail\n", 1, 0, id="excluded-subtree-keeps-tail"),
    pytest.param('<h1>One</h1><h5>Five</h5><p><em>soft</em> <strong>bold</strong></p>',
                 "### One\n\n**Heading 5: Five**\n\n*soft* **bold**\n", 3, 0,
                 id="headings-and-emphasis"),
    pytest.param('<hgroup><h2>I</h2><p>Subtitle</p></hgroup>',
                 "#### I\n\nSubtitle\n", 2, 0, id="hgroup-preserves-heading-and-subtitle"),
    pytest.param('<ol start="4" reversed="reversed"><li>A</li><li value="8">B</li>'
                 '<li>C<ul><li>Nested</li></ul></li></ol>',
                 "4. A\n8. B\n7. C\n   - Nested\n", 4, 0, id="ordered-nested-list"),
    pytest.param('<p>first</p><blockquote><p>quote</p><blockquote><p>inner</p></blockquote>'
                 '</blockquote><p>last</p>',
                 "first\n\n> quote\n>\n> > inner\n\nlast\n", 4, 0,
                 id="nested-quote-prefixes"),
    pytest.param('<p>AT&amp;T &amp;copy; snake_case *x* [y] &lt;z&gt; "q" a &gt; b # 1. C:\\d '
                 '_lead trail_ a_b ~~strike~~ (p) {c} !e C#</p>',
                 'AT&T &amp;copy; snake_case \\*x\\* \\[y\\] &lt;z> "q" a > b # 1. C:\\\\d '
                 '\\_lead trail\\_ a_b ~~strike~~ (p) {c} !e C#\n', 1, 0,
                 id="minimal-inline-escaping"),
    pytest.param('<p>a&amp;<span>amp;</span> c&amp;<span>co<span>p</span></span>y; '
                 'f<span>&amp;#</span>45; AT&amp;T x&amp;</p>',
                 'a&amp;amp; c&amp;copy; f&amp;#45; AT&T x&\n', 1, 0,
                 id="entity-split-across-inline-nodes"),
    pytest.param('<p>Q&amp;</p><table><tr><td>Q&amp;</td></tr></table>',
                 'Q&\n\n| Q& |\n| --- |\n', 1, 1, id="block-final-ampersand-stays-literal"),
    pytest.param('<blockquote><p>Spring, <br/> Or the bees \t<br/>end</p></blockquote>'
                 '<p>One <br/>Two</p>',
                 '> Spring,\n> Or the bees\n> end\n\nOne\nTwo\n', 2, 0,
                 id="line-breaks-leave-no-trailing-blanks"),
    pytest.param('<p>- dash</p><p>+ plus</p><p>1. one</p><p>2) two</p><p>1.5 kg</p><p>===</p>'
                 '<p>~~~ x</p><p>| a |</p><p>&gt; gt</p><blockquote><p># h</p></blockquote>',
                 '\\- dash\n\n\\+ plus\n\n1\\. one\n\n2\\) two\n\n1.5 kg\n\n\\===\n\n'
                 '\\~~~ x\n\n\\| a |\n\n\\> gt\n\n> \\# h\n', 10, 0, id="line-start-escaping"),
    pytest.param('<h1>Issue #</h1><h2>Step ##  </h2><h3>#</h3><h1>C# notes #x</h1><h1>C#</h1>'
                 '<h1># tag</h1><h2>Run<code>x</code>step #</h2>',
                 '### Issue \\#\n\n#### Step \\##\n\n##### \\#\n\n### C# notes #x\n\n### C#\n\n'
                 '### # tag\n\n#### Run\n\n```\nx\n```\n\n#### step \\#\n', 9, 0,
                 id="heading-atx-closing-sequence"),
    pytest.param('<ul><li>- dash</li><li>1. one</li></ul>',
                 '- \\- dash\n- 1\\. one\n', 2, 0, id="list-item-line-start-escaping"),
    pytest.param('<ul><li>a</li><li></li><li>b</li></ul>',
                 '- a\n-\n- b\n', 2, 0, id="list-empty-item-bare-marker"),
    pytest.param('<ul><li>Outer<ul><li></li><li>x</li></ul></li></ul>',
                 '- Outer\n\n  -\n  - x\n', 2, 0, id="list-nested-empty-first-item-blank-before"),
    pytest.param('<ul><li>Outer<ol start="3"><li>Third</li><li>Fourth</li></ol></li></ul>',
                 '- Outer\n\n  3. Third\n  4. Fourth\n', 3, 0, id="list-nested-order-offset"),
    pytest.param('<ol start="-1"><li>A</li><li>B</li></ol>',
                 '0. A\n0. B\n', 2, 0, id="list-negative-start-clamps-to-zero"),
    pytest.param('<ol reversed="reversed" start="1"><li>A</li><li>B</li><li>C</li></ol>',
                 '1. A\n0. B\n0. C\n', 3, 0, id="list-reversed-below-zero-clamps"),
    pytest.param('<ol><li value="1234567890">A</li><li>B</li></ol>',
                 '999999999. A\n999999999. B\n', 2, 0, id="list-huge-value-clamps-to-nine-digits"),
    pytest.param('<ol start="' + '9' * 4301 + '"><li>a</li></ol>', '999999999. a\n', 1, 0,
                 id="list-start-over-int-digit-limit-caps"),
    pytest.param('<ol><li value="' + '9' * 4301 + '">a</li></ol>', '999999999. a\n', 1, 0,
                 id="list-value-over-int-digit-limit-caps"),
    pytest.param('<table><tr><td colspan="-' + '9' * 4301 + '">a</td><td>b</td></tr><tr><td rowspan="-'
                 + '9' * 4301 + '">c</td><td>d</td></tr></table>',
                 '| a | b |\n| --- | --- |\n| c | d |\n', 0, 1, id="huge-negative-spans-are-one"),
    pytest.param('<p>Name</p><input value="x"/><button>Send</button><select><option>A</option>'
                 '</select><textarea>t</textarea><p>Body</p>',
                 'Name\n\nBody\n', 2, 0, id="form-controls-dropped"),
    pytest.param('<form><label>Email</label><input value="x"/><button>Send</button><p>Note</p></form>',
                 'Email\n\nNote\n', 2, 0, id="form-text-kept-as-prose"),
    pytest.param('<ul><li><table><tr><td>A</td></tr></table></li></ul>',
                 '-\n  | A |\n  | --- |\n', 0, 1, id="list-item-starting-with-table"),
    pytest.param('<ul><li><pre>x</pre>After</li></ul>',
                 '-\n  ```\n  x\n  ```\n\n  After\n', 2, 0, id="list-item-starting-with-fence"),
    pytest.param('<header><p>Head</p></header><nav><p>Nav</p></nav><aside><p>Aside</p></aside>'
                 '<footer><p>Foot</p></footer><div role="navigation"><p>Role</p></div>',
                 'Head\n\nNav\n\nAside\n\nFoot\n\nRole\n', 5, 0, id="landmarks-kept-in-epub"),
    pytest.param('<pre>one\n  ``` &lt;x&gt;\n</pre>',
                 "````\none\n  ``` <x>\n\n````\n", 1, 0, id="literal-safe-code-fence"),
    pytest.param('<table><caption>Caption</caption><tbody><tr><th>A</th><th>B</th></tr>'
                 '<tr><td rowspan="0">x|y</td><td>one</td></tr><tr><td>two</td></tr>'
                 '</tbody></table>',
                 "Caption\n\n| A | B |\n| --- | --- |\n| x\\|y | one |\n|  | two |\n",
                 1, 1, id="caption-rowgroup-span-pipe"),
    pytest.param('<table><tr><td>before<table><tr><td>N</td><td>M</td></tr></table>after</td>'
                 '<td>outer</td></tr></table>',
                 '| before Nested table: (row 1, column 1) "N"; (row 1, column 2) "M" after | '
                 'outer |\n| --- | --- |\n', 0, 1, id="nested-table-at-origin-cell"),
    pytest.param('<ul><li>Before<table><tr><td>A</td><td>B</td></tr></table>After</li></ul>',
                 '- Before\n\n  | A | B |\n  | --- | --- |\n\n  After\n', 2, 1,
                 id="table-contained-in-list-item"),
    pytest.param('<ul><li>Before<blockquote><p>quote</p><blockquote><p>inner</p></blockquote>'
                 '</blockquote>After</li></ul>',
                 '- Before\n\n  > quote\n  >\n  > > inner\n\n  After\n',
                 4, 0, id="nested-quotes-contained-in-list-item"),
    pytest.param('<ul><li>Before<pre>&lt;x&gt; &amp;\n</pre>After</li></ul>',
                 '- Before\n\n  ```\n  <x> &\n\n  ```\n\n  After\n', 3, 0,
                 id="literal-code-contained-in-list-item"),
    pytest.param('<ul><li>Before<p id="one">One <span id="inline">inline</span></p>'
                 '<blockquote><p id="quote">quote</p></blockquote>After</li></ul>',
                 '- Before\n\n'
                 '  <a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
                 '  <a id="brewdoc-chapter-000001-fragment-000002"></a>\n  One inline\n\n'
                 '  > <a id="brewdoc-chapter-000001-fragment-000003"></a>\n'
                 '  > quote\n\n  After\n',
                 4, 0, id="paragraph-quote-anchors-with-item-continuation"),
    pytest.param('<h2>Example<code>x&lt;y</code>Title</h2>',
                 '#### Example\n\n```\nx<y\n```\n\n#### Title\n', 3, 0,
                 id="heading-segments-surround-native-code"),
    pytest.param('<table><tr><td>before<code>&lt;x&gt; &amp; "q" ```\n\t  literal</code>after</td>'
                 '<td>x|y</td></tr></table>',
                 '| before Code: text="&lt;x> & &quot;q&quot; \\`\\`\\`&#10;&#9;  literal" after | '
                 'x\\|y |\n| --- | --- |\n', 0, 1, id="literal-code-metadata-at-table-cell-position"),
    pytest.param('<ul><li>one<br/>two</li></ul>', '- one\n  two\n', 1, 0, id="br-in-list-item"),
    pytest.param('<ul><li><b>x</b><br/>y</li></ul>', '- **x**\n  y\n', 1, 0,
                 id="br-after-emphasis-in-list-item"),
    pytest.param('<div>alpha<br/>beta</div>', 'alpha\nbeta\n', 1, 0, id="br-in-div"),
    pytest.param('<section>a<br/>b</section>', 'a\nb\n', 1, 0, id="br-in-section"),
    pytest.param('<blockquote>alpha<br/>beta</blockquote>', '> alpha\n> beta\n', 1, 0,
                 id="br-in-blockquote"),
    pytest.param('alpha<br/>beta', 'alpha\nbeta\n', 1, 0, id="br-in-body"),
    pytest.param('<p><b>Note:</b><i>text</i></p>', '**Note:**_text_\n', 1, 0,
                 id="emphasis-unclosable-star-falls-back-to-underscore"),
    pytest.param('<p><b>A</b><b>B</b></p>', '**AB**\n', 1, 0, id="emphasis-adjacent-strong-merges"),
    pytest.param('<p><em>a</em><em>b</em></p>', '*ab*\n', 1, 0, id="emphasis-adjacent-em-merges"),
    pytest.param('<p>price<b>$5</b>.</p>', 'price$5.\n', 1, 0,
                 id="emphasis-unopenable-strong-drops-markers"),
    pytest.param('<p>foo<em>(bar)</em></p>', 'foo(bar)\n', 1, 0,
                 id="emphasis-unopenable-em-drops-markers"),
    pytest.param('<p>x <em> y </em> z</p>', 'x *y* z\n', 1, 0, id="emphasis-spaces-move-outside"),
    pytest.param('<table><tr><td><b>A</b><b>B</b></td></tr></table>', '| **AB** |\n| --- |\n',
                 0, 1, id="emphasis-adjacent-strong-merges-in-cell"),
    pytest.param('<table><tr><td>x <em> y </em> z</td></tr></table>', '| x *y* z |\n| --- |\n',
                 0, 1, id="emphasis-spaces-move-outside-in-cell"),
    pytest.param('<ol><li>a</li></ol><ol start="5"><li>b</li></ol>', '1. a\n\n5) b\n', 2, 0,
                 id="adjacent-ordered-lists-alternate-delimiter"),
    pytest.param('<ul><li>a</li></ul><ul><li>b</li></ul><ul><li>c</li></ul>',
                 '- a\n\n* b\n\n- c\n', 3, 0, id="adjacent-bullet-lists-alternate-bullet"),
    pytest.param('<ul><li>a<ol start="1"><li>b</li></ol><ol start="3"><li>c</li></ol></li></ul>',
                 '- a\n  1. b\n\n  3) c\n', 3, 0, id="adjacent-nested-ordered-lists-alternate"),
    pytest.param('<p><a href="https://x.test/?a=1&amp;copy;=2&amp;b=3&amp;amp;">L</a></p>',
                 '[L](https://x.test/?a=1&amp;copy;=2&b=3&amp;amp;)\n', 1, 0,
                 id="link-target-entity-ampersand-escaped"),
    pytest.param('<p><img alt="I" src="https://x.test/p.png?x&amp;lt;=1"/></p>'
                 '<p><img alt="R" src="p.png?x&amp;lt;=1"/></p>',
                 '![I](https://x.test/p.png?x&amp;lt;=1)\n\n![R](p.png?x&amp;lt;=1)\n', 2, 0,
                 id="image-target-entity-ampersand-escaped"),
    pytest.param('<p><img alt="J" src="javascript:alert(1)"/> <img alt="D" '
                 'src="data:image/png;base64,AAAA"/> <img alt="F" src="ftp://x.test/p.png"/></p>',
                 '![J]() ![D]() ![F]()\n', 1, 0, id="image-inactive-schemes-empty-target"),
    pytest.param('<ol start="1_0"><li>a</li></ol><p>s</p><ol start="٣"><li>b</li></ol><p>t</p>'
                 '<ol><li value="1_0">c</li><li value="٣">d</li></ol>',
                 '1. a\n\ns\n\n1. b\n\nt\n\n1. c\n2. d\n', 6, 0, id="list-numbers-ascii-digits-only"),
    pytest.param("<p><a href=\"https://x.test/a!b$c'd*e\">L</a> "
                 "<img alt=\"I\" src=\"https://x.test/a!b$c'd*e.png\"/></p>",
                 '[L](https://x.test/a%21b%24c%27d%2Ae) ![I](https://x.test/a%21b%24c%27d%2Ae.png)\n',
                 1, 0, id="url-safe-set-matches-html"),
    pytest.param('<p><a href="https://x.test/"><em> label</em></a></p>', '[*label*](https://x.test/)\n', 1, 0,
                 id="link-label-leading-space-dropped-at-paragraph-start"),
    pytest.param('<p>see<a href="https://x.test/"><em> label</em></a></p>', 'see [*label*](https://x.test/)\n',
                 1, 0, id="link-label-leading-space-moves-before-bracket"),
    pytest.param('<p><a href="https://x.test/"><em>label </em></a>next</p>', '[*label*](https://x.test/) next\n',
                 1, 0, id="link-label-trailing-space-moves-after-target"),
    pytest.param('<p>see<a href="https://x.test/"> label </a>next</p>', 'see [label](https://x.test/) next\n',
                 1, 0, id="plain-link-label-spaces-move-outside"),
    pytest.param('<table><tr><td>see<a href="https://x.test/"> label </a>next</td></tr></table>',
                 '| see [label](https://x.test/) next |\n| --- |\n', 0, 1, id="cell-link-label-spaces-move-outside"),
    *INLINE_BOUNDARY_WHITESPACE,
    *BANG_BEFORE_LINK,
    *CODE_SPLIT_IMAGE_AND_BLANK_LINK,
    pytest.param('<p>see <a href="ftp://x.test/">label </a>next</p>',
                 'see label (Link target: "ftp://x.test/") next\n', 1, 0,
                 id="fallback-link-trailing-space-moves-after-record"),
    pytest.param('<p>see <a href="a.xhtml?q=1">label </a>next</p>',
                 'see label (Link target: "a.xhtml?q=1") next\n', 1, 0,
                 id="query-fallback-link-trailing-space-moves-after-record"),
    pytest.param('<p>see <a href="ftp://x.test/"> label</a> next</p>',
                 'see label (Link target: "ftp://x.test/") next\n', 1, 0,
                 id="fallback-link-leading-space-after-space"),
    pytest.param('<p>see <a href="ftp://x.test/"><em> label </em></a>next</p>',
                 'see *label* (Link target: "ftp://x.test/") next\n', 1, 0,
                 id="fallback-link-emphasis-space-after-space"),
    pytest.param('<p>see <a href="https://x.test/"> foo<code>c</code> bar </a> next</p>',
                 'see [foo](https://x.test/)\n\n```\nc\n```\n\n[bar](https://x.test/) next\n', 3, 0,
                 id="code-split-link-edge-spaces-not-doubled"),
    pytest.param('<p>see <a href="ftp://x.test/"> foo<code>c</code> bar </a>next</p>',
                 'see foo (Link target: "ftp://x.test/")\n\n```\nc\n```\n\n'
                 'bar (Link target: "ftp://x.test/") next\n', 3, 0,
                 id="code-split-fallback-link-leading-space-after-space"),
    pytest.param('<p>lead</p><ol id="t" start="3"><li>a</li></ol>',
                 'lead\n\n<a id="brewdoc-chapter-000001-fragment-000001"></a>\n\n3. a\n', 2, 0,
                 id="anchor-before-offset-ordered-list"),
    pytest.param('<ul id="t"><li></li><li>x</li></ul>',
                 '<a id="brewdoc-chapter-000001-fragment-000001"></a>\n\n-\n- x\n', 1, 0,
                 id="anchor-before-empty-first-item"),
    pytest.param('<ul><li>x<ol id="t" start="3"><li>a</li></ol></li></ul>',
                 '- x\n\n  <a id="brewdoc-chapter-000001-fragment-000001"></a>\n\n  3. a\n', 2, 0,
                 id="anchor-before-offset-ordered-list-in-item"),
    pytest.param('<blockquote><ol id="t" start="2"><li>a</li></ol></blockquote>',
                 '> <a id="brewdoc-chapter-000001-fragment-000001"></a>\n>\n> 2. a\n', 1, 0,
                 id="anchor-before-offset-ordered-list-in-quote"),
    pytest.param('<p>lead</p><ul id="t"><li>a</li></ul>',
                 'lead\n\n<a id="brewdoc-chapter-000001-fragment-000001"></a>\n- a\n', 2, 0,
                 id="anchor-before-interrupting-list-stays-tight"),
])
def test_static_xhtml_preserves_ordered_content_regions(tmp_path, content, expected, regions, tables):
    # GIVEN one chapter with representative static content and a later auxiliary chapter
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml(content)
    path = write_epub(tmp_path / "sample.epub", members)
    assert zipfile.is_zipfile(path) is True, "content probes must retain valid EPUB packaging"
    body = TWO_CHAPTER_BODY.replace("café 東京\n", expected)
    # WHEN the publication renders through the public API
    rc, receipt, markdown = rendering_result(brewdoc.run(path))
    # THEN source order, containment, counters and the complete Markdown envelope are exact
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, regions=regions + 1, tables=tables),
        expected_document(path, body, regions=regions + 1, tables=tables),
    ), "static XHTML must preserve the independently specified content and full receipt"


@pytest.mark.parametrize("options,reason", [({"sheets": ("One",)}, "--sheet and --artifact are workbook-only options"),
                                    ({"artifact_outputs": {"formula/sheet/000001": "calc.json"}},
                                     "unknown artifact key: formula/sheet/000001")],
                         ids=["sheet-selection", "artifact-output"])
def test_epub_refuses_workbook_options_without_replacing_output(tmp_path, options, reason):
    # GIVEN a valid publication and an existing output sentinel
    path = write_epub(tmp_path / "sample.epub", epub3_members())
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert out.read_bytes() == b"existing output\n", "the destination sentinel must start intact"
    # WHEN workbook-only options are supplied to EPUB conversion
    rc, receipt, markdown = rendering_result(brewdoc.run(path, out, **options), output=out)
    # THEN refusal has no partial units or output replacement
    assert (rc, receipt["file_ok"], receipt["route"], receipt["units"], receipt["reason"],
            markdown, out.read_bytes()) == (
        1, False, "epub", 0, reason, "",
        b"existing output\n",
    ), "workbook options must refuse EPUB before output writes"


def test_public_epub_wrapper_and_uppercase_suffix_preserve_tally_and_utf8(tmp_path):
    # GIVEN a valid publication under the uppercase registered suffix
    path = write_epub(tmp_path / "sample.EPUB", epub3_members())
    assert path.suffix == ".EPUB", "the public wrapper probe must exercise suffix normalization"
    # WHEN the public wrapper and service each convert the same publication
    markdown, tally = brewdoc.render_epub(path)
    rc, receipt, api_markdown = rendering_result(brewdoc.run(path))
    # THEN the wrapper retains its two-value shape and the service retains full Unicode output
    expected = expected_document(path).replace('"sample.epub"', '"sample.EPUB"')
    expected_tally = dict(zero_tally(), chapters=2, text_regions=2)
    assert (markdown, tally, rc, receipt, api_markdown) == (
        expected, expected_tally, 0, expected_receipt(path), expected,
    ), "render_epub and uppercase routing must preserve exact Markdown, tally and receipt"


@pytest.mark.parametrize("declared_body", [
    '<!DOCTYPE html>' + xhtml("<p>café 東京</p>"),
    '<?xml version="1.0" encoding="UTF-16"?>' + xhtml("<p>café 東京</p>"),
], ids=["bare-doctype", "utf16-bom-and-declaration"])
def test_xml_preflight_accepts_bare_doctype_and_utf16_without_fetching(tmp_path, declared_body):
    # GIVEN qualified static XHTML encoded as UTF-16 with its BOM
    members = {name: text.encode("utf-8") for name, text in epub3_members().items()}
    members["OPS/b.xhtml"] = declared_body.encode("utf-16")
    path = write_epub_bytes(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"][:2] == codecs.BOM_UTF16, "the XML probe must carry a UTF-16 BOM"
    # WHEN Expat and ElementTree consume the same bounded required member bytes
    result = rendering_result(brewdoc.run(path))
    # THEN XML encodings and inert bare doctypes retain the full Unicode contract
    assert result == (0, expected_receipt(path), expected_document(path)), (
        "UTF-16 and bare doctypes must render exact Unicode without external entity resolution")


def test_empty_qualified_body_remains_a_declared_spine_unit(tmp_path):
    # GIVEN an empty first spine body followed by the auxiliary text chapter
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml("")
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count("<body></body>") == 1, "the first body must be qualified and empty"
    # WHEN the package converts through the public service
    result = rendering_result(brewdoc.run(path))
    # THEN an empty body preserves its ordinal and adds no source text region
    body = TWO_CHAPTER_BODY.replace("café 東京\n\n", "")
    assert result == (0, expected_receipt(path, regions=1),
                      expected_document(path, body, regions=1)), (
        "an empty qualified body must remain in declared spine order")


@pytest.mark.parametrize("attribute", ['hidden="hidden"', 'aria-hidden="true"'],
                         ids=["hidden-body", "aria-hidden-body"])
def test_hidden_qualified_body_preserves_unit_without_included_regions(tmp_path, attribute):
    # GIVEN a qualified body hidden as a whole with a declared source fragment
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<p id="hidden-fragment">secret</p>').replace(
        "<body>", "<body %s>" % attribute)
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count("<body %s>" % attribute) == 1, (
        "the hiding guard must apply to the body itself")
    # WHEN the included publication flow excludes the entire hidden body
    result = rendering_result(brewdoc.run(path))
    # THEN the declared unit stays in order without source text or an available hidden anchor
    body = TWO_CHAPTER_BODY.replace("café 東京\n\n", "")
    assert result == (0, expected_receipt(path, regions=1),
                      expected_document(path, body, regions=1)), (
        "hidden body content and its fragments must remain unavailable without dropping the unit")


def test_list_item_fragments_stay_at_their_own_source_region(tmp_path):
    # GIVEN source IDs belonging to the second list item's block and inline content
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<ul><li>One</li><li id="two">Two '
                                   '<span id="inline">inline</span></li></ul>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count('id="') == 2, "the second item must own both source IDs"
    # WHEN ordered list regions acquire their generated fragment destinations
    result = rendering_result(brewdoc.run(path))
    # THEN second-item anchors follow the first item and start the second item's region
    listing = ('- One\n-\n  <a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
               '  <a id="brewdoc-chapter-000001-fragment-000002"></a>\n  Two inline\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", listing)
    assert result == (0, expected_receipt(path, regions=3),
                      expected_document(path, body, regions=3)), (
        "list fragment anchors must not be hoisted ahead of earlier source items")


def test_inline_code_keeps_literal_bytes_emphasis_and_fragment_position(tmp_path):
    # GIVEN inline XML code enclosed by emphasis, surrounding prose and a fragment link
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<p>Before <em>soft <code id="literal">'
                                   '&lt;x&gt; &amp; ```\n\t  literal</code> tail</em> After '
                                   '<a href="#literal">code</a></p>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count('\n\t  literal') == 1, (
        "literal code must include whitespace to preserve")
    # WHEN inline code is lowered to a native safe fenced region at its source position
    result = rendering_result(brewdoc.run(path))
    # THEN prose keeps independent emphasis while the code and its fragment remain literal
    content = ('Before *soft*\n\n<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
               '````\n<x> & ```\n\t  literal\n````\n\n'
               '*tail* After [code](#brewdoc-chapter-000001-fragment-000001)\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", content)
    assert result == (0, expected_receipt(path, regions=4),
                      expected_document(path, body, regions=4)), (
        "inline code must preserve literal bytes, surrounding emphasis and exact fragment placement")


def test_http_link_output_limit_uses_actual_quoted_bytes_and_is_inclusive(tmp_path, monkeypatch):
    # GIVEN an ordinary safe HTTP link and an independently specified complete document
    epub = importlib.import_module("brewdoc.epub")
    target = "https://example.invalid/" + "a" * 2000
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<p><a href="%s">web</a></p>' % target)
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    body = TWO_CHAPTER_BODY.replace("café 東京\n", "[web](%s)\n" % target)
    expected = expected_document(path, body)
    maximum = len(brewdoc.run(path)[2].encode("utf-8"))
    assert len(target) == 2024, "the URL must contain exactly the bounded ASCII target bytes"
    # WHEN the complete Markdown budget equals its exact byte count and then drops by one
    monkeypatch.setitem(epub.LIMITS, "markdown_bytes", maximum)
    accepted = rendering_result(brewdoc.run(path))
    monkeypatch.setitem(epub.LIMITS, "markdown_bytes", maximum - 1)
    out.write_bytes(b"existing output\n")
    rc, receipt, markdown = rendering_result(brewdoc.run(path, out), output=out)
    # THEN actual URL quoting accepts the inclusive boundary and one-over refuses atomically
    assert accepted == (0, expected_receipt(path), expected), (
        "HTTP link accounting must use actual quoted bytes rather than a pessimistic multiplier")
    assert (rc, receipt, markdown, out.read_bytes()) == (
        1, expected_refusal(path, "EPUB markdown_bytes limit exceeded"), "", b"existing output\n",
    ), "the one-over complete Markdown size must retain the full refusal and destination sentinel"


def test_inline_code_inside_a_link_preserves_safe_segments_and_internal_fragment_links(tmp_path):
    # GIVEN a safe hyperlink enclosing literal code with a following internal code-ID link
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<p><a href="https://example.invalid/">Before'
                                   '<code id="literal">&lt;x&gt; &amp;</code>After</a> '
                                   '<a href="#literal">self</a></p>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count('<a href=') == 2, "the probe requires external and internal links"
    # WHEN inline code splits the enclosing link into native source-order regions
    result = rendering_result(brewdoc.run(path))
    # THEN safe surrounding link segments stay linked and code fragment identity remains available
    content = ('[Before](https://example.invalid/)\n\n'
               '<a id="brewdoc-chapter-000001-fragment-000001"></a>\n```\n<x> &\n```\n\n'
               '[After](https://example.invalid/) '
               '[self](#brewdoc-chapter-000001-fragment-000001)\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", content)
    assert result == (0, expected_receipt(path, regions=4),
                      expected_document(path, body, regions=4)), (
        "native literal-code lowering must preserve safe enclosing and rewritten internal links")


def test_recognized_manifest_font_obfuscation_does_not_block_reading_content(tmp_path):
    # GIVEN an existing declared font with the recognized IDPF obfuscation declaration
    members = epub3_members()
    members["OPS/book.opf"] = members["OPS/book.opf"].replace('</manifest>',
        '<item id="font" href="font.otf" media-type="font/otf"/></manifest>')
    members["OPS/font.otf"] = "synthetic obfuscated font bytes"
    members["META-INF/encryption.xml"] = (
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
        '<EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
        '<CipherData><CipherReference URI="OPS/font.otf"/></CipherData></EncryptedData></encryption>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/book.opf"].count('media-type="font/otf"') == 1, (
        "font acceptance requires a declared font media type")
    # WHEN the EPUB reader validates encryption before reading chapter XML
    result = rendering_result(brewdoc.run(path))
    # THEN font-only obfuscation leaves the publication's exact text and receipts available
    assert result == (0, expected_receipt(path), expected_document(path)), (
        "recognized font obfuscation must not attempt font decoding or refuse reading text")


def test_images_render_as_markdown_images_and_drop_without_alt_text(tmp_path):
    # GIVEN block and inline images with escaped, empty, blank, absent alt and absent source
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<img alt=" [x]  *y* " title="t" src="pictures/a (b).png"/>'
                                   '<img alt="" title="" src="pictures/one.png"/>'
                                   '<img src="pictures/two.png"/><img alt=" " src="z.png"/>'
                                   '<img alt="none"/><p>See <img alt="fig" src="f.png"/> here</p>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert len(members) == 6, "image rendering must not require archive image payloads"
    # WHEN image content renders without extraction or fetching
    result = rendering_result(brewdoc.run(path))
    # THEN alt text images become Markdown images at source position and the rest drop
    image_text = ('![\\[x\\] \\*y\\*](pictures/a%20%28b%29.png)\n\n![none]()\n\n'
                  'See ![fig](f.png) here\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", image_text)
    assert result == (0, expected_receipt(path, regions=4),
                      expected_document(path, body, regions=4)), (
        "images must render as ![alt](target) with quoted targets and no region without alt text")


def test_block_inline_and_cross_chapter_links_use_source_order_fragment_anchors(tmp_path):
    # GIVEN two source IDs in the first chapter and a cross-document link in the second
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<p id="one">One <span id="inline">inline</span> '
                                   '<a href="#one">self</a></p>')
    members["OPS/a.xhtml"] = xhtml('<p><a href="b.xhtml#inline">cross</a> '
                                   '<a href="https://example.invalid/path">web</a></p>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/b.xhtml"].count('id="') == 2, "the anchor probe requires two ordered source IDs"
    # WHEN source-local and cross-document links resolve against included fragments
    result = rendering_result(brewdoc.run(path))
    # THEN generated anchors appear at the containing region and rewritten links remain local
    first = ('<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
             '<a id="brewdoc-chapter-000001-fragment-000002"></a>\n'
             'One inline [self](#brewdoc-chapter-000001-fragment-000001)\n')
    second = ('[cross](#brewdoc-chapter-000001-fragment-000002) '
              '[web](https://example.invalid/path)\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", first).replace(
        "First e\u0301\u00a0word\u00ad.\n", second)
    assert result == (0, expected_receipt(path), expected_document(path, body)), (
        "fragment identities must follow source ID order and stay at their rendered regions")


def test_distinct_spine_items_converging_on_one_fallback_keep_occurrence_identity(tmp_path):
    # GIVEN two spine references resolving to one supported XHTML fallback
    members = epub3_members()
    members["OPS/book.opf"] = members["OPS/book.opf"].replace(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="a" href="a.svg" media-type="image/svg+xml" fallback="b"/>')
    members["OPS/a.svg"] = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    members["OPS/nav.xhtml"] = NAV.replace('href="a.xhtml"', 'href="b.xhtml"')
    members["OPS/b.xhtml"] = xhtml('<p id="self"><a href="#self">self</a></p>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert members["OPS/book.opf"].count('fallback="b"') == 1, (
        "the unsupported item must declare its endpoint")
    # WHEN fallback resolution retains both original spine occurrences
    result = rendering_result(brewdoc.run(path))
    # THEN self-links use current occurrence while repeated navigation targets use the first
    body = ('<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Alpha"\n\n'
            '<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
            '[self](#brewdoc-chapter-000001-fragment-000001)\n\n'
            '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Alpha"\n\n'
            'Auxiliary content.\n\n<a id="brewdoc-chapter-000002-fragment-000001"></a>\n'
            '[self](#brewdoc-chapter-000002-fragment-000001)\n')
    navigation = PUBLICATION_NAV.replace("brewdoc-chapter-000002", "brewdoc-chapter-000001")
    assert result == (0, expected_receipt(path), expected_document(
        path, body, labels=("Alpha", "Alpha"), navigation=navigation)), (
        "converging fallbacks must preserve both units and the explicit earliest-navigation rule")


def test_epub_cli_ascii_stdout_and_file_preserve_the_same_utf8_bytes(tmp_path):
    # GIVEN Unicode publication content and an ASCII-configured subprocess console
    path = write_epub(tmp_path / "sample.epub", epub3_members())
    out = tmp_path / "document.md"
    environment = dict(os.environ, PYTHONIOENCODING="ascii")
    assert out.exists() is False, "the EPUB output must start absent"
    # WHEN CLI stdout and the transactional file path render the same source
    stdout = subprocess.run([sys.executable, "-m", "brewdoc.cli", str(path)],
                            env=environment, capture_output=True, check=False)
    file_run = subprocess.run([sys.executable, "-m", "brewdoc.cli", str(path), "--out", str(out)],
                              env=environment, capture_output=True, check=False)
    assert (stdout.returncode, file_run.returncode) == (0, 0), "both CLI routes must succeed before byte comparison"
    assert stdout.stdout.partition(os.linesep.encode("ascii"))[2] == out.read_bytes(), "full inspected CLI bytes must equal the written file"
    receipt_bytes, _, markdown_bytes = rendering_stdout(stdout.stdout).partition(os.linesep.encode("ascii"))
    # THEN ASCII JSON precedes exact UTF-8 Markdown and --out emits receipt-only stdout
    expected = expected_document(path).encode("utf-8")
    file_receipt = dict(expected_receipt(path), out=str(out))
    assert (stdout.returncode, stdout.stderr, receipt_bytes, markdown_bytes,
            file_run.returncode, file_run.stderr, rendering_stdout(file_run.stdout), rendering_bytes(out.read_bytes())) == (
        0, b"", json.dumps(expected_receipt(path), sort_keys=True, ensure_ascii=True).encode("ascii"),
        expected, 0, b"", (json.dumps(file_receipt, sort_keys=True, ensure_ascii=True) + os.linesep)
        .encode("ascii"), expected,
    ), "CLI JSON framing, binary UTF-8 stdout and UTF-8 files must agree"


@pytest.mark.parametrize("member,replacement,reason_pattern", [
    pytest.param("mimetype", "text/plain", r"EPUB .*mimetype.*", id="wrong-mimetype"),
    pytest.param("META-INF/container.xml", CONTAINER.replace("<rootfiles>", "<rootfiles><rootfile "
                 'full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>'),
                 r"EPUB .*rootfile.*", id="duplicate-rootfile"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('idref="a"', 'idref="b"'),
                 r"EPUB .*spine.*", id="repeated-spine-reference"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('id="a"', 'id="b"'),
                 r"EPUB .*(?:duplicate|ambiguous).*", id="duplicate-package-id"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('href="b.xhtml"',
                 'href="../../outside.xhtml"'), r"EPUB .*(?:path|archive|target).*", id="escaping-path"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('href="b.xhtml"',
                 'href="%2e%2e%2foutside.xhtml"'), r"EPUB .*(?:path|archive|target).*",
                 id="encoded-separator"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('href="b.xhtml"',
                 'href="https://example.invalid/b.xhtml"'), r"EPUB .*(?:external|target|URL).*",
                 id="external-required-resource"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace(
                 '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>',
                 '<item id="b" href="b.xhtml" media-type="image/svg+xml"/>'),
                 r"EPUB .*(?:fallback|media|unsupported).*", id="unsupported-required-media"),
    pytest.param("OPS/book.opf", epub3_members()["OPS/book.opf"].replace('</metadata>',
                 '<meta property="rendition:layout">pre-paginated</meta></metadata>'),
                 r"EPUB .*(?:fixed|pre-paginated).*", id="declared-fixed-layout"),
    pytest.param("OPS/b.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p></body></html>',
                 r"EPUB .*(?:XML|xml|parse).*", id="malformed-chapter"),
    pytest.param("OPS/b.xhtml", xhtml("<p>visible</p>").replace("<body>", '<body xmlns="urn:wrong">'),
                 r"EPUB .*body.*", id="wrong-body-namespace"),
    pytest.param("OPS/b.xhtml", xhtml("<p>visible</p>").replace('</body>', '</body><body/>'),
                 r"EPUB .*body.*", id="duplicate-body"),
    pytest.param("OPS/b.xhtml", '<!DOCTYPE html []>' + xhtml("<p>visible</p>"),
                 r"EPUB .*(?:DTD|doctype|subset).*", id="empty-internal-subset"),
    pytest.param("OPS/b.xhtml", xhtml('<p id="same">one</p><p id="same">two</p>'),
                 r"EPUB .*duplicate.*", id="duplicate-source-id"),
    pytest.param("OPS/nav.xhtml", NAV.replace('href="b.xhtml"', 'href="b.xhtml#missing"'),
                 r"EPUB .*(?:navigation|fragment|target).*", id="unavailable-required-fragment"),
    pytest.param("OPS/b.xhtml", xhtml('<p xml:base="other/">visible</p>'),
                 r"EPUB .*base.*", id="unsupported-xml-base"),
    pytest.param("META-INF/encryption.xml", '<encryption xmlns="urn:oasis:names:tc:'
                 'opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/'
                 'xmlenc#"><EncryptionMethod Algorithm="urn:unknown"/><CipherData>'
                 '<CipherReference URI="OPS/b.xhtml"/></CipherData></EncryptedData></encryption>',
                 r"EPUB .*encrypt.*", id="encrypted-reading-content"),
])
def test_required_content_refusals_are_atomic_and_named(tmp_path, member, replacement, reason_pattern):
    # GIVEN one malformed required member and an existing destination
    members = epub3_members()
    members[member] = replacement
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert out.read_bytes() == b"existing output\n", "the refusal sentinel must start intact"
    # WHEN a hard EPUB refusal occurs after package or chapter parsing
    rc, receipt, markdown = rendering_result(brewdoc.run(path, out), output=out)
    # THEN the named refusal carries the entire zero-unit receipt and no partial Markdown
    assert re.sub(r"\A(?:" + reason_pattern + r")\Z", "MATCH", receipt["reason"]) == "MATCH", (
        "the refusal must name the intended required-content violation: " + receipt["reason"])
    assert (rc, receipt, markdown, out.read_bytes()) == (
        1, expected_refusal(path, receipt["reason"]), "", b"existing output\n",
    ), "a late EPUB refusal must return zero units and preserve destination bytes"


@pytest.mark.parametrize("navigation,reason", [
    pytest.param(NAV.replace("<body>", '<body hidden="hidden">'),
                 "EPUB requires one qualified TOC navigation subtree: OPS/nav.xhtml",
                 id="hidden-navigation-body"),
    pytest.param(NAV.replace('<nav epub:type="toc">', '<div hidden="hidden"><nav epub:type="toc">')
                 .replace('</nav>', '</nav></div>'),
                 "EPUB requires one qualified TOC navigation subtree: OPS/nav.xhtml",
                 id="hidden-navigation-wrapper"),
    pytest.param(NAV.replace('<nav epub:type="toc">', '<div aria-hidden="true"><nav epub:type="toc">')
                 .replace('</nav>', '</nav></div>'),
                 "EPUB requires one qualified TOC navigation subtree: OPS/nav.xhtml",
                 id="aria-hidden-navigation-wrapper"),
    pytest.param(NAV.replace('<ol>', '<ol hidden="hidden">'),
                 "EPUB hidden TOC navigation list: OPS/nav.xhtml", id="hidden-toc-list"),
])
def test_navigation_hidden_by_ancestor_or_list_refuses_atomically(tmp_path, navigation, reason):
    # GIVEN required publication navigation hidden by its body, ancestor or TOC list
    members = epub3_members()
    members["OPS/nav.xhtml"] = navigation
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert out.read_bytes() == b"existing output\n", "the hidden-navigation sentinel must start intact"
    # WHEN the reader resolves the publication's required navigation labels and destinations
    result = rendering_result(brewdoc.run(path, out), output=out)
    # THEN excluded navigation never contributes labels or partial units or replaces output
    assert (result, out.read_bytes()) == (
        (1, expected_refusal(path, reason), ""), b"existing output\n",
    ), "navigation inherited hiding must produce the named complete atomic refusal"


@pytest.mark.parametrize("navigation", [
    pytest.param(NAV.replace('<ol>', '<ol><li hidden="hidden"><a href="not-in-spine.xhtml">'
                            'Hidden</a></li>'), id="hidden-optional-toc-item"),
    pytest.param(NAV.replace('</a></li>', '</a><ol aria-hidden="true"><li>'
                            '<a href="not-in-spine.xhtml">Hidden</a></li></ol></li>', 1),
                 id="hidden-optional-nested-list"),
])
def test_visible_navigation_omits_hidden_optional_branches(tmp_path, navigation):
    # GIVEN visible valid TOC entries plus a hidden optional branch with an unavailable target
    members = epub3_members()
    members["OPS/nav.xhtml"] = navigation
    path = write_epub(tmp_path / "sample.epub", members)
    assert navigation.count("not-in-spine.xhtml") == 1, (
        "the hidden branch must have an unavailable target")
    # WHEN included navigation walks only the visible publication flow
    result = rendering_result(brewdoc.run(path))
    # THEN optional hidden entries contribute neither labels nor required target validation
    assert result == (0, expected_receipt(path), expected_document(path)), (
        "hidden optional TOC branches must preserve exact visible navigation and declared chapter order")


def test_duplicate_zip_member_names_refuse_before_output_replacement(tmp_path):
    # GIVEN a publication with an ambiguous repeated required ZIP member name
    members = epub3_members()
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert zipfile.is_zipfile(path) is True, "the duplicate-member probe must start as a valid ZIP"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("OPS/b.xhtml", members["OPS/b.xhtml"])
    # WHEN archive admission sees both declarations before reading chapters
    rc, receipt, markdown = rendering_result(brewdoc.run(path, out), output=out)
    # THEN duplicate-member ambiguity returns a named complete refusal with unchanged bytes
    assert re.sub(r"\AEPUB .*duplicate.*\Z", "DUPLICATE", receipt["reason"]) == "DUPLICATE", (
        "the archive refusal must identify duplicate member names")
    assert (rc, receipt, markdown, out.read_bytes()) == (
        1, expected_refusal(path, receipt["reason"]), "", b"existing output\n",
    ), "duplicate ZIP members must never yield partial Markdown or replace output"


def test_casefold_archive_name_collisions_refuse_ambiguous_lookup(tmp_path):
    # GIVEN distinct ZIP names that collide under the admitted casefold policy
    members = epub3_members()
    members["OPS/B.xhtml"] = members["OPS/b.xhtml"]
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert len(members) == 7, "the ambiguity probe must retain both exact member spellings"
    # WHEN archive admission checks the member-name lookup boundary
    rc, receipt, markdown = rendering_result(brewdoc.run(path, out), output=out)
    # THEN ambiguous lookup refuses deterministically and preserves the output sentinel
    assert re.sub(r"\AEPUB .*(?:ambiguous|collision|duplicate).*\Z", "AMBIGUOUS", receipt["reason"]) == (
        "AMBIGUOUS"
    ), "the refusal must name the archive spelling ambiguity"
    assert (rc, receipt, markdown, out.read_bytes()) == (
        1, expected_refusal(path, receipt["reason"]), "", b"existing output\n",
    ), "casefold collisions must refuse before lookup can select a chapter"


@pytest.mark.parametrize("budget", [
    "source_bytes", "members", "member_bytes", "declared_bytes", "consumed_bytes",
    "document_elements", "total_elements", "depth", "spine_items", "table_columns",
    "table_slots", "total_table_slots", "markdown_bytes",
])
def test_epub_limits_accept_the_boundary_and_refuse_one_over(tmp_path, monkeypatch, budget):
    # GIVEN two small 2-by-2 tables and independently counted XML/ZIP/output budgets
    epub = importlib.import_module("brewdoc.epub")
    members = epub3_members()
    table = "<table><tr><td>A</td><td>B</td></tr><tr><td>x</td><td>y</td></tr></table>"
    members["OPS/a.xhtml"] = xhtml(table)
    members["OPS/b.xhtml"] = xhtml(table)
    path = write_epub(tmp_path / "sample.epub", members)
    out = tmp_path / "preserved.md"
    rendered_table = "| A | B |\n| --- | --- |\n| x | y |\n"
    body = TWO_CHAPTER_BODY.replace("café 東京\n", rendered_table).replace(
        "First e\u0301\u00a0word\u00ad.\n", rendered_table)
    expected = expected_document(path, body, regions=0, tables=2)
    lengths = {name: len(text.encode("utf-8")) for name, text in members.items()}
    limits = {
        "source_bytes": path.stat().st_size, "members": 6,
        "member_bytes": max(lengths.values()), "declared_bytes": sum(lengths.values()),
        "consumed_bytes": sum(lengths.values()) + lengths["OPS/a.xhtml"] + lengths["OPS/b.xhtml"],
        "document_elements": 14, "total_elements": 71, "depth": 6, "spine_items": 2,
        "table_columns": 2, "table_slots": 4, "total_table_slots": 8,
        "markdown_bytes": len(brewdoc.run(path)[2].encode("utf-8")),
    }
    assert tuple(lengths) == ("mimetype", "META-INF/container.xml", "OPS/book.opf",
                             "OPS/a.xhtml", "OPS/b.xhtml", "OPS/nav.xhtml"), (
        "the boundary calculation requires exactly the six declared synthetic members")
    # WHEN the limit equals the consumed amount and then drops by exactly one
    monkeypatch.setitem(epub.LIMITS, budget, limits[budget])
    accepted = rendering_result(brewdoc.run(path))
    monkeypatch.setitem(epub.LIMITS, budget, limits[budget] - 1)
    out.write_bytes(b"existing output\n")
    refused = rendering_result(brewdoc.run(path, out), output=out)
    # THEN the inclusive case succeeds and the one-over case preserves all output bytes
    assert accepted == (0, expected_receipt(path, regions=0, tables=2), expected), (
        "the EPUB %s budget must accept its exact inclusive boundary" % budget)
    assert (refused[0], refused[1]["file_ok"], refused[1]["units"], refused[2], out.read_bytes()) == (
        1, False, 0, "", b"existing output\n",
    ), "the EPUB %s one-over budget must refuse atomically" % budget
    assert re.sub(r"\AEPUB .*limit.*\Z", "LIMIT", refused[1]["reason"]) == "LIMIT", (
        "the one-over refusal must identify its resource limit: " + refused[1]["reason"])
