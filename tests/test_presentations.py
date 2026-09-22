from __future__ import annotations

import json
import zipfile
from html import escape
from pathlib import Path

import pytest

import brewdoc
from brewdoc import common
from brewdoc.cli import main
from brewdoc.pptx import render_presentation

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"


def relationship(relation_id: str, kind: str, target: str, mode: str = "") -> str:
    """Build one Transitional package relationship."""
    mode_xml = {False: "", True: ' TargetMode="%s"' % mode}[bool(mode)]
    return ('<Relationship Id="%s" Type="%s/%s" Target="%s"%s/>'
            % (relation_id, R, kind, escape(target), mode_xml))


def relationships(*items: str) -> str:
    """Wrap relationship elements in their package root."""
    return '<Relationships xmlns="%s">%s</Relationships>' % (PACKAGE, "".join(items))


def paragraph(*tokens: str | tuple[str, str]) -> str:
    """Build one DrawingML paragraph from text and empty control elements."""
    renderers = {
        str: lambda token: "<a:r><a:t>%s</a:t></a:r>" % escape(token),
        tuple: lambda token: "<a:%s/>" % token[0],
    }
    content = [renderers[type(token)](token) for token in tokens]
    return "<a:p>%s</a:p>" % "".join(content)


def shape(name: str, *paragraphs: str, placeholder: str | None = None) -> str:
    """Build a text shape with optional placeholder semantics."""
    placeholder_xml = {
        False: "", True: '<p:ph type="%s"/>' % placeholder,
    }[placeholder is not None]
    return ('<p:sp><p:nvSpPr><p:cNvPr id="2" name="%s"/><p:cNvSpPr/>'
            '<p:nvPr>%s</p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>'
            '<a:lstStyle/>%s</p:txBody></p:sp>'
            % (escape(name), placeholder_xml, "".join(paragraphs)))


def group(*items: str) -> str:
    """Build a group whose child order remains observable."""
    return ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="8" name="Group"/>'
            '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>%s</p:grpSp>'
            % "".join(items))


def table(rows: list[list[tuple[str, dict[str, str]]]]) -> str:
    """Build a DrawingML table including caller-supplied merge attributes."""
    rendered_rows = []
    for row in rows:
        cells = []
        for text, attributes in row:
            attrs = "".join(' %s="%s"' % item for item in attributes.items())
            cells.append('<a:tc%s><a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody>'
                         '<a:tcPr/></a:tc>' % (attrs, paragraph(text)))
        rendered_rows.append('<a:tr h="1">%s</a:tr>' % "".join(cells))
    return ('<p:graphicFrame><p:nvGraphicFramePr/><p:xfrm/><a:graphic><a:graphicData>'
            '<a:tbl><a:tblPr/><a:tblGrid/>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>' % "".join(rendered_rows))


def picture(relation: str, *, linked: bool = False, name: str = "Photo",
            descr: str | None = "Alt", title: str | None = "Caption",
            cx: str = "120", cy: str = "80") -> str:
    """Build a picture carrying non-visual metadata and one blip relationship."""
    values = (("descr", descr), ("title", title))
    renderers = {
        type(None): lambda key, value: "",
        str: lambda key, value: ' %s="%s"' % (key, escape(value)),
    }
    optional = "".join(renderers[type(value)](key, value) for key, value in values)
    attribute = {False: "embed", True: "link"}[linked]
    return ('<p:pic><p:nvPicPr><p:cNvPr id="4" name="%s"%s/><p:cNvPicPr/>'
            '<p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:%s="%s"/></p:blipFill>'
            '<p:spPr><a:xfrm><a:ext cx="%s" cy="%s"/></a:xfrm></p:spPr></p:pic>'
            % (escape(name), optional, attribute, relation, escape(cx), escape(cy)))


def slide(*items: str) -> str:
    """Wrap shape-tree children in a minimal slide part."""
    return ('<p:sld xmlns:p="%s" xmlns:a="%s" xmlns:r="%s"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr/><p:grpSpPr/>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, R, "".join(items)))


def notes(*items: str) -> str:
    """Wrap shape-tree children in a minimal notes part."""
    return ('<p:notes xmlns:p="%s" xmlns:a="%s" xmlns:r="%s"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr/><p:grpSpPr/>%s</p:spTree></p:cSld></p:notes>'
            % (P, A, R, "".join(items)))


def presentation(declarations: list[tuple[str, bool]]) -> str:
    """Build a presentation list with declared order and hidden flags."""
    slides = "".join('<p:sldId id="%d" r:id="%s"%s/>'
                     % (256 + index, relation_id, {False: "", True: ' show="0"'}[hidden])
                     for index, (relation_id, hidden) in enumerate(declarations))
    return ('<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst>%s'
            '</p:sldIdLst></p:presentation>' % (P, R, slides))


def base_parts(slides: list[tuple[str, str, bool]]) -> dict[str, bytes | str]:
    """Build the package and presentation boundaries for declared slide targets."""
    declarations = [(relation_id, hidden) for relation_id, _part, hidden in slides]
    slide_relations = [relationship(relation_id, "slide", part)
                       for relation_id, part, _hidden in slides]
    parts: dict[str, bytes | str] = {
        "_rels/.rels": relationships(relationship(
            "rIdOffice", "officeDocument", "/deck/presentation.xml")),
        "deck/presentation.xml": presentation(declarations),
        "deck/_rels/presentation.xml.rels": relationships(*slide_relations),
    }
    return parts


def pptx(path: Path, parts: dict[str, bytes | str], *,
         duplicates: tuple[tuple[str, bytes | str], ...] = ()) -> Path:
    """Write a synthetic package, optionally preserving duplicate member names."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, payload in parts.items():
            package.writestr(name, payload)
        for name, payload in duplicates:
            package.writestr(name, payload)
    return path


def zero_tally(**changes) -> dict:
    """Return the exact empty public tally with selected overrides."""
    tally = {
        "pages": 0, "sheets": 0, "chapters": 0, "slides": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0,
        "broken_ligature_words": 0,
        "dropped": dict.fromkeys(common.DROP_KEYS, 0),
    }
    tally.update(changes)
    return tally


def body(markdown: str) -> str:
    """Return rendered slide bodies after metadata and contents navigation."""
    return markdown[markdown.index('<a id="brewdoc-slide-000001"></a>'):]


def test_declared_order_hidden_empty_and_untitled_slides_are_rendered(tmp_path):
    # GIVEN three declarations whose order differs from their member names, including hidden and empty
    slides = [("rId9", "slides/slide9.xml", False),
              ("rId2", "slides/slide2.xml", True),
              ("rId5", "slides/slide5.xml", False)]
    parts = base_parts(slides)
    parts.update({
        "deck/slides/slide9.xml": slide(shape(
            "Title", paragraph("First"), placeholder="title"),
            shape("Body", paragraph("Alpha"))),
        "deck/slides/slide2.xml": slide(),
        "deck/slides/slide5.xml": slide(shape(
            "Centered title", paragraph("Third"), placeholder="ctrTitle")),
    })
    path = pptx(tmp_path / "ordered.pptx", parts)

    # WHEN the presentation is rendered twice through the public API
    first = brewdoc.render_presentation(path)
    second = render_presentation(path)

    # THEN every declared slide keeps presentation order and stable headings, keys and counts
    assert first == second, "re-rendering one presentation must be byte deterministic"
    markdown, tally = first
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "First"\n\nAlpha\n\n'
        '<a id="brewdoc-slide-000002"></a>\n## Slide 2: "Untitled"\n\n'
        '<a id="brewdoc-slide-000003"></a>\n## Slide 3: "Third"\n'
    ), "hidden and empty slides must remain in declared order and title text must not repeat"
    assert tally == zero_tally(slides=3, text_regions=1), (
        "the slide tally and one body text region must be exact"
    )
    assert "slide/000001, slide/000002, slide/000003" in markdown, (
        "metadata must carry the exact ordered slide keys"
    )


def test_grouped_text_preserves_paragraph_run_tab_break_order_and_sanitises(tmp_path):
    # GIVEN a title and grouped body carrying runs, paragraphs, a tab, a break and Unicode
    slides = [("rId1", "slides/one.xml", False)]
    parts = base_parts(slides)
    parts["deck/slides/one.xml"] = slide(
        shape("Title", paragraph("Café"), placeholder="title"),
        group(shape("Body", paragraph("A", ("tab", ""), "B", ("br", ""), "Café"),
                    paragraph("中", "D"))))
    path = pptx(tmp_path / "text.pptx", parts)

    # WHEN the grouped slide text is rendered
    markdown, tally = render_presentation(path)

    # THEN XML order and line boundaries are retained through the shared ASCII sanitizer
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Cafe"\n\nA B\nCafe\n?D\n'
    ), "group recursion, paragraph order, tabs, breaks and Unicode folding must be exact"
    assert tally == {
        **zero_tally(slides=1, text_regions=1),
        "dropped": {**zero_tally()["dropped"], "non_ascii_replaced": 1},
    }, "only the unsupported CJK character must count as an ASCII-path loss"


def test_tables_keep_empty_and_merge_continuation_cells_aligned(tmp_path):
    # GIVEN a DrawingML table with ordinary, empty, horizontal and vertical continuation cells
    slides = [("rId1", "slides/table.xml", False)]
    parts = base_parts(slides)
    parts["deck/slides/table.xml"] = slide(table([
        [("Head", {}), ("", {}), ("Wide", {"gridSpan": "2"}),
         ("ignored", {"hMerge": "1"})],
        [("A", {}), ("B", {}), ("Tall", {"rowSpan": "2"}), ("C", {})],
        [("D", {}), ("E", {}), ("ignored", {"vMerge": "true"}), ("F", {})],
    ]))
    path = pptx(tmp_path / "table.pptx", parts)

    # WHEN the table is rendered
    markdown, tally = render_presentation(path)

    # THEN source cells retain four columns and continuation text is never expanded twice
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Untitled"\n\n'
        '| Head |  | Wide |  |\n| --- | --- | --- | --- |\n'
        '| A | B | Tall | C |\n| D | E |  | F |\n'
    ), "horizontal and vertical merge continuations must remain empty alignment cells"
    assert tally == zero_tally(slides=1, tables=1), "one table and one slide must be tallied"


def test_notes_append_only_body_placeholder_text_after_slide_content(tmp_path):
    # GIVEN a slide with related notes containing body text and all excluded furniture placeholders
    slides = [("rId1", "slides/one.xml", False)]
    parts = base_parts(slides)
    parts["deck/slides/one.xml"] = slide(shape("Body", paragraph("Slide text")))
    parts["deck/slides/_rels/one.xml.rels"] = relationships(
        relationship("rNotes", "notesSlide", "../notesSlides/notes.xml"))
    parts["deck/notesSlides/notes.xml"] = notes(
        shape("Slide image", paragraph("image"), placeholder="sldImg"),
        shape("Date", paragraph("date"), placeholder="dt"),
        shape("Number", paragraph("7"), placeholder="sldNum"),
        shape("Header", paragraph("header"), placeholder="hdr"),
        shape("Footer", paragraph("footer"), placeholder="ftr"),
        shape("Notes", paragraph("First note"), paragraph("Second note"), placeholder="body"))
    path = pptx(tmp_path / "notes.pptx", parts)

    # WHEN the slide and its notes are rendered
    markdown, tally = render_presentation(path)

    # THEN the one body placeholder follows slide content and notes furniture is absent
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Untitled"\n\n'
        'Slide text\n\nSpeaker notes:\nFirst note\nSecond note\n'
    ), "speaker notes must follow content and contain only body-placeholder text"
    assert tally == zero_tally(slides=1, text_regions=2), (
        "slide content and the combined notes block must be two text regions"
    )


def test_embedded_and_external_picture_placeholders_use_only_ooxml_metadata(tmp_path):
    # GIVEN embedded and external pictures plus nearby text that must not become a caption
    slides = [("rId1", "slides/pictures.xml", False)]
    parts = base_parts(slides)
    parts["deck/slides/pictures.xml"] = slide(
        picture("rEmbedded", name="Photo café", descr=None, title="Official"),
        shape("Nearby", paragraph("Nearby text")),
        picture("rExternal", linked=True, name="Linked", title=None,
                descr="Remote", cx="１２", cy="bad中"))
    parts["deck/slides/_rels/pictures.xml.rels"] = relationships(
        relationship("rEmbedded", "image", "../media/photo.bin"),
        relationship("rExternal", "image", "https://example.invalid/photo.png", "External"))
    parts["deck/media/photo.bin"] = b"1234567"
    path = pptx(tmp_path / "pictures.pptx", parts)

    # WHEN picture facts are rendered without decoding or fetching pixels
    markdown, tally = render_presentation(path)

    # THEN exact metadata placeholders report local bytes, external unknowns and title-only captions
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Untitled"\n\n'
        'Image: name="Photo cafe"; size=120x80 EMU; alt="none"; caption="Official"; '
        'source=embedded; bytes=7\n\nNearby text\n\n'
        'Image: name="Linked"; size=12xbad? EMU; alt="Remote"; caption="none"; '
        'source=external; bytes=unknown\n'
    ), "picture placeholders must use OOXML properties and never guess nearby captions"
    assert tally == {
        **zero_tally(slides=1, text_regions=3),
        "dropped": {**zero_tally()["dropped"], "non_ascii_replaced": 1},
    }, "all picture metadata must traverse the shared ASCII sanitizer"


def test_alternate_content_uses_fallback_once_without_rendering_choice(tmp_path):
    # GIVEN unsupported AlternateContent with pictures in both Choice and Fallback branches
    alternate = (
        '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        '<mc:Choice Requires="p14">%s</mc:Choice><mc:Fallback>%s</mc:Fallback>'
        '</mc:AlternateContent>' % (
            picture("rChoice", name="Choice"), picture("rFallback", name="Ink fallback")))
    parts = base_parts([("rId1", "slides/one.xml", False)])
    parts["deck/slides/one.xml"] = slide(alternate)
    parts["deck/slides/_rels/one.xml.rels"] = relationships(
        relationship("rFallback", "image", "../media/ink.emf"))
    parts["deck/media/ink.emf"] = b"ink"
    path = pptx(tmp_path / "alternate.pptx", parts)

    # WHEN the unsupported compatibility branch is rendered
    markdown, tally = render_presentation(path)

    # THEN only the fallback picture appears in its shape-tree position
    assert body(markdown) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Untitled"\n\n'
        'Image: name="Ink fallback"; size=120x80 EMU; alt="Alt"; caption="Caption"; '
        'source=embedded; bytes=3\n'
    ), "unsupported AlternateContent must select Fallback exactly once"
    assert tally == zero_tally(slides=1, text_regions=1), (
        "the ignored Choice branch must not add an image or text region"
    )


def test_api_cli_receipt_out_and_workbook_option_contracts_are_exact(tmp_path, capsys):
    # GIVEN one text-only slide whose slide relationship part is legitimately absent
    slides = [("rId1", "slides/one.xml", False)]
    parts = base_parts(slides)
    parts["deck/slides/one.xml"] = slide(shape("Body", paragraph("Body")))
    path = pptx(tmp_path / "public.pptx", parts)
    expected_markdown, expected_tally = render_presentation(path)

    # WHEN API, CLI stdout, --out and workbook-only selection paths are exercised
    code, receipt, markdown = brewdoc.run(path)
    cli_code = main([str(path)])
    cli_output = capsys.readouterr().out
    out = tmp_path / "rendered.md"
    out_code = main([str(path), "--out", str(out)])
    out_output = capsys.readouterr().out
    refused_out = tmp_path / "refused.md"
    refused = brewdoc.run(path, out=refused_out, sheets=("Sheet1",))

    # THEN public surface, exact receipt fields and all output paths stay coherent
    assert brewdoc.render_presentation is render_presentation, (
        "the package export must be the adapter public function"
    )
    assert (code, markdown, expected_tally) == (0, expected_markdown,
                                                zero_tally(slides=1, text_regions=1)), (
        "the service must return the exact adapter Markdown and tally behavior"
    )
    assert receipt == {
        "artifacts": [], "broken_ligature_words": 0, "columns_split": 0,
        "dropped": zero_tally()["dropped"], "file_ok": True,
        "markdown_schema": "brewdoc.markdown/2",
        "not_carried": [
            "charts, SmartArt and embedded objects", "image pixels, audio and video",
            "animations, transitions, layout and master text, styling and visual positioning",
        ],
        "out": None, "reason": "presentation rendered: 1 slides, 0 tables, 1 text regions, 0 column splits",
        "receipt_schema": "brewdoc.receipt/1", "route": "presentation",
        "source": "public.pptx", "tables": 0, "text_regions": 1,
        "unit_keys": ["slide/000001"], "unit_kind": "slide", "units": 1,
    }, "the additive presentation receipt must match the existing receipt schema exactly"
    assert (cli_code, cli_output) == (
        0, json.dumps(receipt, ensure_ascii=True, sort_keys=True) + "\n" + markdown,
    ), "CLI stdout must contain the exact API receipt followed by Markdown"
    assert (out_code, json.loads(out_output), out.read_text("ascii")) == (
        0, {**receipt, "out": str(out)}, markdown,
    ), "--out must write exact Markdown and report its path in the receipt"
    assert (refused[0], refused[1]["file_ok"], refused[1]["route"],
            refused[1]["unit_kind"], refused[1]["units"], refused[1]["out"],
            refused[1]["reason"], refused[2]) == (
        1, False, "presentation", "slide", 0, None,
        "--sheet and --artifact are workbook-only options", "",
    ), "workbook-only options must refuse PPTX without emitting Markdown"
    assert not refused_out.exists(), "a workbook-option refusal must not create output"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        pytest.param(lambda parts: parts.pop("_rels/.rels"),
                     "OOXML part is missing: _rels/.rels", id="missing-package-relationships"),
        pytest.param(lambda parts: parts.__setitem__("_rels/.rels", "<broken"),
                     "cannot parse OOXML part _rels/.rels: unclosed token: line 1, column 0",
                     id="malformed-package-relationships"),
        pytest.param(lambda parts: parts.__setitem__("_rels/.rels", relationships()),
                     "OOXML office document relationship is missing", id="missing-office-document"),
        pytest.param(lambda parts: parts.__setitem__("_rels/.rels", relationships(
            relationship("rIdOffice", "officeDocument", "https://example.invalid/deck", "External"))),
                     "OOXML office document relationship is external", id="external-office-document"),
        pytest.param(lambda parts: parts.__setitem__("_rels/.rels", relationships(
            '<Relationship Type="%s/officeDocument" Target="/deck/presentation.xml"/>' % R)),
                     "OOXML office document relationship id is missing",
                     id="missing-office-document-id"),
        pytest.param(lambda parts: parts.__setitem__("_rels/.rels", relationships(
            relationship("rIdOffice", "officeDocument", "/deck/presentation.xml"),
            relationship("rIdOffice", "coreProperties", "/docProps/core.xml"))),
                     "OOXML office document relationship rIdOffice occurs more than once",
                     id="duplicate-office-document-id"),
        pytest.param(lambda parts: parts.pop("deck/presentation.xml"),
                     "OOXML part is missing: deck/presentation.xml", id="missing-presentation"),
        pytest.param(lambda parts: parts.__setitem__("deck/presentation.xml", "<broken"),
                     "cannot parse OOXML part deck/presentation.xml: unclosed token: line 1, column 0",
                     id="malformed-presentation"),
        pytest.param(lambda parts: parts.__setitem__("deck/presentation.xml", "<wrong/>"),
                     "OOXML presentation part has unexpected root: deck/presentation.xml",
                     id="wrong-presentation-root"),
        pytest.param(lambda parts: parts.pop("deck/_rels/presentation.xml.rels"),
                     "OOXML part is missing: deck/_rels/presentation.xml.rels",
                     id="missing-presentation-relationships"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", "<broken"),
                     "cannot parse OOXML part deck/_rels/presentation.xml.rels: "
                     "unclosed token: line 1, column 0",
                     id="malformed-presentation-relationships"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships()),
                     "slide relationship rId1 is missing in deck/presentation.xml",
                     id="missing-slide-relationship"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/presentation.xml",
            '<p:presentation xmlns:p="%s"><p:sldIdLst><p:sldId id="256"/>'
            '</p:sldIdLst></p:presentation>' % P),
                     "slide relationship id is missing for slide 1",
                     id="missing-slide-relationship-id"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/presentation.xml", presentation([
                ("rId1", False), ("rId1", False)])),
                     "slide relationship rId1 is declared more than once",
                     id="duplicate-slide-declaration"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "chart", "slides/one.xml"))),
                     "slide relationship rId1 has the wrong type in deck/presentation.xml",
                     id="wrong-slide-relationship-type"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "slide", "https://example.invalid/one", "External"))),
                     "slide relationship rId1 is external in deck/presentation.xml",
                     id="external-slide-relationship"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "slide", "slides/one.xml"),
                relationship("rId1", "slide", "slides/two.xml"))),
                     "slide relationship rId1 occurs more than once in deck/presentation.xml",
                     id="duplicate-slide-relationship"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "slide", "../../outside.xml"))),
                     "OOXML relationship target leaves the package: ../../outside.xml",
                     id="escaping-slide-target"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "slide", ""))),
                     "OOXML relationship target is missing", id="missing-slide-target"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/_rels/presentation.xml.rels", relationships(
                relationship("rId1", "slide", "slides/absent.xml"))),
                     "OOXML part is missing: deck/slides/absent.xml", id="missing-slide"),
        pytest.param(lambda parts: parts.__setitem__("deck/slides/one.xml", "<broken"),
                     "cannot parse OOXML part deck/slides/one.xml: unclosed token: line 1, column 0",
                     id="malformed-slide"),
        pytest.param(lambda parts: parts.__setitem__("deck/slides/one.xml", "<wrong/>"),
                     "OOXML slide part has unexpected root: deck/slides/one.xml",
                     id="wrong-slide-root"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/slides/_rels/one.xml.rels", "<broken"),
                     "cannot parse OOXML part deck/slides/_rels/one.xml.rels: "
                     "unclosed token: line 1, column 0",
                     id="malformed-present-slide-relationships"),
        pytest.param(lambda parts: parts.__setitem__(
            "deck/slides/one.xml", '<p:sld xmlns:p="%s"><p:cSld/></p:sld>' % P),
                     "slide shape tree is missing in deck/slides/one.xml",
                     id="missing-slide-shape-tree"),
    ],
)
def test_required_opc_and_slide_boundaries_refuse_exactly(tmp_path, mutate, reason):
    # GIVEN a package with one specific required OPC, relationship, slide or shape-tree defect
    parts = base_parts([("rId1", "slides/one.xml", False)])
    parts["deck/slides/one.xml"] = slide()
    mutate(parts)
    path = pptx(tmp_path / "broken.pptx", parts)
    out = tmp_path / "broken.md"

    # WHEN the public service reads the broken package
    code, receipt, markdown = brewdoc.run(path, out=out)

    # THEN it refuses at the named boundary with no units, Markdown or output
    assert (code, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], receipt["out"], receipt["reason"], markdown) == (
        1, False, "presentation", "slide", 0, None, reason, "",
    ), "a required package boundary must refuse with its exact part or relationship name"
    assert not out.exists(), "a required package refusal must not leave a Markdown output"


@pytest.mark.parametrize(
    ("slide_xml", "relation_xml", "extra_parts", "reason"),
    [
        pytest.param(slide(), relationships(relationship(
            "rNotes", "notesSlide", "../notesSlides/missing.xml")), {},
                     "OOXML part is missing: deck/notesSlides/missing.xml", id="missing-notes"),
        pytest.param(slide(), relationships(relationship(
            "rNotes", "notesSlide", "../notesSlides/notes.xml")),
                     {"deck/notesSlides/notes.xml": "<broken"},
                     "cannot parse OOXML part deck/notesSlides/notes.xml: "
                     "unclosed token: line 1, column 0", id="malformed-notes"),
        pytest.param(slide(), relationships(relationship(
            "rNotes", "notesSlide", "../notesSlides/notes.xml", "External")), {},
                     "notes relationship rNotes is external in deck/slides/one.xml",
                     id="external-notes"),
        pytest.param(slide(), relationships(relationship(
            "rNotes", "notesSlide", "../../../outside.xml")), {},
                     "OOXML relationship target leaves the package: ../../../outside.xml",
                     id="escaping-notes"),
        pytest.param(slide(), relationships(relationship(
            "rNotes", "notesSlide", "../notesSlides/notes.xml")),
                     {"deck/notesSlides/notes.xml": '<p:notes xmlns:p="%s"><p:cSld/>'
                                                      '</p:notes>' % P},
                     "notes shape tree is missing in deck/notesSlides/notes.xml",
                     id="missing-notes-shape-tree"),
        pytest.param(slide(picture("rPic")), relationships(relationship(
            "rPic", "chart", "../media/image.bin")), {},
                     "picture relationship rPic has the wrong type in deck/slides/one.xml",
                     id="wrong-picture-type"),
        pytest.param(slide(picture("rPic")), relationships(relationship(
            "rPic", "image", "../media/missing.bin")), {},
                     "OOXML part is missing: deck/media/missing.bin", id="missing-picture-part"),
    ],
)
def test_referenced_optional_parts_refuse_at_their_actual_boundary(
        tmp_path, slide_xml, relation_xml, extra_parts, reason):
    # GIVEN a slide that references a malformed notes or picture boundary
    parts = base_parts([("rId1", "slides/one.xml", False)])
    parts["deck/slides/one.xml"] = slide_xml
    parts["deck/slides/_rels/one.xml.rels"] = relation_xml
    parts.update(extra_parts)
    path = pptx(tmp_path / "optional-broken.pptx", parts)
    out = tmp_path / "optional-broken.md"

    # WHEN the public service follows that optional relationship
    code, receipt, markdown = brewdoc.run(path, out=out)

    # THEN the referenced malformed boundary refuses the document without partial output
    assert (code, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], receipt["out"], receipt["reason"], markdown) == (
        1, False, "presentation", "slide", 0, None, reason, "",
    ), (
        "a present optional relationship must not hide a malformed referenced part"
    )
    assert not out.exists(), "a referenced optional-part refusal must not create output"


@pytest.mark.parametrize(
    ("relations", "reason"),
    [
        pytest.param(relationships(),
                     "picture relationship rPic is missing in deck/slides/one.xml",
                     id="missing-picture-relationship"),
        pytest.param(relationships(
            relationship("rPic", "image", "../media/one.bin"),
            relationship("rPic", "image", "../media/two.bin")),
                     "picture relationship rPic occurs more than once in deck/slides/one.xml",
                     id="duplicate-picture-relationship"),
        pytest.param(relationships(relationship(
            "rPic", "image", "../../../outside.bin")),
                     "OOXML relationship target leaves the package: ../../../outside.bin",
                     id="escaping-picture-target"),
    ],
)
def test_picture_relationship_defects_refuse_without_partial_output(tmp_path, relations, reason):
    # GIVEN a picture whose required relationship is missing, duplicated or package-escaping
    parts = base_parts([("rId1", "slides/one.xml", False)])
    parts["deck/slides/one.xml"] = slide(picture("rPic"))
    parts["deck/slides/_rels/one.xml.rels"] = relations
    path = pptx(tmp_path / "picture-broken.pptx", parts)
    out = tmp_path / "picture-broken.md"

    # WHEN the public service resolves the picture relationship
    code, receipt, markdown = brewdoc.run(path, out=out)

    # THEN the presentation refuses at that relationship boundary
    assert (code, receipt["file_ok"], receipt["route"], receipt["unit_kind"],
            receipt["units"], receipt["out"], receipt["reason"], markdown) == (
        1, False, "presentation", "slide", 0, None, reason, "",
    ), "a referenced picture must never be omitted when its relationship is invalid"
    assert not out.exists(), "an invalid picture relationship must not create output"


def test_non_zip_and_duplicate_required_parts_are_refused(tmp_path):
    # GIVEN a non-ZIP input and a package with a duplicate required slide member
    plain = tmp_path / "plain.pptx"
    plain.write_bytes(b"not a zip")
    parts = base_parts([("rId1", "slides/one.xml", False)])
    parts["deck/slides/one.xml"] = slide()
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = pptx(tmp_path / "duplicate.pptx", parts,
                         duplicates=(("deck/slides/one.xml", slide()),))

    # WHEN both invalid inputs are rendered
    plain_out = tmp_path / "plain.md"
    duplicate_out = tmp_path / "duplicate.md"
    plain_result = brewdoc.run(plain, out=plain_out)
    duplicate_result = brewdoc.run(duplicate, out=duplicate_out)

    # THEN each refusal names the archive or exact ambiguous member and emits no Markdown
    assert (plain_result[0], plain_result[1]["file_ok"], plain_result[1]["route"],
            plain_result[1]["unit_kind"], plain_result[1]["units"],
            plain_result[1]["out"], plain_result[1]["reason"], plain_result[2]) == (
        1, False, "presentation", "slide", 0, None,
        "presentation unreadable: %s: File is not a zip file" % plain, "",
    ), "a non-ZIP PPTX must refuse at archive opening"
    assert (duplicate_result[0], duplicate_result[1]["file_ok"],
            duplicate_result[1]["route"], duplicate_result[1]["unit_kind"],
            duplicate_result[1]["units"], duplicate_result[1]["out"],
            duplicate_result[1]["reason"], duplicate_result[2]) == (
        1, False, "presentation", "slide", 0, None,
        "OOXML part occurs more than once: deck/slides/one.xml", "",
    ), "a duplicate required slide member must not resolve by ZIP entry order"
    assert (plain_out.exists(), duplicate_out.exists()) == (False, False), (
        "archive and duplicate-part refusals must leave no output files"
    )


@pytest.mark.parametrize(
    ("name", "slides", "tables", "images", "notes_count"),
    [
        pytest.param("python-pptx-test.pptx", 1, 0, 0, 0, id="python-pptx"),
        pytest.param("poi-testPPT.pptx", 3, 0, 0, 0, id="poi-text"),
        pytest.param("poi-table_test.pptx", 1, 1, 0, 0, id="poi-table"),
        pytest.param("tika-testPPT_various2.pptx", 10, 1, 2, 2, id="tika-various"),
    ],
)
@pytest.mark.corpus
def test_real_pptx_fixtures_render_twice_and_match_out(
        tmp_path, name, slides, tables, images, notes_count):
    # GIVEN one public PPTX fixture and an output target
    source = Path(__file__).parent / "fixtures" / "pptx" / name
    out = tmp_path / (name + ".md")

    # WHEN the fixture is rendered twice and once through the service --out path
    first = render_presentation(source)
    second = render_presentation(source)
    code, receipt, returned_markdown = brewdoc.run(source, out=out)

    # THEN the adapter is deterministic and exact feature counts survive the output transaction
    assert first == second, "a real presentation must render byte-identically on repeated reads"
    markdown, tally = first
    assert (code, receipt["file_ok"], receipt["units"], returned_markdown,
            out.read_text("ascii"), tally["slides"], tally["tables"],
            markdown.count("Image: "), markdown.count("Speaker notes:")) == (
        0, True, slides, markdown, markdown, slides, tables, images, notes_count,
    ), "real fixtures must preserve slide count, feature evidence and exact --out Markdown"
