import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import brewdoc
from brewdoc import common, selfcheck, service
from brewdoc.cli import main

from test_fixtures import RECEIPTS, SUPPORTED, fixture_files, sources_rows

OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
HEADING = '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>%s</w:t></w:r></w:p>'
CELL = "<w:tc>%s<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>"
GROWTH_REGULATORS = (
    HEADING % "Growth regulators"
    + "<w:p><w:r><w:t>first line</w:t><w:br/><w:t>second line</w:t></w:r>"
    "<w:del><w:r><w:delText>struck out</w:delText></w:r></w:del></w:p><w:tbl><w:tr>"
    + CELL % ('<w:tcPr><w:gridSpan w:val="3"/></w:tcPr>', "Dose table") + "</w:tr>"
    + "".join("<w:tr>%s</w:tr>" % "".join(CELL % ("", text) for text in row)
              for row in (("Product", "Dose", "BBCH"), ("Moddus", "0.4", "31")))
    + "</w:tbl>"
)
# Characters this platform refuses inside a filename: Windows bans all nine, POSIX only the separator.
RESERVED_FILENAME_CHARS = '<>:"/\\|?*' if os.name == "nt" else "/"
PDF_NOT_CARRIED = ["images, figures and the text drawn inside them",
                   "a table that spans a page break",
                   "text rotated out of the horizontal reading order"]
PDF_OMISSIONS = "\n".join("- " + item for item in PDF_NOT_CARRIED)
NOT_APPLICABLE = {"formula": "not applicable", "formula_count": "not applicable",
                  "vba": "not applicable", "vba_count": "not applicable"}
ONE_PAGE = dict(NOT_APPLICABLE, route="pdf", unit="page", count=1, keys="page/000001", pages=1,
                sheets=0, chapters=0, tables=0, text_regions=1, omissions=PDF_OMISSIONS,
                contents="- [Page 1](#brewdoc-page-000001)")
HELLO = '<a id="brewdoc-page-000001"></a>\n## Page 1\n\nhello\n'
SCHEMA_TWO = """# "{name}"

<a id="brewdoc-metadata"></a>
## Metadata

| Field | Value |
| --- | --- |
| Markdown schema | brewdoc.markdown/2 |
| Source name | "{name}" |
| Source suffix | {suffix} |
| Source bytes | {source_bytes} |
| Source SHA-256 | {source_sha256} |
| Route | {route} |
| Unit kind | {unit} |
| Source unit count | {count} |
| Rendered unit count | {count} |
| Ordered selection keys | {keys} |
| Rendered body bytes | {body_bytes} |
| Rendered body SHA-256 | {body_sha256} |
| Pages | {pages} |
| Sheets | {sheets} |
| Chapters | {chapters} |
| Tables | {tables} |
| Text regions | {text_regions} |
| Column splits | 0 |
| Broken ligature words | 0 |
| Dropped control chars | 0 |
| Dropped soft hyphens | 0 |
| Dropped nbsp | 0 |
| Dropped pua glyphs | 0 |
| Dropped cid survivors | 0 |
| Dropped ligatures | 0 |
| Dropped running heads | 0 |
| Dropped page numbers | 0 |
| Dropped non ascii replaced | 0 |
| Formula capability | {formula} |
| Selected formula count | {formula_count} |
| VBA project capability | {vba} |
| VBA project count | {vba_count} |
| VBA source module capability | {vba} |
| VBA source module count | {vba_count} |

## Artifacts

None.

## Known omissions

{omissions}

<a id="brewdoc-contents"></a>
## Contents

{contents}

{body}"""


def schema_two(path: Path, body: str, **fields) -> str:
    """Return the whole expected schema 2 document for `path` around route `body`."""
    source, rendered = path.read_bytes(), body.encode("ascii")
    return SCHEMA_TWO.format(
        **fields, body=body, source_bytes=len(source), body_bytes=len(rendered),
        source_sha256=hashlib.sha256(source).hexdigest(),
        body_sha256=hashlib.sha256(rendered).hexdigest())


def zero_tally() -> dict:
    """Return a fresh render tally with every count at zero."""
    return {"pages": 0, "sheets": 0, "chapters": 0, "slides": 0, "tables": 0,
            "text_regions": 0, "columns_split": 0, "broken_ligature_words": 0, "dropped": {
                "control_chars": 0, "soft_hyphens": 0, "nbsp": 0, "pua_glyphs": 0,
                "cid_survivors": 0, "ligatures": 0, "running_heads": 0, "page_numbers": 0,
                "non_ascii_replaced": 0}}


def text_op(x: int, y: int, text: str) -> str:
    """Return one 10 pt text-showing operator at (x, y)."""
    return "BT /F1 10 Tf 1 0 0 1 %s %s Tm (%s) Tj ET\n" % (x, y, text)


def pdf(path: Path, *pages: str) -> Path:
    """Write a synthetic PDF with one content stream per page."""
    path.write_bytes(selfcheck.synthetic_pdf(list(pages)))
    return path


def three_page_pdf(path: Path) -> Path:
    """Write a manual with a title, captioned table, two-column prose and a page number."""
    rows = (("Zone", "Area", "Yield"), ("North", "12.5", "4.10"), ("South", "9.0", "3.75"))
    table = text_op(80, 700, "Table 1. Yield by zone.") + "".join(
        text_op(x, 685 - 15 * index, cell)
        for index, row in enumerate(rows) for x, cell in zip((80, 200, 320), row))
    columns = "".join(text_op(60, 600 - 12 * line, "left column line %d of the body text here" % line)
                      + text_op(330, 600 - 12 * line, "right column line %d of the body text" % line)
                      for line in range(12))
    return pdf(path, *(text_op(72, 760, "Seasonal Operations Manual") + table + columns
                       + text_op(300, 40, page) for page in "123"))


def docx(path: Path, body: str, main: str = "word/document.xml") -> Path:
    """Write a bare DOCX package whose main part is `main` and document body is `body`."""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("_rels/.rels", '<Relationships xmlns="%s"><Relationship Id="rId1" '
                         'Type="%s/officeDocument" Target="%s"/></Relationships>'
                         % (PACKAGE, OFFICE, main))
        package.writestr(main,
                         '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://'
                         'schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
                         "</w:body></w:document>" % body)
    return path


def zones_book(path: Path) -> Path:
    """Write a one-sheet XLSX with text, numbers, a date-styled serial and an absent cell."""
    text = '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>'
    rows = (text % ("A1", "Zone") + text % ("B1", "Area") + text % ("C1", "Sown"),
            text % ("A2", "North") + '<c r="B2"><v>12.5</v></c><c r="C2" s="1"><v>46221</v></c>',
            text % ("A3", "South") + '<c r="B3"><v>9.0</v></c>')
    relationship = '<Relationship Id="%s" Type="' + OFFICE + '/%s" Target="%s"/>'
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("_rels/.rels", '<Relationships xmlns="%s">%s</Relationships>' % (
            PACKAGE, relationship % ("rId1", "officeDocument", "xl/workbook.xml")))
        package.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="%s">%s%s'
                         "</Relationships>" % (
                             PACKAGE, relationship % ("rId1", "worksheet", "worksheets/sheet1.xml"),
                             relationship % ("rId2", "styles", "styles.xml")))
        package.writestr("xl/workbook.xml",
                         '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                         'main" xmlns:r="%s"><sheets><sheet name="Zones" sheetId="1" r:id="rId1"/>'
                         "</sheets></workbook>" % OFFICE)
        package.writestr("xl/styles.xml",
                         '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                         'main"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14" '
                         'applyNumberFormat="1"/></cellXfs></styleSheet>')
        package.writestr("xl/worksheets/sheet1.xml",
                         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                         'main"><sheetData>%s</sheetData></worksheet>' % "".join(
                             '<row r="%d">%s</row>' % item for item in enumerate(rows, 1)))
    return path


def test_public_api_is_exactly_the_package_exports():
    # GIVEN the package root
    # WHEN its declared exports are read
    exports = brewdoc.__all__
    # THEN they are the documented public surface, in order
    assert exports == [
        "ArtifactRef", "BrewdocError", "CellFormula", "FormulaArtifact", "__version__",
        "list_book_artifacts", "read_book_artifact", "read_formulas", "render_book",
        "render_doc", "render_pdf", "render_presentation", "run", "self_check",
    ], "the public API must change only by a deliberate edit of brewdoc.__all__"


def test_two_routes_claiming_one_suffix_are_refused_when_the_suffix_map_is_folded():
    # GIVEN a second route claiming a suffix the live pdf route already owns
    clash = common.Route("clash", "page", (".pdf",), lambda path, sheets: None, ())
    # WHEN the suffix map is folded over both
    with pytest.raises(common.BrewdocError) as error:
        service._route_map(service.ROUTES[".pdf"], clash)
    # THEN it names the suffix and both routes instead of letting the last one silently win
    assert str(error.value) == "suffix '.pdf' is claimed by both the pdf and clash routes", (
        "a duplicate suffix must be refused where the routes are folded, not resolved by order"
    )


def test_every_supported_suffix_has_a_fixture_a_receipt_row_and_a_provenance_row():
    # GIVEN the supported suffixes taken from the live route table and the fixture listing
    fixtures = fixture_files(SUPPORTED)
    # WHEN the listing is reduced to covered suffixes, receipt rows and provenance rows
    covered = {Path(rel).suffix.lower() for rel in fixtures}
    documented = sorted({row["path"] for row in sources_rows()}.intersection(fixtures))
    # THEN all three cover exactly the supported formats - a directory listing, so the fast gate runs it
    assert (covered, sorted(RECEIPTS), documented) == (set(SUPPORTED), fixtures, fixtures), (
        "every suffix in service.ROUTES needs a corpus fixture, a receipts.json row and a"
        " SOURCES.md row; add them with the route, not after it"
    )


def test_version_matches_package_metadata():
    # GIVEN the installed distribution metadata
    expected = importlib.metadata.version("brewdoc")
    # WHEN the package version is read
    # THEN both name one release
    assert brewdoc.__version__ == expected, "version drifted from pyproject"


def test_the_cli_self_check_prints_the_api_verdict_and_both_are_green(capsys):
    # GIVEN the in-process self-check result and its printed verdict
    code = brewdoc.self_check()
    verdict = capsys.readouterr().out
    # WHEN the package self-check runs in a fresh interpreter
    proc = subprocess.run([sys.executable, "-m", "brewdoc.cli", "--self-check"],
                          capture_output=True, text=True, timeout=300)
    # THEN both exit 0 and the CLI prints exactly the API verdict
    assert (code, proc.returncode, proc.stdout, proc.stderr) == (0, 0, verdict, ""), (
        "self-check stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr)
    )


def test_cli_resolves_relative_paths_against_the_caller_s_cwd(tmp_path, monkeypatch, capsys):
    # GIVEN a cwd outside the package holding a workbook under a relative name
    monkeypatch.chdir(tmp_path)
    zones_book(tmp_path / "zones.xlsx")
    _code, receipt, markdown = brewdoc.run("zones.xlsx")
    package = Path(brewdoc.__file__).parent
    before = sorted(path.name for path in package.iterdir())
    # WHEN the CLI writes to a relative --out under a directory that does not exist yet
    code = main(["zones.xlsx", "--out", "nested/zones.md"])
    # THEN stdout is one receipt line, the file holds the API Markdown and the package is unchanged
    assert (code, capsys.readouterr().out, (tmp_path / "nested/zones.md").read_text("ascii"),
            sorted(path.name for path in package.iterdir())) == (
        0, json.dumps({**receipt, "out": "nested/zones.md"}, ensure_ascii=True, sort_keys=True)
        + "\n", markdown, before,
    ), "a relative --out is resolved against the caller's cwd, never against the package"


def test_cli_prints_the_api_refusal_receipt_and_exits_one(tmp_path, capsys):
    # GIVEN a path whose suffix no route reads and its API receipt
    path = tmp_path / "notes.txt"
    receipt = brewdoc.run(path)[1]
    # WHEN the CLI is asked to render it
    code = main([str(path)])
    # THEN stdout is exactly that receipt line and nothing else
    assert (code, capsys.readouterr().out) == (
        1, json.dumps(receipt, ensure_ascii=True, sort_keys=True) + "\n",
    ), "a CLI refusal must print the same receipt the API returns"


@pytest.mark.parametrize(
    ("name", "build", "route", "reason"),
    [
        pytest.param(
            "scan.pdf", lambda path: path.write_bytes(selfcheck.synthetic_pdf(
                ["0 0 0 rg 100 100 200 200 re f\n"] * 2, font=False)), "pdf",
            "no text layer: 2 of 2 pages carry zero characters in {path} - this reader does no OCR",
            id="no-text-layer"),
        pytest.param(
            "brief.doc", lambda path: path.write_bytes(b"\xd0\xcf\x11\xe0"), "none",
            "unsupported suffix '.doc' in {path}: brewdoc reads "
            ".docx .ods .pdf .pptx .xls .xlsb .xlsm .xlsx", id="binary-word"),
        pytest.param("absent.pdf", lambda path: None, "pdf", "no such file: {path}",
                     id="missing-file"),
    ],
)
def test_a_refusal_names_what_it_could_not_read_and_where(tmp_path, name, build, route, reason):
    # GIVEN an image-only PDF, a binary Word file or a missing path
    path = tmp_path / name
    build(path)
    # WHEN it is read
    code, receipt, markdown = brewdoc.run(path)
    # THEN the receipt refuses it by name and no empty render stands in
    assert (code, receipt["file_ok"], receipt["route"], receipt["reason"], markdown) == (
        1, False, route, reason.format(path=path), "",
    ), "a refusal must say what it could not read and where"


@pytest.mark.parametrize(
    ("raw", "text", "broken", "dropped"),
    [
        pytest.param(
            "\xa0Wingdings bullet and (cid:190) survivor\n"
            "soft\xadhyphen oﬃce dišerent \x01control – ≥ 中\n",
            "- Wingdings bullet and survivor\nsofthyphen office diserent control - >= ?\n", 1,
            {"control_chars": 1, "soft_hyphens": 1, "nbsp": 1, "pua_glyphs": 1,
             "cid_survivors": 1, "ligatures": 1, "running_heads": 0, "page_numbers": 0,
             "non_ascii_replaced": 1}, id="every-class"),
        pytest.param("dišerent runoš and Stošic", "diserent runos and Stosic", 3,
                     zero_tally()["dropped"], id="broken-ff-ligature"),
    ],
)
def test_the_sanitiser_folds_to_ascii_and_counts_every_loss(raw, text, broken, dropped):
    # GIVEN a fresh tally and text carrying hygiene classes or an ff ligature lifted as U+0161
    tally = zero_tally()
    # WHEN the text is sanitised
    got = common.sanitise(raw, tally)
    # THEN the text is folded to ASCII, never repaired, and every loss is counted by name
    assert (got, tally) == (
        text, {**zero_tally(), "broken_ligature_words": broken, "dropped": dropped},
    ), "a loss is counted and named, never silent or guessed back"


def test_running_heads_and_page_numbers_are_dropped_and_counted(tmp_path):
    # GIVEN three pages sharing one running head and carrying their own page numbers
    lines = "".join(text_op(72, 700 - 14 * line, "body line %d of this page" % line)
                    for line in range(4))
    path = pdf(tmp_path / "manual.pdf", *(
        text_op(72, 740, "Running head of the manual") + lines + text_op(300, 40, foot)
        for foot in ("Page 1 of 3", "2", "3")))
    page = "".join("\nbody line %d of this page" % line for line in range(4))
    # WHEN the document is rendered
    markdown, tally = brewdoc.render_pdf(path)
    # THEN only the body lines remain and the furniture is counted
    assert (markdown[markdown.index('<a id="brewdoc-page-000001"></a>'):], tally["dropped"]) == (
        "\n".join('<a id="brewdoc-page-%06d"></a>\n## Page %d\n%s\n' % (number, number, page)
                  for number in (1, 2, 3)),
        {**zero_tally()["dropped"], "running_heads": 3, "page_numbers": 3},
    ), "furniture repeats on every page and buries the content a model must read"


def test_two_renders_of_one_document_are_identical(tmp_path):
    # GIVEN a three-page document with a captioned table, two-column prose and furniture
    path = three_page_pdf(tmp_path / "fixture.pdf")
    # WHEN it is rendered twice
    first, second = brewdoc.render_pdf(path), brewdoc.render_pdf(path)
    # THEN Markdown and tally are identical: no timestamp, set order or dict iteration order
    assert first == second, "the render must be deterministic"


def test_the_cli_usage_states_its_contract(monkeypatch, capsys):
    # GIVEN a wide, colourless terminal, so argparse prints usage on one plain line
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    # WHEN a caller asks for --help
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    usage = capsys.readouterr().out.partition("\n")[0]
    # THEN it exits 0 and usage lists exactly the documented options
    assert (raised.value.code, usage) == (
        0, "usage: brewdoc [-h] [--out OUT] [--sheet SHEET] [--artifact ARTIFACT] "
           "[--self-check] [document]",
    ), "the CLI surface must expose repeatable sheet and artifact requests"


@pytest.mark.parametrize(
    ("name", "build", "receipt"),
    [
        pytest.param("fixture.pdf", three_page_pdf, {
            "artifacts": [], "broken_ligature_words": 0, "columns_split": 3,
            "dropped": {**zero_tally()["dropped"], "running_heads": 3, "page_numbers": 3},
            "file_ok": True, "markdown_schema": "brewdoc.markdown/2",
            "not_carried": PDF_NOT_CARRIED, "receipt_schema": "brewdoc.receipt/1",
            "reason": "pdf rendered: 3 pages, 3 tables, 15 text regions, 3 column splits",
            "route": "pdf", "source": "fixture.pdf", "tables": 3, "unit_kind": "page",
            "units": 3, "text_regions": 15,
            "unit_keys": ["page/000001", "page/000002", "page/000003"],
        }, id="pdf"),
        pytest.param("zones.xlsx", zones_book, {
            "artifacts": [], "broken_ligature_words": 0, "columns_split": 0,
            "dropped": zero_tally()["dropped"], "file_ok": True,
            "markdown_schema": "brewdoc.markdown/2", "not_carried": [
                "cell formulas in Markdown content - only cached values are rendered",
                "formatting, colours, comments and data validation",
                "charts and embedded images", "readable VBA source modules"],
            "receipt_schema": "brewdoc.receipt/1",
            "reason": "sheet rendered: 1 sheets, 1 tables, 0 text regions, 0 column splits",
            "route": "sheet", "source": "zones.xlsx", "tables": 1, "unit_kind": "sheet",
            "units": 1, "text_regions": 0, "unit_keys": ["sheet/000001"],
        }, id="xlsx"),
        pytest.param("growth.docx", lambda path: docx(path, GROWTH_REGULATORS), {
            "artifacts": [], "broken_ligature_words": 0, "columns_split": 0,
            "dropped": zero_tally()["dropped"], "file_ok": True,
            "markdown_schema": "brewdoc.markdown/2", "not_carried": [
                "images, charts and the text drawn inside them",
                "tracked changes, comments, footnotes, headers and footers",
                "a table's own formatting - only its cells, row by row"],
            "receipt_schema": "brewdoc.receipt/1",
            "reason": "doc rendered: 1 chapters, 1 tables, 1 text regions, 0 column splits",
            "route": "doc", "source": "growth.docx", "tables": 1, "unit_kind": "chapter",
            "units": 1, "text_regions": 1, "unit_keys": ["chapter/000001"],
        }, id="docx"),
    ],
)
def test_a_success_receipt_names_route_counts_output_and_omissions(tmp_path, name, build, receipt):
    # GIVEN a manual, a workbook or a Word file and an output path
    path = build(tmp_path / name)
    out = tmp_path / "out.md"
    # WHEN it is rendered through the public run path
    code, actual, _markdown = brewdoc.run(path, out)
    # THEN the exact receipt counts the route's own unit kind: pages, sheets or chapters
    assert (code, actual) == (0, {**receipt, "out": str(out)}), (
        "the route line must name the route, unit counts, output and what it cannot carry"
    )


def test_cli_stdout_is_the_receipt_line_then_the_exact_out_file_bytes(tmp_path, capsys):
    # GIVEN a one-page PDF, its --out Markdown and its stdout-mode receipt
    path = pdf(tmp_path / "one.pdf", text_op(72, 700, "hello"))
    out = tmp_path / "one.md"
    assert main([str(path), "--out", str(out)]) == 0, "the --out render must succeed"
    capsys.readouterr()
    receipt = brewdoc.run(path)[1]
    # WHEN the CLI renders it without --out
    code = main([str(path)])
    # THEN stdout is one receipt line followed by exactly the --out file bytes
    line = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
    assert (code, capsys.readouterr().out) == (
        0, line + "\n" + out.read_text(encoding="ascii")
    ), "stdout Markdown must equal the --out file, with no extra trailing blank line"


@pytest.mark.parametrize(
    ("name", "build", "fields", "body"),
    [
        pytest.param("one.pdf", lambda path: pdf(path, text_op(72, 700, "hello")),
                     dict(ONE_PAGE, name="one\\.pdf", suffix=".pdf"), HELLO, id="pdf"),
        pytest.param(
            "partial.pdf", lambda path: pdf(path, text_op(72, 700, "kept body"),
                                            "0 0 0 rg 100 100 20 20 re f\n"),
            dict(NOT_APPLICABLE, name="partial\\.pdf", suffix=".pdf", route="pdf", unit="page",
                 count=2, keys="page/000001, page/000002", pages=2, sheets=0, chapters=0,
                 tables=0, text_regions=1, omissions=PDF_OMISSIONS,
                 contents="- [Page 1](#brewdoc-page-000001)\n- [Page 2](#brewdoc-page-000002)"),
            '<a id="brewdoc-page-000001"></a>\n## Page 1\n\nkept body\n\n'
            '<a id="brewdoc-page-000002"></a>\n## Page 2\n', id="pdf-empty-page"),
        pytest.param(
            "zones.xlsx", zones_book,
            dict(name="zones\\.xlsx", suffix=".xlsx", route="sheet", unit="sheet", count=1,
                 keys="sheet/000001", pages=0, sheets=1, chapters=0, tables=1, text_regions=0,
                 formula="available", formula_count="0", vba="unavailable", vba_count="unknown",
                 omissions="- cell formulas in Markdown content \\- only cached values are "
                           "rendered\n- formatting, colours, comments and data validation\n"
                           "- charts and embedded images\n- readable VBA source modules",
                 contents='- [Sheet 1: "Zones"](#brewdoc-sheet-000001)'),
            '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Zones"\n\n'
            "| Zone | Area | Sown |\n| --- | --- | --- |\n"
            "| North | 12.5 | 2026-07-18 |\n| South | 9.0 |  |\n", id="xlsx-date-and-empty-cell"),
        pytest.param(
            "growth.docx", lambda path: docx(path, GROWTH_REGULATORS),
            dict(NOT_APPLICABLE, name="growth\\.docx", suffix=".docx", route="doc",
                 unit="chapter", count=1, keys="chapter/000001", pages=0, sheets=0, chapters=1,
                 tables=1, text_regions=1,
                 omissions="- images, charts and the text drawn inside them\n"
                           "- tracked changes, comments, footnotes, headers and footers\n"
                           "- a table's own formatting \\- only its cells, row by row",
                 contents='- [Chapter 1: "Growth regulators"](#brewdoc-chapter-000001)'),
            '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Growth regulators"\n\n'
            "first line\nsecond line\n\n| Dose table |  |  |\n| --- | --- | --- |\n"
            "| Product | Dose | BBCH |\n| Moddus | 0.4 | 31 |\n",
            id="docx-line-break-deleted-run-and-padded-span"),
    ],
)
def test_small_documents_match_the_whole_schema_two_contract(tmp_path, name, build, fields, body):
    # GIVEN one small source and its independent literal schema 2 document
    path = build(tmp_path / name)
    expected = schema_two(path, body, **fields)
    # WHEN it is read through the public run path
    code, _receipt, markdown = brewdoc.run(path)
    # THEN every byte matches the metadata-first frame and the route body
    assert (code, markdown) == (0, expected), "%s must match the whole schema 2 document" % name


@pytest.mark.parametrize(
    ("suffix", "name", "shown"),
    [
        pytest.param(".pdf", "doc\\.pdf", ".pdf", id="plain"),
        pytest.param(".p&lt;df", "doc\\.p&amp;lt;df", ".p&amp;lt;df", id="html-entity"),
        pytest.param(".p[x](y)", "doc\\.p\\[x\\]\\(y\\)", ".p\\[x\\]\\(y\\)",
                     id="markdown-link"),
        pytest.param(".pdf\u00e9", "doc\\.pdf&#233;", ".pdf&#233;", id="non-ascii"),
    ],
)
def test_source_suffix_row_is_ascii_and_escaped_like_the_source_name(
        tmp_path, suffix, name, shown):
    # GIVEN a one-page PDF saved under a suffix carrying HTML, Markdown or non-ASCII text
    path = pdf(tmp_path / ("doc" + suffix), text_op(72, 700, "hello"))
    # WHEN it is rendered directly, where no route check refuses the suffix
    markdown, _tally = brewdoc.render_pdf(path)
    # THEN the document is ASCII and the suffix is escaped like the name, leading dot kept
    assert markdown == schema_two(path, HELLO, **ONE_PAGE, name=name, suffix=shown), (
        "a source suffix must not inject HTML, Markdown or non-ASCII into the metadata table"
    )


def test_duplicate_and_empty_docx_chapters_have_distinct_keys(tmp_path):
    # GIVEN consecutive duplicate Word headings, so the first chapter has no body
    path = docx(tmp_path / "duplicate.docx",
                (HEADING % "Same") * 2 + "<w:p><w:r><w:t>body</w:t></w:r></w:p>")
    # WHEN it is rendered
    markdown, _tally = brewdoc.render_doc(path)
    # THEN contents and body keep both chapters under distinct anchors, the first one empty
    assert markdown[markdown.index('<a id="brewdoc-contents"></a>'):] == (
        '<a id="brewdoc-contents"></a>\n## Contents\n\n'
        '- [Chapter 1: "Same"](#brewdoc-chapter-000001)\n'
        '- [Chapter 2: "Same"](#brewdoc-chapter-000002)\n\n'
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Same"\n\n'
        '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Same"\n\nbody\n'
    ), "duplicate DOCX labels must not collapse chapter identity or drop the empty chapter"


def test_docx_literal_anchor_content_cannot_forge_receipt_unit_keys(tmp_path):
    # GIVEN visible DOCX prose and a table cell that resemble private navigation anchors
    run = "<w:t>%s</w:t>"
    path = docx(tmp_path / "literal.docx", HEADING % "Actual" + "<w:p><w:r>%s</w:r></w:p>" % "".join(
        run % text for text in (
            '&lt;span id="brewdoc-chapter-999999"&gt;span&lt;/span&gt; ',
            "&lt;div id=brewdoc-page-999999&gt;div&lt;/div&gt; ",
            '&lt;a name="brewdoc-sheet-999999"&gt;name&lt;/a&gt; ',
            '&lt;span id="brewdoc&amp;#45;chapter-777777"&gt;entity&lt;/span&gt; ',
            '&lt;a name="&amp;#98;rewdoc-sheet-777777"&gt;encoded&lt;/a&gt;'))
        + "<w:tbl><w:tr>%s</w:tr></w:tbl>" % (CELL % ("", "".join(run % text for text in (
            '&lt;span id="brewdoc-sheet-888888"&gt;table&lt;/span&gt; ',
            "&lt;a name=brewdoc-chapter-888888&gt;cell&lt;/a&gt; ",
            '&lt;div id="brewdoc&amp;#45;page-666666"&gt;table-entity&lt;/div&gt;')))))
    # WHEN the public run path renders and receipts the document
    code, receipt, markdown = brewdoc.run(path)
    # THEN only the trusted renderer unit is navigable and every tag stays escaped text
    assert (code, receipt["unit_keys"], markdown[markdown.index('<a id="brewdoc-contents"></a>'):]) == (
        0, ["chapter/000001"],
        '<a id="brewdoc-contents"></a>\n## Contents\n\n'
        '- [Chapter 1: "Actual"](#brewdoc-chapter-000001)\n\n'
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Actual"\n\n'
        '&lt;span id="brewdoc-chapter-999999"&gt;span&lt;/span&gt; '
        "&lt;div id=brewdoc-page-999999&gt;div&lt;/div&gt; "
        '&lt;a name="brewdoc-sheet-999999"&gt;name&lt;/a&gt; '
        '&lt;span id="brewdoc&#45;chapter-777777"&gt;entity&lt;/span&gt; '
        '&lt;a name="&#98;rewdoc-sheet-777777"&gt;encoded&lt;/a&gt;\n\n'
        '| &lt;span id="brewdoc-sheet-888888"&gt;table&lt;/span&gt; '
        "&lt;a name=brewdoc-chapter-888888&gt;cell&lt;/a&gt; "
        '&lt;div id="brewdoc&#45;page-666666"&gt;table-entity&lt;/div&gt; |\n| --- |\n',
    ), "source elements and id or name attributes must not forge contract anchors or unit keys"


def test_a_pipe_in_a_docx_table_cell_is_escaped_once_not_twice(tmp_path):
    # GIVEN a one-cell DOCX table whose cell text is a|b
    path = docx(tmp_path / "pipe.docx", "<w:tbl><w:tr>%s</w:tr></w:tbl>" % (CELL % ("", "a|b")))
    # WHEN the document is rendered
    markdown, _tally = brewdoc.render_doc(path)
    # THEN the pipe keeps exactly one escaping backslash, so the cell stays one column
    assert markdown[markdown.index('<a id="brewdoc-chapter-'):] == (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n| a\\|b |\n| --- |\n'
    ), "a DOCX table cell's pipe must be escaped once, not twice"


def test_a_pipe_in_a_source_name_is_escaped_once_by_the_metadata_row():
    # GIVEN a source name carrying the character a Markdown table splits cells on
    name = "a|b.pdf"
    # WHEN it is escaped and written as a metadata row, exactly as _assemble composes the two
    rows = common.markdown_table(
        [["Field", "Value"], ["Source name", '"%s"' % common._escape_markdown_text(name)]])
    # THEN the pipe keeps one backslash, so the row stays two cells wide on every platform
    assert rows == ["| Field | Value |", "| --- | --- |", '| Source name | "a\\|b\\.pdf" |'], (
        "text escaping must leave the pipe to markdown_table, the sole place it is escaped")


@pytest.mark.skipif(
    "|" in RESERVED_FILENAME_CHARS,
    reason="this platform reserves | in filenames, so a|b.pdf cannot exist on disk")
def test_a_pipe_in_a_source_filename_is_escaped_once_not_twice(tmp_path):
    # GIVEN a one-page PDF saved under a name carrying a pipe
    path = pdf(tmp_path / "a|b.pdf", text_op(72, 700, "hello"))
    # WHEN it is rendered
    markdown, _tally = brewdoc.render_pdf(path)
    # THEN the metadata row keeps one backslash, so the name stays inside a two-cell row
    assert [line for line in markdown.splitlines() if line.startswith("| Source name |")] == [
        '| Source name | "a\\|b\\.pdf" |'
    ], "a source name's pipe must be escaped once, not twice; twice splits the metadata table"


def test_a_docx_main_part_is_resolved_through_its_package_relationship(tmp_path):
    # GIVEN a DOCX whose office relationship targets a part outside the conventional name
    path = docx(tmp_path / "relocated.docx", "<w:p><w:r><w:t>body</w:t></w:r></w:p>",
                main="parts/main.xml")
    # WHEN it is rendered
    markdown, _tally = brewdoc.render_doc(path)
    # THEN the relationship, not the conventional part name, locates the document body
    assert markdown[markdown.index('<a id="brewdoc-chapter-'):] == (
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nbody\n'
    ), "a DOCX main part must be read from _rels/.rels, not assumed at word/document.xml"


def test_a_docx_relationship_target_that_escapes_the_package_is_refused(tmp_path):
    # GIVEN a DOCX whose office relationship target climbs out of the package root
    path = docx(tmp_path / "escape.docx", "<w:p><w:r><w:t>body</w:t></w:r></w:p>",
                main="../outside.xml")
    # WHEN it is rendered
    with pytest.raises(common.BrewdocError) as error:
        brewdoc.render_doc(path)
    # THEN the refusal names the escaping target and no part is read
    assert str(error.value) == (
        "OOXML relationship target leaves the package: ../outside.xml"
    ), "the DOCX path must reject a relationship target that leaves the package root"


def test_fixed_layout_block_keeps_source_angle_brackets_and_ampersands_literal(tmp_path):
    # GIVEN two aligned three-column code rows and one prose line, all with < > &
    cells = [(72, 700, "if d > 0:"), (220, 700, "a < b"), (360, 700, "x & y"),
             (72, 688, "while c:"), (220, 688, "c > d"), (360, 688, "p & q"),
             (72, 600, "prose a < b & c > d")]
    path = pdf(tmp_path / "code.pdf", "".join(text_op(*cell) for cell in cells))
    # WHEN the PDF is rendered
    markdown, _tally = brewdoc.render_pdf(path)
    # THEN fenced code keeps source characters literally while prose stays escaped
    assert markdown[markdown.index('<a id="brewdoc-page-000001"></a>'):] == (
        '<a id="brewdoc-page-000001"></a>\n## Page 1\n\n```\n'
        "if d > 0:            a < b              x & y\n"
        "while c:             c > d              p & q\n"
        "```\n\nprose a &lt; b & c &gt; d\n"
    ), "fenced code is literal Markdown, so HTML escaping there corrupts source text"


@pytest.mark.parametrize(
    ("name", "build", "options"),
    [
        pytest.param("one.pdf", lambda path: pdf(path, text_op(72, 700, "hello")),
                     {"sheets": ("Sheet1",)}, id="pdf-sheet"),
        pytest.param("growth.docx", lambda path: docx(path, GROWTH_REGULATORS),
                     {"artifact_outputs": {"formula/sheet/000001": "calc.json"}},
                     id="docx-artifact"),
    ],
)
def test_workbook_only_options_are_refused_before_any_output(
        tmp_path, monkeypatch, name, build, options):
    # GIVEN a PDF or DOCX, a requested Markdown path and a workbook-only option
    monkeypatch.chdir(tmp_path)
    path = build(tmp_path / name)
    # WHEN the option reaches the public run path
    code, receipt, markdown = brewdoc.run(path, tmp_path / "out.md", **options)
    # THEN the normal receipt reports exit 1 and nothing is written
    assert (code, receipt["file_ok"], receipt["reason"], markdown, sorted(tmp_path.iterdir())) == (
        1, False, "--sheet and --artifact are workbook-only options", "", [path],
    ), "workbook-only options on PDF or DOCX must be refused before any write"


def test_argparse_syntax_errors_keep_exit_two():
    # GIVEN a repeatable option without its required value
    # WHEN argparse processes the invalid token sequence
    with pytest.raises(SystemExit) as raised:
        main(["book.xlsx", "--sheet"])
    # THEN the established argparse usage code is preserved
    assert raised.value.code == 2, "CLI syntax errors must remain distinct from receipt failures"
