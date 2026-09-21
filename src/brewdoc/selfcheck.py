"""Shipped self-check: synthetic PDF, DOCX and workbook documents, no fixture file needed."""

from __future__ import annotations

import datetime
import io
import re
import tempfile
import zipfile
from pathlib import Path

from brewdoc.common import (_escape_markdown_text, cell_text, markdown_table, new_tally,
                            sanitise, sha256)
from brewdoc.docx import DOC_PART, render_doc
from brewdoc.pdf import render_pdf
from brewdoc.service import EXIT_FAIL, EXIT_OK, run
from brewdoc.sheets import render_book


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


SHEET_ROWS = [["Zone", "Area", "Sown"], ["North", 12.5, "2026-07-18"], ["South", 9.0, ""]]


def _synthetic_xlsx(path: Path) -> None:
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
        package.writestr(DOC_PART,
                         '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                         'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body>'
                         '</w:document>' % body)
    return stream.getvalue()


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
    want("a date is one ISO day", cell_text(datetime.date(2026, 7, 18)), "2026-07-18")
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
              "| North | 12.5 | 2026-07-18 |", "| South | 9.0 |  |"])
        want("the sheet route counts its sheets", book_tally["sheets"], 1)
        out = Path(tmp, "out.md")
        rc, line, _ = run(book_path, out=out)
        want("a written render is ASCII and re-readable",
             (rc, line["route"], out.read_text(encoding="ascii") == book_md), (EXIT_OK, "sheet", True))
        rc, line, _ = run(Path(tmp, "absent.pdf"))
        want("a missing file names the path", (rc, line["reason"]),
             (EXIT_FAIL, "no such file: %s" % Path(tmp, "absent.pdf")))

    for failure in failures:
        print("FAIL %s" % failure)
    print("self-check: %s (%d checks)" % ("FAIL" if failures else "ok", len(checks)))
    return EXIT_FAIL if failures else EXIT_OK


def chapter_lines(markdown: str, heading: str) -> list[str]:
    """Return content under a `## <heading>` line or a sheet or chapter display label."""
    lines = markdown.splitlines()
    label = ': "%s"' % _escape_markdown_text(heading)
    matched = next((index for index, line in enumerate(lines) if line == "## " + heading
                    or (line.startswith(("## Sheet ", "## Chapter ")) and line.endswith(label))),
                   None)
    if matched is None:
        raise ValueError("chapter heading not found: %s" % heading)
    start = matched + 1
    rest = [index for index, line in enumerate(lines[start:], start)
            if line.startswith("## ")
            or re.match(r'<a id="brewdoc-(?:page|sheet|chapter)-\d{6}"></a>', line)]
    body = lines[start:rest[0]] if rest else lines[start:]
    return [line for line in body if line.strip()]
