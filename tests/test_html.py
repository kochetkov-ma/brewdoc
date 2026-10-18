"""Static HTML contracts use synthetic source bytes and independent expected documents."""

import hashlib
import importlib
import json
import os
import platform
import re
import subprocess
import sys

import pytest

from brewdoc import htmltext, service
from test_contract import NOT_APPLICABLE, SCHEMA_TWO, docx, schema_two, zero_tally
from test_epub import BANG_BEFORE_LINK, CODE_SPLIT_IMAGE_AND_BLANK_LINK, INLINE_BOUNDARY_WHITESPACE


HTML_SUPPORTED = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
supported_html = pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
HTML_OMISSIONS = [
    "dynamic browser content, scripts and stylesheets",
    "graphical SVG content, canvas pixels and MathML semantics",
    "active media and embeds",
    "inline code layout outside tables: rendered as fenced blocks",
    "inline code monospace inside table cells",
    "form controls and their values",
    "images without alt text",
    "navigation, header, footer and aside landmarks outside main or article content",
]
CHAPTER = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Untitled"\n\n'


@pytest.fixture
def lexbor_parser():
    """Load the native parser only for supported-runtime parser facts."""
    return importlib.import_module("selectolax.lexbor").LexborHTMLParser


def expected_document(path, content, regions, *, title="Untitled", tables=0):
    """Construct the existing envelope independently around literal expected HTML content."""
    body = (CHAPTER.replace('"Untitled"', '"%s"' % title) + content).rstrip("\n") + "\n"
    source_bytes, body_bytes = path.read_bytes(), body.encode("utf-8")
    return SCHEMA_TWO.format(
        **NOT_APPLICABLE, name="sample.html", suffix=".html", route="html", unit="chapter",
        count=1, keys="chapter/000001", pages=0, sheets=0, chapters=1, tables=tables,
        text_regions=regions, omissions="\n".join("- " + value for value in HTML_OMISSIONS),
        contents='- [Chapter 1: "%s"](#brewdoc-chapter-000001)' % title, body=body,
        source_bytes=len(source_bytes), source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        body_bytes=len(body_bytes), body_sha256=hashlib.sha256(body_bytes).hexdigest())


def expected_refusal(reason):
    """Pin the complete HTML failure receipt for one exact refusal reason."""
    return {
        "file_ok": False, "route": "html", "reason": reason, "source": "sample.html",
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": HTML_OMISSIONS,
    }


def expected_receipt(path, out, regions, *, tables=0):
    """Pin the complete success receipt and all retained-Unicode loss counters."""
    return {
        "file_ok": True, "route": "html",
        "reason": "html rendered: 1 chapters, %d tables, %d text regions, 0 column splits"
                  % (tables, regions),
        "source": path.name, "out": str(out), "receipt_schema": "brewdoc.receipt/1",
        "unit_kind": "chapter", "units": 1, "tables": tables, "text_regions": regions,
        "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": HTML_OMISSIONS, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": ["chapter/000001"],
    }


@supported_html
@pytest.mark.parametrize("source,content,regions", [
    pytest.param('<p>café 東京</p>', "café 東京\n", 1, id="absent-style"),
    pytest.param('<p style>café 東京</p>', "café 東京\n", 1, id="valueless-style"),
    pytest.param('<p style="">café 東京</p>', "café 東京\n", 1, id="empty-style"),
    pytest.param('<p hidden style="">secret</p><p>café 東京</p>', "café 東京\n", 1,
                 id="hidden-before-empty-style"),
    pytest.param('<p aria-hidden="true" style="">secret</p><p>café 東京</p>', "café 東京\n", 1,
                 id="aria-hidden-before-empty-style"),
    pytest.param('<p aria-hidden="false" style="">café 東京</p>', "café 東京\n", 1,
                 id="aria-false-visible"),
    pytest.param('<script>secret</script><p>café 東京</p>', "café 東京\n", 1,
                 id="excluded-tag-before-absent-style"),
])
def test_empty_style_guards_preserve_visibility_utf8_and_complete_receipt(
        tmp_path, source, content, regions):
    # GIVEN Unicode paragraphs with absent or empty styles and explicit hiding guards
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(source.encode("utf-8"))
    expected = expected_document(path, content, regions)
    assert out.exists() is False, "visibility output must start absent"
    # WHEN the service renders the document and publishes its UTF-8 output
    result = service.run(path, out)
    # THEN hiding guards dominate the no-style path without altering Unicode or counters
    assert (result, out.read_bytes()) == (
        (0, expected_receipt(path, out, regions), expected), expected.encode("utf-8"),
    ), "empty styles must retain exact visible content, full receipt and UTF-8 file bytes"


@supported_html
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p>one<div>two</div>three",
                     '<body><p>one</p><div>two</div>three</body>', id="recovered-block-boundary"),
        pytest.param("<table><tr><td>A<td>B</table>",
                     '<body><table><tbody><tr><td>A</td><td>B</td></tr></tbody></table></body>',
                     id="implied-table-body-and-cells"),
        pytest.param("<section><p>café &amp; 東京</p></section><aside>tail</aside>",
                     '<body><section><p>café &amp; 東京</p></section><aside>tail</aside></body>',
                     id="fragment-unicode-and-entity"),
    ],
)
def test_lexbor_recovers_exact_fragment_and_malformed_body(source, expected, lexbor_parser):
    # GIVEN synthetic malformed or fragment HTML and its literal recovered tree
    assert (lexbor_parser.__module__, lexbor_parser.__qualname__) == ("selectolax.lexbor", "LexborHTMLParser"), (
        "the fixture must supply the selected native Lexbor parser")
    # WHEN the selected native Lexbor parser recovers the document
    tree = lexbor_parser(source)
    # THEN implied nodes, boundaries, Unicode and decoded entities have the exact tree shape
    assert tree.body.html == expected, "Lexbor must retain the accepted recovery and Unicode facts"


@supported_html
@pytest.mark.parametrize(
    ("source", "content", "regions", "title"),
    [
        pytest.param('<title>Sample</title><p>café &amp; 東京</p>',
                     "café & 東京\n", 1, "Sample", id="unicode-and-utf8-body-hash"),
        pytest.param('<title>A — B</title><p>Body</p>',
                     "Body\n", 1, "A — B", id="title-unicode-kept-literal"),
        pytest.param('<title>Say "hi" &amp; *go* [x] snake_case</title><p>Body</p>',
                     "Body\n", 1, 'Say &quot;hi&quot; & \\*go\\* \\[x\\] snake_case',
                     id="title-quoted-escaping"),
        pytest.param('<title> </title><title>Sample</title><nav><p>Nav</p></nav>'
                     '<main><p>Main</p></main><aside><p>Aside</p></aside><footer><p>Footer</p></footer>',
                     "Main\n", 1, "Sample", id="landmark-tags-outside-main-dropped"),
        pytest.param('<div role="navigation"><p>N</p></div><div role="Banner top"><p>B</p></div>'
                     '<div role="complementary"><p>C</p></div><div role="contentinfo"><p>I</p></div>'
                     '<p>Body</p>', "Body\n", 1, "Untitled", id="landmark-aria-roles-dropped"),
        pytest.param('<header><h1>Site</h1></header><p>Body</p><footer><p>Copyright</p></footer>',
                     "### Site\n\nBody\n", 2, "Untitled", id="page-header-with-h1-kept-footer-dropped"),
        pytest.param('<article><header><p>By</p></header><nav><p>TOC</p></nav><p>Text</p></article>',
                     "By\n\nTOC\n\nText\n", 3, "Untitled", id="landmark-kept-inside-article"),
        pytest.param('<main><aside><p>Note</p></aside><footer><p>Foot</p></footer><p>Text</p></main>',
                     "Note\n\nFoot\n\nText\n", 3, "Untitled", id="landmark-kept-inside-main"),
        pytest.param('<div role="Main app"><nav><p>Local</p></nav></div><nav><p>Global</p></nav>',
                     "Local\n", 1, "Untitled", id="landmark-kept-inside-role-main"),
        pytest.param('<nav><table><tr><td>X</td></tr></table></nav><p>Body</p>',
                     "Body\n", 1, "Untitled", id="landmark-table-in-nav-not-counted"),
        pytest.param('<h1>One</h1><h2>Two</h2><h3>Three</h3><h4>Four</h4><h5>Five</h5><h6>Six</h6>',
                     '### One\n\n#### Two\n\n##### Three\n\n###### Four\n\n'
                     '**Heading 5: Five**\n\n**Heading 6: Six**\n', 6, "Untitled", id="heading-levels"),
        pytest.param('<h1>Issue #</h1><h2>##</h2><h3>C#</h3><h4>Tag ## x</h4>',
                     '### Issue \\#\n\n#### \\##\n\n##### C#\n\n###### Tag ## x\n', 4, "Untitled",
                     id="heading-atx-closing-run-escaped"),
        pytest.param('<ul><li>Outer<ol start="3"><li>Third</li><li>Fourth</li></ol></li></ul>',
                     '- Outer\n\n  3. Third\n  4. Fourth\n', 3, "Untitled", id="list-nested-order-offset"),
        pytest.param('<ol><li>A</li><li value="5">B</li><li>C</li></ol>',
                     '1. A\n5. B\n6. C\n', 3, "Untitled", id="list-item-value-renumbers-rest"),
        pytest.param('<ol reversed><li>A</li><li>B</li><li>C</li></ol>',
                     '3. A\n2. B\n1. C\n', 3, "Untitled", id="list-reversed-counts-down-from-items"),
        pytest.param('<ol reversed start="10"><li>A</li><li>B</li></ol>',
                     '10. A\n9. B\n', 2, "Untitled", id="list-reversed-counts-down-from-start"),
        pytest.param('<ol reversed><li>A</li><li value="7">B</li><li>C</li></ol>',
                     '3. A\n7. B\n6. C\n', 3, "Untitled", id="list-reversed-value-counts-down"),
        pytest.param('<ol reversed><li>A</li><li hidden>H</li><li>B</li></ol>',
                     '2. A\n1. B\n', 2, "Untitled", id="list-reversed-skips-hidden-items"),
        pytest.param('<ol start="-1"><li>A</li><li>B</li></ol>',
                     '0. A\n0. B\n', 2, "Untitled", id="list-negative-start-clamps-to-zero"),
        pytest.param('<ol reversed start="1"><li>A</li><li>B</li><li>C</li></ol>',
                     '1. A\n0. B\n0. C\n', 3, "Untitled", id="list-reversed-below-zero-clamps"),
        pytest.param('<ol><li value="1234567890">A</li><li>B</li></ol>',
                     '999999999. A\n999999999. B\n', 2, "Untitled", id="list-huge-value-clamps-to-nine-digits"),
        pytest.param('<ul><li>a</li><li>b<ul><li>c</li></ul></li><li>d</li></ul>',
                     '- a\n- b\n  - c\n- d\n', 4, "Untitled", id="list-nested-tight"),
        pytest.param('<ul><li>- dash</li><li>1. one</li></ul>',
                     '- \\- dash\n- 1\\. one\n', 2, "Untitled", id="source-item-markers-escaped"),
        pytest.param('<blockquote><p>First<br>Second</p></blockquote>',
                     '> First\n> Second\n', 1, "Untitled", id="quote-and-break"),
        pytest.param('<blockquote><p>Spring, <br> Or the bees \t<br>end</p></blockquote>'
                     '<p>One <br>Two</p>',
                     '> Spring,\n> Or the bees\n> end\n\nOne\nTwo\n', 2, "Untitled",
                     id="line-breaks-leave-no-trailing-blanks"),
        pytest.param('<blockquote><p>q</p><blockquote><p>inner</p></blockquote></blockquote>',
                     '> q\n>\n> > inner\n', 2, "Untitled", id="quote-nested-prefix-stacks"),
        pytest.param('<blockquote><ul><li>a</li><li>b</li></ul></blockquote>',
                     '> - a\n> - b\n', 2, "Untitled", id="quote-holding-list"),
        pytest.param('<blockquote><pre>x\n\ny</pre></blockquote>',
                     '> ```\n> x\n>\n> y\n> ```\n', 1, "Untitled", id="quote-fence-blank-line"),
        pytest.param('<blockquote><p>&gt; not nested</p></blockquote>',
                     '> \\> not nested\n', 1, "Untitled", id="quote-source-marker-escaped"),
        pytest.param('<p>plain <em>soft</em> and <strong>bold</strong></p>',
                     'plain *soft* and **bold**\n', 1, "Untitled", id="inline-emphasis"),
        pytest.param('<p>Before<code>&lt;x&gt; &amp; café\t  ```</code>After</p>',
                     'Before\n\n````\n<x> & café\t  ```\n````\n\nAfter\n',
                     3, "Untitled", id="inline-code-fenced-position"),
        pytest.param('<pre>first\n\t&lt;x&gt;  &amp; 東京\n````\nlast</pre>',
                     '`````\nfirst\n\t<x>  & 東京\n````\nlast\n`````\n',
                     1, "Untitled", id="pre-literal-whitespace-and-fence-growth"),
        pytest.param('<p id="brewdoc-metadata"># Header &lt;a id="forged"&gt; [x](bad)</p>',
                     '\\# Header &lt;a id="forged"> \\[x\\](bad)\n',
                     1, "Untitled", id="source-syntax-cannot-forge-markdown"),
        pytest.param('<p hidden="false">Hidden</p><p aria-hidden="TRUE">Aria</p>'
                     '<section hidden><p>Ancestor</p></section><script>Script</script>'
                     '<style>Style</style><template>Template</template><object>Object</object>'
                     '<p style="display:none;display:block">Last wins</p>'
                     '<p style="display:none!important;display:block">Important hides</p>'
                     '<p style="VISIBILITY: hidden; visibility:visible!important">Visible wins</p>'
                     '<p style="display:none;display:unknown">Unsupported stays hidden</p>',
                     'Last wins\n\nVisible wins\n', 2, "Untitled", id="excluded-subtrees-and-css-precedence"),
        pytest.param('<p><a href="/safe">Safe</a> <a href="doc">Relative</a> '
                     '<a href="#part">Fragment</a> <a href="mailto:a@example.test">Mail</a> '
                     '<a href="java&#x09;script:evil">Unsafe</a> <a href="data:text/plain,x">Data</a></p>',
                     '[Safe](/safe) [Relative](doc) [Fragment](#part) [Mail](mailto:a@example.test) '
                     'Unsafe Data\n', 1, "Untitled", id="link-scheme-policy"),
        pytest.param('<img src="pic" alt="A &amp; B"><img src="data:image/png;base64,secret" alt="Data">',
                     '![A & B](pic)\n\n![Data]()\n', 2, "Untitled", id="image-alt-link-and-data-target"),
        pytest.param('<img src="x.png"><img alt=" " src="y.png"><p>T</p>',
                     'T\n', 1, "Untitled", id="image-missing-or-blank-alt-dropped"),
        pytest.param('<img alt="  a \n *b* [c]  " src="https://example.test/a b.png">'
                     '<img src="javascript:x" alt="J"><img alt="M">',
                     '![a \\*b\\* \\[c\\]](https://example.test/a%20b.png)\n\n![J]()\n\n![M]()\n',
                     3, "Untitled", id="image-alt-collapsed-escaped-and-unsafe-target-empty"),
        pytest.param('<p>See <img src="p" alt="I"> here</p>',
                     'See\n\n![I](p)\n\nhere\n', 3, "Untitled", id="image-inside-paragraph-own-block"),
    ],
)
def test_static_html_renders_exact_document_receipt_and_output_bytes(
    tmp_path, source, content, regions, title,
):
    # GIVEN a synthetic HTML document and independent complete output expectations
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "successful output must start absent"
    expected = expected_document(path, content, regions, title=title)
    # WHEN the public service converts the source to a file and returned Markdown
    rc, receipt, markdown = service.run(path, out)
    # THEN all content, metadata, loss accounting and receipt fields match exactly
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, regions), expected), (
        "static HTML must match its complete schema envelope, body and receipt")
    assert out.read_bytes() == expected.encode("utf-8"), "file bytes must equal returned UTF-8 Markdown"


@supported_html
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param('<p>café 東京</p>'.encode("utf-8"), id="strict-utf8-without-declaration"),
        pytest.param(b'\xef\xbb\xbf' + '<p>café 東京</p>'.encode("utf-8"), id="optional-utf8-bom"),
        pytest.param('<meta charset="UTF8"><p>café 東京</p>'.encode("utf-8"), id="utf8-alias"),
        pytest.param('<meta HTTP-EQUIV="Content-Type" content="text/html; charset=UTF-8">'
                     '<p>café 東京</p>'.encode("utf-8"), id="http-equiv-utf8"),
        pytest.param('<meta charset="utf-8"><meta charset="UTF8"><p>café 東京</p>'.encode("utf-8"),
                     id="consistent-declarations"),
    ],
)
def test_utf8_preflight_retains_raw_source_hash_and_unicode_bytes(tmp_path, payload):
    # GIVEN UTF-8 source bytes with an optional BOM or accepted declaration
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(payload)
    assert path.read_bytes() == payload, "source bytes including any BOM must remain unchanged"
    expected = expected_document(path, "café 東京\n", 1)
    # WHEN HTML passes decoding preflight and rendering
    rc, receipt, markdown = service.run(path, out)
    # THEN raw source hashes and UTF-8 body hashes describe their distinct exact bytes
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "accepted UTF-8 source must preserve Unicode and hash unmodified source bytes")
    assert out.read_bytes() == expected.encode("utf-8"), "HTML output must be UTF-8 without a BOM"


CHARSET_REFUSED = "HTML charset declaration refused"
WINDOW_END = b'<meta charset="latin1">'


@supported_html
@pytest.mark.parametrize(("payload", "content", "title"), [
    pytest.param(b'\xef\xbb\xbf' + '<meta charset="windows-1251"><p>café 東京</p>'.encode("utf-8"),
                 "café 東京\n", "Untitled", id="charset-utf8-bom-beats-meta"),
    pytest.param(b'\xff\xfe' + '<meta charset="koi8-r"><p>café 東京</p>'.encode("utf-16-le"),
                 "café 東京\n", "Untitled", id="charset-utf16le-bom"),
    pytest.param(b'\xfe\xff' + '<p>café 東京</p>'.encode("utf-16-be"),
                 "café 東京\n", "Untitled", id="charset-utf16be-bom"),
    pytest.param('<meta charset=" KOI8-R "><title>Новости</title><p>Привет</p>'.encode("koi8_r"),
                 "Привет\n", "Новости", id="charset-koi8-r-trimmed-case-folded-label"),
    pytest.param('<meta http-equiv="Content-Type" content="text/html; charset=\'Windows-1251\'">'
                 '<p>Привет, мир</p>'.encode("cp1251"),
                 "Привет, мир\n", "Untitled", id="charset-windows-1251-quoted-http-equiv"),
    pytest.param('<meta charset="koi8-r" http-equiv="content-type" content="text/html; charset=utf-8">'
                 '<p>Привет</p>'.encode("koi8_r"),
                 "Привет\n", "Untitled", id="charset-attribute-beats-content-in-one-meta"),
    pytest.param(b'<meta charset="windows-1252"><p>caf\xe9</p>', "café\n", "Untitled",
                 id="charset-windows-1252-meta"),
    pytest.param(b'<meta charset="latin1"><p>\x80 caf\xe9</p>', "€ café\n", "Untitled",
                 id="charset-latin1-alias-is-windows-1252"),
    pytest.param(b'<meta http-equiv="content-type" content="text/html; charset=ascii"><p>\x93q\x94</p>',
                 "“q”\n", "Untitled", id="charset-ascii-alias-is-windows-1252"),
    pytest.param(b'<meta charset="gbk"><p>' + "中文 😀".encode("gb18030") + b'</p>',
                 "中文 😀\n", "Untitled", id="charset-gbk-label-decodes-as-gb18030"),
    pytest.param(b'<meta charset="windows-1252"><p>a\x81b</p>', "a&#129;b\n", "Untitled",
                 id="charset-undefined-c1-byte-maps-to-u0080-range"),
    pytest.param(b'<meta charset="iso-8859-8"><p>a\xa1b \xe0</p>', "a�b א\n", "Untitled",
                 id="charset-undefined-high-byte-maps-to-replacement"),
    pytest.param('<meta charset="utf-8"><meta charset="latin1"><p>café</p>'.encode("utf-8"),
                 "café\n", "Untitled", id="charset-first-declaring-meta-wins"),
    pytest.param('<meta charset="utf-16"><p>café 東京</p>'.encode("utf-8"),
                 "café 東京\n", "Untitled", id="charset-utf16-meta-means-utf8"),
    pytest.param(b' ' * (1024 - len(WINDOW_END)) + WINDOW_END + b'<p>caf\xe9</p>', "café\n", "Untitled",
                 id="charset-meta-ending-at-byte-1024-applies"),
    pytest.param(b'<meta charset="gbk"><p>\x80 \x81\x80</p>', "€ 亐\n", "Untitled",
                 id="charset-gbk-lead-0x80-is-euro-trail-0x80-kept"),
    pytest.param(b'<meta charset="gb2312"><p>a\x80b</p>', "a€b\n", "Untitled",
                 id="charset-gb2312-label-0x80-is-euro"),
    pytest.param(b'<meta charset="gb18030"><p>\x80 \x81\x80</p>', "€ 亐\n", "Untitled",
                 id="charset-gb18030-lead-0x80-is-euro-trail-0x80-kept"),
    pytest.param(b'<meta charset="gbk"><title>T</title><p>Price \x805', "Price €5\n", "T",
                 id="charset-gbk-euro-then-digit-at-end-of-input"),
    pytest.param(b'<meta charset="gb18030"><title>T</title><p>Price \x800A', "Price €0A\n", "T",
                 id="charset-gb18030-euro-then-digit-at-end-of-input"),
    pytest.param(b'<meta charset="gb18030"><p>\x815\xf47 \xa8\xbc</p>', "\ue7c7 \u1e3f\n", "Untitled",
                 id="charset-gb18030-four-byte-8135f437-is-whatwg-e7c7"),
])
def test_legacy_charsets_decode_local_html_with_raw_source_hash(tmp_path, payload, content, title):
    # GIVEN legacy-encoded or BOM-marked source bytes and literal expected Unicode text
    path, out = tmp_path / "sample.html", tmp_path / "charset.md"
    path.write_bytes(payload)
    assert path.read_bytes() == payload, "the source bytes must reach decoding unchanged"
    expected = expected_document(path, content, 1, title=title)
    # WHEN local HTML decoding applies BOM, then first meta, then UTF-8 precedence
    rc, receipt, markdown = service.run(path, out)
    # THEN text converts to exact Unicode while the source hash stays on the raw bytes
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "declared legacy charsets must decode to exact Unicode Markdown and a complete receipt")
    assert out.read_bytes() == expected.encode("utf-8"), "charset file and API bytes must match"


@supported_html
@pytest.mark.parametrize(("payload", "reason"), [
    pytest.param(b'<p>bad \xff</p>', "HTML UTF-8 decoding failed: 'utf-8' codec can't decode byte 0xff "
                 "in position 7: invalid start byte", id="invalid-utf8-without-declaration"),
    pytest.param(b'<meta charset="unknown"><p>text</p>', CHARSET_REFUSED, id="charset-unknown-label"),
    pytest.param(b'<meta charset=""><p>text</p>', CHARSET_REFUSED, id="charset-empty-label"),
    pytest.param(b'<meta charset="  "><p>text</p>', CHARSET_REFUSED, id="charset-whitespace-label"),
    pytest.param(b'<meta charset><p>text</p>', CHARSET_REFUSED, id="charset-valueless-attribute"),
    pytest.param(b'<meta http-equiv="content-type" content="text/html; charset"><p>Kept</p>',
                 CHARSET_REFUSED, id="charset-http-equiv-without-value"),
    pytest.param(b'<meta http-equiv="content-type" content="text/html; charset="><p>Kept</p>',
                 CHARSET_REFUSED, id="charset-http-equiv-empty-value"),
    pytest.param(b'<meta charset="iso-2022-kr"><p>text</p>', CHARSET_REFUSED, id="charset-replacement-label"),
    pytest.param(b'<meta charset="x-user-defined"><p>text</p>', CHARSET_REFUSED, id="charset-x-user-defined"),
    pytest.param(b'<meta charset="rot13"><p>text</p>', CHARSET_REFUSED, id="charset-python-only-codec-name"),
    pytest.param(b'<meta charset="shift_jis"><p>\x81</p>', "HTML Shift_JIS encoding refused: invalid bytes",
                 id="charset-multibyte-invalid-bytes"),
    pytest.param(b'\xff\xfe' + '<p>x</p>'.encode("utf-16-le") + b'\x00',
                 "HTML UTF-16LE encoding refused: invalid bytes", id="charset-utf16le-truncated"),
    pytest.param(b' ' * (1025 - len(WINDOW_END)) + WINDOW_END + b'<p>caf\xe9</p>',
                 "HTML UTF-8 decoding failed: 'utf-8' codec can't decode byte 0xe9 in position 1031: "
                 "invalid continuation byte", id="charset-meta-ending-at-byte-1025-ignored"),
])
def test_charset_refusals_keep_exact_reason_and_atomic_failure_receipt(tmp_path, payload, reason):
    # GIVEN undecodable bytes or an unusable label and an existing output that must survive
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(payload)
    out.write_bytes(b"keep existing output\n")
    assert out.read_bytes() == b"keep existing output\n", "the atomicity sentinel must exist first"
    # WHEN decoding or label resolution refuses HTML
    result = service.run(path, out)
    # THEN the designed reason, zero counts and the untouched destination are exact
    assert (result, out.read_bytes()) == ((1, expected_refusal(reason), ""), b"keep existing output\n"), (
        "charset refusals must carry the designed reason and the complete atomic failure envelope")


@supported_html
@pytest.mark.parametrize(
    ("source", "content", "regions", "tables"),
    [
        pytest.param('<blockquote><p>Outer</p><blockquote><pre>&lt;x&gt;\n\t東京</pre>'
                     '<h2>Inner</h2></blockquote><p>Tail</p></blockquote><p>Outside</p>',
                     '> Outer\n>\n> > ```\n> > <x>\n> > \t東京\n> > ```\n> >\n> > #### Inner\n>\n'
                     '> Tail\n\nOutside\n', 5, 0, id="quote-nested-code-and-child-order"),
        pytest.param('<blockquote></blockquote><blockquote><p>First</p></blockquote>'
                     '<blockquote><p>Second</p></blockquote>',
                     '> First\n\n> Second\n', 2, 0, id="quote-adjacent-and-empty-container"),
        pytest.param('<p>Before</p><table><caption>Measurements &amp; 東京</caption>'
                     '<thead><tr><th rowspan="2">Kind</th><th colspan="2">Values</th></tr>'
                     '<tr><th>Low</th><th>High</th></tr></thead>'
                     '<tbody><tr><td>A</td><td>1</td><td>2</td></tr></tbody>'
                     '<tfoot><tr><td colspan="2">Total</td><td>3</td></tr></tfoot></table><p>After</p>',
                     'Before\n\nCaption: Measurements & 東京\n\n'
                     '| Kind | Values |  |\n| --- | --- | --- |\n|  | Low | High |\n'
                     '| A | 1 | 2 |\n| Total |  | 3 |\n\nAfter\n', 3, 1,
                     id="caption-section-order-and-expanded-spans"),
        pytest.param('<table><tr><td>Before<table><caption>Nested</caption>'
                     '<tr><th colspan="2">Inner</th></tr><tr><td>A</td><td>B</td></tr>'
                     '</table>After</td><td>Peer</td></tr></table>',
                     'Before\n\nCaption: Nested\n\n| Inner |  |\n| --- | --- |\n| A | B |\n\n'
                     'After\n\nPeer\n', 4, 1, id="layout-table-nested-data-table-renders-as-table"),
        pytest.param('<table><tr><td><code>&lt;x&gt; &amp; 東京\n\t  tail | &amp;#10;</code></td>'
                     '<td>Peer</td></tr></table>',
                     '| Code: text="&lt;x> & 東京&#10;&#9;  tail \\| &amp;#10;" | Peer |\n'
                     '| --- | --- |\n', 0, 1, id="cell-code-literal-data-without-monospace"),
    ],
)
def test_quote_and_table_structures_preserve_full_data_order_and_accounting(
    tmp_path, source, content, regions, tables,
):
    # GIVEN synthetic structured HTML with exact quotation and table expectations
    path, out = tmp_path / "sample.html", tmp_path / "structured.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "structured output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN the existing service renders the source in recovered DOM order
    rc, receipt, markdown = service.run(path, out)
    # THEN structures and counters describe every source datum once in its original position
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "quotation boundaries and table records must retain complete data and exact accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "structured file and API bytes must match"


@supported_html
@pytest.mark.parametrize(("source", "content", "regions"), [
    pytest.param('<form><label for="e">Email</label><input id="e" value="x"><button>Send</button>'
                 '<select><option>A</option></select><textarea>t</textarea></form>',
                 'Email\n', 1, id="control-form-keeps-label-prose-only"),
    pytest.param('<form action="javascript:go" method="" id="f" name="form"><p>Kept</p></form>',
                 'Kept\n', 1, id="control-form-attributes-not-recorded"),
    pytest.param('<fieldset id="fs" name="group" disabled="false"><legend>Legend</legend>'
                 '<label id="label" for="n">Name</label><input name="n" value=""></fieldset>',
                 'Legend\n\nName\n', 2, id="control-fieldset-legend-label-transparent"),
    pytest.param('<label>Plain</label><label for="">Empty target</label>',
                 'Plain\n\nEmpty target\n', 2, id="control-labels-are-plain-prose"),
    pytest.param('<button aria-label="Aria" title="Tip" type="submit" formaction="javascript:go" '
                 'formtarget="_blank">Visible <span>child</span></button><p>Kept</p>',
                 'Kept\n', 1, id="control-button-subtree-dropped"),
    pytest.param('<div role="button">Open menu</div>', 'Open menu\n', 1, id="control-role-button-is-prose"),
    pytest.param('<input><input type="number" value="café &amp; 東京" min="1"><input type="checkbox" '
                 'checked><input type="radio" name="r"><input type="hidden" value="hidden-secret">'
                 '<input type="image" alt="Send" src="pic"><p>Kept</p>',
                 'Kept\n', 1, id="control-inputs-of-every-type-dropped"),
    pytest.param('<input type="button" value="Go"><input type="submit" value="Go">'
                 '<input type="reset" value="Go"><p>Kept</p>', 'Kept\n', 1, id="control-input-buttons-dropped"),
    pytest.param('<input type="password" value="secret"><input type="FILE" value="private">'
                 '<input type="PASSWORD" role="button" aria-label="Choose" value="protected"><p>Kept</p>',
                 'Kept\n', 1, id="control-sensitive-inputs-dropped-whatever-role"),
    pytest.param('<textarea id="t" placeholder="Hint">first\n\t  東京</textarea>'
                 '<output id="o" for="a b">first\nsecond</output><p>Kept</p>',
                 'Kept\n', 1, id="control-textarea-output-dropped"),
    pytest.param('<select id="s" multiple><option value="a">A</option><optgroup label="Group">'
                 '<option>B</option></optgroup></select><datalist id="d"><option value="a">C</option>'
                 '</datalist><p>Kept</p>', 'Kept\n', 1, id="control-select-options-and-suggestions-dropped"),
    pytest.param('<progress value="1" max="10">Progress fallback</progress>'
                 '<meter value="2" min="0" max="9">Meter fallback</meter><p>Kept</p>',
                 'Kept\n', 1, id="control-progress-meter-fallback-dropped"),
    pytest.param('<details id=""><summary>Closed summary</summary><p>Closed content</p></details>'
                 '<details open="false"><summary>Open summary</summary><p>Open content</p></details>',
                 'Closed summary\n\nClosed content\n\nOpen summary\n\nOpen content\n',
                 4, id="control-details-summary-transparent"),
    pytest.param('<ul><li>Before<input value="x"> After</li></ul>',
                 '- Before After\n', 1, id="control-inside-item-dropped-inline"),
])
def test_form_controls_drop_while_transparent_containers_keep_prose(tmp_path, source, content, regions):
    # GIVEN form controls, their values and transparent form containers in DOM order
    path, out = tmp_path / "sample.html", tmp_path / "controls.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "control output must start absent"
    expected = expected_document(path, content, regions)
    # WHEN static HTML renders with the clean default profile
    rc, receipt, markdown = service.run(path, out)
    # THEN control subtrees leave no record or value and container text stays plain prose
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, regions), expected), (
        "controls must drop whole subtrees while form, label, legend and details text remain prose")
    assert out.read_bytes() == expected.encode("utf-8"), "control file and API bytes must match"


def input_limit_source(size):
    """Pad an excluded comment to an exact raw byte size without enlarging output."""
    frame = b'<p>Kept</p><!--' + b'-->'
    return b'<p>Kept</p><!--' + b'x' * (size - len(frame)) + b'-->'


def depth_limit_source(depth):
    """Count recovered body as depth one and the included paragraph as the leaf."""
    return ("<div>" * (depth - 2) + "<p>Kept</p>" + "</div>" * (depth - 2)).encode("ascii")


def element_limit_source(count):
    """Include html/head/body, a hidden container, hidden spans and one paragraph."""
    return ('<div hidden>' + '<span></span>' * (count - 5) + '</div><p>Kept</p>').encode("ascii")


def attribute_limit_source(size):
    """Use decoded UTF-8 attribute bytes in an excluded subtree."""
    value = "é" * (size // 2) + "x" * (size % 2)
    return ('<div hidden data-value="' + value + '"></div><p>Kept</p>').encode("utf-8")


def table_limit_source(cells):
    """Amplify one origin cell into a wide grid while keeping input tiny."""
    return ('<table><tr><td colspan="%d">X</td></tr></table>' % cells).encode("ascii")


def expanded_table_content(cells):
    """Specify the entire expected grid independently of adapter expansion."""
    return ('| ' + ' | '.join(['X'] + [''] * (cells - 1)) + ' |\n| '
            + ' | '.join(['---'] * cells) + ' |\n')


ACTUAL_LIMITS = [
    pytest.param(input_limit_source, 8 * 1048576, "Kept\n", 1, 0, id="raw-input-8-mib"),
    pytest.param(depth_limit_source, 256, "Kept\n", 1, 0, id="recovered-depth-256"),
    pytest.param(element_limit_source, 200000, "Kept\n", 1, 0, id="all-elements-200000"),
    pytest.param(attribute_limit_source, 1048576, "Kept\n", 1, 0, id="decoded-attribute-1-mib"),
]


@supported_html
@pytest.mark.parametrize(("build", "limit", "content", "regions", "tables"), ACTUAL_LIMITS)
@pytest.mark.parametrize("delta", [-1, 0], ids=["below", "at"])
def test_real_resource_limits_accept_values_below_and_at_the_boundary(
    tmp_path, build, limit, content, regions, tables, delta,
):
    # GIVEN a synthetic document at a practical real default resource boundary
    path, out = tmp_path / "sample.html", tmp_path / "limited.md"
    source = build(limit + delta)
    path.write_bytes(source)
    assert path.read_bytes() == source, "the boundary source must reach the parser unchanged"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN HTML enforces the declared inclusive default limit
    rc, receipt, markdown = service.run(path, out)
    # THEN the complete output remains exact at and below the limit
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "inclusive real resource boundaries must retain valid HTML and exact output"
    assert out.read_bytes() == expected.encode("utf-8"), "accepted boundary bytes must match the API"


def assert_resource_refusal(path, out, result):
    """Check a whole failure envelope and untouched destination without accepting parser errors."""
    rc, receipt, markdown = result
    assert (rc, receipt, markdown, out.read_bytes()) == (1, {
        "file_ok": False, "route": "html", "reason": receipt["reason"], "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": HTML_OMISSIONS,
    }, "", b"keep destination\n"), "resource refusal must leave zero counts, empty Markdown and intact output"
    assert re.search(r"(?i)(limit|exceed|too (?:large|deep|many))", receipt["reason"]) is not None, (
        "resource refusal must identify a limit rather than an incidental parser exception")


@supported_html
@pytest.mark.parametrize("build,limit", [
    pytest.param(input_limit_source, 8 * 1048576, id="raw-input"),
    pytest.param(depth_limit_source, 256, id="recovered-depth"),
    pytest.param(element_limit_source, 200000, id="excluded-elements-count"),
    pytest.param(attribute_limit_source, 1048576, id="excluded-attribute-count"),
    pytest.param(table_limit_source, 100000, id="span-amplification"),
])
def test_real_resource_limits_refuse_one_over_atomically(tmp_path, build, limit):
    # GIVEN one over the real resource limit and an existing destination
    path, out = tmp_path / "sample.html", tmp_path / "limited.md"
    path.write_bytes(build(limit + 1))
    out.write_bytes(b"keep destination\n")
    assert out.read_bytes() == b"keep destination\n", "the refusal sentinel must exist before rendering"
    # WHEN the resource boundary rejects conversion before output staging
    result = service.run(path, out)
    # THEN the registered HTML failure envelope is atomic and explicitly identifies the limit
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("cells,size", [(99999, 50), (100000, 51)], ids=["below", "at"])
def test_real_table_span_limit_accepts_exact_expanded_grid(tmp_path, cells, size):
    # GIVEN one origin spanning a complete near-limit rectangular grid
    path, out = tmp_path / "sample.html", tmp_path / "table.md"
    path.write_bytes(table_limit_source(cells))
    assert len(path.read_bytes()) == size, "span amplification must start from a tiny source"
    expected = expected_document(path, expanded_table_content(cells), 0, tables=1)
    # WHEN the real 100000 expanded-cell limit is enforced inclusively
    rc, receipt, markdown = service.run(path, out)
    # THEN every continuation remains empty and the full emitted table is deterministic
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "the actual table boundary must retain the whole span grid without truncation")
    assert out.read_bytes() == expected.encode("utf-8"), "large grid file and API bytes must agree"


@supported_html
@pytest.mark.parametrize("delta", [-1, 0], ids=["below", "at"])
def test_scaled_visible_text_limit_counts_utf8_before_whitespace_reduction(tmp_path, monkeypatch, delta):
    # GIVEN 63 or 64 decoded UTF-8 bytes, including whitespace that rendering reduces
    monkeypatch.setattr(htmltext, "_VISIBLE_TEXT_BYTE_LIMIT", 64, raising=False)
    text = "é" * 16 + " " * (32 + delta)
    assert len(text.encode("utf-8")) == 64 + delta, "the scaled limit measures decoded UTF-8 bytes"
    path, out = tmp_path / "sample.html", tmp_path / "visible.md"
    path.write_bytes(("<p>" + text + "</p>").encode("utf-8"))
    expected = expected_document(path, "é" * 16 + "\n", 1)
    # WHEN included source text reaches the scaled inclusive limit before normalization
    rc, receipt, markdown = service.run(path, out)
    # THEN normalizing whitespace does not invalidate acceptable source byte totals
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "scaled visible-text boundaries must use decoded bytes before whitespace reduction")


@supported_html
def test_scaled_visible_text_limit_refuses_one_over_before_reduction(tmp_path, monkeypatch):
    # GIVEN 65 included text bytes whose reduced rendered text would fit the scaled limit
    monkeypatch.setattr(htmltext, "_VISIBLE_TEXT_BYTE_LIMIT", 64, raising=False)
    text = "é" * 16 + " " * 33
    assert len(text.encode("utf-8")) == 65, "the source must exceed 64 decoded UTF-8 bytes exactly once"
    path, out = tmp_path / "sample.html", tmp_path / "visible.md"
    path.write_bytes(("<p>" + text + "</p>").encode("utf-8"))
    out.write_bytes(b"keep destination\n")
    # WHEN accounting precedes whitespace normalization and output assembly
    result = service.run(path, out)
    # THEN the oversized source text is refused even though its reduced text is short
    assert_resource_refusal(path, out, result)


@supported_html
def test_dropped_landmark_text_still_counts_toward_the_visible_text_limit(tmp_path, monkeypatch):
    # GIVEN 65 visible text bytes where all but the main paragraph sit in a dropped nav
    monkeypatch.setattr(htmltext, "_VISIBLE_TEXT_BYTE_LIMIT", 64, raising=False)
    source = "<nav><p>" + "n" * 61 + "</p></nav><main><p>Main</p></main>"
    assert len(("n" * 61 + "Main").encode("utf-8")) == 65, "nav plus main text must exceed 64 bytes by one"
    path, out = tmp_path / "sample.html", tmp_path / "visible.md"
    path.write_bytes(source.encode("utf-8"))
    out.write_bytes(b"keep destination\n")
    # WHEN rendering drops the landmark but limits still cover the whole body
    result = service.run(path, out)
    # THEN the oversized source is refused even though rendered text would fit
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("slack", [1, 0], ids=["below", "at"])
def test_scaled_markdown_limit_counts_complete_utf8_envelope(tmp_path, monkeypatch, slack):
    # GIVEN an independently calculated complete UTF-8 document byte length
    path, out = tmp_path / "sample.html", tmp_path / "markdown.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    limit = len(expected.encode("utf-8")) + slack
    monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", limit, raising=False)
    assert len(expected.encode("utf-8")) == limit - slack, "the scaled bound must include the whole envelope"
    # WHEN final Markdown is checked at or below its exact complete UTF-8 byte limit
    rc, receipt, markdown = service.run(path, out)
    # THEN output includes metadata and all framing bytes without false refusal
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "the Markdown limit must use the complete UTF-8 envelope rather than characters or body alone")


@supported_html
def test_scaled_markdown_limit_refuses_one_over_complete_envelope(tmp_path, monkeypatch):
    # GIVEN a complete UTF-8 Markdown document one byte over its independently chosen limit
    path, out = tmp_path / "sample.html", tmp_path / "markdown.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", len(expected.encode("utf-8")) - 1, raising=False)
    out.write_bytes(b"keep destination\n")
    assert len(expected.encode("utf-8")) - len(expected) == 5, "Unicode must distinguish characters from bytes"
    # WHEN final byte accounting includes both body and schema envelope
    result = service.run(path, out)
    # THEN the oversized complete document is refused before destination publication
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("nested_cells,total,content", [
    (2, 5, '| X |  |\n| --- | --- |\n'), (3, 6, '| X |  |  |\n| --- | --- | --- |\n'),
], ids=["below-total", "at-total"])
def test_scaled_nested_span_budget_sums_parent_and_nested_grids(
        tmp_path, monkeypatch, nested_cells, total, content):
    # GIVEN three parent positions plus two or three nested span-expanded positions
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 6, raising=False)
    path, out = tmp_path / "sample.html", tmp_path / "nested.md"
    path.write_bytes(('<table><tr><td colspan="3"><table><tr><td colspan="%d">X</td>'
                      '</tr></table></td></tr></table>' % nested_cells).encode("ascii"))
    assert 3 + nested_cells == total, "the nested total must be exactly below or at six positions"
    expected = expected_document(path, content, 0, tables=1)
    # WHEN a single global expanded-position budget accounts for both grids
    rc, receipt, markdown = service.run(path, out)
    # THEN the layout parent renders its nested table while the aggregate limit stays inclusive
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "parent and nested grids must share the same expanded-cell budget")


@supported_html
def test_scaled_nested_span_budget_refuses_aggregate_one_over(tmp_path, monkeypatch):
    # GIVEN individually small grids whose combined expanded total is seven
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 6, raising=False)
    path, out = tmp_path / "sample.html", tmp_path / "nested.md"
    path.write_bytes(b'<table><tr><td colspan="3"><table><tr><td colspan="4">X</td></tr></table></td></tr></table>')
    out.write_bytes(b"keep destination\n")
    assert 3 + 4 == 7, "the aggregate must exceed the six-position bound by one"
    # WHEN the table plan checks aggregate amplification before allocating grids
    result = service.run(path, out)
    # THEN individual per-table checks cannot admit the oversized nested total
    assert_resource_refusal(path, out, result)


@supported_html
def test_deep_malformed_html_refuses_without_recursive_parser_traceback(tmp_path):
    # GIVEN unclosed elements whose recovered depth greatly exceeds 256
    path, out = tmp_path / "sample.html", tmp_path / "deep.md"
    path.write_bytes(b"<div>" * 2048 + b"Kept")
    out.write_bytes(b"keep destination\n")
    assert path.stat().st_size == 10244, "depth refusal must not depend on raw input size"
    # WHEN malformed recovery produces a deeply nested body
    result = service.run(path, out)
    # THEN an explicit resource refusal reaches the normal atomic failure contract
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><strong></strong></p><span><em></em></span><h1></h1><p>Kept</p>',
                 'Kept\n', 1, 0, id="empty-markup-does-not-invent-text"),
    pytest.param('<svg><title>Name</title> <desc>Description</desc> <text>東京</text></svg>'
                 '<canvas><p>Canvas fallback</p></canvas><noscript><p>No script</p></noscript>'
                 '<custom><p>Unknown child</p></custom>',
                 'Name Description 東京\n\nCanvas fallback\n\nNo script\n\nUnknown child\n',
                 4, 0, id="svg-canvas-noscript-and-unknown-text"),
    pytest.param('<p><a href="https://example.test/a_(b)?x=1&amp;y=2">Safe</a> '
                 '<a href="\tjava&#x0a;script:go">Unsafe</a></p>',
                 '[Safe](https://example.test/a_%28b%29?x=1&y=2) Unsafe\n',
                 1, 0, id="url-parentheses-and-obfuscated-scheme"),
    pytest.param('<img src="a (b)" alt="&lt;x&gt;" title="café &amp; 東京">',
                 '![&lt;x>](a%20%28b%29)\n', 1, 0, id="image-target-quoted-and-title-dropped"),
    pytest.param('<table><tr><td><table></table></td><td>Peer</td></tr></table>',
                 'Peer\n', 1, 0, id="layout-table-empty-nested-table-has-no-record"),
    pytest.param('<table><tr><td><table><tr><td><code>&lt;x&gt; &amp; 東京 "quoted"\n\t  end</code>'
                 '</td></tr></table></td></tr></table>',
                 '| Code: text="&lt;x> & 東京 &quot;quoted&quot;&#10;&#9;  end" |\n| --- |\n', 0, 1,
                 id="layout-nested-code-label-quotes-stay-unambiguous"),
    pytest.param('<p><span>AT&amp;</span>amp;T &amp;<i></i>#45; <span>&amp;#</span>x2D;</p>'
                 '<table><tr><td><span>a&amp;</span>lt;</td></tr></table>',
                 'AT&amp;amp;T &amp;#45; &amp;#x2D;\n\n| a&amp;lt; |\n| --- |\n',
                 1, 1, id="ampersand-split-across-nodes-cannot-join-into-reference"),
    pytest.param('<p>Q&amp;</p><table><tr><td>Q&amp;</td></tr></table>',
                 'Q&\n\n| Q& |\n| --- |\n', 1, 1, id="block-final-ampersand-stays-literal"),
    pytest.param('<p>a&amp;<span>amp;</span></p>',
                 'a&amp;amp;\n', 1, 0, id="node-final-ampersand-joining-entity-stays-escaped"),
])
def test_security_and_fallback_edges_preserve_exact_inert_data(tmp_path, source, content, regions, tables):
    # GIVEN source syntax and fallback edges with independent safe output expectations
    path, out = tmp_path / "sample.html", tmp_path / "safe.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "security output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN rendering preserves source data without activating syntax or inventing empty regions
    rc, receipt, markdown = service.run(path, out)
    # THEN the complete document retains only approved syntax, data and counters
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "safety edges must preserve useful inert source data and exact accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "safe file and API output bytes must agree"


@supported_html
def test_charset_preflight_does_not_extend_past_the_inspected_window(tmp_path):
    # GIVEN a declaration starting at byte offset 1024 and otherwise valid UTF-8 text
    path, out = tmp_path / "sample.html", tmp_path / "window.md"
    prefix = b' ' * 1024
    assert len(prefix) == 1024, "the declaration must start after the inspected window"
    path.write_bytes(prefix + '<meta charset="latin1"><p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    # WHEN the bounded preflight leaves declarations outside its window unexamined
    rc, receipt, markdown = service.run(path, out)
    # THEN strict UTF-8 decoding still preserves exact Unicode and source hashes
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "charset inspection must honor its explicit 1024-byte bound")


RUNTIME_SIMULATION = '''
import contextlib
import importlib.abc
import io
import json
from pathlib import Path
import platform
import sys
import pdfplumber
import python_calamine

mode, directory = sys.argv[1], Path(sys.argv[2])
attempts = []
class ParserBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selectolax" or fullname.startswith("selectolax."):
            attempts.append(fullname)
            if mode == "broken-parser":
                raise OSError("simulated native binding failure")
            raise ModuleNotFoundError("simulated parser import failure")
sys.meta_path.insert(0, ParserBlocker())
if mode == "unsupported-version":
    sys.version_info = (3, 15, 0, "final", 0)
if mode == "unsupported-implementation":
    platform.python_implementation = lambda: "PyPy"
import brewdoc
from brewdoc import service
from brewdoc.selfcheck import synthetic_docx
html, legacy, out = directory / "sample.html", directory / "legacy.docx", directory / "refused.md"
html.write_bytes(b"<p>Kept</p>")
legacy.write_bytes(synthetic_docx())
out.write_bytes(b"keep destination\\n")
rc, line, markdown = brewdoc.run(html, out)
old_rc, old_line, old_markdown = brewdoc.run(legacy)
with contextlib.redirect_stdout(io.StringIO()):
    self_rc = brewdoc.self_check()
print(json.dumps({"suffix_registered": ".html" in service.ROUTES,
    "api_exported": "render_html" in brewdoc.__all__, "native_attempts": len(attempts),
    "html": [rc, line["file_ok"], line["route"], line["unit_kind"], line["units"],
             line["tables"], line["text_regions"], markdown, out.read_bytes() == b"keep destination\\n"],
    "legacy": [old_rc, old_line["route"], old_line["units"], old_line["tables"],
               old_line["text_regions"], old_markdown.isascii()], "self_check": self_rc}))
'''


@supported_html
@pytest.mark.parametrize("mode,attempts,self_rc", [
    pytest.param("missing-parser", 1, 1, id="supported-import-failure-simulation"),
    pytest.param("broken-parser", 1, 1, id="supported-native-failure-simulation"),
    pytest.param("unsupported-version", 0, 0, id="cpython-315-version-simulation"),
    pytest.param("unsupported-implementation", 0, 0, id="pypy-implementation-simulation"),
])
def test_runtime_simulations_keep_static_api_legacy_success_and_expected_html_refusal(
    tmp_path, mode, attempts, self_rc,
):
    # GIVEN a fresh child interpreter with an explicitly labelled runtime/import simulation
    directory = tmp_path / mode
    directory.mkdir()
    assert (directory / "refused.md").exists() is False, "the subprocess must own its fresh output path"
    environment = {**os.environ, "TMPDIR": str(directory), "TEMP": str(directory), "TMP": str(directory)}
    # WHEN native imports or declared HTML runtime prerequisites are simulated unavailable
    completed = subprocess.run([sys.executable, '-c', RUNTIME_SIMULATION, mode, str(directory)],
                               capture_output=True, env=environment, timeout=60)
    # THEN static routes/API and every old self-check remain available with explicit HTML refusal
    assert (completed.returncode, completed.stderr) == (0, b""), "the simulation must finish without import traceback"
    assert json.loads(completed.stdout) == {
        "suffix_registered": True, "api_exported": True, "native_attempts": attempts,
        "html": [1, False, "html", "chapter", 0, 0, 0, "", True],
        "legacy": [0, "doc", 1, 1, 1, True], "self_check": self_rc,
    }, "runtime simulations must distinguish unsupported capability from a broken supported installation"


@supported_html
def test_ascii_configured_cli_stdout_matches_api_and_utf8_file_bytes(tmp_path):
    # GIVEN a Unicode HTML source and the independent complete UTF-8 Markdown expectation
    path, out = tmp_path / "sample.html", tmp_path / "cli.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    assert out.exists() is False, "the byte-equivalence file must start absent"
    api_rc, api_line, api_markdown = service.run(path, out)
    stdout_line = {**expected_receipt(path, out, 1), "out": None}
    environment = {**os.environ, "PYTHONIOENCODING": "ascii", "TMPDIR": str(tmp_path)}
    # WHEN the CLI must emit HTML through an ASCII-configured Python stream
    completed = subprocess.run([sys.executable, '-m', 'brewdoc.cli', str(path)],
                               capture_output=True, env=environment, timeout=30)
    # THEN JSON remains ASCII and Markdown bytes equal API/file UTF-8 without a traceback or BOM
    expected_stdout = json.dumps(stdout_line, ensure_ascii=True, sort_keys=True).encode("ascii")
    assert (api_rc, api_line, api_markdown, out.read_bytes(), completed.returncode,
            completed.stdout, completed.stderr) == (
        0, expected_receipt(path, out, 1), expected, expected.encode("utf-8"), 0,
        expected_stdout + os.linesep.encode("ascii") + expected.encode("utf-8"), b"",
    ), "ASCII-configured CLI output must deliberately preserve exact ordered HTML UTF-8 bytes"


@supported_html
@pytest.mark.parametrize("source,content", [
    pytest.param('<table><tr><td>A <input value="v"> B</td></tr></table>',
                 '| A B |\n| --- |\n', id="control-cell-input-dropped"),
    pytest.param('<table><tr><td>Before <textarea>first "quoted"</textarea> Next <output>sum</output> After'
                 '</td><td>Peer <select><option>A</option></select></td></tr></table>',
                 '| Before Next After | Peer |\n| --- | --- |\n', id="control-cell-textarea-output-select-dropped"),
    pytest.param('<table><tr><td>A <img src="p" alt="I"> B <img src="q"> C</td></tr></table>',
                 '| A ![I](p) B C |\n| --- |\n', id="image-cell-inline-alt-only"),
    pytest.param('<table><tr><td><b><img alt=I src=p></b></td></tr></table>',
                 '| **![I](p)** |\n| --- |\n', id="image-cell-inside-strong"),
])
def test_table_cells_drop_controls_and_inline_alt_images_without_extra_regions(tmp_path, source, content):
    # GIVEN ordinary or nested cells holding controls or images with literal expectations
    path, out = tmp_path / "sample.html", tmp_path / "cell-controls.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "table-control output must start absent"
    expected = expected_document(path, content, 0, tables=1)
    # WHEN the table cell emitter renders the clean default profile
    rc, receipt, markdown = service.run(path, out)
    # THEN controls vanish, alt images stay inline and only the outer table is counted
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "cells must drop controls, keep alt images inline and add zero text regions")
    assert out.read_bytes() == expected.encode("utf-8"), "table-control file and API bytes must match"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><em>Before<code>&lt;x&gt; &amp; café</code>After</em></p>',
                 '*Before*\n\n```\n<x> & café\n```\n\n*After*\n', 3, 0,
                 id="emphasis-balanced-around-fence"),
    pytest.param('<p><a href="/safe"><strong>Before<em>Inner<code>literal</code>'
                 'After</em>Tail</strong></a></p>',
                 '[**Before*Inner***](/safe)\n\n```\nliteral\n```\n\n[***After*Tail**](/safe)\n',
                 3, 0, id="nested-strong-emphasis-link-segments-balanced"),
    pytest.param('<a href="a_(b)"><code>café &amp; &lt;x&gt;</code></a>',
                 '```\ncafé & <x>\n```\n\n[Link target](a_%28b%29)\n',
                 2, 0, id="code-only-safe-link-target-once"),
    pytest.param('<em><code>literal</code></em>', '```\nliteral\n```\n',
                 1, 0, id="code-only-emphasis-no-phantom-delimiters"),
    pytest.param('<ul><li><p>First</p><p>Second</p></li></ul>',
                 '- First\n\n  Second\n', 2, 0, id="plain-list-paragraph-continuation-count"),
    pytest.param('<ul><li>Outer<ul><li>A</li><li>B</li></ul>Tail</li></ul>',
                 '- Outer\n  - A\n  - B\n\n  Tail\n', 4, 0, id="list-parent-tail-offset"),
    pytest.param('<ul><li>Before<pre>&lt;x&gt;</pre>After</li></ul>',
                 '- Before\n\n  ```\n  <x>\n  ```\n\n  After\n', 3, 0, id="list-item-fence-offset"),
    pytest.param('<ul><li><code>literal</code></li></ul>',
                 '-\n  ```\n  literal\n  ```\n', 1, 0, id="list-code-only-item-next-line"),
    pytest.param('<ul><li><table><tr><td>A</td></tr></table></li></ul>',
                 '-\n  | A |\n  | --- |\n', 0, 1, id="list-table-only-item-next-line"),
    pytest.param('<ul><li>Outer<ul><li><code>literal</code></li><li>Plain</li></ul>'
                 '<p>Tail</p></li></ul>',
                 '- Outer\n\n  -\n    ```\n    literal\n    ```\n\n  - Plain\n\n  Tail\n',
                 4, 0, id="list-nested-code-only-child-and-parent-tail"),
    pytest.param('<ol start="3"><li>Before<code>literal</code>After</li></ol>',
                 '3. Before\n\n   ```\n   literal\n   ```\n\n   After\n',
                 3, 0, id="list-ordered-complex-item-offset"),
    pytest.param('<ul><li>Before<table><caption>Data</caption><tr><td>A</td></tr></table>'
                 'After</li></ul>',
                 '- Before\n\n  Caption: Data\n\n  | A |\n  | --- |\n\n  After\n',
                 3, 1, id="list-table-caption-in-item"),
    pytest.param('<ul><li><img src="pic" alt="Icon"></li></ul>',
                 '- ![Icon](pic)\n', 1, 0, id="list-item-with-only-picture"),
    pytest.param('<ul><li><blockquote><p>Quoted</p></blockquote></li></ul>',
                 '-\n  > Quoted\n', 1, 0, id="list-item-holding-blockquote"),
    pytest.param('<ul><li>a</li><li></li><li>b</li></ul>',
                 '- a\n-\n- b\n', 2, 0, id="list-empty-item-bare-marker"),
    pytest.param('<ul><li>Outer<ul><li></li><li>x</li></ul></li></ul>',
                 '- Outer\n\n  -\n  - x\n', 2, 0, id="list-nested-empty-first-item-blank-before"),
    pytest.param('<ul><li>Outer<ol><li>T</li></ol></li></ul>',
                 '- Outer\n  1. T\n', 2, 0, id="list-nested-ordered-from-one-stays-tight"),
    pytest.param('<a href="/safe"></a><p>Kept</p>', 'Kept\n',
                 1, 0, id="empty-safe-anchor-no-generated-region"),
])
def test_inline_fences_and_list_children_keep_balanced_semantic_scopes(
    tmp_path, source, content, regions, tables,
):
    # GIVEN confirmed inline/list boundary cases and independently specified complete output
    path, out = tmp_path / "sample.html", tmp_path / "inline-list.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "inline/list output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN standalone child blocks and inline-code fallbacks preserve their semantic contexts
    rc, receipt, markdown = service.run(path, out)
    # THEN generated delimiters balance, list identity survives and every semantic block counts once
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "inline/list boundaries must retain source order, containment and exact emitted-block accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "inline/list file and API bytes must match"


@supported_html
@pytest.mark.parametrize("source,size", [
    pytest.param('<!-- <meta charset="iso-8859-1"> --><p>café</p>', 48, id="comment-example"),
    pytest.param('<script>const example = \'<meta charset="iso-8859-1">\';</script><p>café</p>', 75,
                 id="script-raw-text-example"),
    pytest.param('<div title=\'<meta charset="iso-8859-1">\'><p>café</p></div>', 59,
                 id="quoted-attribute-example"),
])
def test_charset_examples_in_inert_lexical_contexts_do_not_declare_an_encoding(tmp_path, source, size):
    # GIVEN valid UTF-8 whose apparent meta tag is inert source data inside the preflight window
    path, out = tmp_path / "sample.html", tmp_path / "charset-context.md"
    path.write_bytes(source.encode("utf-8"))
    assert path.stat().st_size == size, "the apparent declaration must be inside the inspected byte window"
    expected = expected_document(path, "café\n", 1)
    # WHEN bounded preflight recognizes actual HTML tags rather than source examples
    rc, receipt, markdown = service.run(path, out)
    # THEN no false encoding refusal suppresses the included Unicode paragraph
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "comments, script raw text and quoted attributes must not become encoding declarations")
    assert out.read_bytes() == expected.encode("utf-8"), "valid lexical-context file and API bytes must agree"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><em> soft </em></p>', '*soft*\n', 1, 0, id="body-em-whitespace"),
    pytest.param('<p><strong> bold </strong></p>', '**bold**\n', 1, 0, id="body-strong-whitespace"),
    pytest.param('<table><tr><td><em> soft </em></td></tr></table>',
                 '| *soft* |\n| --- |\n', 0, 1, id="cell-em-whitespace"),
    pytest.param('<table><tr><td><strong> bold </strong></td></tr></table>',
                 '| **bold** |\n| --- |\n', 0, 1, id="cell-strong-whitespace"),
    pytest.param('<p><em> before <code>x</code> after </em></p>',
                 '*before*\n\n```\nx\n```\n\n*after*\n', 3, 0, id="fence-segment-whitespace"),
    pytest.param('<p>left<em> soft </em>right</p>',
                 'left *soft* right\n', 1, 0, id="body-word-separation-outside-delimiters"),
    pytest.param('<table><tr><td>left<strong> bold </strong>right</td></tr></table>',
                 '| left **bold** right |\n| --- |\n', 0, 1, id="cell-word-separation-outside-delimiters"),
])
def test_emphasis_whitespace_stays_outside_valid_body_cell_and_fence_delimiters(
    tmp_path, source, content, regions, tables,
):
    # GIVEN source emphasis padded with whitespace and exact valid Markdown expectations
    path, out = tmp_path / "sample.html", tmp_path / "emphasis.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "emphasis output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN whitespace normalization preserves word separation and generated emphasis semantics
    rc, receipt, markdown = service.run(path, out)
    # THEN markers surround non-whitespace text independently in each body/cell/fence segment
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "whitespace-padded delimiters must not turn source emphasis into literal markers or list syntax"
    assert out.read_bytes() == expected.encode("utf-8"), "valid emphasis file and API bytes must agree"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><a href="/x"><em> label</em></a></p>', '[*label*](/x)\n', 1, 0,
                 id="link-label-leading-space-dropped-at-paragraph-start"),
    pytest.param('<p>see<a href="/x"><em> label</em></a></p>', 'see [*label*](/x)\n', 1, 0,
                 id="link-label-leading-space-moves-before-bracket"),
    pytest.param('<p><a href="/x"><em>label </em></a>next</p>', '[*label*](/x) next\n', 1, 0,
                 id="link-label-trailing-space-moves-after-target"),
    pytest.param('<p>see<a href="/x"> label </a>next</p>', 'see [label](/x) next\n', 1, 0,
                 id="plain-link-label-spaces-move-outside"),
    pytest.param('<table><tr><td>see<a href="/x"> label </a>next</td></tr></table>',
                 '| see [label](/x) next |\n| --- |\n', 0, 1, id="cell-link-label-spaces-move-outside"),
])
def test_link_label_whitespace_moves_outside_the_brackets(tmp_path, source, content, regions, tables):
    # GIVEN body and cell links whose labels start or end with source whitespace
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "link source must reach the parser unchanged"
    # WHEN body and cell writers emit link brackets
    result = service.run(path)
    # THEN the label is trimmed and its word separation stays outside the brackets
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "a link label must never start or end with a space inside its brackets")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", BANG_BEFORE_LINK)
def test_text_bang_before_a_generated_link_is_escaped(tmp_path, source, content, regions, tables):
    # GIVEN body and cell text ending in `!` right before a link, also after dropped strong markers
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "bang source must reach the parser unchanged"
    # WHEN body and cell writers join the text and the generated link opener
    result = service.run(path)
    # THEN `!` is written `\!`, so the link stays a link and the text keeps its `!`
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "text `!` before a generated link must not turn the link into an image")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", CODE_SPLIT_IMAGE_AND_BLANK_LINK)
def test_code_split_images_and_blank_link_labels_match_epub(tmp_path, source, content, regions, tables):
    # GIVEN an image in an inline element split by code, and links with blank labels
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "inline source must reach the parser unchanged"
    # WHEN body and cell writers render the shared rows
    result = service.run(path)
    # THEN the image block stays and a blank-label link renders as its plain text
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "HTML must keep code-split images and drop blank link brackets, the shape EPUB shares")


# Node 24.19.0 TextDecoder("gb18030") over single 0x80 then every two-byte pair, joined by "\n".
WHATWG_GB18030_TWO_BYTE_SHA256 = "23c8d87241dc5c670e2a663a9cddaec0557164d81a2876998594eda3fa83e1c5"
GB18030_TWO_BYTE_SEQUENCES = [b"\x80"] + [bytes((lead, trail)) for lead in range(0x81, 0xFF)
                                          for trail in (*range(0x40, 0x7F), *range(0x80, 0xFF))]


@pytest.mark.parametrize("label", ["gbk", "gb18030"])
def test_gbk_labels_decode_every_two_byte_pair_like_whatwg(label):
    # GIVEN single 0x80 and all 23,940 lead/trail pairs, plus the recorded WHATWG output hash
    assert len(set(GB18030_TWO_BYTE_SEQUENCES)) == 23941, "the table must hold 0x80 and 126 x 190 pairs"
    # WHEN the HTML decoder decodes each sequence alone
    decoded = "\n".join(htmltext._decode_bytes(sequence, label) for sequence in GB18030_TWO_BYTE_SEQUENCES)
    # THEN the joined text hashes to the WHATWG gb18030 decoder output
    assert hashlib.sha256(decoded.encode("utf-8")).hexdigest() == WHATWG_GB18030_TWO_BYTE_SHA256, (
        "every one- and two-byte GBK or gb18030 sequence must decode as WHATWG TextDecoder does")


@pytest.mark.parametrize(("code", "expected"), [
    ("a3a0", "　"), ("a6d9", "︐"), ("a6da", "︒"), ("a6db", "︑"), ("a6dc", "︓"),
    ("a6dd", "︔"), ("a6de", "︕"), ("a6df", "︖"), ("a6ec", "︗"), ("a6ed", "︘"),
    ("a6f3", "︙"), ("a8bc", "ḿ"), ("fe59", "龴"), ("fe61", "龵"), ("fe66", "龶"),
    ("fe67", "龷"), ("fe6d", "龸"), ("fe7e", "龹"), ("fe90", "龺"), ("fea0", "龻"),
])
def test_gbk_codes_decode_to_whatwg_characters_not_private_use(code, expected):
    # GIVEN a two-byte code Python gb18030 maps to Private Use and its WHATWG character
    assert len(bytes.fromhex(code)) == 2, "each row must be one two-byte GBK code"
    # WHEN both GBK labels decode the code
    decoded = (htmltext._decode_bytes(bytes.fromhex(code), "gbk"),
               htmltext._decode_bytes(bytes.fromhex(code), "gb18030"))
    # THEN both give the standard WHATWG character
    assert decoded == (expected, expected), "GBK and gb18030 must not decode standard codes to Private Use"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", INLINE_BOUNDARY_WHITESPACE)
def test_inline_whitespace_collapses_across_inline_element_boundaries(tmp_path, source, content, regions, tables):
    # GIVEN whitespace runs meeting at inline element edges, plus pre, code and verse controls
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "inline source must reach the parser unchanged"
    # WHEN body and cell writers join text across the element boundaries
    result = service.run(path)
    # THEN meeting runs become one space while pre, code and verse keep their own rules
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "whitespace meeting at an inline element boundary must collapse to one space, as HTML renders it")


NATIVE_INITIALIZATION_SIMULATION = '''
import builtins
import contextlib
import importlib.abc
import io
import json
from pathlib import Path
import sys

error_type, directory = sys.argv[1], Path(sys.argv[2])
class NativeInitializationFailure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selectolax.lexbor":
            raise getattr(builtins, error_type)("simulated native initialization failure")
sys.meta_path.insert(0, NativeInitializationFailure())
import brewdoc
from brewdoc import service
html = brewdoc.run(directory / "sample.html", directory / "native.md")
legacy = brewdoc.run(directory / "legacy.docx")
with contextlib.redirect_stdout(io.StringIO()):
    self_rc = brewdoc.self_check()
print(json.dumps({"suffix_registered": ".html" in service.ROUTES,
                  "api_exported": "render_html" in brewdoc.__all__,
                  "html": html, "legacy": legacy, "self_check": self_rc}))
'''


@supported_html
@pytest.mark.parametrize("error_type", ["SystemError", "OSError"])
def test_native_initialization_failures_preserve_package_legacy_and_html_refusal(tmp_path, error_type):
    # GIVEN an isolated native-import simulation and independent legacy/refusal expectations
    path, out = tmp_path / "sample.html", tmp_path / "native.md"
    path.write_bytes(b'<p>Kept</p>')
    out.write_bytes(b"keep native destination\n")
    old_path = docx(tmp_path / "legacy.docx", '<w:p><w:r><w:t>Old text</w:t></w:r></w:p>')
    old_omissions = ["images, charts and the text drawn inside them",
                    "tracked changes, comments, footnotes, headers and footers",
                    "a table's own formatting - only its cells, row by row"]
    old_body = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nOld text\n'
    old_markdown = schema_two(old_path, old_body, **NOT_APPLICABLE, name="legacy.docx",
                              suffix=".docx", route="doc", unit="chapter", count=1,
                              keys="chapter/000001", pages=0, sheets=0, chapters=1, tables=0,
                              text_regions=1, omissions="- images, charts and the text drawn inside them\n"
                              "- tracked changes, comments, footnotes, headers and footers\n"
                              "- a table's own formatting - only its cells, row by row",
                              contents='- [Chapter 1: "Body"](#brewdoc-chapter-000001)')
    assert out.read_bytes() == b"keep native destination\n", "the native-failure sentinel must start intact"
    environment = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    # WHEN selectolax.lexbor initialization raises a real loader exception class in the subprocess
    completed = subprocess.run([sys.executable, '-c', NATIVE_INITIALIZATION_SIMULATION, error_type, str(tmp_path)],
                               capture_output=True, env=environment, timeout=60)
    # THEN package imports, complete legacy output and supported-parser refusal remain available
    assert (completed.returncode, completed.stderr) == (0, b""), "native initialization must not break package import"
    assert (json.loads(completed.stdout), out.read_bytes()) == ({
        "suffix_registered": True, "api_exported": True, "self_check": 1,
        "html": [1, {
            "file_ok": False, "route": "html",
            "reason": "HTML parser unavailable: selectolax 0.4.13: simulated native initialization failure",
            "source": "sample.html", "out": None, "receipt_schema": "brewdoc.receipt/1",
            "unit_kind": "chapter", "units": 0, "tables": 0, "text_regions": 0,
            "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
            "not_carried": HTML_OMISSIONS,
        }, ""],
        "legacy": [0, {
            "file_ok": True, "route": "doc",
            "reason": "doc rendered: 1 chapters, 0 tables, 1 text regions, 0 column splits",
            "source": "legacy.docx", "out": None, "receipt_schema": "brewdoc.receipt/1",
            "unit_kind": "chapter", "units": 1, "tables": 0, "text_regions": 1,
            "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
            "not_carried": old_omissions, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
            "unit_keys": ["chapter/000001"],
        }, old_markdown],
    }, b"keep native destination\n"), "native loader failures must retain exact old-route bytes and HTML atomic refusal"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<code><a href="../mod/core.html#traceenable">TraceEnable</a></code>',
                 '```\nTraceEnable\n```\n\n'
                 '[Link target](../mod/core.html#traceenable); text="TraceEnable"\n',
                 2, 0, id="body-code-descendant-anchor"),
    pytest.param('<pre><a href="/multi">café "quoted"\n\t  東京 &amp;#10;</a></pre>',
                 '```\ncafé "quoted"\n\t  東京 &#10;\n```\n\n'
                 '[Link target](/multi); text="café &quot;quoted&quot;&#10;&#9;  東京 &amp;#10;"\n',
                 2, 0, id="body-pre-lossless-descendant-label"),
    pytest.param('<code><a href="/same">First</a> / <a href="/same">Second</a></code>',
                 '```\nFirst / Second\n```\n\n[Link target](/same); text="First"\n\n'
                 '[Link target](/same); text="Second"\n',
                 3, 0, id="repeated-url-anchors-remain-distinct"),
    pytest.param('<a href="./"><img src="back" alt="Back"></a>'
                 '<a href="#page-header"><img src="top" alt="Top"></a>',
                 '![Back](back)\n\n[Link target](./)\n\n![Top](top)\n\n[Link target](#page-header)\n',
                 4, 0, id="image-only-links-keep-target-records"),
    pytest.param('<a href="/wrap"><code>literal</code></a>',
                 '```\nliteral\n```\n\n[Link target](/wrap)\n',
                 2, 0, id="existing-enclosing-code-target-once"),
    pytest.param('<code><a hidden href="/hidden">Hidden</a><span hidden>'
                 '<a href="/ancestor">Lost</a></span><a href="javascript:go">Unsafe</a>'
                 '<a>Missing</a><a href="/empty"></a></code>',
                 '```\nUnsafeMissing\n```\n', 1, 0,
                 id="excluded-unsafe-missing-empty-descendants-no-record"),
    pytest.param('<a href="/none"><img hidden src="pic"></a><p>Kept</p>',
                 'Kept\n', 1, 0, id="excluded-enclosing-image-no-fabricated-target"),
    pytest.param('<table><tr><td>Before <code><a href="/same">café</a> / '
                 '<a href="/same">東京</a></code> After</td></tr></table>',
                 '| Before Code: text="café / 東京" [Link target](/same); text="café" '
                 '[Link target](/same); text="東京" After |\n| --- |\n',
                 0, 1, id="cell-descendant-records-zero-regions"),
    pytest.param('<table><tr><td><a href="/image"><img src="pic" alt="Icon"></a> ; '
                 '<a href="/code"><code>x</code></a></td></tr></table>',
                 '| [![Icon](pic)](/image) ; [Code: text="x"](/code) |\n| --- |\n',
                 0, 1, id="image-cell-inside-link-and-code-once"),
    pytest.param('<pre>Before<code><a href="/nested">Linked</a></code>After</pre>',
                 '```\nBeforeLinkedAfter\n```\n\n[Link target](/nested); text="Linked"\n',
                 2, 0, id="nested-consumed-code-pre-collect-once"),
    pytest.param('<pre><a href="/first">One<a href="/second">Two</a></pre>',
                 '```\nOneTwo\n```\n\n[Link target](/first); text="One"\n\n'
                 '[Link target](/second); text="Two"\n',
                 3, 0, id="recovered-malformed-anchors-once-in-dom-order"),
    pytest.param('<code><a href="/whitespace"> \n\t </a></code>',
                 '```\n \n\t \n```\n\n[Link target](/whitespace); text=" &#10;&#9; "\n',
                 2, 0, id="retained-literal-whitespace-is-not-an-empty-label"),
])
def test_consumed_literal_and_image_anchors_preserve_each_safe_target_once(
    tmp_path, source, content, regions, tables,
):
    # GIVEN consumed literal/image content with exact included-anchor records and control cases
    path, out = tmp_path / "sample.html", tmp_path / "targets.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "link-target output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN body or cell rendering preserves targets without modifying consumed literal text
    rc, receipt, markdown = service.run(path, out)
    # THEN every included source anchor retains its own destination, label, position and count
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "consumed content must preserve each safe anchor occurrence without fabricated or duplicate records"
    assert out.read_bytes() == expected.encode("utf-8"), "target-preserving file and API bytes must agree"


CELL_BLOCKS = [
    ('<ul><li>First</li><li>Second</li></ul>', 'First Second', '- First\n- Second\n', 2,
     "unordered-siblings"),
    ('<ol><li>First</li><li>Second</li></ol>', 'First Second', '1. First\n2. Second\n', 2, "ordered-siblings"),
    ('<ul><li>First<ol><li>Second</li><li>Third</li></ol>Fourth</li><li>Fifth</li></ul>',
     'First Second Third Fourth Fifth',
     '- First\n  1. Second\n  2. Third\n\n  Fourth\n\n- Fifth\n', 5, "nested-mixed-lists"),
    ('<blockquote>First</blockquote><blockquote>Second</blockquote>', 'First Second',
     '> First\n\n> Second\n', 2, "blockquote-siblings"),
    ('<h1>First</h1><h1>Second</h1>', 'First Second', '### First\n\n### Second\n', 2, "h1-siblings"),
    ('<h2>First</h2><h2>Second</h2>', 'First Second', '#### First\n\n#### Second\n', 2, "h2-siblings"),
    ('<h3>First</h3><h3>Second</h3>', 'First Second', '##### First\n\n##### Second\n', 2, "h3-siblings"),
    ('<h4>First</h4><h4>Second</h4>', 'First Second', '###### First\n\n###### Second\n', 2, "h4-siblings"),
    ('<h5>First</h5><h5>Second</h5>', 'First Second',
     '**Heading 5: First**\n\n**Heading 5: Second**\n', 2, "h5-siblings"),
    ('<h6>First</h6><h6>Second</h6>', 'First Second',
     '**Heading 6: First**\n\n**Heading 6: Second**\n', 2, "h6-siblings"),
    ('<ul><li><a href="/one">First</a></li><li><em>Second</em></li></ul>', '[First](/one) *Second*',
     '- [First](/one)\n- *Second*\n', 2, "linked-and-emphasized-list-items"),
    ('<ul><li>A|B &amp; &lt;C&gt;</li><li>café 東京</li></ul>', 'A\\|B & &lt;C> café 東京',
     '- A|B & &lt;C>\n- café 東京\n', 2, "list-boundaries-preserve-escaping"),
    ('<div>First</div><p>Second</p>', 'First Second', 'First\n\nSecond\n', 2, "existing-block-boundaries"),
]


@supported_html
@pytest.mark.parametrize("cell,words", [pytest.param(cell, words, id=name) for cell, words, *_, name in CELL_BLOCKS]
                         + [pytest.param('<span>foo</span><span>bar</span>', 'foobar', id="true-inline-adjacency")])
def test_data_table_cell_structure_preserves_word_boundaries_and_inline_adjacency(tmp_path, cell, words):
    # GIVEN a header-row data table whose one body cell holds blocks or inline siblings
    path, out = tmp_path / "sample.html", tmp_path / "cell-words.md"
    path.write_text('<table><tr><th>H</th></tr><tr><td>' + cell + '</td></tr></table>', encoding="utf-8")
    assert out.exists() is False, "table word-boundary output must start absent"
    expected = expected_document(path, '| H |\n| --- |\n| ' + words + ' |\n', 0, tables=1)
    # WHEN the shared static adapter renders the recovered cell contents
    result = service.run(path, out)
    # THEN separate blocks keep every word while inline siblings and escaping remain exact
    assert result == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "data cell structure must preserve word boundaries without changing inline adjacency or escaping")
    assert out.read_bytes() == expected.encode("utf-8"), "table file and public API bytes must agree"


@supported_html
@pytest.mark.parametrize("cell,content,regions", [
    pytest.param(cell, content, regions, id=name) for cell, _, content, regions, name in CELL_BLOCKS])
def test_single_cell_layout_table_renders_cell_blocks_as_body_blocks(tmp_path, cell, content, regions):
    # GIVEN a header-less one-cell table whose cell holds block content
    path, out = tmp_path / "sample.html", tmp_path / "cell-blocks.md"
    path.write_text('<table><tr><td>' + cell + '</td></tr></table>', encoding="utf-8")
    assert out.exists() is False, "layout output must start absent"
    expected = expected_document(path, content, regions)
    # WHEN the adapter classifies it as a layout table
    result = service.run(path, out)
    # THEN the cell blocks render exactly as body blocks and no table is counted
    assert result == (0, expected_receipt(path, out, regions), expected), (
        "layout cells must render lists, quotes, headings and paragraphs as ordinary blocks")
    assert out.read_bytes() == expected.encode("utf-8"), "layout file and public API bytes must agree"


@supported_html
@pytest.mark.parametrize("source,content,regions", [
    pytest.param('<section><header><h2>Section heading</h2></header><p>Body</p></section>',
                 '#### Section heading\n\nBody\n', 2, id="header-inside-section-kept"),
    pytest.param('<section><footer><p>SecFoot</p></footer></section><p>B</p>',
                 'SecFoot\n\nB\n', 2, id="footer-inside-section-kept"),
    pytest.param('<aside role="note"><p>Callout</p></aside><p>B</p>',
                 'Callout\n\nB\n', 2, id="aside-with-non-landmark-role-kept"),
    pytest.param('<header role="presentation"><p>H</p></header><p>B</p>',
                 'H\n\nB\n', 2, id="header-with-non-landmark-role-kept"),
    pytest.param('<nav role="note"><p>N</p></nav><p>B</p>', 'N\n\nB\n', 2,
                 id="nav-with-non-landmark-role-kept"),
    pytest.param('<header><h1>Sample flexbox example</h1></header><section><p>Body</p></section>',
                 '### Sample flexbox example\n\nBody\n', 2, id="page-header-with-h1-kept"),
    pytest.param('<header><div><h1>Deep H1</h1></div><p>tagline</p></header><p>B</p>',
                 '### Deep H1\n\ntagline\n\nB\n', 3, id="page-header-with-deep-h1-kept-whole"),
    pytest.param('<div role="banner"><h1>Site</h1><p>tag</p></div><p>B</p>',
                 '### Site\n\ntag\n\nB\n', 3, id="role-banner-with-h1-kept-whole"),
    pytest.param('<nav><main><p>InNav</p></main></nav><p>Out</p>', 'InNav\n\nOut\n', 2,
                 id="landmark-containing-main-is-not-chrome"),
    pytest.param('<div role="foo navigation"><p>N</p></div><p>B</p>', 'B\n', 1,
                 id="first-known-role-token-is-navigation"),
    pytest.param('<div role="note navigation"><p>N</p></div><p>B</p>', 'N\n\nB\n', 2,
                 id="first-aria-role-token-note-is-content"),
    pytest.param('<footer><h1>FootH1</h1></footer><p>B</p>', 'B\n', 1, id="page-footer-with-h1-dropped"),
    pytest.param('<header><h2>Sub</h2></header><p>B</p>', 'B\n', 1, id="page-header-without-h1-dropped"),
    pytest.param('<aside><footer><p>F</p></footer></aside><p>B</p>', 'B\n', 1,
                 id="footer-inside-dropped-aside-dropped"),
    pytest.param('<nav><header><p>H</p></header></nav><p>B</p>', 'B\n', 1,
                 id="header-inside-dropped-nav-dropped"),
    pytest.param('<div role="region"><header><p>H</p></header></div><p>B</p>', 'B\n', 1,
                 id="region-role-does-not-scope-header"),
])
def test_page_chrome_follows_sectioning_scope_explicit_roles_and_page_title(tmp_path, source, content, regions):
    # GIVEN header, footer, nav and aside shapes with literal kept or dropped content
    path, out = tmp_path / "sample.html", tmp_path / "chrome.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "chrome output must start absent"
    expected = expected_document(path, content, regions)
    # WHEN the clean default profile drops only page chrome
    result = service.run(path, out)
    # THEN sectioned, role-overridden and titled chrome stays while plain page chrome goes
    assert (result, out.read_bytes()) == ((0, expected_receipt(path, out, regions), expected),
                                          expected.encode("utf-8")), (
        "header/footer scoping, explicit roles, page h1 and contained main must decide chrome exactly")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><a href="/a?x=1&amp;copy;=2">L</a></p>', '[L](/a?x=1&amp;copy;=2)\n', 1, 0,
                 id="link-named-reference"),
    pytest.param('<p><a href="/a?x&amp;#35;y">L</a></p>', '[L](/a?x&amp;#35;y)\n', 1, 0,
                 id="link-numeric-reference"),
    pytest.param('<img alt="I" src="p?q&amp;lt;b">', '![I](p?q&amp;lt;b)\n', 1, 0, id="image-reference"),
    pytest.param('<table><tr><td><a href="/a&amp;amp;b">L</a></td></tr></table>',
                 '| [L](/a&amp;amp;b) |\n| --- |\n', 0, 1, id="cell-link-reference"),
    pytest.param('<table><tr><td><img alt="I" src="p?q&amp;lt;b"></td></tr></table>',
                 '| ![I](p?q&amp;lt;b) |\n| --- |\n', 0, 1, id="cell-image-reference"),
    pytest.param('<p><a href="/a?a=1&amp;b=2">L</a></p>', '[L](/a?a=1&b=2)\n', 1, 0,
                 id="plain-query-ampersand-stays"),
])
def test_link_and_image_targets_escape_ampersands_that_start_references(
        tmp_path, source, content, regions, tables):
    # GIVEN decoded targets whose ampersand would start a Markdown character reference
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "target source must reach the parser unchanged"
    # WHEN body and cell writers emit link and image destinations
    result = service.run(path)
    # THEN destinations escape the ampersand exactly as body text does
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "link and image destinations must not let source text forge a character reference")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><b>Note:</b><i>text</i></p>', '**Note:**_text_\n', 1, 0,
                 id="strong-then-emphasis-alternates-delimiter"),
    pytest.param('<p><b>A</b><b>B</b></p>', '**AB**\n', 1, 0, id="adjacent-strong-runs-merge"),
    pytest.param('<p><em>a</em><em>b</em></p>', '*ab*\n', 1, 0, id="adjacent-emphasis-runs-merge"),
    pytest.param('<p>price<b>$5</b>.</p>', 'price$5.\n', 1, 0, id="unopenable-strong-drops-markers"),
    pytest.param('<p>foo<em>(bar)</em></p>', 'foo(bar)\n', 1, 0, id="unopenable-emphasis-drops-markers"),
    pytest.param('<p>x <em> y </em> z</p>', 'x *y* z\n', 1, 0, id="padded-emphasis-single-spaces"),
    pytest.param('<table><tr><td><b>A</b><b>B</b></td></tr></table>', '| **AB** |\n| --- |\n', 0, 1,
                 id="cell-adjacent-strong-runs-merge"),
    pytest.param('<table><tr><td>x <em> y </em> z</td></tr></table>', '| x *y* z |\n| --- |\n', 0, 1,
                 id="cell-padded-emphasis-single-spaces"),
])
def test_adjacent_and_flanking_emphasis_stays_valid_commonmark(tmp_path, source, content, regions, tables):
    # GIVEN emphasis runs that touch each other, punctuation or padded spaces
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "emphasis source must reach the parser unchanged"
    # WHEN body and cell writers emit generated delimiters
    result = service.run(path)
    # THEN every delimiter opens and closes under CommonMark or is omitted
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "generated emphasis must never become literal asterisks or merge into a wrong run")


@supported_html
@pytest.mark.parametrize("source,content,regions", [
    pytest.param('<ul><li>a</li></ul><ul><li>b</li></ul>', '- a\n\n* b\n', 2, id="bullet-alternates"),
    pytest.param('<ul><li>a</li></ul><ul><li>b</li></ul><ul><li>c</li></ul>', '- a\n\n* b\n\n- c\n', 3,
                 id="bullet-alternates-back"),
    pytest.param('<ol><li>a</li></ol><ol start="5"><li>b</li></ol>', '1. a\n\n5) b\n', 2,
                 id="ordered-start-five-alternates-delimiter"),
    pytest.param('<ol><li>a</li></ol><ol><li>b</li><li>c</li></ol>', '1. a\n\n1) b\n2) c\n', 3,
                 id="ordered-delimiter-kept-within-list"),
    pytest.param('<ul><li>a<ol start="1"><li>b</li></ol><ol start="3"><li>c</li></ol></li></ul>',
                 '- a\n  1. b\n\n  3) c\n', 3, id="nested-ordered-start-three-alternates"),
    pytest.param('<ul><li>a<ol><li>b</li></ol><ol><li>c</li></ol></li></ul>',
                 '- a\n  1. b\n  1) c\n', 3, id="nested-ordered-from-one-alternates"),
    pytest.param('<ul><li>a</li></ul><p>p</p><ul><li>b</li></ul>', '- a\n\np\n\n- b\n', 3,
                 id="paragraph-between-lists-keeps-dash"),
    pytest.param('<ul><li>a</li></ul><ol><li>b</li></ol>', '- a\n\n1. b\n', 2,
                 id="different-list-types-keep-markers"),
])
def test_adjacent_same_type_lists_alternate_markers_to_stay_separate(tmp_path, source, content, regions):
    # GIVEN sibling lists of one type with no block between them
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "list source must reach the parser unchanged"
    # WHEN the shared list helper writes adjacent lists
    result = service.run(path)
    # THEN the second list switches bullet or delimiter, so CommonMark starts a new list
    assert result == (0, dict(expected_receipt(path, None, regions), out=None),
                      expected_document(path, content, regions)), (
        "adjacent same-type lists must remain separate lists with their own start numbers")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<table><tr><td><div>Site</div><ul><li>Menu</li></ul></td><td><h1>Headline</h1>'
                 '<p>Para one.</p><p>Para two.</p><table><tr><th>K</th><th>V</th></tr><tr><td>1</td>'
                 '<td>2</td></tr></table></td></tr></table>',
                 'Site\n\n- Menu\n\n### Headline\n\nPara one.\n\nPara two.\n\n'
                 '| K | V |\n| --- | --- |\n| 1 | 2 |\n', 5, 1, id="layout-cells-become-blocks-nested-data-table"),
    pytest.param('<table><tr><td>Left</td><td><p>Right</p></td></tr><tr><td><p>Next</p></td></tr></table>',
                 'Left\n\nRight\n\nNext\n', 3, 0, id="layout-cells-in-source-order"),
    pytest.param('<table><tr><td><p>A</p><table><tr><td>1</td></tr></table></td></tr></table>',
                 'A\n\n| 1 |\n| --- |\n', 1, 1, id="layout-nested-plain-table-renders-as-table"),
    pytest.param('<table><tr><th>H</th></tr><tr><td><p>x</p></td></tr></table>',
                 '| H |\n| --- |\n| x |\n', 0, 1, id="header-cell-keeps-data-table"),
    pytest.param('<table><caption>C</caption><tr><td><p>x</p></td></tr></table>',
                 'Caption: C\n\n| x |\n| --- |\n', 1, 1, id="caption-keeps-data-table"),
    pytest.param('<table><tr><td>Before<table><tr><td><input type="PASSWORD" role="button" '
                 'aria-label="Choose" value="protectednestedvalue"></td></tr></table>After</td></tr></table>',
                 'Before\n\n|  |\n| --- |\n\nAfter\n', 2, 1, id="layout-nested-cell-sensitive-input-dropped"),
])
def test_layout_tables_render_cells_as_blocks_while_data_tables_stay(tmp_path, source, content, regions, tables):
    # GIVEN tables with or without header cells, captions and block-content cells
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "table source must reach the parser unchanged"
    # WHEN the adapter classifies layout and data tables
    result = service.run(path)
    # THEN layout cells render as ordinary blocks and only data tables are counted as tables
    assert result == (0, dict(expected_receipt(path, None, regions, tables=tables), out=None),
                      expected_document(path, content, regions, tables=tables)), (
        "layout tables must not collapse page blocks into one GFM row")


@supported_html
@pytest.mark.parametrize("source,content,regions", [
    pytest.param('<ol start="1_0"><li>a</li></ol>', '1. a\n', 1, id="start-with-underscore-ignored"),
    pytest.param('<ol start="٣"><li>a</li></ol>', '1. a\n', 1, id="start-arabic-indic-digit-ignored"),
    pytest.param('<ol><li value="1_0">a</li><li>b</li></ol>', '1. a\n2. b\n', 2,
                 id="value-with-underscore-ignored"),
    pytest.param('<ol><li value="٧">a</li></ol>', '1. a\n', 1, id="value-arabic-indic-digit-ignored"),
    pytest.param('<ol start="+3"><li>a</li></ol>', '3. a\n', 1, id="start-ascii-signed-digits-kept"),
])
def test_list_numbers_accept_only_ascii_digits(tmp_path, source, content, regions):
    # GIVEN ordered list numbers written with Python-only integer syntax or ASCII digits
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "list source must reach the parser unchanged"
    # WHEN start and value attributes are parsed
    result = service.run(path)
    # THEN only ASCII digits set the number, like the EPUB adapter
    assert result == (0, dict(expected_receipt(path, None, regions), out=None),
                      expected_document(path, content, regions)), (
        "list numbers must ignore underscores and non-ASCII digits")


@supported_html
@pytest.mark.parametrize("source,content", [
    pytest.param('<table><tr><td><a href="x"></a>t</td></tr></table>', '| t |\n| --- |\n',
                 id="cell-empty-link-dropped"),
    pytest.param('<table><tr><td><nav>cell nav</nav>text</td></tr></table>', '| text |\n| --- |\n',
                 id="cell-page-landmark-dropped"),
])
def test_table_cells_drop_page_landmarks_and_empty_links_like_the_body(tmp_path, source, content):
    # GIVEN a data table cell holding an empty link or a page landmark
    path = tmp_path / "sample.html"
    path.write_bytes(source.encode("utf-8"))
    assert path.read_bytes() == source.encode("utf-8"), "cell source must reach the parser unchanged"
    # WHEN the cell writer renders the clean default profile
    result = service.run(path)
    # THEN the cell keeps only text the body would also keep
    assert result == (0, dict(expected_receipt(path, None, 0, tables=1), out=None),
                      expected_document(path, content, 0, tables=1)), (
        "cells must open links lazily and drop page landmarks")
