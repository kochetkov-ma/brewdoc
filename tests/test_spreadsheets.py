import hashlib
import json
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


def rename_formula_sheet(path: Path, old: str, new_xml: str) -> None:
    """Replace one workbook sheet label while preserving the synthetic package."""
    with zipfile.ZipFile(path) as source:
        members = [(item.filename, source.read(item.filename)) for item in source.infolist()]
    with zipfile.ZipFile(path, "w") as target:
        for name, data in members:
            if name == "xl/workbook.xml":
                data = data.replace(('name="%s"' % old).encode(),
                                    ('name="%s"' % new_xml).encode("utf-8"))
            target.writestr(name, data)


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
    assert artifacts[0].to_dict() == {
        "formulas": [
            {"attributes": {}, "cell": "A1", "formula": "SUM(Inputs!A1:A2)", "sheet": "Calc"},
            {"attributes": {"ref": "B1:B2", "si": "7", "t": "shared"}, "cell": "B1",
             "formula": "Inputs!A1*2", "sheet": "Calc"},
            {"attributes": {"si": "7", "t": "shared"}, "cell": "B2", "formula": "", "sheet": "Calc"},
            {"attributes": {"aca": "1", "bx": "0"}, "cell": "C3",
             "formula": 'IF(A1<4,"café","")', "sheet": "Calc"},
        ],
        "key": "formula/sheet/000002",
        "kind": "formula",
        "sheet": "Calc",
        "source_name": path.name,
        "source_ordinal": 2,
        "source_sha256": digest,
    }, "artifact payload must be JSON-ready without losing formula attributes"
    with pytest.raises(FrozenInstanceError):
        artifacts[0].sheet = "Changed"


def test_formula_discovery_returns_one_reference_per_formula_sheet(tmp_path):
    # GIVEN two formula-bearing sheets, one with four formula cells
    path = formula_book(tmp_path / "book.xlsx")
    # WHEN artifacts are discovered and one key is read
    references = reader.discover_formula_artifacts(path)
    artifact = reader.read_formula_artifact(path, "formula/sheet/000002")
    # THEN discovery is per sheet, not per formula, and reading returns that sheet only
    assert [(item.key, item.source_ordinal, item.sheet, item.formula_count) for item in references] == [
        ("formula/sheet/000001", 1, "Inputs", 1),
        ("formula/sheet/000002", 2, "Calc", 4),
    ], "each formula-bearing source sheet must own exactly one stable key"
    assert artifact.formulas == reader.read_formulas(path, sheets=("Calc",))[0].formulas, (
        "keyed reads and selected reads must return the same formula payload"
    )
    assert reader.discover_formula_artifacts(path, sheets=("Empty",)) == (), (
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
        reader.discover_formula_artifacts(path)


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


def test_formula_api_is_public():
    # GIVEN the package root
    expected = (
        reader.CellFormula, reader.FormulaArtifact, reader.FormulaArtifactRef,
        reader.discover_formula_artifacts, reader.read_formula_artifact, reader.read_formulas,
    )
    # WHEN formula APIs are inspected
    actual = (
        brewdoc.CellFormula, brewdoc.FormulaArtifact, brewdoc.FormulaArtifactRef,
        brewdoc.discover_formula_artifacts, brewdoc.read_formula_artifact, brewdoc.read_formulas,
    )
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
    assert artifact.reference() == references[0], "full and count-only models must agree exactly"
    assert references[0].to_dict() == {
        "byte_size": size,
        "key": "vba/project/000001",
        "kind": "vba-project",
        "package_part": "xl/vbaProject.bin",
        "project_sha256": project_sha256,
        "relationship_type": VBA_RELATIONSHIP,
        "representation": "opaque",
        "source_name": path.name,
        "source_sha256": source_sha256,
    }, "VBA discovery metadata must be deterministic and explicit about opacity"
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
            'Target="/xl/vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP,
            (("xl/vbaProject.bin", b"project"),),
            "OOXML relationship target is absolute: /xl/vbaProject.bin",
        ),
        (
            '<Relationships><Relationship Id="rId1" Type="%s" '
            'Target="../vbaProject.bin"/></Relationships>' % VBA_RELATIONSHIP,
            (("vbaProject.bin", b"project"),),
            "OOXML relationship target contains traversal: ../vbaProject.bin",
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
        artifact_outputs=[("formula/sheet/000002", artifact_out)],
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


@pytest.mark.parametrize(
    ("artifact_outputs", "reason"),
    [
        (["formula/sheet/000002"], "malformed artifact assignment"),
        (["missing/key=missing.json"], "unknown artifact key: missing/key"),
        (["formula/sheet/000002=a.json", "formula/sheet/000002=b.json"],
         "duplicate artifact key: formula/sheet/000002"),
    ],
)
def test_invalid_artifact_requests_fail_before_any_write(tmp_path, artifact_outputs, reason):
    # GIVEN an invalid artifact request and a valid Markdown destination
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    # WHEN the request is validated
    code, receipt, markdown = reader.run(
        path, markdown_out, sheets=("Calc",), artifact_outputs=artifact_outputs)
    # THEN it fails with a receipt and creates no output
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "invalid artifact requests must use the normal exit 1 receipt"
    )
    assert reason in receipt["reason"], "the receipt must identify the invalid request"
    assert not markdown_out.exists(), "validation must precede the Markdown write"


def test_every_output_collision_fails_without_partial_files(tmp_path):
    # GIVEN two formula artifacts and output paths that alias each other or the source
    path = formula_book(tmp_path / "book.xlsx")
    first = tmp_path / "first.json"
    before = path.read_bytes()
    cases = [
        (path, None),
        (tmp_path / "same", [("formula/sheet/000001", tmp_path / "same")]),
        (tmp_path / "book.md", [
            ("formula/sheet/000001", first), ("formula/sheet/000002", first),
        ]),
        (tmp_path / "book.md", [("formula/sheet/000001", path)]),
    ]
    # WHEN each collision is requested
    for markdown_out, outputs in cases:
        code, receipt, markdown = reader.run(
            path, markdown_out, artifact_outputs=outputs)
        # THEN every request fails before a write
        assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
            "source, Markdown, and artifact aliases must all be refused"
        )
    assert path.read_bytes() == before, "an alias refusal must leave the source unchanged"
    assert not first.exists(), "artifact-to-artifact collision must not create a partial file"


def test_case_variant_targets_follow_the_actual_filesystem_identity(tmp_path):
    # GIVEN two formula outputs whose nonexistent names differ only by case
    path = formula_book(tmp_path / "book.xlsx")
    upper = tmp_path / "A.json"
    lower = tmp_path / "a.json"
    case_sensitive = reader._filesystem_case_sensitive(tmp_path)
    # WHEN both targets are requested in one transaction
    code, receipt, _markdown = reader.run(
        path, artifact_outputs=[
            ("formula/sheet/000001", upper), ("formula/sheet/000002", lower),
        ])
    # THEN insensitive aliases are refused, while a sensitive volume keeps both names distinct
    assert (code == 0) is case_sensitive, (
        "case variants must follow measured filesystem identity rather than OS assumptions"
    )
    assert (upper.exists() and lower.exists()) is case_sensitive, (
        "an alias refusal must happen before either final artifact is written"
    )
    assert ("artifact outputs collide" in receipt["reason"]) is (not case_sensitive), (
        "a case-insensitive collision must be named in the failure receipt"
    )


def test_directory_artifact_target_is_rejected_before_valid_markdown_write(tmp_path):
    # GIVEN valid Markdown output and an artifact target that is an existing directory
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    directory_target = tmp_path / "artifact.json"
    directory_target.mkdir()
    # WHEN output paths are preflighted
    code, receipt, markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[("formula/sheet/000001", directory_target)],
    )
    # THEN no Markdown is published and the directory remains untouched
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "non-regular artifact targets must fail through the receipt contract"
    )
    assert "output target is not a regular file" in receipt["reason"], (
        "preflight must identify the invalid final target"
    )
    assert not markdown_out.exists(), "valid earlier outputs must not publish after preflight failure"
    assert directory_target.is_dir(), "preflight must not replace or remove the invalid target"


def test_later_publish_failure_rolls_back_existing_and_new_outputs(tmp_path, monkeypatch):
    # GIVEN existing Markdown and first artifact bytes plus a new later artifact target
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    first_out = tmp_path / "first.json"
    later_out = tmp_path / "later.json"
    markdown_out.write_bytes(b"old markdown")
    first_out.write_bytes(b"old artifact")
    real_replace = reader.os.replace
    failed = []

    def fail_later_publish(source, target):
        source = Path(source)
        target = Path(target)
        if (not failed and source.name.startswith(".brewdoc-stage-")
                and target == later_out):
            failed.append(target)
            raise OSError("injected later publish failure")
        return real_replace(source, target)

    monkeypatch.setattr(reader.os, "replace", fail_later_publish)
    # WHEN the last atomic publish fails after earlier outputs were replaced
    code, receipt, markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[
            ("formula/sheet/000001", first_out),
            ("formula/sheet/000002", later_out),
        ],
    )
    # THEN prior files are restored, the new file is absent, and staging files are cleaned
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "a publish failure must retain the normal failure receipt"
    )
    assert "injected later publish failure" in receipt["reason"], (
        "the receipt must preserve the concrete publish failure"
    )
    assert markdown_out.read_bytes() == b"old markdown", (
        "rollback must restore the previous Markdown bytes"
    )
    assert first_out.read_bytes() == b"old artifact", (
        "rollback must restore the previous artifact bytes"
    )
    assert not later_out.exists(), "rollback must remove a newly published final target"
    assert list(tmp_path.glob(".brewdoc-*-*")) == [], (
        "rollback must remove every staged or backup file"
    )


def test_double_publish_and_restore_failure_materializes_exact_recovery(tmp_path, monkeypatch):
    # GIVEN two existing outputs, a new later output, and failures in publish then restore
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    first_out = tmp_path / "first.json"
    later_out = tmp_path / "later.json"
    markdown_out.write_bytes(b"old markdown")
    markdown_out.chmod(0o640)
    first_out.write_bytes(b"old artifact")
    new_markdown = reader.render_book(path)[0].encode("ascii")
    real_replace = reader.os.replace
    publish_failed = []
    restore_failed = []

    def fail_publish_then_restore(source, target):
        source = Path(source)
        target = Path(target)
        if not publish_failed and source.name.startswith(".brewdoc-stage-") and target == later_out:
            publish_failed.append(True)
            raise OSError("injected artifact publish failure")
        if (publish_failed and not restore_failed and source.name.startswith(".brewdoc-stage-")
                and target == markdown_out):
            restore_failed.append(True)
            raise OSError("injected Markdown restore failure")
        return real_replace(source, target)

    monkeypatch.setattr(reader.os, "replace", fail_publish_then_restore)
    # WHEN the later artifact fails and rollback cannot replace the prior Markdown
    code, receipt, markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[
            ("formula/sheet/000001", first_out),
            ("formula/sheet/000002", later_out),
        ],
    )
    recovery_match = re.search(r"original bytes saved at ([^;]+)", receipt["reason"])
    # THEN the new target state and exact durable recovery path are disclosed
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "double failure must retain the failure receipt contract"
    )
    assert markdown_out.read_bytes() == new_markdown, (
        "the receipt must describe a target that still contains the newly published bytes"
    )
    assert recovery_match is not None, "restore failure must report an exact recovery path"
    recovery = Path(recovery_match.group(1))
    assert recovery.read_bytes() == b"old markdown", (
        "the recovery file must preserve every original Markdown byte"
    )
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o640, (
        "the recovery file must preserve the original output mode"
    )
    assert first_out.read_bytes() == b"old artifact", (
        "unrelated published outputs must still roll back successfully"
    )
    assert not later_out.exists(), "the failed new output must remain absent"
    assert list(tmp_path.glob(".brewdoc-stage-*")) == [], (
        "successful recovery materialization must leave no stage files"
    )


def test_recovery_materialization_failure_retains_and_reports_complete_stage(
        tmp_path, monkeypatch):
    # GIVEN the same double failure plus an injected recovery-file creation failure
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    first_out = tmp_path / "first.json"
    later_out = tmp_path / "later.json"
    markdown_out.write_bytes(b"old markdown")
    markdown_out.chmod(0o640)
    first_out.write_bytes(b"old artifact")
    real_replace = reader.os.replace
    publish_failed = []
    restore_failed = []

    def fail_publish_then_restore(source, target):
        source = Path(source)
        target = Path(target)
        if not publish_failed and source.name.startswith(".brewdoc-stage-") and target == later_out:
            publish_failed.append(True)
            raise OSError("injected artifact publish failure")
        if (publish_failed and not restore_failed and source.name.startswith(".brewdoc-stage-")
                and target == markdown_out):
            restore_failed.append(True)
            raise OSError("injected Markdown restore failure")
        return real_replace(source, target)

    def fail_recovery_creation(_parent):
        raise OSError("injected recovery materialization failure")

    monkeypatch.setattr(reader.os, "replace", fail_publish_then_restore)
    monkeypatch.setattr(reader, "_open_recovery", fail_recovery_creation)
    # WHEN named recovery materialization also fails
    code, receipt, markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[
            ("formula/sheet/000001", first_out),
            ("formula/sheet/000002", later_out),
        ],
    )
    retained_match = re.search(r"original bytes retained at ([^;]+)", receipt["reason"])
    # THEN the complete mode-preserving restore stage remains and is reported exactly
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "materialization failure must retain the failure receipt contract"
    )
    assert "injected recovery materialization failure" in receipt["reason"], (
        "the receipt must disclose the failed durable-recovery step"
    )
    assert retained_match is not None, "the strongest remaining recovery path must be reported"
    retained = Path(retained_match.group(1))
    assert retained.read_bytes() == b"old markdown", (
        "the retained restore stage must preserve every original byte"
    )
    assert stat.S_IMODE(retained.stat().st_mode) == 0o640, (
        "the retained restore stage must preserve the original mode"
    )
    assert first_out.read_bytes() == b"old artifact", (
        "unrelated outputs must roll back despite recovery materialization failure"
    )
    assert not later_out.exists(), "the failed later output must remain absent"
    retained.unlink()


def test_later_staging_failure_cleans_all_temporary_and_final_outputs(tmp_path, monkeypatch):
    # GIVEN three new outputs and an injected fsync failure during the second stage
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    first_out = tmp_path / "first.json"
    second_out = tmp_path / "second.json"
    real_fsync = reader.os.fsync
    calls = []

    def fail_second_stage(descriptor):
        calls.append(descriptor)
        if len(calls) == 2:
            raise OSError("injected staging failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(reader.os, "fsync", fail_second_stage)
    # WHEN staging fails after one payload was fully prepared
    code, receipt, markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[
            ("formula/sheet/000001", first_out),
            ("formula/sheet/000002", second_out),
        ],
    )
    # THEN no final or temporary output survives the failed staging phase
    assert (code, receipt["file_ok"], markdown) == (1, False, ""), (
        "staging failures must retain the normal failure receipt"
    )
    assert "injected staging failure" in receipt["reason"], (
        "the receipt must preserve the concrete staging failure"
    )
    assert [path.exists() for path in (markdown_out, first_out, second_out)] == [False] * 3, (
        "staging must finish for every payload before any final target is published"
    )
    assert list(tmp_path.glob(".brewdoc-*-*")) == [], (
        "a staging failure must remove every temporary file"
    )


@pytest.mark.parametrize(
    ("first_name", "second_name", "case_difference"),
    [
        ("café.json", "cafe\u0301.json", False),
        ("CAFÉ.json", "cafe\u0301.json", True),
    ],
)
def test_unicode_normalization_aliases_follow_the_actual_filesystem(
        tmp_path, first_name, second_name, case_difference):
    # GIVEN normalization-equivalent formula targets, optionally with a case difference
    path = formula_book(tmp_path / "book.xlsx")
    first = tmp_path / first_name
    second = tmp_path / second_name
    normalization_sensitive = reader._filesystem_normalization_sensitive(tmp_path)
    case_sensitive = reader._filesystem_case_sensitive(tmp_path)
    expected_success = normalization_sensitive or case_difference and case_sensitive
    # WHEN both nonexistent paths are requested in one transaction
    code, receipt, _markdown = reader.run(
        path, artifact_outputs=[
            ("formula/sheet/000001", first), ("formula/sheet/000002", second),
        ])
    # THEN only actual filesystem aliases are refused before either final write
    assert (code == 0) is expected_success, (
        "normalization and case identity must use live filesystem behavior"
    )
    assert (first.exists() and second.exists()) is expected_success, (
        "a Unicode alias refusal must precede every final output write"
    )
    assert ("artifact outputs collide" in receipt["reason"]) is (not expected_success), (
        "normalization-equivalent aliases must be named as an output collision"
    )


def test_successful_publish_uses_no_named_backup_cleanup(tmp_path, monkeypatch):
    # GIVEN an existing Markdown target and a trap for fallible named-backup deletion
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    markdown_out.write_bytes(b"old markdown")
    real_unlink = Path.unlink
    named_backup_calls = []

    def reject_named_backup_cleanup(target, *args, **kwargs):
        if target.name.startswith(".brewdoc-backup-"):
            named_backup_calls.append(target)
            raise OSError("named backup cleanup must not exist")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_named_backup_cleanup)
    # WHEN the existing target is replaced successfully
    code, _receipt, markdown = reader.run(path, markdown_out)
    # THEN commit succeeds without creating or deleting any named backup
    assert code == 0, "successful commit must not depend on fallible named-backup cleanup"
    assert markdown_out.read_bytes() == markdown.encode("ascii"), (
        "the committed Markdown must contain the new bytes"
    )
    assert named_backup_calls == [], "anonymous rollback data must have no cleanup pathname"
    assert list(tmp_path.glob(".brewdoc-backup-*")) == [], (
        "successful publish must leave no named backup artifacts"
    )
    assert list(tmp_path.glob(".brewdoc-recovery-*")) == [], (
        "successful publish must never create emergency recovery files"
    )


def test_backup_close_failure_cannot_overturn_a_committed_success(tmp_path, monkeypatch):
    # GIVEN an existing output and an anonymous backup that closes then raises
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    markdown_out.write_bytes(b"old markdown")
    real_temporary_file = reader.tempfile.TemporaryFile
    wrappers = []

    class CloseRaisesAfterClosing:
        """Proxy an anonymous backup and inject one post-close error."""

        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def close(self):
            self.wrapped.close()
            raise OSError("injected backup close failure")

    def close_failing_temporary_file(*args, **kwargs):
        wrapper = CloseRaisesAfterClosing(real_temporary_file(*args, **kwargs))
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(reader.tempfile, "TemporaryFile", close_failing_temporary_file)
    # WHEN the final target is committed before backup cleanup
    code, receipt, markdown = reader.run(path, markdown_out)
    # THEN cleanup failure cannot change the resolved successful transaction
    assert (code, receipt["file_ok"]) == (0, True), (
        "post-commit anonymous cleanup must not convert success into a refusal"
    )
    assert markdown and markdown_out.read_bytes() == markdown.encode("ascii"), (
        "successful receipt and Markdown bytes must describe the committed output"
    )
    assert len(wrappers) == 1 and wrappers[0].wrapped.closed, (
        "the injected anonymous backup must still be closed exactly once"
    )
    assert list(tmp_path.glob(".brewdoc-stage-*")) == [], (
        "post-commit cleanup failure must not leave stage files"
    )
    assert list(tmp_path.glob(".brewdoc-recovery-*")) == [], (
        "successful commit must not materialize emergency recovery files"
    )


def test_parent_symlink_retarget_cannot_redirect_publish_or_leak_stage(tmp_path, monkeypatch):
    # GIVEN an output parent symlink that initially resolves to directory A
    path = formula_book(tmp_path / "book.xlsx")
    first_parent = tmp_path / "dir-a"
    second_parent = tmp_path / "dir-b"
    first_parent.mkdir()
    second_parent.mkdir()
    link = tmp_path / "out"
    link.symlink_to(first_parent, target_is_directory=True)
    lexical_out = link / "book.md"
    real_replace = reader.os.replace
    retargeted = []

    def retarget_before_publish(source, target):
        source = Path(source)
        if not retargeted and source.name.startswith(".brewdoc-stage-"):
            link.unlink()
            link.symlink_to(second_parent, target_is_directory=True)
            retargeted.append(True)
        return real_replace(source, target)

    monkeypatch.setattr(reader.os, "replace", retarget_before_publish)
    # WHEN the lexical parent is retargeted immediately before atomic publish
    code, _receipt, markdown = reader.run(path, lexical_out)
    # THEN the captured real parent receives the output and neither directory leaks a stage
    assert code == 0, "parent retargeting must not break a captured output transaction"
    assert (first_parent / "book.md").read_bytes() == markdown.encode("ascii"), (
        "publish must use the real parent captured before staging"
    )
    assert not (second_parent / "book.md").exists(), (
        "retargeting the lexical symlink must not redirect the final output"
    )
    assert list(first_parent.glob(".brewdoc-stage-*")) == [], (
        "captured parent must not retain staging files"
    )
    assert list(second_parent.glob(".brewdoc-stage-*")) == [], (
        "retarget destination must not receive staging files"
    )


def test_output_modes_preserve_existing_and_follow_umask_for_new_files(tmp_path):
    # GIVEN one existing target mode and a control file created with 0666 under the current umask
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    artifact_out = tmp_path / "formula.json"
    markdown_out.write_bytes(b"old markdown")
    markdown_out.chmod(0o640)
    control = tmp_path / "control"
    descriptor = reader.os.open(control, reader.os.O_WRONLY | reader.os.O_CREAT | reader.os.O_EXCL,
                                0o666)
    reader.os.close(descriptor)
    expected_new_mode = stat.S_IMODE(control.stat().st_mode)
    control.unlink()
    # WHEN existing Markdown and a new artifact are published together
    code, _receipt, _markdown = reader.run(
        path, markdown_out,
        artifact_outputs=[("formula/sheet/000001", artifact_out)],
    )
    # THEN the existing mode survives and the new file uses normal 0666 and umask semantics
    assert code == 0, "mode-preserving transaction must publish successfully"
    assert stat.S_IMODE(markdown_out.stat().st_mode) == 0o640, (
        "replacement of an existing regular output must preserve its mode"
    )
    assert stat.S_IMODE(artifact_out.stat().st_mode) == expected_new_mode, (
        "new outputs must use normal creation mode rather than mkstemp 0600"
    )


def test_mode_preservation_does_not_require_unix_fchmod(tmp_path, monkeypatch):
    # GIVEN an existing output mode on a runtime without os.fchmod
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out = tmp_path / "book.md"
    markdown_out.write_bytes(b"old markdown")
    markdown_out.chmod(0o640)
    monkeypatch.delattr(reader.os, "fchmod", raising=False)
    # WHEN the existing output is replaced
    code, _receipt, markdown = reader.run(path, markdown_out)
    # THEN the portable path preserves mode and leaves no stage or uncaught error
    assert code == 0, "mode preservation must work without the Unix-only fchmod API"
    assert markdown_out.read_bytes() == markdown.encode("ascii"), (
        "portable mode handling must still publish the requested bytes"
    )
    assert stat.S_IMODE(markdown_out.stat().st_mode) == 0o640, (
        "portable chmod must preserve the existing output mode"
    )
    assert list(tmp_path.glob(".brewdoc-stage-*")) == [], (
        "portable mode handling must not leak a stage file"
    )


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
    rename_formula_sheet(path, "Calc", "R&amp;D &lt;Plan&gt; café #1")
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
