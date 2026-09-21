import hashlib
import itertools
import json
import os
import re
import stat
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import brewdoc
from brewdoc import reader

FIXTURES = Path(__file__).parent / "fixtures"
VBA_RELATIONSHIP = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"


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
    """Write the package boundary needed for VBA relationship tests."""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        package.writestr("xl/workbook.xml", "<workbook/>")
        package.writestr("xl/_rels/workbook.xml.rels", relationships)
        for name, data in parts:
            package.writestr(name, data)
    return path


def rewrite_member(path: Path, member: str, old: bytes, new: bytes) -> None:
    """Replace bytes in one package member while preserving the synthetic package."""
    with zipfile.ZipFile(path) as source:
        members = [(item.filename, source.read(item.filename)) for item in source.infolist()]
    with zipfile.ZipFile(path, "w") as target:
        for name, data in members:
            target.writestr(name, data.replace(old, new) if name == member else data)


def mark_member_encrypted(path: Path, member: str) -> None:
    """Set the encrypted flag in one synthetic ZIP member's two headers."""
    data = bytearray(path.read_bytes())
    encoded = member.encode("ascii")
    local = data.index(encoded, data.index(b"PK\x03\x04")) - 30
    central = data.index(encoded, data.index(b"PK\x01\x02")) - 46
    for offset in (local + 6, central + 8):
        flags = int.from_bytes(data[offset:offset + 2], "little") | 1
        data[offset:offset + 2] = flags.to_bytes(2, "little")
    path.write_bytes(data)


def test_sheet_selection_preserves_default_and_renders_caller_order(tmp_path):
    # GIVEN a three-sheet workbook with formulas and cached values
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN it is rendered by default, explicitly by default, and in caller order
    implicit = reader.render_book(path)
    explicit = reader.render_book(path, sheets=None)
    selected, tally = reader.render_book(path, sheets=("Calc", "Inputs"))
    # THEN the default is unchanged and selection only changes chapter order and count
    assert implicit == explicit, "explicit None must be byte-compatible with the existing default"
    assert [line for line in implicit[0].splitlines() if line.startswith("## Sheet ")] == [
        '## Sheet 1: "Inputs"', '## Sheet 2: "Calc"', '## Sheet 3: "Empty"'
    ], "default rendering must keep workbook order"
    assert [line for line in selected.splitlines() if line.startswith("## Sheet ")] == [
        '## Sheet 2: "Calc"', '## Sheet 1: "Inputs"'
    ], "explicit sheets must render in caller order"
    assert (tally["sheets"], tally["tables"]) == (2, 2), "selection must count rendered sheets"
    assert reader.chapter_lines(selected, "Calc") == [
        "| 3.0 | 2.0 |  |", "| --- | --- | --- |", "|  | 4.0 |  |", "|  |  | 1.0 |"
    ], "Markdown must keep cached values rather than formula text"


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
    markdown, tally = reader.render_book(FIXTURES / relative, sheets=selection)
    # THEN only those sheets render in caller order through the existing value path
    headings = [line for line in markdown.splitlines() if line.startswith("## Sheet ")]
    assert [line.split(': "', 1)[1][:-1] for line in headings] == list(selection), (
        "sheet selection must share one contract across every workbook format"
    )
    assert tally["sheets"] == 2, "the sheet tally must count selections for every workbook format"


@pytest.mark.parametrize(
    ("selection", "reason"),
    [
        ((), "sheet selection is empty"),
        (("Inputs", "Inputs"), "duplicate sheet selection: Inputs"),
        (("Missing",), "unknown sheet selection: Missing; available sheets: Inputs, Calc, Empty"),
        (("calc",), "unknown sheet selection: calc; available sheets: Inputs, Calc, Empty"),
    ],
)
def test_invalid_sheet_selection_fails_before_rendering(tmp_path, selection, reason):
    # GIVEN a workbook and an invalid explicit selection
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN the caller requests it
    # THEN the exact selection error is raised
    with pytest.raises(reader.BrewdocError, match="^%s$" % reason):
        reader.render_book(path, sheets=selection)


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
    with pytest.raises(reader.BrewdocError, match="^sheet selection must be a sequence of names$"):
        call(path, selection)


@pytest.mark.parametrize("selection", [5, "Calc", b"Calc"], ids=["int", "str", "bytes"])
def test_run_reports_a_non_iterable_or_bare_string_selection_as_a_selection_error(
        tmp_path, selection):
    # GIVEN a readable workbook and a selection that is not a sequence of names
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN run receives it
    code, receipt, markdown = reader.run(path, sheets=selection)
    # THEN the receipt carries the same selection error as the raising entry points
    assert (code, receipt["file_ok"], receipt["reason"], markdown) == (
        1, False, "sheet selection must be a sequence of names", "",
    ), "run must report the shared selection error, not an unreadable-file error"


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


def test_unknown_selection_is_validated_before_any_sheet_read(monkeypatch):
    # GIVEN a workbook double that records sheet reads
    reads = []

    class Book:
        sheet_names = ["Inputs", "Calc"]

        @classmethod
        def from_path(cls, path):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def get_sheet_by_name(self, name):
            reads.append(name)
            raise AssertionError("selection validation must precede reads")

    monkeypatch.setattr(reader, "CalamineWorkbook", Book)
    # WHEN an unknown name is selected
    # THEN no sheet is opened
    with pytest.raises(reader.BrewdocError, match="unknown sheet selection: Missing"):
        reader.render_book("book.xlsx", sheets=("Missing",))
    assert reads == [], "invalid selection must fail before the first sheet read"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_ooxml_formula_models_preserve_source_text_attributes_and_ordinals(tmp_path, suffix):
    # GIVEN an OOXML workbook with normal, shared, and empty shared formulas
    path = formula_book(tmp_path / ("book" + suffix))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # WHEN its formula artifacts are read in caller sheet order
    artifacts = reader.read_formulas(path, sheets=("Calc", "Inputs"))
    # THEN keys retain full-source ordinals and every formula field is exact
    assert artifacts == (
        reader.FormulaArtifact(
            key="formula/sheet/000002", source_name=path.name, source_sha256=digest,
            source_ordinal=2, sheet="Calc", formulas=(
                reader.CellFormula("Calc", "A1", "SUM(Inputs!A1:A2)", ()),
                reader.CellFormula(
                    "Calc", "B1", "Inputs!A1*2", (("ref", "B1:B2"), ("si", "7"), ("t", "shared"))
                ),
                reader.CellFormula("Calc", "B2", "", (("si", "7"), ("t", "shared"))),
                reader.CellFormula("Calc", "C3", 'IF(A1<4,"café","")', (("aca", "1"), ("bx", "0"))),
            ),
        ),
        reader.FormulaArtifact(
            key="formula/sheet/000001", source_name=path.name, source_sha256=digest,
            source_ordinal=1, sheet="Inputs", formulas=(
                reader.CellFormula("Inputs", "A3", "SUM(A1:A2)", ()),
            ),
        ),
    ), "formula models must preserve XML content and full workbook identity"
    with pytest.raises(FrozenInstanceError):
        artifacts[0].sheet = "Changed"


def test_formula_discovery_returns_one_reference_per_formula_sheet(tmp_path):
    # GIVEN two formula-bearing sheets, one with four formula cells
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN artifacts are discovered and one key is read
    references = reader.list_book_artifacts(path)
    artifact = json.loads(reader.read_book_artifact(path, "formula/sheet/000002"))
    # THEN discovery is per sheet, not per formula, and reading returns that sheet only
    assert [(item.key, item.location, item.count) for item in references] == [
        ("formula/sheet/000001", "#brewdoc-sheet-000001", 1),
        ("formula/sheet/000002", "#brewdoc-sheet-000002", 4),
    ], "each formula-bearing source sheet must own exactly one stable key"
    assert (artifact["sheet"], artifact["formulas"]) == (
        {"key": "sheet/000002", "name": "Calc", "ordinal": 2},
        [item.to_dict() for item in reader.read_formulas(path, sheets=("Calc",))[0].formulas],
    ), "keyed reads and selected reads must return the same formula payload"
    assert reader.list_book_artifacts(path, sheets=("Empty",)) == (), (
        "a supported formula-free sheet has a known zero inventory"
    )


@pytest.mark.parametrize("suffix", [".xls", ".xlsb", ".ods", ".pdf"])
def test_formula_status_is_unavailable_for_unsupported_formats(tmp_path, suffix):
    # GIVEN a format whose released Python reader exposes no OOXML formula source
    path = tmp_path / ("book" + suffix)
    path.write_bytes(b"not inspected")
    # WHEN formula discovery is requested
    # THEN it reports unavailable rather than zero
    with pytest.raises(
        reader.BrewdocError,
        match=r"^formula artifacts unavailable for '%s'; supported suffixes: .xlsm .xlsx$" % suffix,
    ):
        reader.read_formulas(path)


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"missing_relationship": True}, "worksheet relationship rId9 is missing for sheet Calc"),
        ({"malformed_sheet": True}, "cannot parse OOXML part xl/worksheets/sheet2.xml"),
    ],
)
def test_malformed_ooxml_formula_parts_fail_clearly(tmp_path, options, reason):
    # GIVEN an OOXML workbook with a broken relationship or worksheet
    path = formula_book(tmp_path / "broken.xlsx", **options)
    # WHEN formulas are read
    # THEN the failing source part is named
    with pytest.raises(reader.BrewdocError, match=reason):
        reader.read_formulas(path)


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


def test_formula_api_is_public():
    # GIVEN the package root
    expected = (reader.CellFormula, reader.FormulaArtifact, reader.read_formulas)
    # WHEN formula APIs are inspected
    actual = (brewdoc.CellFormula, brewdoc.FormulaArtifact, brewdoc.read_formulas)
    # THEN the immutable layer is exported
    assert actual == expected, "formula models and keyed readers must be publicly available"


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
def test_vba_models_preserve_exact_related_project_bytes(relative, size, project_sha256):
    # GIVEN a licensed workbook fixture with one related VBA project
    path = FIXTURES / relative
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as package:
        expected_data = package.read("xl/vbaProject.bin")
    # WHEN its VBA artifact is discovered and read by key
    references = reader.discover_vba_artifacts(path)
    artifact = reader.read_vba_artifact(path, "vba/project/000001")
    # THEN discovery is count-only and the read preserves the exact opaque bytes
    assert references == (
        reader.OpaqueVbaProjectRef(
            key="vba/project/000001", source_name=path.name,
            source_sha256=source_sha256, relationship_type=VBA_RELATIONSHIP,
            package_part="xl/vbaProject.bin", byte_size=size,
            project_sha256=project_sha256,
        ),
    ), "VBA discovery must expose one stable workbook-level project reference"
    assert artifact == reader.OpaqueVbaProject(
        key="vba/project/000001", source_name=path.name,
        source_sha256=source_sha256, relationship_type=VBA_RELATIONSHIP,
        package_part="xl/vbaProject.bin", byte_size=size,
        project_sha256=project_sha256, data=expected_data,
    ), "the keyed VBA read must preserve every related project byte"
    with pytest.raises(FrozenInstanceError):
        artifact.data = b"changed"


def test_vba_project_is_independent_of_sheet_selection():
    # GIVEN a macro workbook whose second sheet alone is rendered
    path = FIXTURES / "xlsm/calamine-vba.xlsm"
    before = reader.read_vba_artifact(path, "vba/project/000001")
    # WHEN a caller selects one sheet through the separate rendering layer
    _markdown, tally = reader.render_book(path, sheets=("Sheet2",))
    after = reader.read_vba_artifact(path, "vba/project/000001")
    # THEN the workbook-scoped project remains complete and unchanged
    assert tally["sheets"] == 1, "the precondition must render only the requested sheet"
    assert after == before, "sheet selection must not filter or rewrite the VBA project"


def test_vba_discovery_reports_known_zero_for_valid_xlsm_without_project():
    # GIVEN a valid macro-enabled workbook with no VBA relationship
    path = FIXTURES / "xlsm/calamine-issue221.xlsm"
    # WHEN it is discovered
    # THEN zero is known and keyed reads fail clearly
    assert reader.discover_vba_artifacts(path) == (), "a supported no-project XLSM has known zero"
    with pytest.raises(reader.BrewdocError, match="^VBA artifact not found: vba/project/000001$"):
        reader.read_vba_artifact(path, "vba/project/000001")


@pytest.mark.parametrize("suffix", [".xls", ".xlsx", ".ods", ".pdf"])
def test_vba_status_is_unavailable_outside_xlsm_and_xlsb(tmp_path, suffix):
    # GIVEN a format without the accepted related opaque-project capability
    path = tmp_path / ("book" + suffix)
    path.write_bytes(b"not inspected")
    # WHEN discovery is requested
    # THEN capability is unavailable rather than false zero
    with pytest.raises(
        reader.BrewdocError,
        match=r"^VBA artifacts unavailable for '%s'; supported suffixes: .xlsb .xlsm$" % suffix,
    ):
        reader.discover_vba_artifacts(path)


@pytest.mark.parametrize(
    ("relationships", "parts", "reason"),
    [
        (
            '<Relationships><Relationship Id="rId1" Type="%s" Target="vbaProject.bin"/>'
            '<Relationship Id="rId2" Type="%s" Target="other.bin"/></Relationships>'
            % (VBA_RELATIONSHIP, VBA_RELATIONSHIP),
            (("xl/vbaProject.bin", b"one"), ("xl/other.bin", b"two")),
            "multiple VBA project relationships",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" Target="vbaProject.bin" '
            'TargetMode="External"/></Relationships>' % VBA_RELATIONSHIP,
            (("xl/vbaProject.bin", b"project"),),
            "VBA project relationship is external",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s"/></Relationships>'
            % VBA_RELATIONSHIP,
            (),
            "OOXML relationship target is missing",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" Target=""/></Relationships>'
            % VBA_RELATIONSHIP,
            (),
            "OOXML relationship target is missing",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" '
            'Target="../../vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP,
            (("vbaProject.bin", b"project"),),
            r"^OOXML relationship target leaves the package: \.\./\.\./vbaProject\.bin$",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" '
            'Target="/../vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP,
            (("vbaProject.bin", b"project"),),
            r"^OOXML relationship target leaves the package: /\.\./vbaProject\.bin$",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" '
            'Target="vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP,
            (),
            "OOXML part is missing: xl/vbaProject.bin",
        ),
        (
            "<Relationships><Relationship",
            (),
            "cannot parse OOXML part xl/_rels/workbook.xml.rels",
        ),
    ],
)
def test_malformed_vba_relationships_fail_at_the_archive_boundary(
        tmp_path, relationships, parts, reason):
    # GIVEN an XLSM with one malformed relationship boundary
    path = vba_book(tmp_path / "broken.xlsm", relationships, parts=parts)
    # WHEN discovery runs
    # THEN the exact package boundary is refused
    with pytest.raises(reader.BrewdocError, match=reason):
        reader.discover_vba_artifacts(path)


@pytest.mark.parametrize(
    "target", ["vbaProject.bin", "/xl/vbaProject.bin", "../xl/vbaProject.bin"],
    ids=["relative", "absolute", "parent-relative"],
)
def test_vba_relationship_target_resolves_to_its_opc_part_name(tmp_path, target):
    # GIVEN an XLSM whose one VBA relationship names xl/vbaProject.bin in a legal OPC spelling
    data = b"opaque project"
    path = vba_book(
        tmp_path / "book.xlsm",
        '<Relationships><Relationship Id="rId1" Type="%s" Target="%s"/></Relationships>'
        % (VBA_RELATIONSHIP, target),
        parts=(("xl/vbaProject.bin", data),),
    )
    # WHEN the keyed project is read
    project = brewdoc.read_vba_artifact(path, "vba/project/000001")
    # THEN every spelling yields the same normalized part and exact bytes
    assert project == brewdoc.OpaqueVbaProject(
        "vba/project/000001", "book.xlsm", hashlib.sha256(path.read_bytes()).hexdigest(),
        VBA_RELATIONSHIP, "xl/vbaProject.bin", len(data), hashlib.sha256(data).hexdigest(), data,
    ), "absolute and in-package relative OPC targets must resolve like the plain relative one"


def test_encrypted_vba_member_is_refused_before_read(tmp_path):
    # GIVEN an XLSM whose related project member is marked encrypted
    relationships = (
        '<Relationships><Relationship Id="rId1" Type="%s" '
        'Target="vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP
    )
    path = vba_book(
        tmp_path / "encrypted.xlsm", relationships,
        parts=(("xl/vbaProject.bin", b"opaque project"),),
    )
    mark_member_encrypted(path, "xl/vbaProject.bin")
    # WHEN discovery runs
    # THEN no password or decryption attempt is made
    with pytest.raises(reader.BrewdocError, match="^VBA project part is encrypted"):
        reader.discover_vba_artifacts(path)


def test_vba_member_size_limit_is_enforced_while_reading(tmp_path, monkeypatch):
    # GIVEN a related project larger than the configured passive-read limit
    relationships = (
        '<Relationships><Relationship Id="rId1" Type="%s" '
        'Target="vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP
    )
    path = vba_book(
        tmp_path / "large.xlsm", relationships,
        parts=(("xl/vbaProject.bin", b"12345"),),
    )
    monkeypatch.setattr(reader, "MAX_VBA_PROJECT_BYTES", 4)
    # WHEN discovery runs
    # THEN the declared and actual expansion boundary is enforced
    with pytest.raises(reader.BrewdocError, match="^VBA project part exceeds 4 bytes"):
        reader.discover_vba_artifacts(path)


def test_vba_api_is_public():
    # GIVEN the package root
    expected = (
        reader.OpaqueVbaProject, reader.OpaqueVbaProjectRef,
        reader.discover_vba_artifacts, reader.read_vba_artifact,
    )
    # WHEN VBA APIs are inspected
    actual = (
        brewdoc.OpaqueVbaProject, brewdoc.OpaqueVbaProjectRef,
        brewdoc.discover_vba_artifacts, brewdoc.read_vba_artifact,
    )
    # THEN the immutable opaque-project layer is exported
    assert actual == expected, "VBA models and keyed readers must be publicly available"


def test_generic_artifact_api_is_public_and_returns_deterministic_formula_json(tmp_path):
    # GIVEN a workbook with two formula-bearing sheets
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN its generic inventory and one selected artifact are read
    references = brewdoc.list_book_artifacts(path, sheets=("Calc",))
    payload = brewdoc.read_book_artifact(
        path, "formula/sheet/000002", sheets=("Calc",)
    )
    repeated = brewdoc.read_book_artifact(
        path, "formula/sheet/000002", sheets=("Calc",)
    )
    # THEN one sheet-level reference and ASCII schema JSON preserve source formulas
    assert references == (
        brewdoc.ArtifactRef(
            key="formula/sheet/000002", kind="formula", availability="available", count=4,
            location="#brewdoc-sheet-000002", media_type="application/json",
        ),
    ), "generic discovery must retain the selected sheet's full-source ordinal"
    decoded = json.loads(payload)
    assert decoded == {
        "formulas": [
            {"attributes": {}, "cell": "A1", "formula": "SUM(Inputs!A1:A2)",
             "sheet": "Calc"},
            {"attributes": {"ref": "B1:B2", "si": "7", "t": "shared"},
             "cell": "B1", "formula": "Inputs!A1*2", "sheet": "Calc"},
            {"attributes": {"si": "7", "t": "shared"}, "cell": "B2", "formula": "",
             "sheet": "Calc"},
            {"attributes": {"aca": "1", "bx": "0"}, "cell": "C3",
             "formula": 'IF(A1<4,"café","")', "sheet": "Calc"},
        ],
        "schema": "brewdoc.formulas/1",
        "sheet": {"key": "sheet/000002", "name": "Calc", "ordinal": 2},
        "source": {
            "bytes": path.stat().st_size,
            "name": "book.xlsx",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "suffix": ".xlsx",
        },
    }, "formula JSON must preserve every formula and source identity field exactly"
    assert payload == repeated, "two keyed reads must return byte-identical JSON"
    assert payload.endswith(b"\n") and payload.isascii(), (
        "formula JSON must be deterministic ASCII with one final newline"
    )


def test_artifact_request_does_not_change_markdown_and_receipt_names_output(tmp_path):
    # GIVEN one selected formula-bearing sheet and separate Markdown and artifact paths
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    artifact_out = tmp_path / "calc.json"
    # WHEN the same selection is rendered without and with an artifact request
    plain = reader.run(path, sheets=("Calc",))
    requested = reader.run(
        path, markdown_out, sheets=("Calc",),
        artifact_outputs={"formula/sheet/000002": artifact_out},
    )
    # THEN Markdown is invariant and the receipt identifies the exact written artifact
    assert (plain[0], requested[0], plain[2], requested[2]) == (
        0, 0, plain[2], plain[2]
    ), "artifact retrieval must not alter Markdown bytes"
    assert markdown_out.read_bytes() == plain[2].encode("ascii"), (
        "the Markdown output must equal the returned schema 2 bytes"
    )
    assert artifact_out.read_bytes() == reader.read_book_artifact(
        path, "formula/sheet/000002", sheets=("Calc",)
    ), "the keyed output must equal the public artifact API bytes"
    assert requested[1]["markdown_schema"] == "brewdoc.markdown/2", (
        "successful receipts must name the Markdown schema"
    )
    assert requested[1]["unit_keys"] == ["sheet/000002"], (
        "receipt unit keys must preserve the selected full-source ordinal"
    )
    assert requested[1]["artifacts"][0]["out"] == str(artifact_out), (
        "the requested artifact row must name its output path"
    )


FORMULA_KEYS = ("formula/sheet/000001", "formula/sheet/000002")


def tree_state(root: Path) -> dict:
    """Map each path under root to its bytes and permission bits; directories map to None."""
    return {
        item.relative_to(root).as_posix():
            (item.read_bytes(), stat.S_IMODE(item.lstat().st_mode)) if item.is_file() else None
        for item in sorted(root.rglob("*"))
    }


def new_file_mode(directory: Path) -> int:
    """Measure the mode a fresh 0666 file receives under the current umask."""
    control = directory / "mode-control"
    control.touch(mode=0o666)
    mode = stat.S_IMODE(control.stat().st_mode)
    control.unlink()
    return mode


@pytest.mark.parametrize(
    ("artifact_outputs", "reason"),
    [
        (["formula/sheet/000002"], "malformed artifact assignment: formula/sheet/000002"),
        (["missing/key=missing.json"], "unknown artifact key: missing/key"),
        (["formula/sheet/000002=a.json", "formula/sheet/000002=b.json"],
         "duplicate artifact key: formula/sheet/000002"),
    ],
)
def test_invalid_artifact_requests_fail_before_any_write(
        tmp_path, monkeypatch, capsys, artifact_outputs, reason):
    # GIVEN an invalid CLI artifact request resolved inside tmp_path and a valid Markdown destination
    monkeypatch.chdir(tmp_path)
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)
    # WHEN the request is validated
    code = reader.main([str(path), "--out", str(tmp_path / "book.md"), "--sheet", "Calc",
                        *(f"--artifact={item}" for item in artifact_outputs)])
    line, _newline, markdown = capsys.readouterr().out.partition("\n")
    receipt = json.loads(line)
    # THEN it fails with the exact receipt reason and writes nothing
    assert (code, receipt["file_ok"], receipt["reason"], markdown, tree_state(tmp_path)) == (
        1, False, reason, "", before,
    ), "invalid artifact requests must be refused before any write"


@pytest.mark.parametrize(
    ("files", "dirs", "links", "out", "artifacts", "reason"),
    [
        pytest.param((), (), (), "book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-is-source"),
        pytest.param((), ("sub",), (), "sub/../book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-hardlink"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-is-source"),
        pytest.param((), ("sub",), (), "book.md", ((FORMULA_KEYS[0], "sub/../book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-hardlink"),
        pytest.param((), (), (), "same.out", ((FORMULA_KEYS[0], "same.out"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-same-path"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-same-path"),
        pytest.param((), ("sub",), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "sub/../calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-spelling"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "BOOK.md"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="casefold-markdown-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "Calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="casefold-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "café.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "CAFÉ.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-casefold-artifact-artifact"),
        pytest.param((), ("book.md",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-directory"),
        pytest.param((), ("calc.json",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {artifacts[0]}",
                     id="artifact-directory"),
        pytest.param(("real.md",), (), (("symlink_to", "book.md", "real.md"),), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-symlink"),
        pytest.param(("parent",), (), (), "parent/book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output parent is not a directory: {out.parent}",
                     id="markdown-parent-is-file"),
    ],
)
def test_invalid_output_plans_are_refused_before_any_write(
        tmp_path, files, dirs, links, out, artifacts, reason):
    # GIVEN a workbook, prepared filesystem state, and an output plan that aliases,
    # collides after NFC casefolding, or names a non-regular target
    path = formula_book(tmp_path / "book.xlsx")
    for name in files:
        (tmp_path / name).write_bytes(b"old " + name.encode("ascii"))
    for name in dirs:
        (tmp_path / name).mkdir()
    for method, link, target in links:
        getattr(tmp_path / link, method)(tmp_path / target)
    markdown_out = tmp_path / out
    outputs = [(key, tmp_path / name) for key, name in artifacts]
    before = tree_state(tmp_path)
    # WHEN the plan is requested through the public run path
    code, receipt, markdown = reader.run(path, markdown_out, artifact_outputs=dict(outputs))
    # THEN it is refused with the exact reason and no file or stage changes
    assert (code, receipt["file_ok"], receipt["reason"], markdown, tree_state(tmp_path)) == (
        1, False, reason.format(out=markdown_out, artifacts=[item for _key, item in outputs]),
        "", before,
    ), "invalid output plans must be refused before any byte is written"


@pytest.mark.parametrize("failing_call", [1, 2, 3])
def test_failed_replace_restores_earlier_targets_and_removes_new_ones(
        tmp_path, monkeypatch, failing_call):
    # GIVEN existing Markdown and artifact targets with their own modes, one new target,
    # and os.replace failing on the Nth call
    path = formula_book(tmp_path / "book.xlsx")
    targets = [tmp_path / "book.md", tmp_path / "first.json", tmp_path / "later.json"]
    targets[0].write_bytes(b"old markdown")
    targets[0].chmod(0o640)
    targets[1].write_bytes(b"old artifact")
    targets[1].chmod(0o600)
    before = tree_state(tmp_path)

    def fail(_source, _target):
        raise OSError("injected replace failure")

    steps = itertools.chain(itertools.repeat(os.replace, failing_call - 1), [fail],
                            itertools.repeat(os.replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    pattern = r"output write failed: (?=.*%s)(?=.*injected replace failure).*" % re.escape(
        str(targets[failing_call - 1]))
    # WHEN publishing fails part way through
    code, receipt, markdown = reader.run(
        path, targets[0], artifact_outputs=dict(zip(FORMULA_KEYS, targets[1:])))
    # THEN originals keep bytes and modes, the new target is absent, and no stage remains
    named = re.fullmatch(pattern, receipt["reason"]) is not None
    assert (code, receipt["file_ok"], markdown, named, tree_state(tmp_path)) == (
        1, False, "", True, before,
    ), "a failed replace must restore every target and name it: %s" % receipt["reason"]


def test_staging_failure_fails_with_a_receipt_and_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and os.fsync failing while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def fail(_descriptor):
        raise OSError("injected staging failure")

    steps = itertools.chain([os.fsync], [fail], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN staging fails after one payload was prepared
    code, receipt, markdown = reader.run(
        path, tmp_path / "book.md",
        artifact_outputs=dict(zip(FORMULA_KEYS, (tmp_path / "a.json", tmp_path / "b.json"))))
    # THEN the receipt reports the failure and neither outputs nor stages exist
    reason = re.fullmatch(r"output write failed: (?=.*injected staging failure).*",
                          receipt["reason"])
    assert (code, receipt["file_ok"], reason is not None, markdown, tree_state(tmp_path)) == (
        1, False, True, "", before,
    ), "a staging failure must leave the directory exactly as it was: %s" % receipt["reason"]


def test_keyboard_interrupt_during_staging_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and an interrupt while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def interrupt(_descriptor):
        raise KeyboardInterrupt

    steps = itertools.chain([os.fsync], [interrupt], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN the interrupt propagates out of run
    with pytest.raises(KeyboardInterrupt):
        reader.run(path, tmp_path / "book.md",
                   artifact_outputs=dict(zip(FORMULA_KEYS, (tmp_path / "a.json",
                                                            tmp_path / "b.json"))))
    # THEN no output or .brewdoc-* stage file remains
    assert tree_state(tmp_path) == before, "an interrupt must not leak stages or partial outputs"


def test_successful_run_writes_exact_bytes_keeps_modes_and_leaves_no_stage(tmp_path):
    # GIVEN existing Markdown and artifact targets with their own modes and one new target
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, first, later = (tmp_path / name for name in ("book.md", "a.json", "b.json"))
    markdown_out.write_bytes(b"old markdown")
    markdown_out.chmod(0o640)
    first.write_bytes(b"old artifact")
    first.chmod(0o604)
    created_mode = new_file_mode(tmp_path)
    before = tree_state(tmp_path)
    # WHEN all three are published
    code, receipt, markdown = reader.run(
        path, markdown_out, artifact_outputs=dict(zip(FORMULA_KEYS, (first, later))))
    # THEN bytes are exact, existing modes survive, the new file follows 0666 & ~umask
    assert (code, receipt["file_ok"], tree_state(tmp_path)) == (0, True, {
        **before,
        "book.md": (markdown.encode("ascii"), before["book.md"][1]),
        "a.json": (reader.read_book_artifact(path, FORMULA_KEYS[0]), before["a.json"][1]),
        "b.json": (reader.read_book_artifact(path, FORMULA_KEYS[1]), created_mode),
    }), "success must publish exact bytes with preserved or umask modes and no stage files"


def test_parent_symlink_retarget_cannot_redirect_publish_or_leak_stage(tmp_path, monkeypatch):
    # GIVEN an output parent symlink to directory A that is retargeted to B before publish
    path = formula_book(tmp_path / "book.xlsx")
    first_parent, second_parent = tmp_path / "dir-a", tmp_path / "dir-b"
    first_parent.mkdir()
    second_parent.mkdir()
    created_mode = new_file_mode(first_parent)
    link = tmp_path / "out"
    link.symlink_to(first_parent, target_is_directory=True)

    def retarget_then_replace(source, target):
        link.unlink()
        link.symlink_to(second_parent, target_is_directory=True)
        return real_replace(source, target)

    real_replace = os.replace
    steps = itertools.chain([retarget_then_replace], itertools.repeat(real_replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN the Markdown is published through the lexical symlink path
    code, _receipt, markdown = reader.run(path, link / "book.md")
    # THEN the parent captured before staging receives it and neither directory keeps a stage
    assert (code, tree_state(first_parent), tree_state(second_parent)) == (
        0, {"book.md": (markdown.encode("ascii"), created_mode)}, {},
    ), "publish must use the real parent captured before staging"


def test_formula_inventory_has_one_linked_row_per_sheet_not_per_cell(tmp_path):
    # GIVEN one selected sheet carrying four formulas
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN its metadata-first Markdown is rendered
    markdown, _tally = reader.render_book(path, sheets=("Calc",))
    rows = [line for line in markdown.splitlines() if line.startswith("| formula/sheet/")]
    # THEN one artifact row links the sheet and reports the exact cell count
    assert rows == [
        "| formula/sheet/000002 | formula | available | 4 | "
        "[sheet/000002](#brewdoc-sheet-000002) | application/json | not applicable | "
        "not applicable |"
    ], "formula-heavy sheets must not emit one Markdown row per formula cell"
    assert "| Selected formula count | 4 |" in markdown, (
        "metadata must report the exact selected formula count"
    )


def test_generic_vba_artifact_returns_exact_opaque_project_bytes():
    # GIVEN a workbook fixture with one related opaque VBA project
    path = FIXTURES / "xlsm/calamine-vba.xlsm"
    # WHEN generic discovery and keyed reading run
    references = reader.list_book_artifacts(path, sheets=("Sheet2",))
    reference = next(item for item in references if item.kind == "vba-project")
    payload = reader.read_book_artifact(path, reference.key, sheets=("Sheet2",))
    # THEN the generic layer reports one project and returns exact container bytes
    assert (reference.key, reference.count, reference.location, reference.byte_size) == (
        "vba/project/000001", 1, "#brewdoc-metadata", 15360,
    ), "VBA inventory must not invent source modules or script counts"
    assert payload == reader.read_vba_artifact(path, reference.key).data, (
        "generic VBA retrieval must preserve the opaque project byte for byte"
    )


def test_cli_repeats_sheet_and_artifact_options_in_caller_order(tmp_path, capsys):
    # GIVEN a workbook, two sheet options, and one artifact output
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    artifact_out = tmp_path / "calc.json"
    # WHEN repeatable CLI options are supplied
    code = reader.main([
        str(path), "--out", str(markdown_out), "--sheet", "Calc", "--sheet", "Inputs",
        "--artifact", "formula/sheet/000002=%s" % artifact_out,
    ])
    receipt = json.loads(capsys.readouterr().out)
    # THEN selection order, full-source keys, and artifact output are exact
    assert (code, receipt["selected_sheets"], receipt["unit_keys"]) == (
        0, ["Calc", "Inputs"], ["sheet/000002", "sheet/000001"],
    ), "repeatable CLI sheet options must retain caller order"
    assert receipt["artifacts"][0]["out"] == str(artifact_out), (
        "repeatable CLI artifact options must reach the generic writer"
    )
    assert artifact_out.is_file(), "the requested CLI artifact must be written"


def test_special_sheet_labels_and_selected_empty_sheet_remain_navigable(tmp_path):
    # GIVEN a formula sheet with Unicode, Markdown and HTML punctuation plus an empty sheet
    path = formula_book(tmp_path / "book.xlsx")
    label = "R&D <Plan> café #1"
    rewrite_member(path, "xl/workbook.xml", b'name="Calc"',
                   'name="R&amp;D &lt;Plan&gt; café #1"'.encode("utf-8"))
    # WHEN both sheets are selected in caller order
    markdown, tally = reader.render_book(path, sheets=(label, "Empty"))
    # THEN semantic labels are safely ASCII encoded and the empty sheet keeps its own anchor
    assert [line for line in markdown.splitlines() if line.startswith("## Sheet ")] == [
        '## Sheet 2: "R&amp;D &lt;Plan&gt; caf&#233; \\#1"',
        '## Sheet 3: "Empty"',
    ], "untrusted labels must not become Markdown or HTML syntax"
    assert ("- [Sheet 2: \"R&amp;D &lt;Plan&gt; caf&#233; \\#1\"]"
            "(#brewdoc-sheet-000002)") in markdown, (
        "the contents label must link to the full-source sheet ordinal"
    )
    assert '<a id="brewdoc-sheet-000003"></a>\n## Sheet 3: "Empty"\n' in markdown, (
        "a selected empty sheet must remain navigable"
    )
    assert tally["sheets"] == 2, "empty selected sheets still count as rendered units"
