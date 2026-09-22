import zipfile
from pathlib import Path

import pytest

import brewdoc


TRANSITIONAL = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT = "http://purl.oclc.org/ooxml/wordprocessingml/main"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def package(path: Path, members: dict[str, str | bytes]) -> Path:
    """Write the supplied members as one synthetic DOCX package."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def relationships(target="word/document.xml") -> str:
    """Return one package relationship naming the main document part."""
    return ('<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" '
            'Target="%s"/></Relationships>' % (PACKAGE, OFFICE, target))


def document(body: str, namespace=TRANSITIONAL, prefix="w") -> str:
    """Wrap main-story XML in a document using the selected WordprocessingML prefix."""
    return ('<%s:document xmlns:%s="%s"><%s:body>%s</%s:body></%s:document>'
            % (prefix, prefix, namespace, prefix, body, prefix, prefix))


def docx(path: Path, body: str, namespace=TRANSITIONAL, prefix="w") -> Path:
    """Write a minimal readable DOCX package."""
    return package(path, {"_rels/.rels": relationships(),
                          "word/document.xml": document(body, namespace, prefix)})


def body(markdown: str) -> str:
    """Return rendered units without the metadata and contents frame."""
    marker = '<a id="brewdoc-chapter-000001"></a>'
    return markdown[markdown.index(marker):]


def paragraph(text: str, style="", prefix="w") -> str:
    """Return one text paragraph with an optional direct style."""
    return paragraph_xml("<%s:t>%s</%s:t>" % (prefix, text, prefix), style, prefix)


def paragraph_xml(content: str, style="", prefix="w") -> str:
    """Return one paragraph around caller-supplied run XML."""
    properties = ("<%s:pPr><%s:pStyle %s:val=\"%s\"/></%s:pPr>"
                  % (prefix, prefix, prefix, style, prefix)) if style else ""
    return "<%s:p>%s<%s:r>%s</%s:r></%s:p>" % (
        prefix, properties, prefix, content, prefix, prefix)


def cell(text: str, span="", prefix="w") -> str:
    """Return one WordprocessingML table cell with optional cell properties."""
    return "<%s:tc>%s%s</%s:tc>" % (prefix, span, paragraph(text, prefix=prefix), prefix)


def test_docx_keeps_main_story_paragraph_and_table_order(tmp_path):
    # GIVEN paragraph, table, and paragraph blocks in main-story order
    table = "<w:tbl><w:tr>%s%s</w:tr></w:tbl>" % (cell("A"), cell("B"))
    path = docx(tmp_path / "ordered.docx", paragraph("before") + table + paragraph("after"))

    # WHEN the public DOCX renderer reads the package
    markdown, tally = brewdoc.render_doc(path)

    # THEN the Markdown preserves block order and the table remains aligned
    assert body(markdown) == (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'
        "before\n\n| A | B |\n| --- | --- |\n\nafter\n"
    ), "paragraphs and tables must follow their order in the main document body"
    assert (tally["chapters"], tally["tables"], tally["text_regions"]) == (1, 1, 2), (
        "the ordered synthetic document must count one chapter, one table, and two text regions"
    )


def test_docx_recognizes_only_title_and_heading_one_through_nine(tmp_path):
    # GIVEN supported heading styles around an unsupported Heading0 paragraph
    source = (paragraph("Document title", "Title") + paragraph("title body")
              + paragraph("First", "Heading1") + paragraph("first body")
              + paragraph("Ninth", "Heading9") + paragraph("Heading zero", "Heading0")
              + paragraph("ninth body"))
    path = docx(tmp_path / "headings.docx", source)

    # WHEN the document is rendered
    markdown, tally = brewdoc.render_doc(path)

    # THEN Title, Heading1, and Heading9 split chapters while Heading0 remains body text
    expected = (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Document title"\n\ntitle body\n\n'
        '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "First"\n\nfirst body\n\n'
        '<a id="brewdoc-chapter-000003"></a>\n## Chapter 3: "Ninth"\n\n'
        "Heading zero\n\nninth body\n"
    )
    assert body(markdown) == expected, "Heading0 must not be promoted to a document chapter"
    assert tally["chapters"] == 3, "only the three supported heading styles must create chapters"


def test_docx_treats_tabs_breaks_and_carriage_returns_as_text_controls(tmp_path):
    # GIVEN a heading and body paragraph containing tab, break, and carriage-return elements
    heading = paragraph_xml("<w:t>First</w:t><w:tab/><w:t>Second</w:t><w:br/>"
                            "<w:t>Third</w:t><w:cr/><w:t>Fourth</w:t>", "Title")
    prose = paragraph_xml("<w:t>one</w:t><w:tab/><w:t>two</w:t><w:br/>"
                          "<w:t>three</w:t><w:cr/><w:t>four</w:t>")
    path = docx(tmp_path / "controls.docx", heading + prose)

    # WHEN the document is rendered
    markdown, tally = brewdoc.render_doc(path)

    # THEN all three controls affect labels and lines through the same text-order walk
    assert body(markdown) == (
        '<a id="brewdoc-chapter-000001"></a>\n'
        '## Chapter 1: "First Second Third Fourth"\n\none two\nthree\nfour\n'
    ), "w:cr must behave like w:br, while w:tab becomes a space"
    assert tally["text_regions"] == 1, "the body paragraph must remain one text region"


def test_strict_docx_with_a_nonstandard_prefix_renders_like_transitional(tmp_path):
    # GIVEN Strict WordprocessingML whose namespace is bound to q instead of w
    source = (paragraph("Strict title", "Heading1", "q")
              + paragraph("strict body", prefix="q")
              + "<q:tbl><q:tr>%s%s</q:tr></q:tbl>" % (
                  cell("Left", prefix="q"), cell("Right", prefix="q")))
    path = docx(tmp_path / "strict.docx", source, STRICT, "q")

    # WHEN the public DOCX renderer reads the package
    markdown, tally = brewdoc.render_doc(path)

    # THEN namespace URI drives body, style, text, and table lookup
    assert body(markdown) == (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Strict title"\n\n'
        "strict body\n\n| Left | Right |\n| --- | --- |\n"
    ), "Strict WordprocessingML must render independently of its XML prefix"
    assert (tally["chapters"], tally["tables"], tally["text_regions"]) == (1, 1, 1), (
        "Strict content must feed the same chapter, table, and text counters"
    )


def test_docx_grid_spans_default_invalid_and_nonpositive_values_to_one(tmp_path):
    # GIVEN cells with valid, absent, missing-value, malformed, zero, and negative spans
    valid = '<w:tcPr><w:gridSpan w:val="3"/></w:tcPr>'
    cases = (cell("valid", valid), cell("absent"),
             cell("missing", "<w:tcPr><w:gridSpan/></w:tcPr>"),
             cell("malformed", '<w:tcPr><w:gridSpan w:val="abc"/></w:tcPr>'),
             cell("zero", '<w:tcPr><w:gridSpan w:val="0"/></w:tcPr>'),
             cell("negative", '<w:tcPr><w:gridSpan w:val="-2"/></w:tcPr>'))
    table = "<w:tbl>%s</w:tbl>" % "".join("<w:tr>%s</w:tr>" % item for item in cases)
    path = docx(tmp_path / "spans.docx", table)

    # WHEN the table is rendered
    markdown, tally = brewdoc.render_doc(path)

    # THEN only the valid span expands; every invalid or nonpositive spelling occupies one column
    assert body(markdown) == (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'
        "| valid |  |  |\n| --- | --- | --- |\n| absent |  |  |\n| missing |  |  |\n"
        "| malformed |  |  |\n| zero |  |  |\n| negative |  |  |\n"
    ), "an unusable gridSpan value must preserve one cell instead of failing or expanding it"
    assert tally["tables"] == 1, "the six-row source must remain one rendered table"


@pytest.mark.parametrize(
    ("name", "members", "reason"),
    [
        pytest.param("missing-rels.docx", {"word/document.xml": document("")},
                     "OOXML part is missing: _rels/.rels", id="missing-package-relationships"),
        pytest.param("malformed-rels.docx", {"_rels/.rels": "<",
                                             "word/document.xml": document("")},
                     "cannot parse OOXML part _rels/.rels: unclosed token: line 1, column 0",
                     id="malformed-package-relationships"),
        pytest.param("missing-office.docx", {"_rels/.rels":
                                             '<Relationships xmlns="%s"/>' % PACKAGE},
                     "OOXML office document relationship is missing",
                     id="missing-office-document-relationship"),
        pytest.param("missing-main.docx", {"_rels/.rels": relationships()},
                     "OOXML part is missing: word/document.xml", id="missing-main-part"),
        pytest.param("malformed-main.docx", {"_rels/.rels": relationships(),
                                             "word/document.xml": "<"},
                     "cannot parse OOXML part word/document.xml: unclosed token: line 1, column 0",
                     id="malformed-main-part"),
        pytest.param("missing-body.docx", {"_rels/.rels": relationships(),
                                           "word/document.xml":
                                           '<w:document xmlns:w="%s"/>' % TRANSITIONAL},
                     "no document body: {path} carries no <w:body> in word/document.xml",
                     id="missing-document-body"),
    ],
)
def test_docx_archive_and_xml_boundaries_return_exact_refusals(tmp_path, name, members, reason):
    # GIVEN a DOCX package broken at one required archive or XML boundary
    path = package(tmp_path / name, members)
    out = tmp_path / "must-not-exist.md"

    # WHEN the service is asked to render it to a file
    code, receipt, markdown = brewdoc.run(path, out)

    # THEN the refusal is empty, names its boundary, and never creates an output
    assert (code, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], markdown) == (
        1, False, "doc", "chapter", 0, ""
    ), "a broken DOCX must return the document route's zero-unit refusal receipt"
    assert receipt["reason"] == reason.format(path=path), "the refusal must name the exact failed boundary"
    assert not out.exists(), "a refused DOCX must not leave an output file"


def test_non_zip_docx_returns_an_exact_refusal(tmp_path):
    # GIVEN a .docx path whose bytes are not a ZIP archive
    path = tmp_path / "not-zip.docx"
    path.write_bytes(b"not a ZIP archive")
    out = tmp_path / "must-not-exist.md"

    # WHEN the service is asked to render it to a file
    code, receipt, markdown = brewdoc.run(path, out)

    # THEN the archive reader's exact failure is carried by a zero-unit document refusal
    assert (code, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], markdown) == (
        1, False, "doc", "chapter", 0, ""
    ), "a non-ZIP .docx must return the document route's zero-unit refusal receipt"
    assert receipt["reason"] == "document unreadable: %s: File is not a zip file" % path, (
        "the refusal must preserve the BadZipFile boundary and source path"
    )
    assert not out.exists(), "a non-ZIP .docx must not leave an output file"
