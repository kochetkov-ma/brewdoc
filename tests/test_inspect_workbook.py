"""Exact native workbook metadata absent from calamine's cached-value projection."""

import io
import struct
import zipfile

import pytest
from xml.sax.saxutils import escape

import brewdoc

from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc._inspection_workbook import _shift_formula, inspect_workbook
from test_workbook_fidelity import fidelity_book

XML_SST_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
XML_SST_RELATION = ('<Relationship Id="strings" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
                    'Target="sharedStrings.xml"/>').encode()


def _inspect(data, name="source.xlsx", *, limits=None):
    """Run the workbook overlay on occurrence-preserving admitted member bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = tuple((ordinal, info.filename, archive.read(info))
                        for ordinal, info in enumerate(archive.infolist(), 1))
    context = InspectionContext(limits=limits)
    document = context.document(data, name, parent=None, location={"kind": "source"})
    inspect_workbook(data, name, context, document, members=members)
    return context


def _rewrite(data, replacements):
    """Replace literal synthetic members without changing unrelated members."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, replacements.get(info.filename, source.read(info)))
    return output.getvalue()


def _cells(context):
    return {item["location"]["cell"]: item for item in context.findings if item["kind"] == "cell"}


def _shared_string_book(path, *, orphan_type="application/xml", orphan_root="sst"):
    """Write one related SST after a separately declared orphan XML resource."""
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row>'
    fidelity_book(path, rows=rows, label="Inputs")
    original = path.read_bytes()
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(path, "w") as target:
        target.writestr("xl/orphanStrings.xml", '<' + orphan_root + ' xmlns="' + namespace
                        + '"><si><t>ORPHAN-STRING</t></si></' + orphan_root + '>')
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "xl/_rels/workbook.xml.rels":
                payload = payload.replace(b"</Relationships>", XML_SST_RELATION + b"</Relationships>")
            target.writestr(info, payload)
        target.writestr("xl/sharedStrings.xml", '<sst xmlns="' + namespace
                        + '"><si><t>ACTIVE-STRING</t></si></sst>')
        target.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                        '<Default Extension="xml" ContentType="application/xml"/>'
                        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                        '<Override PartName="/xl/workbook.xml" ContentType="'
                        + {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                           ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml"}[path.suffix] + '"/>'
                        '<Override PartName="/xl/worksheets/sheet1.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                        '<Override PartName="/xl/styles.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                        '<Override PartName="/xl/sharedStrings.xml" ContentType="' + XML_SST_MEDIA + '"/>'
                        '<Override PartName="/xl/orphanStrings.xml" ContentType="' + orphan_type + '"/></Types>')
    return path


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
@pytest.mark.parametrize("orphan_type,orphan_root,coverage", [
    ("application/xml", "sst", "complete"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml", "sst", "partial"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml", "unsupported", "partial"),
], ids=["generic-resource", "typed-extra-sst", "typed-unsupported-extra"])
def test_public_xml_shared_strings_bind_only_to_the_owning_workbook_relationship(
        tmp_path, suffix, orphan_type, orphan_root, coverage):
    # GIVEN an earlier orphan XML resource containing a different string at index zero
    path = _shared_string_book(tmp_path / ("strings" + suffix), orphan_type=orphan_type, orphan_root=orphan_root)
    with zipfile.ZipFile(path) as archive:
        payloads = {part: archive.read(part) for part in ("xl/orphanStrings.xml", "xl/sharedStrings.xml")}
        parts = archive.namelist()
    assert (parts.index("xl/orphanStrings.xml"), parts.index("xl/sharedStrings.xml")) == (0, 6), (
        "the orphan must physically precede the actual related string table")
    # WHEN public inspection binds source cache indices through the workbook's real relationship
    report = brewdoc.inspect_document(path)
    cell = _cells_from_report(report)["A1"]
    # THEN native cache evidence stays correct before and after optional typed-value enrichment
    assert (cell["location"], cell["details"]["value"], cell["details"].get("source_cached_value"),
            cell["details"]["raw_value"], cell["details"]["value_type"]) == (
        {"kind": "workbook-cell", "part": "xl/worksheets/sheet1.xml", "member_ordinal": 6,
         "sheet": "Inputs", "sheet_ordinal": 1, "cell": "A1", "qualified_cell": "'Inputs'!A1"},
        "ACTIVE-STRING", "ACTIVE-STRING", "0", "string",
    ), "an orphan table must never shift a native shared-string cache or its original cell location"
    assert [(item["text"], item["location"]["part"], item["location"]["member_ordinal"])
            for item in report["findings"] if item["text"] == "ORPHAN-STRING"] == [
        ("ORPHAN-STRING", "xl/orphanStrings.xml", 1),
    ], "orphan text must remain independently recoverable at its original package occurrence"
    assert [row["status"] for row in report["coverage"]
            if row["document"] == "document/000001" and row["capability"] == "cells"] == [coverage], (
        "generic XML resources and extra declared shared-string parts have distinct native coverage")
    assert {item["location"]["part"]: brewdoc.read_document_artifact(path, item["artifact"])
            for item in report["components"] if item["byte_size"] is not None
            and item["location"].get("part") in payloads} == payloads, (
        "both active and orphan payloads must round-trip as their exact original bytes")
    assert brewdoc.inspect_document(path) == report, "repeated source findings and artifact references must be deterministic"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
@pytest.mark.parametrize("replacement", [
    b"",
    XML_SST_RELATION.replace(b"sharedStrings.xml", b"absent.xml"),
    XML_SST_RELATION + XML_SST_RELATION.replace(b'Id="strings"', b'Id="strings2"'),
    XML_SST_RELATION.replace(b'/>', b' TargetMode="External"/>'),
], ids=["missing-relationship", "missing-target", "duplicate-relationships", "external-target"])
def test_public_xml_shared_string_missing_or_ambiguous_owner_cannot_invent_native_cache(tmp_path, suffix, replacement):
    # GIVEN an otherwise recoverable source whose active string relationship cannot identify one owner
    path = _shared_string_book(tmp_path / ("unresolved" + suffix))
    with zipfile.ZipFile(path) as archive:
        original = archive.read("xl/_rels/workbook.xml.rels")
    assert original.count(XML_SST_RELATION) == 1, "the source must initially contain one actual string relationship"
    path.write_bytes(_rewrite(path.read_bytes(), {
        "xl/_rels/workbook.xml.rels": original.replace(XML_SST_RELATION, replacement),
    }))
    # WHEN independent inspection reports source evidence without choosing an arbitrary table
    report = brewdoc.inspect_document(path)
    # THEN the unresolved string cell stays unreported while independent numeric cells survive
    assert [(item["location"]["cell"], item["details"]["value"])
            for item in report["findings"] if item["kind"] == "cell"] == [("B1", 42.0)], (
        "optional calamine values cannot manufacture a native cache for an unresolved shared-string owner")
    assert [row["status"] for row in report["coverage"]
            if row["document"] == "document/000001" and row["capability"] == "cells"] == ["partial"], (
        "missing, duplicate and external string owners must remain explicitly incomplete")
    assert brewdoc.inspect_document(path) == report, "unresolved ownership findings and source artifacts must be deterministic"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
@pytest.mark.parametrize("duplicate_part", ["xl/sharedStrings.xml", "xl/_rels/workbook.xml.rels"])
@pytest.mark.parametrize("duplicate_kind", ["identical", "unreadable"])
def test_public_xml_duplicate_shared_string_members_keep_occurrences_without_guessing_owner(
        tmp_path, suffix, duplicate_part, duplicate_kind):
    # GIVEN two physical occurrences of the active table or its relationship member
    path = _shared_string_book(tmp_path / ("duplicate" + suffix))
    with zipfile.ZipFile(path) as archive:
        original = archive.read(duplicate_part)
    duplicate = {"identical": original, "unreadable": b"<unclosed>"}[duplicate_kind]
    with zipfile.ZipFile(path, "a") as archive, pytest.warns(UserWarning, match="Duplicate name:"):
        archive.writestr(duplicate_part, duplicate)
    with zipfile.ZipFile(path) as archive:
        ordinals = [ordinal for ordinal, info in enumerate(archive.infolist(), 1) if info.filename == duplicate_part]
    assert len(ordinals) == 2, "the source must retain exactly two original physical member occurrences"
    # WHEN public inspection preserves occurrence provenance and rejects ambiguous active ownership
    report = brewdoc.inspect_document(path)
    # THEN neither first nor last duplicate can supply a fabricated source-cache cell
    assert [(item["location"]["cell"], item["details"]["value"])
            for item in report["findings"] if item["kind"] == "cell"] == [("B1", 42.0)] * {
                ("xl/sharedStrings.xml", "identical"): 1, ("xl/sharedStrings.xml", "unreadable"): 1,
                ("xl/_rels/workbook.xml.rels", "identical"): 2, ("xl/_rels/workbook.xml.rels", "unreadable"): 1,
            }[duplicate_part, duplicate_kind], (
        "an ambiguous active member cannot resolve index zero even when duplicate payloads are identical")
    assert [(item["location"]["member_ordinal"], brewdoc.read_document_artifact(path, item["artifact"]))
            for item in report["components"] if item["location"].get("part") == duplicate_part
            and item["byte_size"] is not None] == [(ordinals[0], original), (ordinals[1], duplicate)], (
        "each duplicate occurrence must retain its exact source ordinal and retrievable payload")
    assert [row["status"] for row in report["coverage"]
            if row["document"] == "document/000001" and row["capability"] == "cells"] == ["partial"], (
        "duplicate package evidence must remain incomplete even when every duplicate payload is retained")
    assert brewdoc.inspect_document(path) == report, "duplicate occurrence identities and artifact keys must be deterministic"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_public_xml_shared_string_target_with_unsupported_root_keeps_bytes_without_native_guess(tmp_path, suffix):
    # GIVEN a correctly related part whose XML cannot provide the declared shared-string schema
    path = _shared_string_book(tmp_path / ("unsupported" + suffix))
    unsupported = b'<unknown xmlns="urn:example:unsupported"><si><t>UNSUPPORTED-STRING</t></si></unknown>'
    path.write_bytes(_rewrite(path.read_bytes(), {"xl/sharedStrings.xml": unsupported}))
    with zipfile.ZipFile(path) as archive:
        original = archive.read("xl/sharedStrings.xml")
    assert original == unsupported, "the active relationship must actually target the unsupported XML source"
    # WHEN public inspection preserves generic XML evidence without treating it as a decoded SST
    report = brewdoc.inspect_document(path)
    # THEN the native string cell is unavailable while the unsupported source remains retrievable
    assert [(item["location"]["cell"], item["details"]["value"])
            for item in report["findings"] if item["kind"] == "cell"] == [("B1", 42.0)], (
        "matching local child names inside an unsupported XML namespace cannot prove a native cell cache")
    assert [brewdoc.read_document_artifact(path, item["artifact"]) for item in report["components"]
            if item["location"].get("part") == "xl/sharedStrings.xml" and item["byte_size"] is not None] == [unsupported], (
        "unsupported source XML must remain byte-exact evidence after native semantic extraction refuses it")
    assert [(row["status"], row["reason"], row["locations"]) for row in report["coverage"]
            if row["document"] == "document/000001" and row["capability"] == "cells"] == [(
        "partial", "unsupported shared-string target root; list index out of range", [
            {"kind": "package", "part": "xl/sharedStrings.xml", "member_ordinal": 7},
            {"kind": "workbook-cell", "part": "xl/worksheets/sheet1.xml", "member_ordinal": 6,
             "sheet": "Inputs", "sheet_ordinal": 1, "cell": "A1", "qualified_cell": "'Inputs'!A1"},
        ],
    )], (
        "the unsupported active root must produce an explicit failure at its original package occurrence")


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_source_cells_keep_offset_errors_missing_cache_shared_formulas_and_merge(tmp_path, suffix):
    # GIVEN explicit sparse cells, a hidden row/column and a shared formula definition
    rows = ('<row r="5" hidden="1"><c r="C5" t="inlineStr"><is><t>offset</t></is></c>'
            '<c r="D5" t="e"><v>#DIV/0!</v></c><c r="E5" s="1"/>'
            '<c r="F5"><f>1+2</f></c><c r="G5"><f t="shared" si="7" ref="G5:G6">'
            'A5+$B$2+SUM(C$3)+"A1"</f><v>3</v></c></row>'
            '<row r="6"><c r="G6"><f t="shared" si="7"/><v>4</v></c></row>'
            '<row r="100"><c r="AA100"><v>42</v></c></row>')
    path = fidelity_book(tmp_path / ("source" + suffix), rows=rows, label="Bob's Inputs")
    with zipfile.ZipFile(path) as archive:
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="D5" t="e"><v>#DIV/0!</v></c>' in worksheet, "native source must contain an error cache"
    worksheet = worksheet.replace("<sheetData>", '<cols><col min="4" max="4" hidden="1"/></cols><sheetData>')
    worksheet = worksheet.replace("</worksheet>", '<mergeCells><mergeCell ref="C5:C6"/></mergeCells></worksheet>')
    data = _rewrite(path.read_bytes(), {"xl/worksheets/sheet1.xml": worksheet})
    # WHEN independent inspection reads all native metadata before a readable selection
    context = _inspect(data, "source" + suffix)
    cells = _cells(context)
    # THEN errors, blanks, absent caches and shared references remain distinguishable
    actual = {address: (item["location"]["qualified_cell"], item["details"]["value"],
                        item["details"]["value_type"], item["details"]["formula"],
                        item["details"]["cached_value_state"], item["details"]["error"],
                        item["details"]["merge_origin"], item["details"]["merge_range"])
              for address, item in cells.items()}
    assert actual == {
        "C5": ("'Bob''s Inputs'!C5", "offset", "string", None, "not_formula", None, "C5", "C5:C6"),
        "D5": ("'Bob''s Inputs'!D5", "#DIV/0!", "error", None, "not_formula", "#DIV/0!", None, None),
        "E5": ("'Bob''s Inputs'!E5", None, "blank", None, "not_formula", None, None, None),
        "F5": ("'Bob''s Inputs'!F5", None, "blank", "1+2", "missing", None, None, None),
        "G5": ("'Bob''s Inputs'!G5", 3.0, "number", 'A5+$B$2+SUM(C$3)+"A1"', "present", None, None, None),
        "G6": ("'Bob''s Inputs'!G6", 4.0, "number", 'A6+$B$2+SUM(C$3)+"A1"', "present", None, None, None),
        "AA100": ("'Bob''s Inputs'!AA100", 42.0, "number", None, "not_formula", None, None, None),
    }, "source-addressed records must retain every populated or metadata-bearing cell exactly"
    assert [(item["kind"], item["location"]["sheet"], item["details"].get("row"),
             item["details"].get("min")) for item in context.findings
            if item["kind"] in ("hidden-row", "hidden-column")] == [
        ("hidden-row", "Bob's Inputs", None, None), ("hidden-column", "Bob's Inputs", None, "4"),
    ], "row and column hiding must remain independent metadata"


def test_native_workbook_names_hidden_sheet_external_targets_and_determinism(tmp_path):
    # GIVEN a very-hidden source sheet and a hidden external defined expression
    path = fidelity_book(tmp_path / "hidden.xlsx", rows='<row r="5"><c r="C5"><v>5</v></c></row>')
    with zipfile.ZipFile(path) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
    assert 'name="東京"' in workbook, "the original Unicode sheet name must be present"
    workbook = workbook.replace('sheetId="1"', 'state="veryHidden" sheetId="1"')
    workbook = workbook.replace("</workbook>", '<definedNames><definedName name="remote" hidden="1" '
                                'localSheetId="0">\'[remote.xlsx]Inputs\'!$C$5</definedName>'
                                '</definedNames></workbook>')
    data = _rewrite(path.read_bytes(), {"xl/workbook.xml": workbook})
    # WHEN the same original bytes are inspected twice
    first, second = _inspect(data), _inspect(data)
    # THEN named expressions and hidden source state retain exact stable provenance
    assert first.report({}) == second.report({}), "repeated independent workbook reports must be identical"
    assert [(item["kind"], item["text"], item["details"]) for item in first.findings
            if item["kind"] == "defined-name"] == [(
        "defined-name", "'[remote.xlsx]Inputs'!$C$5",
        {"source_attributes": {"name": "remote", "hidden": "1", "localSheetId": "0"}},
    )], "defined names must retain hidden state, scope and original external expression"
    assert [(item["location"]["sheet_ordinal"], item["details"]["state"],
             item["details"]["occupied_range"]) for item in first.findings
            if item["kind"] == "sheet"] == [(1, "veryHidden", "C5:C5")], (
        "hidden source sheet ordinal and occupied range must survive independently of readable selection")


def _ods(rows):
    output = io.BytesIO()
    content = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
               'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0" '
               'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0">'
               '<office:automatic-styles><style:style style:name="hidden" style:family="table">'
               '<style:table-properties table:display="false"/></style:style></office:automatic-styles>'
               '<office:body><office:spreadsheet><table:table table:name="Inputs" table:style-name="hidden">'
               + rows + '</table:table><table:named-expressions><table:named-expression table:name="limit" '
               'table:expression="of:=1+2" table:base-cell-address="$Inputs.$A$1"/>'
               '</table:named-expressions></office:spreadsheet></office:body></office:document-content>')
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)
        archive.writestr("META-INF/manifest.xml",
                         '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                         '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
                         '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                         '</manifest:manifest>')
    return output.getvalue()


def test_ods_source_repeats_formula_error_visibility_and_merge_are_addressed():
    # GIVEN repeated empty rows and columns preceding source cells and a formula error
    data = _ods('<table:table-column table:number-columns-repeated="3" table:visibility="collapse"/>'
                '<table:table-row table:number-rows-repeated="4"><table:table-cell/></table:table-row>'
                '<table:table-row table:visibility="collapse"><table:table-cell table:number-columns-repeated="2"/>'
                '<table:table-cell office:value-type="string" table:number-rows-spanned="2">'
                '<text:p>offset</text:p></table:table-cell><table:table-cell office:value-type="string" '
                'calcext:value-type="error" table:formula="of:=1/0"><text:p>#DIV/0!</text:p></table:table-cell></table:table-row>'
                '<table:table-row><table:table-cell table:number-columns-repeated="2"/>'
                '<table:covered-table-cell/></table:table-row>')
    assert b'table:number-rows-repeated="4"' in data, "source must use native repeat coordinates"
    # WHEN native ODS structure is inspected without rendering a selected sheet
    context = _inspect(data, "source.ods")
    cells = _cells(context)
    # THEN offset values and errors retain exact source addresses and merge provenance
    assert {key: (cells[key]["details"]["value"], cells[key]["details"]["formula"],
                  cells[key]["details"]["error"], cells[key]["details"]["merge_range"])
            for key in ("C5", "D5", "C6")} == {
        "C5": ("offset", None, None, "C5:C6"),
        "D5": ("#DIV/0!", "of:=1/0", "#DIV/0!", None),
        "C6": (None, None, None, "C5:C6"),
    }, "ODS repeated structures must not erase addressed error or merge information"
    assert [(item["kind"], item["text"]) for item in context.findings if item["capability"] == "names"] == [
        ("named-expression", "of:=1+2"),
    ], "ODS named expressions must be preserved as original formula strings"


@pytest.mark.parametrize("source,cell_status,formula_status", [
    ("missing-shared-master", "complete", "partial"),
    ("invalid-error-index", "unavailable", "unavailable"),
    ("malformed-sheet", "unavailable", "unavailable"),
])
def test_unreadable_native_cell_evidence_cannot_claim_complete(tmp_path, source, cell_status, formula_status):
    # GIVEN a specific malformed cell or sheet source that cached values cannot repair
    variants = {
        "missing-shared-master": '<row r="5"><c r="C5"><f t="shared" si="99"/><v>3</v></c></row>',
        "invalid-error-index": '<row r="5"><c r="C5" t="s"><v>99</v></c></row>',
        "malformed-sheet": '<row r="5"><c r="C5"',
    }
    path = fidelity_book(tmp_path / "malformed.xlsx", rows=variants[source])
    assert path.is_file() is True, "the malformed source must exist before inspection"
    # WHEN the workbook scanner encounters incomplete native evidence
    context = _inspect(path.read_bytes())
    # THEN the affected cell/formula capability remains explicitly incomplete
    assert {item["capability"]: item["status"] for item in context.coverage.values()
            if item["capability"] in ("cells", "formulas")} == {
        "cells": cell_status, "formulas": formula_status,
    }, "native parse failures must not be overwritten by a final complete coverage update"


def test_ods_repeated_populated_cells_share_the_operation_record_limit():
    # GIVEN a compact ODS record that expands into more cells than admitted
    data = _ods('<table:table-row table:number-rows-repeated="50"><table:table-cell '
                'table:number-columns-repeated="50" office:value-type="float" office:value="1"/>'
                '</table:table-row>')
    assert b'table:number-columns-repeated="50"' in data, "compact source must encode repeated populated cells"
    # WHEN the shared operation has a small remaining record budget
    with pytest.raises(InspectionLimit, match="records limit exceeded"):
        _inspect(data, "source.ods", limits={"records": 100})
    # THEN expansion is refused before materializing thousands of findings


@pytest.mark.parametrize("formula,origin,destination,expected", [
    ("'Input A1'!A5+$B$2", "G5", "G6", "'Input A1'!A6+$B$2"),
    ("'Bob''s A1'!C$3", "D4", "AA100", "'Bob''s A1'!Z$3"),
])
def test_shared_formula_translation_preserves_quoted_sheet_names(formula, origin, destination, expected):
    # GIVEN a relative cell reference after a quoted sheet name containing cell-like text
    assert formula.count("!") == 1, "the source must contain exactly one qualified sheet reference"
    # WHEN a shared formula is translated to its follower cell
    actual = _shift_formula(formula, origin, destination)
    # THEN only the cell reference changes and the original sheet name remains literal
    assert actual == expected, "quoted sheet names must not be mistaken for shared A1 cell references"


def test_formula_hyperlink_and_empty_string_cache_remain_distinct_from_blanks(tmp_path):
    # GIVEN an inert hyperlink formula and a present empty string cache beside an explicit blank
    rows = ('<row r="5"><c r="C5" t="str"><f>HYPERLINK("javascript:original(1)","label")</f>'
            '<v>label</v></c><c r="D5" t="str"><f>""</f><v/></c><c r="E5"/></row>')
    path = fidelity_book(tmp_path / "links.xlsx", rows=rows)
    assert b'javascript:original(1)' in path.read_bytes(), "the original unsafe target must exist literally in the source"
    # WHEN static native inspection recovers formulas without evaluating them
    context = _inspect(path.read_bytes())
    cells = _cells(context)
    # THEN literal hyperlink targets and present empty strings remain exact
    assert [(item["location"]["cell"], item["target"]) for item in context.findings
            if item["kind"] == "formula-hyperlink"] == [("C5", "javascript:original(1)")], (
        "hyperlink formula source must expose its exact target without executing or filtering it")
    assert {key: (cells[key]["details"]["value"], cells[key]["details"]["value_type"],
                  cells[key]["details"]["cached_value_state"]) for key in ("D5", "E5")} == {
        "D5": ("", "string", "present"), "E5": (None, "blank", "not_formula"),
    }, "a present empty formula string cache must not be conflated with an explicit blank cell"


def test_embedded_odf_directory_uses_native_caches_and_preserves_original_scope(monkeypatch):
    # GIVEN two original member scopes and a spreadsheet without contiguous ZIP source bytes
    source = _ods('<table:table-row table:number-rows-repeated="4"><table:table-cell/></table:table-row>'
                  '<table:table-row table:visibility="collapse"><table:table-cell table:number-columns-repeated="2"/>'
                  '<table:table-cell office:value-type="float" office:value="2" table:number-rows-spanned="2" '
                  'table:formula="of:=1+1"/><table:table-cell office:value-type="string" '
                  'calcext:value-type="error" table:formula="of:=1/0">'
                  '<text:p>#DIV/0!</text:p></table:table-cell><table:table-cell table:formula="of:=2+2"/>'
                  '<table:table-cell office:value-type="date" office:date-value="2024-02-29"/>'
                  '<table:table-cell office:value-type="time" office:time-value="PT1H2M3S"/></table:table-row>'
                  '<table:table-row><table:table-cell table:number-columns-repeated="2"/>'
                  '<table:covered-table-cell/></table:table-row>'
                  '<table:table-row table:number-rows-repeated="93"><table:table-cell/></table:table-row>'
                  '<table:table-row><table:table-cell table:number-columns-repeated="26"/>'
                  '<table:table-cell office:value-type="float" office:value="42"/></table:table-row>')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        content = archive.read("content.xml")
    members = ((7, "Object A/content.xml", content), (8, "Object B/content.xml", content))
    context = InspectionContext()
    parent = context.document(source, "parent.odt", parent=None, location={"kind": "source"})
    child = context.document_parts("Object A/", "application/vnd.oasis.opendocument.spreadsheet",
                                   parent=parent, location={"kind": "manifest", "prefix": "Object A/"},
                                   component=None, source_members=())
    assert context.payloads == {}, "the overlay must start with no newly registered physical payloads"
    monkeypatch.setattr("brewdoc._inspection_workbook._cached_values",
                        lambda *args: pytest.fail("directory overlay must never invoke calamine"))
    monkeypatch.setattr("brewdoc._inspection_workbook.zipfile.ZipFile",
                        lambda *args, **kwargs: pytest.fail("directory overlay must never rebuild a ZIP"))
    # WHEN native inspection receives the original scoped members without fabricated ZIP bytes
    inspect_workbook(None, "Object A/", context, child, members=members, prefix="Object A/",
                     contiguous_source=False, media_type="application/vnd.oasis.opendocument.spreadsheet")
    cells = _cells(context)
    # THEN all native caches, locations and formula states remain within the selected directory
    assert {address: (item["details"]["value"], item["details"]["value_type"],
                     item["details"]["cached_value_state"], item["details"]["merge_range"],
                     item["location"]["part"], item["location"]["member_ordinal"])
            for address, item in cells.items()} == {
        "C5": (2.0, "number", "present", "C5:C6", "Object A/content.xml", 7),
        "D5": ("#DIV/0!", "error", "present", None, "Object A/content.xml", 7),
        "E5": (None, "blank", "missing", None, "Object A/content.xml", 7),
        "F5": ("2024-02-29", "date", "not_formula", None, "Object A/content.xml", 7),
        "G5": ("PT1H2M3S", "time", "not_formula", None, "Object A/content.xml", 7),
        "C6": (None, "blank", "not_formula", "C5:C6", "Object A/content.xml", 7),
        "AA100": (42.0, "number", "not_formula", None, "Object A/content.xml", 7),
    }, "child source caches must be decoded directly without mixing identical sibling sheets"
    assert (context.payloads, context.consumed["retained_bytes"],
            [(item["text"], item["location"]["part"]) for item in context.findings
             if item["kind"] == "named-expression"]) == (
        {}, 0, [("of:=1+2", "Object A/content.xml")],
    ), "semantic overlay must not retain or inventory the already admitted bytes a second time"


def test_xls_vba_project_marker_without_directory_cannot_claim_complete_macros():
    # GIVEN real CFB streams containing a valid BIFF workbook and a PROJECT marker without VBA dir
    from test_biff import stream
    from test_compound import compound_fixture
    from brewdoc._compound import read_compound

    workbook = stream(b"", sheet_name="S")
    data = bytearray(compound_fixture())
    directory = 1024
    encoded = "Workbook\0".encode("utf-16le")
    data[directory + 128:directory + 128 + len(encoded)] = encoded
    struct.pack_into("<H", data, directory + 128 + 64, len(encoded))
    struct.pack_into("<I", data, directory + 128 + 72, 2)
    struct.pack_into("<Q", data, directory + 128 + 120, len(workbook))
    struct.pack_into("<Q", data, directory + 120, 128)
    encoded = "PROJECT\0".encode("utf-16le")
    data[directory + 256:directory + 256 + len(encoded)] = encoded
    struct.pack_into("<HBBIII", data, directory + 256 + 64, len(encoded), 2, 1,
                     0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<IQ", data, directory + 256 + 116, 1, 3)
    struct.pack_into("<I", data, 1536 + 4, 0xFFFFFFFE)
    data[2048:2048 + len(workbook)] = workbook
    data[2112:2115] = b"abc"
    source = bytes(data)
    precondition = read_compound(source, InspectionContext(), "precondition")
    assert [(item["name"], item["data"]) for item in precondition[1:]] == [
        ("Workbook", workbook), ("PROJECT", b"abc"),
    ], "actual CFB inventory must expose both original streams before semantic inspection"
    # WHEN the workbook reader recognizes VBA evidence independently of a dir stream
    context = InspectionContext()
    document = context.document(source, "source.xls", parent=None, location={"kind": "source"})
    inspect_workbook(source, "source.xls", context, document)
    # THEN missing VBA metadata is explicit and its original payload remains retrievable
    assert (context.coverage[(document, "macros")]["status"],
            context.coverage[(document, "macros")]["reason"],
            [item for item in context.payloads.values() if item == b"abc"]) == (
        "unavailable", "VBA metadata dir stream missing", [b"abc"],
    ), "malformed macro-bearing XLS content must not become a complete zero-macro report"


@pytest.mark.parametrize("formula,expected", [
    ("SUM(A:A)+SUM(1:1)", "SUM(B:B)+SUM(2:2)"),
    ("SUM($A:$B)+SUM($1:$3)+SUM(A:$C)+SUM(1:$3)", "SUM($A:$B)+SUM($1:$3)+SUM(B:$C)+SUM(2:$3)"),
    ("LOG10.name+A1", "LOG10.name+B2"),
    ("模型A1+A1模型+LOG10.данные+A1", "模型A1+A1模型+LOG10.данные+B2"),
    ("A1\u0301+A1?name+A1", "A1\u0301+A1?name+B2"),
    ("'Budget A1'!A1+'Bob''s A1'!$B2", "'Budget A1'!B2+'Bob''s A1'!$B3"),
    ("SUM(A1:B2!C5)", "SUM(A1:B2!D6)"),
    ("'[external.xlsx]Budget A1'!A1+[1]A1:B2!C5", "'[external.xlsx]Budget A1'!B2+[1]A1:B2!D6"),
    ('SUM(Table1[[#Headers],[A1]])+A1+LOG10.name+LOG10(2)+"A1"',
     'SUM(Table1[[#Headers],[A1]])+B2+LOG10.name+LOG10(2)+"A1"'),
    ("SUM(Table1[Column']A1])+A1", "SUM(Table1[Column']A1])+B2"),
])
def test_public_shared_formula_translation_preserves_complete_reference_and_name_tokens(tmp_path, formula, expected):
    # GIVEN a shared formula master and follower offset by one original row and column
    rows = ('<row r="5"><c r="C5"><f t="shared" si="1" ref="C5:D6">'
            + escape(formula) + '</f><v>1</v></c></row>'
            '<row r="6"><c r="D6"><f t="shared" si="1"/><v>2</v></c></row>')
    path = fidelity_book(tmp_path / "shared.xlsx", rows=rows, label="Inputs")
    with zipfile.ZipFile(path) as archive:
        source = archive.read("xl/worksheets/sheet1.xml")
    assert escape(formula).encode() in source, "the exact master expression must exist in the original XML"
    # WHEN independent inspection and normal conversion resolve the follower's relative references
    report = brewdoc.inspect_document(path)
    cells = _cells_from_report(report)
    code, receipt, markdown = brewdoc.run(path)
    # THEN row/column axes shift while strings, sheet qualifiers and full defined names stay literal
    assert (cells["C5"]["details"]["formula"], cells["D6"]["details"]["formula"],
            code, receipt["file_ok"], expected in markdown) == (
        formula, expected, 0, True, True,
    ), "shared formula evidence and default Markdown must preserve the exact translated token semantics"


def _cells_from_report(report):
    return {item["location"]["cell"]: item for item in report["findings"] if item["kind"] == "cell"}


@pytest.mark.parametrize("formula", ['of:=("#N/A")', 'of:="#N/A"&""', 'of:=NA()', 'of:=1/0'])
def test_public_ods_string_cache_never_uses_formula_syntax_to_guess_error_type(tmp_path, formula):
    # GIVEN an explicit standard ODF string cache without any separate error discriminator
    attributes = ('office:value-type="string" office:string-value="#N/A" table:formula="'
                  + escape(formula, {'"': '&quot;'}) + '"')
    path = tmp_path / "string-result.ods"
    path.write_bytes(_ods('<table:table-row><table:table-cell ' + attributes
                          + '><text:p>displayed result</text:p></table:table-cell></table:table-row>'))
    with zipfile.ZipFile(path) as archive:
        source = archive.read("content.xml").decode()
    assert attributes in source, "the source must declare the exact string cache and unevaluated formula"
    # WHEN public inspection and normal conversion read the original stored result
    report = brewdoc.inspect_document(path)
    details = _cells_from_report(report)["A1"]["details"]
    code, receipt, markdown = brewdoc.run(path)
    # THEN formula text cannot change the source cache type or make its string token a proven error
    assert (details["value"], details["value_type"], details["formula"], details["error"],
            details["cached_value_state"], code, receipt["file_ok"], "A1: #N/A" in markdown) == (
        "#N/A", "string", formula, None, "present", 0, True, True,
    ), "standard ODF strings and formula source must survive without formula evaluation or error inference"
    assert brewdoc.inspect_document(path) == report, "repeated reports must preserve all string-cache fields exactly"


def test_public_ods_missing_and_empty_string_or_error_caches_stay_distinct(tmp_path):
    # GIVEN typed formula cells with absent caches, explicit empty caches and a declared cached error
    cells = ('<table:table-cell office:value-type="string" table:formula="of:=[.B1]"/>'
             '<table:table-cell office:value-type="string" office:string-value="" table:formula="of:=[.C1]"/>'
             '<table:table-cell office:value-type="string" table:formula="of:=[.D1]"><text:p/></table:table-cell>'
             '<table:table-cell office:value-type="string" calcext:value-type="error" table:formula="of:=NA()"/>'
             '<table:table-cell office:value-type="string" calcext:value-type="error" '
             'office:string-value="#N/A" table:formula="of:=[.A1]"/>')
    path = tmp_path / "cache-states.ods"
    path.write_bytes(_ods('<table:table-row>' + cells + '</table:table-row>'))
    with zipfile.ZipFile(path) as archive:
        source = archive.read("content.xml").decode()
    assert cells in source, "all five cache representations must occur exactly in the admitted source"
    # WHEN public inspection reads cache presence without evaluating any formula
    report = brewdoc.inspect_document(path)
    actual = {address: (item["details"]["value"], item["details"]["value_type"],
                        item["details"]["error"], item["details"]["cached_value_state"])
              for address, item in _cells_from_report(report).items()}
    # THEN an empty string result is present while a missing typed result stays absent
    assert actual == {
        "A1": (None, "string", None, "missing"),
        "B1": ("", "string", None, "present"),
        "C1": ("", "string", None, "present"),
        "D1": (None, "error", None, "missing"),
        "E1": ("#N/A", "error", "#N/A", "present"),
    }, "native string and error types must preserve missing, empty and token-valued cache distinctions"
    assert brewdoc.inspect_document(path) == report, "typed cache states and original source attributes must be deterministic"


@pytest.mark.parametrize("attributes,text,expected_value,expected_type,expected_error,expected_cache", [
    ('office:value-type="string"', "#N/A is literal text", "#N/A is literal text", "string", None, "not_formula"),
    ('office:value-type="string"', "Err: sample text", "Err: sample text", "string", None, "not_formula"),
    ('office:value-type="string"', "#N/A", "#N/A", "string", None, "not_formula"),
    ('office:value-type="string" table:formula="of:=&quot;#N/A&quot;"', "#N/A", "#N/A", "string", None, "present"),
    ('office:value-type="float" office:value="42"', "#N/A is formatted text", 42.0, "number", None, "not_formula"),
    ('office:value-type="string" calcext:value-type="error" table:formula="of:=1/0"',
     "#DIV/0!", "#DIV/0!", "error", "#DIV/0!", "present"),
    ('office:value-type="string" calcext:value-type="error" office:string-value="#N/A" table:formula="of:=NA()"',
     "formatted error", "#N/A", "error", "#N/A", "present"),
    ('office:value-type="string" calcext:value-type="error" office:string-value="" table:formula="of:=NA()"',
     "#N/A", "#N/A", "error", "#N/A", "present"),
    ('office:value-type="string" calcext:value-type="string" office:string-value="#N/A" table:formula="of:=NA()"',
     "formatted text", "#N/A", "string", None, "present"),
])
def test_public_ods_error_tokens_require_explicit_native_error_type(
        tmp_path, attributes, text, expected_value, expected_type, expected_error, expected_cache):
    # GIVEN explicit native ODS types and cached source text that may resemble calculation errors
    data = _ods('<table:table-row><table:table-cell ' + attributes + '><text:p>' + text
                + '</text:p></table:table-cell></table:table-row>')
    path = tmp_path / "error-evidence.ods"
    path.write_bytes(data)
    with zipfile.ZipFile(path) as archive:
        original = archive.read("content.xml").decode()
    assert attributes in original, "the declared native value/cache/formula evidence must be present"
    # WHEN the public report and normal workbook projection read the original native cache
    report = brewdoc.inspect_document(path)
    details = _cells_from_report(report)["A1"]["details"]
    code, receipt, markdown = brewdoc.run(path)
    # THEN ordinary strings remain strings while actual computed error caches preserve their exact tokens
    assert (details["value"], details["value_type"], details["error"], details["cached_value_state"],
            code, receipt["file_ok"], "A1: " + str(expected_value) in markdown) == (
        expected_value, expected_type, expected_error, expected_cache, 0, True, True,
    ), "error-looking source text must not override native string or numeric cache semantics"


def test_public_ods_deep_row_groups_preserve_cells_order_and_inherited_hidden_state(tmp_path):
    # GIVEN valid row-group nesting within the existing admitted XML depth budget
    row = ('<table:table-row><table:table-cell office:value-type="string">'
           '<text:p>deep source</text:p></table:table-cell></table:table-row>')
    tail = ('<table:table-row><table:table-cell office:value-type="string">'
            '<text:p>visible sibling</text:p></table:table-cell></table:table-row>')
    groups = '<table:table-row-group table:display="false">' + '<table:table-row-group>' * 1099
    path = tmp_path / "deep.ods"
    path.write_bytes(_ods(groups + row + '</table:table-row-group>' * 1100 + tail))
    with zipfile.ZipFile(path) as archive:
        original = archive.read("content.xml")
    assert original.count(b"<table:table-row-group") == 1100, "the source must have valid deep group structure"
    # WHEN native inspection and normal conversion traverse the admitted source iteratively
    report = brewdoc.inspect_document(path)
    code, receipt, markdown = brewdoc.run(path)
    # THEN source order and inherited visibility survive without a recursion failure or lowered limit
    assert ([(item["location"]["cell"], item["details"]["value"]) for item in report["findings"]
             if item["kind"] == "cell"],
            [(item["details"]["row"], item["details"]["repeated"]) for item in report["findings"]
             if item["kind"] == "hidden-row"],
            code, receipt["file_ok"], "A1: deep source" in markdown, "A2: visible sibling" in markdown,
            report["limits"]["exhausted"]) == (
        [("A1", "deep source"), ("A2", "visible sibling")], [(1, 1)], 0, True, True, True, [],
    ), "valid admitted ODS row groups must preserve all source cells and exact inherited hidden state"


def test_public_ods_malformed_row_group_does_not_bypass_native_xml_validation(tmp_path):
    # GIVEN mismatched row-group XML and a pre-existing Markdown destination
    path = tmp_path / "malformed.ods"
    path.write_bytes(_ods('<table:table-row-group><table:table-row><table:table-cell '
                          'office:value-type="string"><text:p>unclosed row</text:p>'
                          '</table:table-cell></table:table-row-group>'))
    out = tmp_path / "source.md"
    out.write_bytes(b"original Markdown\n")
    with zipfile.ZipFile(path) as archive:
        source = archive.read("content.xml")
    assert b"</table:table-row-group>" in source, "the source must retain the actual mismatched closing tag"
    # WHEN original native XML validation runs before iterative row traversal
    report = brewdoc.inspect_document(path)
    code, receipt, markdown = brewdoc.run(path, out)
    # THEN malformed evidence remains unavailable and refusal preserves the destination
    assert ([item for item in report["findings"] if item["kind"] == "cell"],
            [item["status"] for item in report["coverage"] if item["capability"] == "cells"],
            code, receipt["file_ok"], markdown, out.read_bytes()) == (
        [], ["unavailable"], 1, False, "", b"original Markdown\n",
    ), "iterative valid-depth support must not accept malformed XML or replace failed Markdown outputs"
