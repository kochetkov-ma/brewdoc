"""Shipped self-check for synthetic documents and presentations, with no fixture files."""

from __future__ import annotations

import datetime
import io
import re
import tempfile
import zipfile
from pathlib import Path

import brewdoc.htmltext as htmltext
from brewdoc.common import (UNIT_COUNTERS, _escape_markdown_text, cell_text, markdown_table,
                            new_tally, sanitise, sha256)
from brewdoc.docx import render_doc
from brewdoc.epub import render_epub
from brewdoc.pdf import render_pdf
from brewdoc.pptx import render_presentation
from brewdoc.service import EXIT_FAIL, EXIT_OK, run
from brewdoc.sheets import _sheet_cell_text, render_book


def synthetic_pdf(pages, font: bool = True) -> bytes:
    """A minimal valid PDF printing each page's content stream verbatim; `font=False` for a scan."""
    objects, count = {}, len(pages)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (" ".join("%d 0 R" % (3 + i) for i in range(count)), count)).encode("ascii")
    first_stream = 3 + count
    resources = " /Font << /F1 %d 0 R >>" % (first_stream + count) if font else ""
    for index, body in enumerate(pages):
        objects[3 + index] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources <<%s >> "
            "/Contents %d 0 R >>" % (resources, first_stream + index)).encode("ascii")
        data = body.encode("latin-1")
        objects[first_stream + index] = (("<< /Length %d >>\nstream\n" % len(data)).encode("ascii")
                                         + data + b"\nendstream")
    if font:
        objects[first_stream + count] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    buffer, offsets = io.BytesIO(), {}
    buffer.write(b"%PDF-1.4\n")
    for number in sorted(objects):
        offsets[number] = buffer.tell()
        buffer.write(("%d 0 obj\n" % number).encode("ascii"))
        buffer.write(objects[number])
        buffer.write(b"\nendobj\n")
    start, size = buffer.tell(), max(objects) + 1
    buffer.write(("xref\n0 %d\n" % size).encode("ascii") + b"0000000000 65535 f \n")
    for number in range(1, size):
        buffer.write(("%010d 00000 n \n" % offsets.get(number, 0)).encode("ascii"))
    buffer.write(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (size, start)).encode("ascii"))
    return buffer.getvalue()


def _text_op(x: float, y: float, text: str, size: int = 10) -> str:
    return "BT /F1 %d Tf 1 0 0 1 %s %s Tm (%s) Tj ET\n" % (size, x, y, text)


def _rule(x: float, y: float, width: float, height: float = 0.5) -> str:
    return "%s %s %s %s re f\n" % (x, y, width, height)


FIXTURE_TABLE = [_text_op(80, 700, "Table 1. Yield by zone."),
                 _text_op(80, 685, "Zone") + _text_op(200, 685, "Area") + _text_op(320, 685, "Yield"),
                 _text_op(80, 670, "North") + _text_op(200, 670, "12.5") + _text_op(320, 670, "4.10"),
                 _text_op(80, 655, "South") + _text_op(200, 655, "9.0") + _text_op(320, 655, "3.75")]
FIXTURE_COLUMNS = [_text_op(60, 600 - 12 * i, "left column line %d of the body text here" % i)
                   + _text_op(330, 600 - 12 * i, "right column line %d of the body text" % i)
                   for i in range(12)]


def _fixture_pages() -> list[str]:
    body = "".join(FIXTURE_TABLE) + "".join(FIXTURE_COLUMNS)
    return [_text_op(72, 760, "Seasonal Operations Manual") + body + _text_op(300, 40, "%d" % n)
            for n in (1, 2, 3)]


# p1: a grid closed on the header row alone, plus a subscripted cell. p2: rules only across.
FIXTURE_UNCLOSED = (
    "".join(_rule(100, y, 300) for y in (700, 685, 670, 655, 640))
    + "".join(_rule(x, 640, 0.5, 60) for x in (100, 200, 300))
    + _rule(400, 685, 0.5, 15)
    + "".join(_text_op(x, 689, text) for x, text in ((105, "Zone"), (205, "Area"), (305, "Yield")))
    + "".join(_text_op(x, 674, text) for x, text in ((105, "North"), (205, "12.5"), (305, "4.10")))
    + "".join(_text_op(x, 659, text) for x, text in ((105, "South"), (205, "9.0"), (305, "3.75")))
    + _text_op(105, 644, "Salt") + _text_op(305, 644, "1.0")
    + _text_op(205, 644, "P") + _text_op(211, 641, "2", 6)
    + _text_op(215, 644, "O") + _text_op(221, 641, "5", 6))
FIXTURE_SEGMENTED = (
    "".join(_rule(x, 600, 99.5) for x in (100, 200, 300, 400))
    + "".join(_text_op(x, 585, text)
              for x, text in ((105, "Crop"), (205, "Zone"), (305, "Area"), (405, "Yield")))
    + "".join(_text_op(x, 570, text)
              for x, text in ((105, "Maize"), (205, "North"), (305, "12.5"), (405, "4.10")))
    + "".join(_text_op(x, 555, text)
              for x, text in ((105, "Wheat"), (205, "South"), (305, "9.0"), (405, "3.75")))
    # Prose, so the region's word-space yardstick is a word space and not a column gap.
    + "".join(_text_op(100, 520 - 12 * i, "a prose line with ordinary word spaces")
              for i in range(6)))


SHEET_ROWS = [["Zone", "Area", "Sown"], ["café 東京", 12.5, "2026-07-18"], ["South", 9.0, ""]]


def _synthetic_xlsx(path: Path) -> None:
    """Write the fixed Zones workbook with cached text and float values for the self-check."""
    def cell(column: str, row: int, value):
        reference = "%s%d" % (column, row)
        if isinstance(value, float):
            return '<c r="%s"><v>%r</v></c>' % (reference, value)
        return ('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (reference, value)
                if value != "" else '<c r="%s"/>' % reference)
    rows = "".join(
        '<row r="%d">%s</row>' % (index, "".join(
            cell(chr(ord("A") + position), index, value) for position, value in enumerate(row)))
        for index, row in enumerate(SHEET_ROWS, 1))
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("[Content_Types].xml",
                      '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/content-types"><Default Extension="rels" ContentType='
                      '"application/vnd.openxmlformats-package.relationships+xml"/><Override '
                      'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
                      '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr("_rels/.rels",
                      '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                      '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
                      'schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
                      ' Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/_rels/workbook.xml.rels",
                      '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                      '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
                      'schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                      'Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr("xl/workbook.xml",
                      '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                      'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                      'officeDocument/2006/relationships"><sheets><sheet name="Zones" sheetId="1" '
                      'r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/worksheets/sheet1.xml",
                      '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
                      'spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % rows)


DOC_TABLE = [[("Dose table", 3)], [("Product", 1), ("Dose", 1), ("BBCH", 1)],
             [("Moddus", 1), ("0.4", 1), ("31", 1)]]


def synthetic_docx() -> bytes:
    """A minimal .docx: one Heading1, one paragraph with a `w:br`, one table with a spanning row."""
    def cell(text: str, span: int) -> str:
        grid = '<w:tcPr><w:gridSpan w:val="%d"/></w:tcPr>' % span if span > 1 else ""
        return "<w:tc>%s<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % (grid, text)
    rows = "".join("<w:tr>%s</w:tr>" % "".join(cell(text, span) for text, span in row)
                   for row in DOC_TABLE)
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Growth regulators</w:t>'
            '</w:r></w:p>'
            '<w:p><w:r><w:t>first line</w:t><w:br/><w:t>second line</w:t></w:r>'
            '<w:del><w:r><w:delText>struck out</w:delText></w:r></w:del></w:p>'
            '<w:tbl>%s</w:tbl>' % rows)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("[Content_Types].xml",
                         '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/content-types"><Default Extension="rels" ContentType='
                         '"application/vnd.openxmlformats-package.relationships+xml"/><Override '
                         'PartName="/word/document.xml" ContentType="application/vnd.'
                         'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                         '</Types>')
        package.writestr("_rels/.rels",
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                         'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
                         'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         'relationships/officeDocument" Target="word/document.xml"/>'
                         '</Relationships>')
        package.writestr("word/document.xml",
                         '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                         'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body>'
                         '</w:document>' % body)
    return stream.getvalue()


def synthetic_pptx() -> bytes:
    """Return a one-slide presentation with a title and body text."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>',
        )
        package.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        package.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
        )
        package.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/>'
            '<p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Harvest plan</a:t></a:r>'
            '</a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id="3" name="Content 2"/>'
            '<p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:spPr/>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>North field</a:t></a:r>'
            '</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        )
    return stream.getvalue()


def _synthetic_epub() -> bytes:
    """Build an offline EPUB whose archive/navigation order differs from its spine."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("mimetype", "application/epub+zip")
        package.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:'
                         'xmlns:container"><rootfiles><rootfile full-path="book.opf" '
                         'media-type="application/oebps-package+xml"/></rootfiles></container>')
        package.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                         '<metadata/><manifest><item id="a" href="a.xhtml" '
                         'media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" '
                         'media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" '
                         'media-type="application/xhtml+xml" properties="nav"/></manifest>'
                         '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        package.writestr("b.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                         '<title>Second</title></head><body><p>Back matter.</p></body></html>')
        package.writestr("a.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                         '<title>First</title></head><body><p>café 東京\u00a0</p></body></html>'.encode("utf-8"))
        package.writestr("nav.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml" '
                         'xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>'
                         '<li><a href="b.xhtml">Second</a></li><li><a href="a.xhtml">First</a></li>'
                         '</ol></nav></body></html>')
    return buffer.getvalue()


def _synthetic_odf(kind: str) -> bytes:
    """Build one qualified ODF body with no optional package parts."""
    mime = "application/vnd.oasis.opendocument." + {"odt": "text", "odp": "presentation"}[kind]
    blocks = {"odt": '<text:h text:outline-level="1">Title</text:h><text:p>café 東京</text:p>',
              "odp": '<draw:page draw:name="Title"><draw:frame><draw:text-box>'
                     '<text:p>café 東京</text:p></draw:text-box></draw:frame></draw:page>'}[kind]
    body_tag = {"odt": "text", "odp": "presentation"}[kind]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("mimetype", mime)
        package.writestr("META-INF/manifest.xml", '<manifest:manifest xmlns:manifest="urn:oasis:'
                         'names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry '
                         'manifest:full-path="/" manifest:media-type="%s"/>'
                         '</manifest:manifest>' % mime)
        package.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:'
                         'tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:'
                         'opendocument:xmlns:text:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:'
                         'xmlns:drawing:1.0"><office:body><office:%s>%s</office:%s>'
                         '</office:body></office:document-content>' % (body_tag, blocks, body_tag))
    return buffer.getvalue()


def self_check() -> int:
    """Render synthetic fixtures and compare against pinned expectations; 0 when green."""
    failures, checks = [], []

    def want(label, got, expected):
        checks.append(label)
        if got != expected:
            failures.append("%s: got %r want %r" % (label, got, expected))

    tally = new_tally()
    want("sanitise folds and counts",
         sanitise(" a b (cid:9) oﬃce dišerent­ \x01x – ≥", tally),
         "- a b office diserent x - >=")
    want("every class counted", (tally["dropped"]["pua_glyphs"], tally["dropped"]["nbsp"],
                                 tally["dropped"]["cid_survivors"], tally["dropped"]["ligatures"],
                                 tally["dropped"]["soft_hyphens"],
                                 tally["dropped"]["control_chars"],
                                 tally["broken_ligature_words"]),
         (1, 1, 1, 1, 1, 1, 1))
    want("a float keeps its spreadsheet form", cell_text(85.0), "85.0")
    want("an empty cell is '' and renders empty", cell_text(""), "")
    want("dates and workbook datetime precision survive",
         (cell_text(datetime.date(2026, 7, 18)),
          _sheet_cell_text(datetime.datetime(2026, 7, 18, 12, 34, 56, 789012), tally)),
         ("2026-07-18", "2026-07-18T12:34:56.789012"))
    want("a ragged table is padded, never cut", markdown_table([["a", "b"], ["c"]]),
         ["| a | b |", "| --- | --- |", "| c |  |"])

    with tempfile.TemporaryDirectory(prefix="brewdoc_docx_") as tmp:
        doc_path = Path(tmp, "fixture.docx")
        doc_path.write_bytes(synthetic_docx())
        markdown, tally = render_doc(doc_path)
        want("a Word heading opens its own chapter",
             markdown.count('## Chapter 1: "Growth regulators"'), 1)
        want("a w:br is a line break, and a deleted run is not carried",
             chapter_lines(markdown, "Growth regulators")[:2], ["first line", "second line"])
        want("a spanning cell is padded, so every column stays under its own head",
             chapter_lines(markdown, "Growth regulators")[2:],
             ["| Dose table |  |  |", "| --- | --- | --- |", "| Product | Dose | BBCH |",
              "| Moddus | 0.4 | 31 |"])
        want("the docx tally counts its chapter and its table",
             (tally["chapters"], tally["tables"], tally["text_regions"]), (1, 1, 1))

    with tempfile.TemporaryDirectory(prefix="brewdoc_pptx_") as tmp:
        presentation_path = Path(tmp, "fixture.pptx")
        presentation_path.write_bytes(synthetic_pptx())
        markdown, tally = render_presentation(presentation_path)
        want("a presentation title labels its slide once",
             (markdown.count('## Slide 1: "Harvest plan"'),
              chapter_lines(markdown, "Harvest plan")),
             (1, ["North field"]))
        want("the presentation tally counts its declared slide", tally["slides"], 1)
        want("a presentation render is deterministic",
             sha256(render_presentation(presentation_path)[0]), sha256(markdown))

    with tempfile.TemporaryDirectory(prefix="brewdoc_") as tmp:
        pdf_path = Path(tmp, "fixture.pdf")
        pdf_path.write_bytes(synthetic_pdf(_fixture_pages()))
        markdown, tally = render_pdf(pdf_path)
        lines = markdown.splitlines()
        want("the captioned unlined table recovers its cells",
             lines[lines.index("| Zone | Area | Yield |") + 2], "| North | 12.5 | 4.10 |")
        want("the blank rows the text strategy invents are gone",
             markdown.count("|  |  |  |"), 0)
        want("the caption stays prose", markdown.count("Table 1. Yield by zone."), 3)
        want("two-column prose is read column by column",
             lines[lines.index("left column line 0 of the body text here") + 1],
             "left column line 1 of the body text here")
        want("the region was split at the gutter", tally["columns_split"], 3)
        want("the running head is dropped once per page",
             (markdown.count("Seasonal Operations Manual"), tally["dropped"]["running_heads"]),
             (0, 3))
        want("the page number is dropped", tally["dropped"]["page_numbers"], 3)
        want("determinism sha256", sha256(render_pdf(pdf_path)[0]), sha256(markdown))

        ruled_path = Path(tmp, "ruled.pdf")
        ruled_path.write_bytes(synthetic_pdf([FIXTURE_UNCLOSED, FIXTURE_SEGMENTED]))
        ruled, _ = render_pdf(ruled_path)
        want("a column closed on the header row alone is still read",
             chapter_lines(ruled, "Page 1")[:5],
             ["| Zone | Area | Yield |", "| --- | --- | --- |", "| North | 12.5 | 4.10 |",
              "| South | 9.0 | 3.75 |", "| Salt | P2O5 | 1.0 |"])
        want("a rule cut into one segment per column carries the grid",
             chapter_lines(ruled, "Page 2")[:4],
             ["| Crop | Zone | Area | Yield |", "| --- | --- | --- | --- |",
              "| Maize | North | 12.5 | 4.10 |", "| Wheat | South | 9.0 | 3.75 |"])
        want("a repaired ruling stays deterministic",
             sha256(render_pdf(ruled_path)[0]), sha256(ruled))

        scan = Path(tmp, "scan.pdf")
        scan.write_bytes(synthetic_pdf(["0 0 0 rg 100 100 200 200 re f\n"] * 2, font=False))
        rc, line, _ = run(scan)
        want("a document with no text layer is refused by name",
             (rc, line["file_ok"], line["reason"].split(" in ")[0]),
             (EXIT_FAIL, False, "no text layer: 2 of 2 pages carry zero characters"))

        book_path = Path(tmp, "fixture.xlsx")
        _synthetic_xlsx(book_path)
        book_md, book_tally = render_book(book_path)
        want("one chapter per sheet",
             [row for row in book_md.splitlines() if row.startswith("## Sheet ")],
             ['## Sheet 1: "Zones"'])
        want("the sheet's measured types survive",
             chapter_lines(book_md, "Zones"),
             ["| Zone | Area | Sown |", "| --- | --- | --- |",
              "| café 東京 | 12.5 | 2026-07-18 |", "| South | 9.0 |  |"])
        want("the sheet route counts its sheets", book_tally["sheets"], 1)
        out = Path(tmp, "out.md")
        rc, line, _ = run(book_path, out=out)
        want("a written workbook preserves exact UTF-8",
             (rc, line["route"], out.read_bytes()), (EXIT_OK, "sheet", book_md.encode("utf-8")))
        rc, line, _ = run(Path(tmp, "absent.pdf"))
        want("a missing file names the path", (rc, line["reason"]),
             (EXIT_FAIL, "no such file: %s" % Path(tmp, "absent.pdf")))

        html_path = Path(tmp, "fixture.html")
        html_path.write_bytes('<title>Static HTML</title><p>café 東京</p><table><tr><th>Zone</th>'
                              '<th>Area</th></tr><tr><td>North</td><td>12.5</td></tr></table>'.encode("utf-8"))
        rc, line, html_md = run(html_path)
        if htmltext._SUPPORTED_RUNTIME:
            want("HTML's native parser is available on supported CPython",
                 (rc, line["file_ok"], line["route"], line["unit_kind"], line["units"]),
                 (EXIT_OK, True, "html", "chapter", 1))
            if rc == EXIT_OK:
                want("HTML preserves Unicode prose and complete table cells",
                     chapter_lines(html_md, "Static HTML"),
                     ["café 東京", "| Zone | Area |", "| --- | --- |", "| North | 12.5 |"])
                want("HTML counts its chapter, table and prose",
                     (line["units"], line["tables"], line["text_regions"], line["unit_keys"]),
                     (1, 1, 1, ["chapter/000001"]))
                html_out = Path(tmp, "html.md")
                second_rc, second_line, second_md = run(html_path, out=html_out)
                want("HTML writes deterministic UTF-8 without a BOM",
                     (second_rc, second_line["file_ok"], second_md,
                      html_out.read_bytes() if second_rc == EXIT_OK else None),
                     (EXIT_OK, True, html_md, html_md.encode("utf-8")))
        else:
            want("an unsupported HTML runtime explicitly refuses its static route",
                 (rc, line["file_ok"], line["route"], line["unit_kind"], line["units"],
                  line["reason"], html_md),
                 (EXIT_FAIL, False, "html", "chapter", 0, "HTML requires CPython 3.12-3.14", ""))

        epub_path = Path(tmp, "fixture.epub")
        epub_path.write_bytes(_synthetic_epub())
        rc, line, epub_md = run(epub_path)
        want("EPUB follows the declared spine instead of ZIP or navigation order",
             (rc, line["file_ok"], line["route"], line["unit_kind"], line["unit_keys"]),
             (EXIT_OK, True, "epub", "chapter", ["chapter/000001", "chapter/000002"]))
        expected_tally = new_tally()
        expected_tally.update(chapters=2, text_regions=2)
        want("EPUB preserves Unicode and its public wrapper matches the service",
             (render_epub(epub_path), chapter_lines(epub_md, "First"), chapter_lines(epub_md, "Second")),
             ((epub_md, expected_tally), ["café 東京\u00a0"], ["Back matter."]))
        want("EPUB conversion is deterministic", run(epub_path), (rc, line, epub_md))
        epub_out = Path(tmp, "epub.md")
        written_rc, written_line, written_md = run(epub_path, out=epub_out)
        new_routes, expected_routes = [], []
        plain = "\ufeffcafé 東京\r\nno final newline".encode("utf-8")
        inputs = [("md", plain, "none", plain.decode("utf-8")),
                  ("txt", plain, "none", plain.decode("utf-8")),
                  ("odt", _synthetic_odf("odt"), "chapter", "café 東京\n"),
                  ("odp", _synthetic_odf("odp"), "slide", "café 東京\n"),
                  ("eml", b"Subject: Title\nContent-Type: text/plain; charset=utf-8\n\n"
                   + "café 東京".encode("utf-8"), "chapter", "```\ncafé 東京\n```\n")]
        for suffix, payload, unit, ending in inputs:
            source = Path(tmp, "new." + suffix)
            source.write_bytes(payload)
            target = Path(tmp, suffix + ".md")
            code, receipt, content = run(source, out=target)
            raw = unit == "none"
            new_routes.append((code, receipt["route"], receipt.get("markdown_schema"),
                               receipt.get("unit_keys"), content == ending if raw else content.endswith(ending),
                               target.read_bytes() == content.encode("utf-8") if target.exists() else False))
            expected_routes.append((EXIT_OK, "text" if raw else suffix,
                                    "brewdoc.markdown/passthrough/1" if raw else "brewdoc.markdown/2",
                                    [] if raw else [unit + "/000001"], True, True))
        want("Unicode formats write exact UTF-8 and text keeps its source bytes",
             ((written_rc, written_line["file_ok"], written_md,
               epub_out.read_bytes() if written_rc == EXIT_OK else None), new_routes),
             ((EXIT_OK, True, epub_md, epub_md.encode("utf-8")), expected_routes))

    for failure in failures:
        print("FAIL %s" % failure)
    print("self-check: %s (%d checks)" % ("FAIL" if failures else "ok", len(checks)))
    return EXIT_FAIL if failures else EXIT_OK


def chapter_lines(markdown: str, heading: str) -> list[str]:
    """Return content under a plain heading or any registered unit display label."""
    lines = markdown.splitlines()
    label = ': "%s"' % _escape_markdown_text(heading, quoted=True)
    unit_headings = tuple("## %s " % kind.title() for kind in UNIT_COUNTERS)
    unit_anchor = r'<a id="brewdoc-(?:%s)-\d{6}"></a>' % "|".join(
        re.escape(kind) for kind in UNIT_COUNTERS)
    matched = next((index for index, line in enumerate(lines) if line == "## " + heading
                    or (line.startswith(unit_headings) and line.endswith(label))),
                   None)
    if matched is None:
        raise ValueError("chapter heading not found: %s" % heading)
    start = matched + 1
    rest = [index for index, line in enumerate(lines[start:], start)
            if line.startswith("## ")
            or re.match(unit_anchor, line)]
    body = lines[start:rest[0]] if rest else lines[start:]
    return [line for line in body if line.strip()]
