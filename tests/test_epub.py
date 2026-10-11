"""Synthetic EPUB contracts stay separate from licensed publication acceptance."""

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import zipfile

import pytest

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
    "image pixels and graphical SVG content", "unsupported SVG and MathML semantics",
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
                    'First e\u0301\u00a0word\u00ad\\.\n')


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
        **NOT_APPLICABLE, name="sample\\.epub", suffix=".epub", route="epub", unit="chapter",
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
    rc, receipt, markdown = brewdoc.run(path)
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
                 "4. A\n8. B\n7. C\n    - Nested\n", 4, 0, id="ordered-nested-list"),
    pytest.param('<p>first</p><blockquote><p>quote</p><blockquote><p>inner</p></blockquote>'
                 '</blockquote><p>last</p>',
                 "first\n\nQuote (level 1):\n\nquote\n\nQuote (level 2):\n\ninner\n\n"
                 "End quote (level 2).\n\nEnd quote (level 1).\n\nlast\n", 4, 0,
                 id="nested-quote-boundaries"),
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
                 'List item (level 1, marker "-"):\n\nBefore\n\n| A | B |\n| --- | --- |\n\n'
                 'After\n\nEnd list item (level 1).\n', 2, 1,
                 id="table-contained-in-list-item"),
    pytest.param('<ul><li>Before<blockquote><p>quote</p><blockquote><p>inner</p></blockquote>'
                 '</blockquote>After</li></ul>',
                 'List item (level 1, marker "-"):\n\nBefore\n\nQuote (level 1):\n\nquote\n\n'
                 'Quote (level 2):\n\ninner\n\nEnd quote (level 2).\n\nEnd quote (level 1).\n\n'
                 'After\n\nEnd list item (level 1).\n',
                 4, 0, id="nested-quotes-contained-in-list-item"),
    pytest.param('<ul><li>Before<pre>&lt;x&gt; &amp;\n</pre>After</li></ul>',
                 'List item (level 1, marker "-"):\n\nBefore\n\n```\n<x> &\n\n```\n\n'
                 'After\n\nEnd list item (level 1).\n', 3, 0, id="literal-code-contained-in-list-item"),
    pytest.param('<ul><li>Before<p id="one">One <span id="inline">inline</span></p>'
                 '<blockquote><p id="quote">quote</p></blockquote>After</li></ul>',
                 'List item (level 1, marker "-"):\n\nBefore\n\n'
                 '<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
                 '<a id="brewdoc-chapter-000001-fragment-000002"></a>\nOne inline\n\n'
                 'Quote (level 1):\n\n<a id="brewdoc-chapter-000001-fragment-000003"></a>\n'
                 'quote\n\nEnd quote (level 1).\n\nAfter\n\nEnd list item (level 1).\n',
                 4, 0, id="paragraph-quote-anchors-with-item-continuation"),
    pytest.param('<h2>Example<code>x&lt;y</code>Title</h2>',
                 '#### Example\n\n```\nx<y\n```\n\n#### Title\n', 3, 0,
                 id="heading-segments-surround-native-code"),
    pytest.param('<table><tr><td>before<code>&lt;x&gt; &amp; ```\n\t  literal</code>after</td>'
                 '<td>x|y</td></tr></table>',
                 '| before Code: text="&lt;x&gt;&#32;&amp;&#32;\\`\\`\\`&#10;&#9;&#32;&#32;literal" after | '
                 'x\\|y |\n| --- | --- |\n', 0, 1, id="literal-code-metadata-at-table-cell-position"),
])
def test_static_xhtml_preserves_ordered_content_regions(tmp_path, content, expected, regions, tables):
    # GIVEN one chapter with representative static content and a later auxiliary chapter
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml(content)
    path = write_epub(tmp_path / "sample.epub", members)
    assert zipfile.is_zipfile(path) is True, "content probes must retain valid EPUB packaging"
    body = TWO_CHAPTER_BODY.replace("café 東京\n", expected)
    # WHEN the publication renders through the public API
    rc, receipt, markdown = brewdoc.run(path)
    # THEN source order, containment, counters and the complete Markdown envelope are exact
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, regions=regions + 1, tables=tables),
        expected_document(path, body, regions=regions + 1, tables=tables),
    ), "static XHTML must preserve the independently specified content and full receipt"


@pytest.mark.parametrize("options", [{"sheets": ("One",)},
                                    {"artifact_outputs": {"formula/sheet/000001": "calc.json"}}],
                         ids=["sheet-selection", "artifact-output"])
def test_epub_refuses_workbook_options_without_replacing_output(tmp_path, options):
    # GIVEN a valid publication and an existing output sentinel
    path = write_epub(tmp_path / "sample.epub", epub3_members())
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert out.read_bytes() == b"existing output\n", "the destination sentinel must start intact"
    # WHEN workbook-only options are supplied to EPUB conversion
    rc, receipt, markdown = brewdoc.run(path, out, **options)
    # THEN refusal has no partial units or output replacement
    assert (rc, receipt["file_ok"], receipt["route"], receipt["units"], receipt["reason"],
            markdown, out.read_bytes()) == (
        1, False, "epub", 0, "--sheet and --artifact are workbook-only options", "",
        b"existing output\n",
    ), "workbook options must refuse EPUB before output writes"


def test_public_epub_wrapper_and_uppercase_suffix_preserve_tally_and_utf8(tmp_path):
    # GIVEN a valid publication under the uppercase registered suffix
    path = write_epub(tmp_path / "sample.EPUB", epub3_members())
    assert path.suffix == ".EPUB", "the public wrapper probe must exercise suffix normalization"
    # WHEN the public wrapper and service each convert the same publication
    markdown, tally = brewdoc.render_epub(path)
    rc, receipt, api_markdown = brewdoc.run(path)
    # THEN the wrapper retains its two-value shape and the service retains full Unicode output
    expected = expected_document(path).replace('"sample\\.epub"', '"sample\\.EPUB"')
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
    assert members["OPS/b.xhtml"][:2] in (b"\xff\xfe", b"\xfe\xff"), "the XML probe must carry a UTF-16 BOM"
    # WHEN Expat and ElementTree consume the same bounded required member bytes
    result = brewdoc.run(path)
    # THEN XML encodings and inert bare doctypes retain the full Unicode contract
    assert result == (0, expected_receipt(path), expected_document(path)), (
        "UTF-16 and bare doctypes must render exact Unicode without external entity resolution")


def test_empty_qualified_body_remains_a_declared_spine_unit(tmp_path):
    # GIVEN an empty first spine body followed by the auxiliary text chapter
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml("")
    path = write_epub(tmp_path / "sample.epub", members)
    assert "<body></body>" in members["OPS/b.xhtml"], "the first body must be qualified and empty"
    # WHEN the package converts through the public service
    result = brewdoc.run(path)
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
    assert attribute in members["OPS/b.xhtml"], "the hiding guard must apply to the body itself"
    # WHEN the included publication flow excludes the entire hidden body
    result = brewdoc.run(path)
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
    result = brewdoc.run(path)
    # THEN second-item anchors follow the first item and start the second item's region
    listing = ('- One\n\n<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
               'List item (level 1, marker "-"):\n\n'
               '<a id="brewdoc-chapter-000001-fragment-000002"></a>\nTwo inline\n\n'
               'End list item (level 1).\n')
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
    assert '\n\t  literal' in members["OPS/b.xhtml"], "literal code must include whitespace to preserve"
    # WHEN inline code is lowered to a native safe fenced region at its source position
    result = brewdoc.run(path)
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
    maximum = len(expected.encode("utf-8"))
    assert len(target) == 2024, "the URL must contain exactly the bounded ASCII target bytes"
    # WHEN the complete Markdown budget equals its exact byte count and then drops by one
    monkeypatch.setitem(epub.LIMITS, "markdown_bytes", maximum)
    accepted = brewdoc.run(path)
    monkeypatch.setitem(epub.LIMITS, "markdown_bytes", maximum - 1)
    out.write_bytes(b"existing output\n")
    rc, receipt, markdown = brewdoc.run(path, out)
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
    result = brewdoc.run(path)
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
    assert "font/otf" in members["OPS/book.opf"], "font acceptance requires a declared font media type"
    # WHEN the EPUB reader validates encryption before reading chapter XML
    result = brewdoc.run(path)
    # THEN font-only obfuscation leaves the publication's exact text and receipts available
    assert result == (0, expected_receipt(path), expected_document(path)), (
        "recognized font obfuscation must not attempt font decoding or refuse reading text")


def test_images_distinguish_absent_metadata_from_declared_empty_values(tmp_path):
    # GIVEN image-only content with empty metadata followed by absent metadata
    members = epub3_members()
    members["OPS/b.xhtml"] = xhtml('<img alt="" title="" src="pictures/one.png"/>'
                                   '<img src="pictures/two.png"/>')
    path = write_epub(tmp_path / "sample.epub", members)
    assert len(members) == 6, "image metadata must not require archive image payloads"
    # WHEN image-only declared content renders without extraction or fetching
    result = brewdoc.run(path)
    # THEN source image position and absent-versus-empty declarations are preserved inertly
    image_text = ('Image: alt=""; title=""; src="pictures/one\\.png"\n\n'
                  'Image: alt=unspecified; title=unspecified; src="pictures/two\\.png"\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", image_text)
    assert result == (0, expected_receipt(path, regions=3),
                      expected_document(path, body, regions=3)), (
        "image metadata must preserve declared values and mark absent values explicitly")


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
    result = brewdoc.run(path)
    # THEN generated anchors appear at the containing region and rewritten links remain local
    first = ('<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
             '<a id="brewdoc-chapter-000001-fragment-000002"></a>\n'
             'One inline [self](#brewdoc-chapter-000001-fragment-000001)\n')
    second = ('[cross](#brewdoc-chapter-000001-fragment-000002) '
              '[web](https://example.invalid/path)\n')
    body = TWO_CHAPTER_BODY.replace("café 東京\n", first).replace(
        "First e\u0301\u00a0word\u00ad\\.\n", second)
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
    assert 'fallback="b"' in members["OPS/book.opf"], "the unsupported item must declare its endpoint"
    # WHEN fallback resolution retains both original spine occurrences
    result = brewdoc.run(path)
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
    receipt_bytes, _, markdown_bytes = stdout.stdout.partition(os.linesep.encode("ascii"))
    # THEN ASCII JSON precedes exact UTF-8 Markdown and --out emits receipt-only stdout
    expected = expected_document(path).encode("utf-8")
    file_receipt = dict(expected_receipt(path), out=str(out))
    assert (stdout.returncode, stdout.stderr, receipt_bytes, markdown_bytes,
            file_run.returncode, file_run.stderr, file_run.stdout, out.read_bytes()) == (
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
    rc, receipt, markdown = brewdoc.run(path, out)
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
    result = brewdoc.run(path, out)
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
    assert "not-in-spine.xhtml" in navigation, "the hidden branch must have an unavailable target"
    # WHEN included navigation walks only the visible publication flow
    result = brewdoc.run(path)
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
    rc, receipt, markdown = brewdoc.run(path, out)
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
    rc, receipt, markdown = brewdoc.run(path, out)
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
        "First e\u0301\u00a0word\u00ad\\.\n", rendered_table)
    expected = expected_document(path, body, regions=0, tables=2)
    lengths = {name: len(text.encode("utf-8")) for name, text in members.items()}
    limits = {
        "source_bytes": path.stat().st_size, "members": 6,
        "member_bytes": max(lengths.values()), "declared_bytes": sum(lengths.values()),
        "consumed_bytes": sum(lengths.values()) + lengths["OPS/a.xhtml"] + lengths["OPS/b.xhtml"],
        "document_elements": 14, "total_elements": 71, "depth": 6, "spine_items": 2,
        "table_columns": 2, "table_slots": 4, "total_table_slots": 8,
        "markdown_bytes": len(expected.encode("utf-8")),
    }
    assert tuple(lengths) == ("mimetype", "META-INF/container.xml", "OPS/book.opf",
                             "OPS/a.xhtml", "OPS/b.xhtml", "OPS/nav.xhtml"), (
        "the boundary calculation requires exactly the six declared synthetic members")
    # WHEN the limit equals the consumed amount and then drops by exactly one
    monkeypatch.setitem(epub.LIMITS, budget, limits[budget])
    accepted = brewdoc.run(path)
    monkeypatch.setitem(epub.LIMITS, budget, limits[budget] - 1)
    out.write_bytes(b"existing output\n")
    refused = brewdoc.run(path, out)
    # THEN the inclusive case succeeds and the one-over case preserves all output bytes
    assert accepted == (0, expected_receipt(path, regions=0, tables=2), expected), (
        "the EPUB %s budget must accept its exact inclusive boundary" % budget)
    assert (refused[0], refused[1]["file_ok"], refused[1]["units"], refused[2], out.read_bytes()) == (
        1, False, 0, "", b"existing output\n",
    ), "the EPUB %s one-over budget must refuse atomically" % budget
    assert re.sub(r"\AEPUB .*limit.*\Z", "LIMIT", refused[1]["reason"]) == "LIMIT", (
        "the one-over refusal must identify its resource limit: " + refused[1]["reason"])
