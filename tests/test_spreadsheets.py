import hashlib
import json
import re
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import brewdoc
from brewdoc import common
from brewdoc.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
VBA_RELATIONSHIP = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
VBA_KEY = "vba/project/000001"
FORMULA_KEYS = ("formula/sheet/000001", "formula/sheet/000002")
DROPPED = dict.fromkeys(("control_chars", "soft_hyphens", "nbsp", "pua_glyphs", "cid_survivors",
                         "ligatures", "running_heads", "page_numbers", "non_ascii_replaced"), 0)
SHEET_RECEIPT = {
    "broken_ligature_words": 0, "columns_split": 0, "dropped": DROPPED,
    "not_carried": ["cell formulas in Markdown content - only cached values are rendered",
                    "formatting, colours, comments and data validation",
                    "charts and embedded images", "readable VBA source modules"],
    "receipt_schema": "brewdoc.receipt/1", "route": "sheet", "source": "book.xlsx",
    "text_regions": 0, "unit_kind": "sheet",
}
INPUTS = '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Inputs"\n\n| 1.0 |\n| --- |\n| 2.0 |\n| 3.0 |\n'
CALC_TABLE = "| 3.0 | 2.0 |  |\n| --- | --- | --- |\n|  | 4.0 |  |\n|  |  | 1.0 |\n"
CALC = '<a id="brewdoc-sheet-000002"></a>\n## Sheet 2: "Calc"\n\n' + CALC_TABLE
EMPTY = '<a id="brewdoc-sheet-000003"></a>\n## Sheet 3: "Empty"\n'


def exact(message: str) -> str:
    """Return a pytest.raises pattern matching exactly `message`."""
    return "^%s$" % re.escape(message)


def tally(sheets: int, tables: int) -> dict:
    """Return the full render tally of a workbook with these sheet and table counts."""
    return {"pages": 0, "sheets": sheets, "chapters": 0, "slides": 0, "tables": tables,
            "text_regions": 0, "columns_split": 0, "broken_ligature_words": 0,
            "dropped": DROPPED}


@pytest.mark.parametrize(
    ("part", "relationships"),
    [
        ("presentation.xml", "_rels/presentation.xml.rels"),
        ("ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"),
        ("xl/worksheets/sheet1.xml", "xl/worksheets/_rels/sheet1.xml.rels"),
    ],
)
def test_relationship_part_follows_the_opc_sidecar_convention(part, relationships):
    # GIVEN a package part at the root or inside a nested package directory
    # WHEN its paired relationship part is derived
    actual = common._relationship_part(part)
    # THEN the relationship file sits in the adjacent _rels directory
    assert actual == relationships, "OPC consumers must share one sidecar-path rule"


def refused(reason: str) -> dict:
    """Return the full receipt of a workbook run refused for `reason`."""
    return {**SHEET_RECEIPT, "file_ok": False, "out": None, "reason": reason, "units": 0,
            "tables": 0}


def formula_row(ordinal: int, count: int, out: Path | None) -> dict:
    """Return one receipt artifact row for a formula sheet."""
    return {"availability": "available", "byte_size": None, "count": count,
            "key": "formula/sheet/%06d" % ordinal, "kind": "formula",
            "location": "#brewdoc-sheet-%06d" % ordinal, "media_type": "application/json",
            "out": None if out is None else str(out), "sha256": None}


def formula_ref(ordinal: int, count: int) -> brewdoc.ArtifactRef:
    """Return the inventory row of one formula-bearing sheet."""
    return brewdoc.ArtifactRef("formula/sheet/%06d" % ordinal, "formula", "available", count,
                               "#brewdoc-sheet-%06d" % ordinal, "application/json")


def formula_book(path: Path, *, missing_relationship=False, malformed_sheet=False) -> Path:
    """Write a small OOXML workbook with cached values and shared formulas."""
    main_type = (
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
        if path.suffix == ".xlsm"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    )
    sheet2 = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1"><f>SUM(Inputs!A1:A2)</f><v>3</v></c>'
        '<c r="B1"><f t="shared" ref="B1:B2" si="7">Inputs!A1*2</f><v>2</v></c></row>'
        '<row r="2"><c r="B2"><f si="7" t="shared"/><v>4</v></c></row>'
        '<row r="3"><c r="C3"><f bx="0" aca="1">IF(A1&lt;4,&quot;café&quot;,&quot;&quot;)</f>'
        '<v>1</v></c></row></sheetData></worksheet>'
    )
    if malformed_sheet:
        sheet2 = "<worksheet><sheetData>"
    calc_rel = "rId9" if missing_relationship else "rId2"
    content_types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="%s"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/></Types>' % main_type
    )
    workbook = (
        '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Inputs" sheetId="1" r:id="rId1"/>'
        '<sheet name="Calc" sheetId="2" r:id="%s"/>'
        '<sheet name="Empty" sheetId="3" r:id="rId3"/></sheets></workbook>' % calc_rel
    )
    relationships = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet3.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        package.writestr("xl/workbook.xml", workbook)
        package.writestr("xl/_rels/workbook.xml.rels", relationships)
        package.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1"><v>1</v></c></row><row r="2"><c r="A2"><v>2</v></c></row>'
            '<row r="3"><c r="A3"><f>SUM(A1:A2)</f><v>3</v></c></row>'
            "</sheetData></worksheet>",
        )
        package.writestr("xl/worksheets/sheet2.xml", sheet2)
        package.writestr(
            "xl/worksheets/sheet3.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData/></worksheet>",
        )
    return path


def vba_book(path: Path, relationships: str, *, parts=()) -> Path:
    """Write a one-sheet macro workbook whose workbook relationships end with `relationships`."""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        package.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Data" sheetId="1" r:id="rIdSheet"/></sheets></workbook>',
        )
        package.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdSheet" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>%s</Relationships>'
            % relationships,
        )
        package.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>',
        )
        for name, data in parts:
            package.writestr(name, data)
    return path


def vba_relationship(target: str, attributes: str = "") -> str:
    """Return one workbook-level VBA project relationship element."""
    return '<Relationship Id="rIdVba" Type="%s" Target="%s"%s/>' % (
        VBA_RELATIONSHIP, target, attributes)


def vba_ref(data: bytes) -> brewdoc.ArtifactRef:
    """Return the inventory row expected for one opaque VBA project payload."""
    return brewdoc.ArtifactRef(
        VBA_KEY, "vba-project", "available", 1, "#brewdoc-metadata",
        "application/vnd.ms-office.vbaProject", len(data), hashlib.sha256(data).hexdigest(),
    )


def rewrite_member(path: Path, member: str, old: bytes, new: bytes) -> None:
    """Replace bytes in one package member while preserving the synthetic package."""
    with zipfile.ZipFile(path) as source:
        members = [(item.filename, source.read(item.filename)) for item in source.infolist()]
    with zipfile.ZipFile(path, "w") as target:
        for name, data in members:
            target.writestr(name, data.replace(old, new) if name == member else data)



def rewrite_member_headers(path: Path, member: str, local: int, central: int, value: bytes) -> None:
    """Overwrite one field in a synthetic ZIP member's local and central headers."""
    data = bytearray(path.read_bytes())
    encoded = member.encode("ascii")
    for signature, size, offset in ((b"PK\x03\x04", 30, local), (b"PK\x01\x02", 46, central)):
        start = data.index(encoded, data.index(signature)) - size + offset
        data[start:start + len(value)] = value
    path.write_bytes(data)


def test_explicit_none_selection_is_the_default(tmp_path):
    # GIVEN a three-sheet workbook with formulas and cached values
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN it is rendered by default and with an explicit None selection
    implicit, explicit = brewdoc.render_book(path), brewdoc.render_book(path, sheets=None)
    # THEN both renders are identical
    assert explicit == implicit, "explicit None must be byte-compatible with the default"


@pytest.mark.parametrize(
    ("selection", "body", "expected_tally"),
    [
        pytest.param(None, INPUTS + "\n" + CALC + "\n" + EMPTY, tally(3, 2), id="default"),
        pytest.param(("Calc", "Inputs"), CALC + "\n" + INPUTS, tally(2, 2), id="caller-order"),
    ],
)
def test_sheet_selection_renders_cached_values_in_caller_order(
        tmp_path, selection, body, expected_tally):
    # GIVEN a three-sheet workbook with formulas and cached values
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN it is rendered with the default or an explicit selection
    markdown, actual_tally = brewdoc.render_book(path, sheets=selection)
    # THEN only the selected sheets render, in order, with cached values instead of formulas
    assert (markdown[markdown.index('<a id="brewdoc-sheet-'):], actual_tally) == (
        body, expected_tally,
    ), "selection must change only chapter order and count"


@pytest.mark.corpus
@pytest.mark.parametrize(
    ("relative", "selection"),
    [
        ("ods/calamine-issues.ods", ("issue2", "datatypes")),
        ("xls/calamine-xls_formula.xls", ("Sheet2", "Sheet1")),
        ("xlsb/calamine-issues.xlsb", ("Sheet1", "datatypes")),
        ("xlsm/calamine-issue3.xlsm", ("Sheet3", "Sheet1")),
        ("xlsx/calamine-temperature-table.xlsx", ("Sheet2", "Sheet1")),
    ],
)
def test_sheet_selection_works_for_every_workbook_format(relative, selection):
    # GIVEN a supported multi-sheet workbook
    # WHEN two sheets are selected in reverse source order
    markdown, actual_tally = brewdoc.render_book(FIXTURES / relative, sheets=selection)
    headings = [line.split(': "', 1)[1][:-1] for line in markdown.splitlines()
                if line.startswith("## Sheet ")]
    # THEN only those sheets render in caller order through the existing value path
    assert (headings, actual_tally["sheets"]) == (list(selection), 2), (
        "sheet selection must share one contract across every workbook format"
    )


@pytest.mark.parametrize(
    ("selection", "reason"),
    [
        ((), "sheet selection is empty"),
        (("Calc", "Calc"), "duplicate sheet selection: Calc"),
        (("Calc", "Missing"),
         "unknown sheet selection: Missing; available sheets: Inputs, Calc, Empty"),
        (("calc",), "unknown sheet selection: calc; available sheets: Inputs, Calc, Empty"),
    ],
)
def test_invalid_sheet_selection_fails_before_any_sheet_read(tmp_path, selection, reason):
    # GIVEN a workbook whose Calc sheet cannot be read
    path = formula_book(tmp_path / "book.xlsx", malformed_sheet=True)
    with pytest.raises(brewdoc.BrewdocError,
                       match=exact("spreadsheet unreadable: %s: sheetData" % path)):
        brewdoc.render_book(path, sheets=("Calc",))
    # WHEN the caller requests an invalid selection
    # THEN the selection error wins over the broken sheet read
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)):
        brewdoc.render_book(path, sheets=selection)


@pytest.mark.parametrize("selection", [5, "Calc", b"Calc"], ids=["int", "str", "bytes"])
@pytest.mark.parametrize(
    "call",
    [
        lambda path, sheets: brewdoc.render_book(path, sheets=sheets),
        lambda path, sheets: brewdoc.list_book_artifacts(path, sheets=sheets),
        lambda path, sheets: brewdoc.read_book_artifact(
            path, "formula/sheet/000002", sheets=sheets),
        lambda path, sheets: brewdoc.read_formulas(path, sheets=sheets),
    ],
    ids=["render_book", "list_book_artifacts", "read_book_artifact", "read_formulas"],
)
def test_non_iterable_or_bare_string_selection_is_refused_as_a_selection_error(
        tmp_path, call, selection):
    # GIVEN a readable workbook and a selection that is not a sequence of names
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN a public entry point receives it
    # THEN it raises the one selection error, not a raw TypeError or unreadable-file error
    with pytest.raises(brewdoc.BrewdocError,
                       match=exact("sheet selection must be a sequence of names")):
        call(path, selection)


@pytest.mark.parametrize("selection", [5, "Calc", b"Calc"], ids=["int", "str", "bytes"])
def test_run_reports_a_non_iterable_or_bare_string_selection_as_a_selection_error(
        tmp_path, selection):
    # GIVEN a readable workbook and a selection that is not a sequence of names
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN run receives it
    actual = brewdoc.run(path, sheets=selection)
    # THEN the receipt carries the same selection error as the raising entry points
    assert actual == (1, refused("sheet selection must be a sequence of names"), ""), (
        "run must report the shared selection error, not an unreadable-file error"
    )


ZERO_SHEET_PROJECT = b"zero-sheet project"


def zero_sheet_book(path: Path) -> Path:
    """Write a valid XLSM whose workbook lists no sheet but relates one VBA project."""
    vba_book(path, vba_relationship("vbaProject.bin"),
             parts=(("xl/vbaProject.bin", ZERO_SHEET_PROJECT),))
    rewrite_member(path, "xl/workbook.xml",
                   b'<sheet name="Data" sheetId="1" r:id="rIdSheet"/>', b"")
    return path


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        pytest.param(lambda path: brewdoc.list_book_artifacts(path),
                     (vba_ref(ZERO_SHEET_PROJECT),), id="list_book_artifacts"),
        pytest.param(lambda path: brewdoc.read_book_artifact(path, VBA_KEY),
                     ZERO_SHEET_PROJECT, id="read_book_artifact"),
        pytest.param(lambda path: [
            line for line in brewdoc.render_book(path)[0].splitlines()
            if line.startswith(("| Rendered unit count ", "| VBA project count "))
        ], ["| Rendered unit count | 0 |", "| VBA project count | 1 |"], id="render_book"),
        pytest.param(lambda path: (lambda code, receipt, markdown: (code, receipt["reason"]))(
            *brewdoc.run(path)),
            (0, "sheet rendered: 0 sheets, 0 tables, 0 text regions, 0 column splits"),
            id="run"),
    ],
)
def test_default_selection_of_a_zero_sheet_workbook_keeps_workbook_level_results(
        tmp_path, call, expected):
    # GIVEN a valid XLSM with no worksheet and one related VBA project
    path = zero_sheet_book(tmp_path / "book.xlsm")
    with zipfile.ZipFile(path) as package:
        assert package.read("xl/workbook.xml").count(b"<sheet ") == 0, "precondition: no sheet"
    # WHEN a public entry point uses the default selection
    actual = call(path)
    # THEN None selects every sheet, here none, and workbook-level results remain
    assert actual == expected, "only an explicit empty selection may be refused as empty"


@pytest.mark.parametrize(
    "call",
    [
        lambda path, sheets: brewdoc.render_book(path, sheets=sheets),
        lambda path, sheets: brewdoc.list_book_artifacts(path, sheets=sheets),
        lambda path, sheets: brewdoc.read_book_artifact(path, VBA_KEY, sheets=sheets),
    ],
    ids=["render_book", "list_book_artifacts", "read_book_artifact"],
)
def test_explicit_empty_selection_is_refused_even_for_a_zero_sheet_workbook(tmp_path, call):
    # GIVEN a valid XLSM with no worksheet
    path = zero_sheet_book(tmp_path / "book.xlsm")
    # WHEN a caller explicitly selects no sheet
    # THEN the explicit empty sequence is refused
    with pytest.raises(brewdoc.BrewdocError, match=exact("sheet selection is empty")):
        call(path, ())


@pytest.mark.parametrize(
    "call",
    [
        lambda path, sheets: brewdoc.render_book(path, sheets=sheets),
        lambda path, sheets: brewdoc.list_book_artifacts(path, sheets=sheets),
        lambda path, sheets: brewdoc.read_book_artifact(
            path, "formula/sheet/000002", sheets=sheets),
    ],
    ids=["render_book", "list_book_artifacts", "read_book_artifact"],
)
def test_one_shot_sheet_iterator_selects_like_the_equivalent_tuple(tmp_path, call):
    # GIVEN a workbook and its result for a two-sheet tuple selection
    path = formula_book(tmp_path / "book.xlsx")
    expected = call(path, ("Calc", "Inputs"))
    # WHEN the same names arrive as a one-shot iterator
    actual = call(path, iter(("Calc", "Inputs")))
    # THEN the result is identical to the tuple selection
    assert actual == expected, "any iterable of sheet names must be consumed exactly once"


@pytest.mark.parametrize("artifacts", [(), (("formula/sheet/000002", "calc.json"),)],
                         ids=["markdown", "markdown-and-artifact"])
def test_run_accepts_a_one_shot_sheet_iterator_like_the_equivalent_tuple(tmp_path, artifacts):
    # GIVEN a workbook and its successful run for a two-sheet tuple selection
    path = formula_book(tmp_path / "book.xlsx")
    outputs = {key: tmp_path / name for key, name in artifacts}
    expected = brewdoc.run(path, sheets=("Calc", "Inputs"), artifact_outputs=outputs)
    assert (expected[0], expected[1]["selected_sheets"]) == (0, ["Calc", "Inputs"]), (
        "the tuple selection must render successfully"
    )
    # WHEN the same names arrive as a one-shot iterator
    actual = brewdoc.run(path, sheets=iter(("Calc", "Inputs")), artifact_outputs=outputs)
    # THEN exit code, receipt and Markdown are identical to the tuple selection
    assert actual == expected, "run must consume any iterable of sheet names exactly once"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_ooxml_formula_models_preserve_source_text_attributes_and_ordinals(tmp_path, suffix):
    # GIVEN an OOXML workbook with normal, shared, and empty shared formulas
    path = formula_book(tmp_path / ("book" + suffix))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # WHEN its formula artifacts are read in caller sheet order
    artifacts = brewdoc.read_formulas(path, sheets=("Calc", "Inputs"))
    # THEN keys retain full-source ordinals and every formula field is exact
    assert artifacts == (
        brewdoc.FormulaArtifact(
            key="formula/sheet/000002", source_name=path.name, source_sha256=digest,
            source_ordinal=2, sheet="Calc", formulas=(
                brewdoc.CellFormula("Calc", "A1", "SUM(Inputs!A1:A2)", ()),
                brewdoc.CellFormula(
                    "Calc", "B1", "Inputs!A1*2", (("ref", "B1:B2"), ("si", "7"), ("t", "shared"))
                ),
                brewdoc.CellFormula("Calc", "B2", "", (("si", "7"), ("t", "shared"))),
                brewdoc.CellFormula("Calc", "C3", 'IF(A1<4,"café","")', (("aca", "1"), ("bx", "0"))),
            ),
        ),
        brewdoc.FormulaArtifact(
            key="formula/sheet/000001", source_name=path.name, source_sha256=digest,
            source_ordinal=1, sheet="Inputs", formulas=(
                brewdoc.CellFormula("Inputs", "A3", "SUM(A1:A2)", ()),
            ),
        ),
    ), "formula models must preserve XML content and full workbook identity"


def test_formula_models_are_immutable(tmp_path):
    # GIVEN one formula artifact read from a workbook
    artifact = brewdoc.read_formulas(formula_book(tmp_path / "book.xlsx"), sheets=("Calc",))[0]
    # WHEN a caller assigns to one of its fields
    # THEN the frozen dataclass refuses the change
    with pytest.raises(FrozenInstanceError, match=exact("cannot assign to field 'sheet'")):
        artifact.sheet = "Changed"


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(None, (formula_ref(1, 1), formula_ref(2, 4)), id="default"),
        pytest.param(("Calc", "Inputs"), (formula_ref(2, 4), formula_ref(1, 1)),
                     id="caller-order"),
        pytest.param(("Calc",), (formula_ref(2, 4),), id="one-sheet"),
        pytest.param(("Empty",), (), id="formula-free-sheet"),
    ],
)
def test_formula_inventory_has_one_row_per_formula_sheet_in_selection_order(
        tmp_path, selection, expected):
    # GIVEN two formula-bearing sheets, one with four formula cells, and an empty sheet
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN the artifact inventory is listed for a selection
    references = brewdoc.list_book_artifacts(path, sheets=selection)
    # THEN each selected formula sheet owns one full-source key and an empty sheet a known zero
    assert references == expected, "formula inventory must be per sheet, never per cell"


@pytest.mark.parametrize("suffix", [".xls", ".xlsb", ".ods", ".pdf"])
def test_formula_status_is_unavailable_for_unsupported_formats(tmp_path, suffix):
    # GIVEN a format whose released Python reader exposes no OOXML formula source
    path = tmp_path / ("book" + suffix)
    path.write_bytes(b"not inspected")
    # WHEN formula discovery is requested
    # THEN it reports unavailable rather than zero
    with pytest.raises(brewdoc.BrewdocError, match=exact(
            "formula artifacts unavailable for '%s'; supported suffixes: .xlsm .xlsx" % suffix)):
        brewdoc.read_formulas(path)


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"missing_relationship": True}, "worksheet relationship rId9 is missing for sheet Calc"),
        ({"malformed_sheet": True},
         "cannot parse OOXML part xl/worksheets/sheet2.xml: no element found: line 1, column 22"),
    ],
)
def test_malformed_ooxml_formula_parts_fail_clearly(tmp_path, options, reason):
    # GIVEN an OOXML workbook with a broken relationship or worksheet
    path = formula_book(tmp_path / "broken.xlsx", **options)
    # WHEN formulas are read
    # THEN the failing source part is named
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)):
        brewdoc.read_formulas(path)


def source_identity_rows(path: Path) -> str:
    """The two adjacent metadata rows that identify one source file."""
    return "| Source bytes | %d |\n| Source SHA-256 | %s |" % (
        path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize(
    ("member", "relative", "absolute", "count"),
    [
        ("_rels/.rels", b'Target="xl/workbook.xml"', b'Target="/xl/workbook.xml"', 1),
        ("xl/_rels/workbook.xml.rels", b'Target="worksheets/', b'Target="/xl/worksheets/', 3),
    ],
    ids=["office-document", "worksheets"],
)
def test_absolute_opc_relationship_targets_render_like_relative_ones(
        tmp_path, member, relative, absolute, count):
    # GIVEN one workbook with relative targets and its twin with absolute ones, as openpyxl writes
    (tmp_path / "relative").mkdir()
    (tmp_path / "absolute").mkdir()
    reference = formula_book(tmp_path / "relative" / "book.xlsx")
    path = formula_book(tmp_path / "absolute" / "book.xlsx")
    rewrite_member(path, member, relative, absolute)
    with zipfile.ZipFile(path) as package:
        assert package.read(member).count(absolute) == count, "twin must use absolute targets"
    expected, expected_tally = brewdoc.render_book(reference)
    assert expected.count(source_identity_rows(reference)) == 1, "source rows must be unique"
    # WHEN the absolute-target twin is rendered
    actual = brewdoc.render_book(path)
    # THEN only the source identity rows differ from the relative-target render
    assert actual == (
        expected.replace(source_identity_rows(reference), source_identity_rows(path)),
        expected_tally,
    ), "absolute OPC part names are legal relationship targets and must resolve from the root"




@pytest.mark.corpus
@pytest.mark.parametrize(
    ("relative", "size", "project_sha256"),
    [
        (
            "xlsm/calamine-vba.xlsm",
            15360,
            "35c41ef85ec3a2076a5324159db6fa1b9a6c2ba0bf9b52ac7cf61ebddd9ebae8",
        ),
        (
            "xlsb/calamine-issues.xlsb",
            17920,
            "d4d33fa604e2072a9a4828c116e0b4b28984ea7d4ebfa9ead6f85685dc20ac2c",
        ),
    ],
)
def test_vba_inventory_and_read_preserve_exact_fixture_project_bytes(
        relative, size, project_sha256):
    # GIVEN a licensed workbook fixture with one related VBA project
    path = FIXTURES / relative
    with zipfile.ZipFile(path) as package:
        expected_data = package.read("xl/vbaProject.bin")
    # WHEN its inventory is listed and the project is read by key
    references = brewdoc.list_book_artifacts(path)
    data = brewdoc.read_book_artifact(path, VBA_KEY)
    # THEN the inventory has one workbook-level project and the read keeps every byte
    assert (references, data) == ((
        brewdoc.ArtifactRef(
            VBA_KEY, "vba-project", "available", 1, "#brewdoc-metadata",
            "application/vnd.ms-office.vbaProject", size, project_sha256,
        ),
    ), expected_data), "VBA inventory and keyed read must preserve the opaque project exactly"


@pytest.mark.corpus
def test_vba_project_is_independent_of_sheet_selection():
    # GIVEN a macro workbook whose second sheet alone is selected
    path = FIXTURES / "xlsm/calamine-vba.xlsm"
    _markdown, tally = brewdoc.render_book(path, sheets=("Sheet2",))
    assert tally["sheets"] == 1, "the precondition must render only the requested sheet"
    # WHEN the inventory is listed and the project read with and without that selection
    selected = (brewdoc.list_book_artifacts(path, sheets=("Sheet2",)),
                brewdoc.read_book_artifact(path, VBA_KEY, sheets=("Sheet2",)))
    full = (brewdoc.list_book_artifacts(path), brewdoc.read_book_artifact(path, VBA_KEY))
    # THEN the workbook-scoped project is neither filtered nor rewritten
    assert selected == full, "sheet selection must not filter or rewrite the VBA project"


@pytest.mark.parametrize(
    ("build", "capability", "count"),
    [
        pytest.param(lambda tmp_path: vba_book(tmp_path / "book.xlsm", ""),
                     "available", "0", id="xlsm-without-project"),
        pytest.param(lambda tmp_path: vba_book(
            tmp_path / "book.xlsm", vba_relationship("vbaProject.bin"),
            parts=(("xl/vbaProject.bin", b"project"),)),
            "available", "1", id="xlsm-with-project"),
        pytest.param(lambda tmp_path: formula_book(tmp_path / "book.xlsx"),
                     "unavailable", "unknown", id="xlsx"),
        pytest.param(lambda _tmp_path: FIXTURES / "xls/calamine-xls_formula.xls",
                     "unavailable", "unknown", id="xls", marks=pytest.mark.corpus),
        pytest.param(lambda _tmp_path: FIXTURES / "ods/calamine-issues.ods",
                     "unavailable", "unknown", id="ods", marks=pytest.mark.corpus),
    ],
)
def test_vba_metadata_distinguishes_known_zero_from_unavailable(
        tmp_path, build, capability, count):
    # GIVEN a workbook whose format may or may not carry a related VBA project
    path = build(tmp_path)
    # WHEN it is rendered
    markdown, _tally = brewdoc.render_book(path)
    # THEN a supported format reports a known count and others report unknown
    assert [line for line in markdown.splitlines() if line.startswith("| VBA project ")] == [
        "| VBA project capability | %s |" % capability, "| VBA project count | %s |" % count,
    ], "VBA metadata must separate a known zero from an unavailable capability"


@pytest.mark.parametrize(
    "build",
    [
        lambda tmp_path: vba_book(tmp_path / "book.xlsm", ""),
        lambda tmp_path: formula_book(tmp_path / "book.xlsx"),
    ],
    ids=["xlsm-without-project", "xlsx"],
)
def test_absent_vba_project_read_is_refused(tmp_path, build):
    # GIVEN a workbook without a related VBA project
    path = build(tmp_path)
    # WHEN the project key is read
    # THEN the generic artifact reader names the missing key
    with pytest.raises(brewdoc.BrewdocError,
                       match=exact("workbook artifact not found: vba/project/000001")):
        brewdoc.read_book_artifact(path, VBA_KEY)


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
def test_workbook_artifacts_are_refused_outside_workbook_suffixes(tmp_path, suffix):
    # GIVEN a non-workbook path whose bytes must not be inspected
    path = tmp_path / ("book" + suffix)
    path.write_bytes(b"not inspected")
    # WHEN its artifact inventory is requested
    # THEN the capability is refused by suffix
    with pytest.raises(brewdoc.BrewdocError,
                       match=exact("workbook artifacts unavailable for '%s'" % suffix)):
        brewdoc.list_book_artifacts(path)


@pytest.mark.parametrize(
    ("relationships", "parts", "reason"),
    [
        pytest.param(
            vba_relationship("vbaProject.bin") + vba_relationship("other.bin"),
            (("xl/vbaProject.bin", b"one"), ("xl/other.bin", b"two")),
            "multiple VBA project relationships", id="multiple",
        ),
        pytest.param(
            vba_relationship("vbaProject.bin", ' TargetMode="External"'),
            (("xl/vbaProject.bin", b"project"),),
            "VBA project relationship is external", id="external",
        ),
        pytest.param(
            '<Relationship Id="rIdVba" Type="%s"/>' % VBA_RELATIONSHIP, (),
            "OOXML relationship target is missing", id="no-target",
        ),
        pytest.param(
            vba_relationship(""), (), "OOXML relationship target is missing", id="empty-target",
        ),
        pytest.param(
            vba_relationship("../../vbaProject.bin"), (("vbaProject.bin", b"project"),),
            "OOXML relationship target leaves the package: ../../vbaProject.bin",
            id="parent-escape",
        ),
        pytest.param(
            vba_relationship("/../vbaProject.bin"), (("vbaProject.bin", b"project"),),
            "OOXML relationship target leaves the package: /../vbaProject.bin",
            id="root-escape",
        ),
        pytest.param(
            vba_relationship("vbaProject.bin"), (),
            "OOXML part is missing: xl/vbaProject.bin", id="missing-part",
        ),
        pytest.param(
            "<Relationship", (), "spreadsheet unreadable: {path}: Relationships",
            id="unparseable",
        ),
    ],
)
def test_malformed_vba_relationships_fail_at_the_archive_boundary(
        tmp_path, relationships, parts, reason):
    # GIVEN a one-sheet XLSM with one malformed VBA relationship boundary
    path = vba_book(tmp_path / "broken.xlsm", relationships, parts=parts)
    # WHEN its artifact inventory is listed
    # THEN the exact package boundary is refused
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason.format(path=path))):
        brewdoc.list_book_artifacts(path)


@pytest.mark.parametrize(
    "target", ["vbaProject.bin", "/xl/vbaProject.bin", "../xl/vbaProject.bin"],
    ids=["relative", "absolute", "parent-relative"],
)
def test_vba_relationship_target_spellings_return_the_same_project(tmp_path, target):
    # GIVEN an XLSM whose one VBA relationship names xl/vbaProject.bin in a legal OPC spelling
    data = b"opaque project"
    path = vba_book(tmp_path / "book.xlsm", vba_relationship(target),
                    parts=(("xl/vbaProject.bin", data),))
    # WHEN the inventory is listed and the keyed project is read
    actual = (brewdoc.list_book_artifacts(path), brewdoc.read_book_artifact(path, VBA_KEY))
    # THEN every spelling yields the same inventory row and exact bytes
    assert actual == ((vba_ref(data),), data), (
        "absolute and in-package relative OPC targets must resolve like the plain relative one"
    )


@pytest.mark.parametrize(
    ("local", "central", "value", "header", "reason"),
    [
        pytest.param(6, 8, (1).to_bytes(2, "little"), (1, 14),
                     "VBA project part is encrypted: xl/vbaProject.bin", id="encrypted"),
        pytest.param(22, 24, (16 * 1024 * 1024 + 1).to_bytes(4, "little"), (0, 16777217),
                     "VBA project part exceeds 16777216 bytes: xl/vbaProject.bin",
                     id="declared-oversize"),
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda path: brewdoc.list_book_artifacts(path),
        lambda path: brewdoc.read_book_artifact(path, VBA_KEY),
    ],
    ids=["list_book_artifacts", "read_book_artifact"],
)
def test_unsafe_vba_member_headers_are_refused_before_read(
        tmp_path, call, local, central, value, header, reason):
    # GIVEN an XLSM whose project member headers declare encryption or a size over 16 MiB
    path = vba_book(tmp_path / "book.xlsm", vba_relationship("vbaProject.bin"),
                    parts=(("xl/vbaProject.bin", b"opaque project"),))
    rewrite_member_headers(path, "xl/vbaProject.bin", local, central, value)
    with zipfile.ZipFile(path) as package:
        info = package.getinfo("xl/vbaProject.bin")
    assert (info.flag_bits, info.file_size) == header, "precondition: forged member header"
    # WHEN its inventory is listed or the project is read by key
    # THEN no password, decryption or unbounded read is attempted
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)):
        call(path)


def test_formula_artifact_json_is_deterministic_ascii_with_every_field(tmp_path):
    # GIVEN a workbook with two formula-bearing sheets and the exact expected JSON bytes
    path = formula_book(tmp_path / "book.xlsx")
    expected = (json.dumps({
        "formulas": [
            {"attributes": {}, "cell": "A1", "formula": "SUM(Inputs!A1:A2)", "sheet": "Calc"},
            {"attributes": {"ref": "B1:B2", "si": "7", "t": "shared"},
             "cell": "B1", "formula": "Inputs!A1*2", "sheet": "Calc"},
            {"attributes": {"si": "7", "t": "shared"}, "cell": "B2", "formula": "",
             "sheet": "Calc"},
            {"attributes": {"aca": "1", "bx": "0"}, "cell": "C3",
             "formula": 'IF(A1<4,"café","")', "sheet": "Calc"},
        ],
        "schema": "brewdoc.formulas/1",
        "sheet": {"key": "sheet/000002", "name": "Calc", "ordinal": 2},
        "source": {"bytes": path.stat().st_size, "name": "book.xlsx",
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "suffix": ".xlsx"},
    }, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    # WHEN one selected artifact is read twice by key
    payloads = [brewdoc.read_book_artifact(path, FORMULA_KEYS[1], sheets=("Calc",))
                for _read in range(2)]
    # THEN both reads are the same sorted ASCII JSON with one final newline
    assert payloads == [expected, expected], "formula JSON must keep every field, byte for byte"


def test_artifact_request_does_not_change_markdown_and_receipt_names_output(tmp_path):
    # GIVEN one selected formula-bearing sheet and separate Markdown and artifact paths
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    artifact_out = tmp_path / "calc.json"
    receipt = {**SHEET_RECEIPT, "file_ok": True, "markdown_schema": "brewdoc.markdown/2",
               "reason": "sheet rendered: 1 sheets, 1 tables, 0 text regions, 0 column splits",
               "selected_sheets": ["Calc"], "units": 1, "tables": 1,
               "unit_keys": ["sheet/000002"]}
    # WHEN the same selection is rendered without and with an artifact request
    plain = brewdoc.run(path, sheets=("Calc",))
    requested = brewdoc.run(path, markdown_out, sheets=("Calc",),
                            artifact_outputs={FORMULA_KEYS[1]: artifact_out})
    # THEN Markdown is invariant and the receipt identifies the exact written artifact
    assert (plain, requested, markdown_out.read_bytes(), artifact_out.read_bytes()) == (
        (0, {**receipt, "artifacts": [formula_row(2, 4, None)], "out": None}, plain[2]),
        (0, {**receipt, "artifacts": [formula_row(2, 4, artifact_out)],
             "out": str(markdown_out)}, plain[2]),
        plain[2].encode("ascii"),
        brewdoc.read_book_artifact(path, FORMULA_KEYS[1], sheets=("Calc",)),
    ), "artifact retrieval must not alter Markdown and must name every written output"


def test_formula_inventory_has_one_linked_row_per_sheet_not_per_cell(tmp_path):
    # GIVEN one selected sheet carrying four formulas
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN its metadata-first Markdown is rendered
    markdown, _tally = brewdoc.render_book(path, sheets=("Calc",))
    rows = [line for line in markdown.splitlines()
            if line.startswith(("| Selected formula count ", "| formula/sheet/"))]
    # THEN metadata has the exact count and one artifact row links the sheet
    assert rows == [
        "| Selected formula count | 4 |",
        "| formula/sheet/000002 | formula | available | 4 | "
        "[sheet/000002](#brewdoc-sheet-000002) | application/json | not applicable | "
        "not applicable |",
    ], "formula-heavy sheets must not emit one Markdown row per formula cell"


def test_cli_repeats_sheet_and_artifact_options_in_caller_order(tmp_path, capsys):
    # GIVEN a workbook, two sheet options, and one artifact output
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    artifact_out = tmp_path / "calc.json"
    # WHEN repeatable CLI options are supplied
    code = main([str(path), "--out", str(markdown_out), "--sheet", "Calc", "--sheet", "Inputs",
                 "--artifact", "formula/sheet/000002=%s" % artifact_out])
    # THEN selection order, full-source keys, and the written artifact are exact
    assert (code, json.loads(capsys.readouterr().out), artifact_out.read_bytes()) == (0, {
        **SHEET_RECEIPT, "artifacts": [formula_row(2, 4, artifact_out), formula_row(1, 1, None)],
        "file_ok": True, "markdown_schema": "brewdoc.markdown/2", "out": str(markdown_out),
        "reason": "sheet rendered: 2 sheets, 2 tables, 0 text regions, 0 column splits",
        "selected_sheets": ["Calc", "Inputs"], "units": 2, "tables": 2,
        "unit_keys": ["sheet/000002", "sheet/000001"],
    }, brewdoc.read_book_artifact(path, FORMULA_KEYS[1])), (
        "repeatable CLI options must keep caller order and reach the artifact writer"
    )


def test_a_pipe_in_a_workbook_cell_is_escaped_once_not_twice(tmp_path):
    # GIVEN a workbook whose first Inputs cell is the text a|b
    path = formula_book(tmp_path / "book.xlsx")
    inline = b'<c r="A1" t="inlineStr"><is><t>a|b</t></is></c>'
    rewrite_member(path, "xl/worksheets/sheet1.xml", b'<c r="A1"><v>1</v></c>', inline)
    with zipfile.ZipFile(path) as package:
        assert package.read("xl/worksheets/sheet1.xml").count(inline) == 1, (
            "precondition: A1 must hold the inline string a|b"
        )
    # WHEN that sheet is rendered
    markdown, _tally = brewdoc.render_book(path, sheets=("Inputs",))
    # THEN the pipe keeps exactly one escaping backslash, so the cell stays one column
    assert markdown[markdown.index('<a id="brewdoc-sheet-'):] == (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Inputs"\n\n'
        "| a\\|b |\n| --- |\n| 2.0 |\n| 3.0 |\n"
    ), "a workbook cell's pipe must be escaped once, not twice"


def test_special_sheet_labels_and_selected_empty_sheet_remain_navigable(tmp_path):
    # GIVEN a formula sheet with Unicode, Markdown and HTML punctuation plus an empty sheet
    path = formula_book(tmp_path / "book.xlsx")
    rewrite_member(path, "xl/workbook.xml", b'name="Calc"',
                   'name="R&amp;D &lt;Plan&gt; café #1"'.encode("utf-8"))
    # WHEN both sheets are selected in caller order
    markdown, actual_tally = brewdoc.render_book(path, sheets=("R&D <Plan> café #1", "Empty"))
    # THEN labels are safely ASCII encoded, link full-source ordinals, and the empty sheet keeps
    # its own anchor and count
    label = '"R&amp;D &lt;Plan&gt; caf&#233; \\#1"'
    assert (markdown[markdown.index('<a id="brewdoc-contents"></a>'):], actual_tally) == (
        '<a id="brewdoc-contents"></a>\n## Contents\n\n'
        "- [Sheet 2: %s](#brewdoc-sheet-000002)\n"
        '- [Sheet 3: "Empty"](#brewdoc-sheet-000003)\n\n'
        '<a id="brewdoc-sheet-000002"></a>\n## Sheet 2: %s\n\n%s\n%s' % (
            label, label, CALC_TABLE, EMPTY),
        tally(2, 1),
    ), "untrusted labels must not become Markdown or HTML syntax"
