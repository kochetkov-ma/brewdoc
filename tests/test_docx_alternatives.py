"""DOCX fallback selection preserves independent repetition and ordinary content."""

import hashlib
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

import brewdoc
from inspection_contract import rendering_result
from test_contract import NOT_APPLICABLE, schema_two, zero_tally
from test_documents import STRICT, TRANSITIONAL, docx, paragraph, paragraph_xml


MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
OMISSIONS = [
    "images, charts and the text drawn inside them",
    "tracked changes, comments, footnotes, headers and footers",
    "a table's own formatting - only its cells, row by row",
]
BODY_START = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'


def textbox(text):
    """Wrap a synthetic paragraph in an ordinary Word textbox."""
    return "<w:txbxContent>" + paragraph(text) + "</w:txbxContent>"


def choice(content, requires="w", prefix="mc"):
    """Declare a Choice whose requirement must not trigger extension interpretation."""
    return '<%s:Choice Requires="%s">%s</%s:Choice>' % (prefix, requires, content, prefix)


def fallback(content, prefix="mc"):
    """Declare one direct fallback branch."""
    return "<%s:Fallback>%s</%s:Fallback>" % (prefix, content, prefix)


def alternative(*branches, prefix="mc", namespace=MC):
    """Bind an explicit compatibility namespace around synthetic branch content."""
    return '<%s:AlternateContent xmlns:%s="%s">%s</%s:AlternateContent>' % (
        prefix, prefix, namespace, "".join(branches), prefix)


def expected_result(path, rendered_body, *, label="Body", regions=1, tables=0,
                    non_ascii=0, shown_name="sample.docx"):
    """Build the existing full receipt and schema from independent expected body bytes."""
    receipt = {
        "file_ok": True, "route": "doc", "reason":
        "doc rendered: 1 chapters, %d tables, %d text regions, 0 column splits" % (tables, regions),
        "source": path.name, "out": None, "receipt_schema": "brewdoc.receipt/1",
        "unit_kind": "chapter", "units": 1, "tables": tables, "text_regions": regions,
        "columns_split": 0, "broken_ligature_words": 0,
        "dropped": {**zero_tally()["dropped"], "non_ascii_replaced": non_ascii},
        "not_carried": OMISSIONS, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": ["chapter/000001"],
    }
    markdown = schema_two(
        path, rendered_body, **NOT_APPLICABLE, name=shown_name, suffix=".docx", route="doc",
        unit="chapter", count=1, keys="chapter/000001", pages=0, sheets=0, chapters=1,
        tables=tables, text_regions=regions,
        omissions="\n".join("- " + item for item in OMISSIONS),
        contents='- [Chapter 1: "%s"](#brewdoc-chapter-000001)' % label)
    markdown = markdown.replace("| Dropped non ascii replaced | 0 |",
                                "| Dropped non ascii replaced | %d |" % non_ascii)
    return 0, receipt, markdown


@pytest.mark.parametrize("namespace,prefix", [(TRANSITIONAL, "mc"), (STRICT, "compat")],
                         ids=["transitional", "strict-prefix-alias"])
def test_first_direct_qualified_fallback_is_selected_once(tmp_path, namespace, prefix):
    # GIVEN two supported-looking Choices and two different direct fallback branches
    content = alternative(
        choice(textbox("Wrong one"), prefix=prefix), choice(textbox("Wrong two"), prefix=prefix),
        fallback(textbox("Kept"), prefix), fallback(textbox("Later"), prefix), prefix=prefix)
    source = paragraph_xml('<w:t>before </w:t>' + content + '<w:t> after</w:t>')
    path = docx(tmp_path / "sample.docx", source, namespace)
    assert (source.count(":Choice "), source.count(":Fallback>")) == (2, 4), (
        "the input must contain two Choices and two separately closed direct fallbacks")
    # WHEN the public reader renders one logical alternative
    actual = rendering_result(brewdoc.run(path))
    # THEN the exact full document and receipt contain only the first direct fallback
    assert actual == expected_result(path, BODY_START + "before Kept after\n"), (
        "fallback selection must use qualified names, survive prefix aliases and preserve surrounding text")


@pytest.mark.parametrize("branches", [
    choice(textbox("Unsupported"), requires="unknown"),
    choice(alternative(fallback(textbox("Nested wrong")))),
], ids=["choice-only", "fallback-under-choice-only"])
def test_alternatives_without_a_direct_fallback_omit_choice_content(tmp_path, branches):
    # GIVEN an alternative with no direct fallback between two ordinary paragraphs
    content = alternative(branches)
    path = docx(tmp_path / "sample.docx", paragraph("before") + paragraph_xml(content) + paragraph("after"))
    root = ET.fromstring('<root xmlns:w="%s">%s</root>' % (TRANSITIONAL, content))
    assert root[0].find("{%s}Fallback" % MC) is None, "the outer alternative must have no direct fallback"
    # WHEN Choice-only content is read through the narrowed fallback contract
    actual = rendering_result(brewdoc.run(path))
    # THEN unsupported content adds neither text nor an empty region
    assert actual == expected_result(path, BODY_START + "before\n\nafter\n", regions=2), (
        "a fallback hidden inside a Choice must not become the outer alternative's selected content")


def test_nested_fallback_selection_preserves_surrounding_text_order(tmp_path):
    # GIVEN a nested alternative inside the selected outer fallback
    inner = alternative(choice(textbox("Wrong inner")), fallback(textbox("inner")))
    content = alternative(choice(textbox("Wrong outer")),
                          fallback('<w:t>before </w:t>' + inner + '<w:t> after</w:t>'))
    path = docx(tmp_path / "sample.docx", paragraph_xml(content))
    assert content.count(":AlternateContent ") == 2, "the input must contain outer and nested alternatives"
    # WHEN both selected branches are traversed in source order
    actual = rendering_result(brewdoc.run(path))
    # THEN nested selection leaves ordinary text on both sides in place
    assert actual == expected_result(path, BODY_START + "before inner after\n"), (
        "nested fallbacks must preserve depth-first content order without consuming either Choice")


def test_heading_label_uses_the_same_selected_fallback_as_heading_text(tmp_path):
    # GIVEN different Choice and Fallback labels inside a Heading1 paragraph
    content = alternative(choice(textbox("Wrong title")), fallback(textbox("Chosen title")))
    path = docx(tmp_path / "sample.docx", paragraph_xml(content, "Heading1") + paragraph("body"))
    assert content.count("Chosen title") == 1, "the selected heading label must have one physical source"
    # WHEN a heading defines the chapter label and contents entry
    actual = rendering_result(brewdoc.run(path))
    # THEN both navigation and body heading retain the same exact selected label
    expected = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Chosen title"\n\nbody\n'
    assert actual == expected_result(path, expected, label="Chosen title"), (
        "heading-label traversal must obey fallback selection just like paragraph extraction")


def test_table_cell_text_selects_fallback_without_changing_column_alignment(tmp_path):
    # GIVEN a two-column table with alternatives in one header and one data cell
    header = paragraph_xml(alternative(choice(textbox("Wrong header")), fallback(textbox("A"))))
    data = paragraph_xml(alternative(choice(textbox("Wrong cell")), fallback(textbox("x"))))
    source = ("<w:tbl><w:tr><w:tc>" + header + "</w:tc><w:tc>" + paragraph("B")
              + "</w:tc></w:tr><w:tr><w:tc>" + data + "</w:tc><w:tc>" + paragraph("y")
              + "</w:tc></w:tr></w:tbl>")
    path = docx(tmp_path / "sample.docx", source)
    assert source.count("<w:tc>") == 4, "the selected text must belong to four independent table cells"
    # WHEN the existing direct row/cell reader flattens each selected cell's text
    actual = rendering_result(brewdoc.run(path))
    # THEN header and data values retain their source columns and exact table count
    assert actual == expected_result(path, BODY_START + "| A | B |\n| --- | --- |\n| x | y |\n",
                                     regions=0, tables=1), (
        "fallback selection must flow into table cells without deduplicating rows or shifting columns")


@pytest.mark.parametrize("source,rendered,regions,textboxes", [
    (paragraph("Repeat") + paragraph("Repeat"), "Repeat\n\nRepeat\n", 2, 0),
    (paragraph_xml(textbox("Repeat")) + paragraph_xml(textbox("Repeat")), "Repeat\n\nRepeat\n", 2, 2),
    (paragraph_xml(textbox("Repeat") + textbox("Repeat")), "RepeatRepeat\n", 1, 2),
], ids=["separate-paragraphs", "separate-textbox-paragraphs", "textboxes-in-one-paragraph"])
def test_identical_independent_paragraphs_and_textboxes_are_retained(tmp_path, source, rendered, regions, textboxes):
    # GIVEN two physically independent repetitions without an alternative wrapper
    path = docx(tmp_path / "sample.docx", source)
    assert (source.count("<w:txbxContent>"), source.count("Repeat")) == (textboxes, 2), (
        "the input must carry two separate text sources, not one logical alternative")
    # WHEN ordinary Word content is rendered
    actual = rendering_result(brewdoc.run(path))
    # THEN each independent repetition remains in its original paragraph position
    assert actual == expected_result(path, BODY_START + rendered, regions=regions), (
        "alternative selection must not become text-value deduplication")


@pytest.mark.parametrize("source,rendered", [
    (paragraph_xml(textbox("Solo")), "Solo\n"),
    (paragraph_xml('<w:t>one</w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t>'), "one two\nthree\n"),
    (paragraph_xml(alternative(choice(textbox("Choice")), fallback(textbox("Fallback")), namespace=MC + "/")),
     "ChoiceFallback\n"),
], ids=["ordinary-textbox", "ordinary-text-controls", "near-match-compatibility-namespace"])
def test_ordinary_content_without_qualified_alternatives_stays_exact(tmp_path, source, rendered):
    # GIVEN a textbox, text controls or lookalike names outside the exact compatibility namespace
    path = docx(tmp_path / "sample.docx", source)
    assert path.is_file() is True, "the unchanged-control input must be a readable synthetic DOCX"
    # WHEN the public reader follows ordinary qualified Word text
    actual = rendering_result(brewdoc.run(path))
    # THEN full bytes and receipts preserve the existing ordinary-content behavior
    assert actual == expected_result(path, BODY_START + rendered), (
        "only exact compatibility AlternateContent names may trigger fallback selection")


def test_deep_selected_fallbacks_do_not_depend_on_python_recursion(tmp_path):
    # GIVEN more selected fallback levels than the current Python recursion allowance
    levels = sys.getrecursionlimit() + 50
    opening = '<mc:AlternateContent xmlns:mc="%s"><mc:Fallback>' % MC
    closing = "</mc:Fallback></mc:AlternateContent>"
    content = opening * levels + textbox("Deep") + closing * levels
    path = docx(tmp_path / "sample.docx", paragraph_xml(content))
    assert content.count("<mc:AlternateContent ") == levels, "every selected level must be physically present"
    # WHEN the public reader traverses the accepted XML depth
    actual = rendering_result(brewdoc.run(path))
    # THEN fallback handling retains exact content without a recursive-walker failure
    assert actual == expected_result(path, BODY_START + "Deep\n"), (
        "selected fallback traversal must remain iterative at valid XML depths")


@pytest.mark.corpus
def test_licensed_textbox_alternative_renders_once_with_exact_receipt():
    # GIVEN the unchanged Apache-2.0 Tika fixture with two representations of one textbox
    path = Path(__file__).parent / "fixtures/docx/tika-testWORD_various.docx"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "f7761e3893d795ed09f12f89a16d3265c3919da549adf00127b0f7094bccd0ee"
    ), "the corpus regression must preserve the licensed fixture's recorded bytes"
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    alternatives = root.findall(".//{%s}AlternateContent" % MC)
    assert len(alternatives) == 1, "the licensed document must contain one logical alternative"
    branches = tuple((node.tag, tuple(text.text for text in node.findall(".//{%s}t" % TRANSITIONAL)))
                     for node in alternatives[0])
    assert (len(alternatives), branches) == (1, (
        ("{%s}Choice" % MC, ("Here is a text box",)),
        ("{%s}Fallback" % MC, ("Here is a text box",)),
    )), "the source must contain exactly two alternative representations of one textbox"
    # WHEN the public service renders the real main-story content
    actual = rendering_result(brewdoc.run(path))
    # THEN the logical textbox appears once while every other body region and receipt field survives
    expected_body = BODY_START + (
        "Here is a text boxFootnote appears here\n\n"
        "Bold italic underline superscript subscript strikethrough\n\nitalic\n\nitalic\n\nunderline\n\n"
        "Here is a list:\n\nBullet 1\n\nBullet 2\n\nBullet 3\n\nHere is a numbered list:\n\n"
        "Number bullet 1\n\nNumber bullet 2\n\nNumber bullet 3\n\nKeyword1 Keyword2\n\n"
        "This is a hyperlink\n\nSubject is here\n\n"
        "| Row 1 Col 1 | Row 1 Col 2 | Row 1 Col 3 |\n| --- | --- | --- |\n"
        "| Row 2 Col 1 | Row 2 Col 2 | Row 2 Col 3 |\n\nSuddenly some Japanese text:\n\n????????????\n\n"
        "(GHQ)\n\nAnd then some Gothic text:\n\n??????\n\nHere is a citation:\n\n(Kramer)\n\n"
        "Figure 1 This is a caption for Figure 1\n\nRow 1 column 1\n\nRow 2 column 1\n\n"
        "Row 1 column 2\n\nRow 2 column 2\n")
    assert actual == expected_result(
        path, expected_body, regions=28, tables=1, non_ascii=18,
        shown_name="tika-testWORD_various.docx"), (
        "the licensed duplicate must disappear without dropping independent document text or changing counters")
