"""Addressed readable workbook output and its additive inspection evidence."""

import hashlib
import json
import re
import struct
import zipfile

import pytest

import brewdoc
from python_calamine import CalamineWorkbook
from brewdoc.sheets import _render_book
from test_workbook_fidelity import fidelity_book


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_offset_cells_keep_source_rows_generated_columns_errors_and_missing_caches(tmp_path, suffix):
    # GIVEN an offset value, an error, an explicit blank and a formula without cached result
    rows = ('<row r="5"><c r="C5" t="inlineStr"><is><t>offset</t></is></c>'
            '<c r="D5" t="e"><v>#DIV/0!</v></c><c r="E5" s="1"/><c r="F5"><f>1+2</f></c></row>'
            '<row r="7"><c r="D7"><v>42</v></c></row>')
    path = fidelity_book(tmp_path / ("offset" + suffix), rows=rows, label="Bob's Inputs")
    assert path.suffix == suffix, "the source must exercise the selected supported workbook extension"
    # WHEN the public renderer generates readable source-addressed cells
    markdown, tally = brewdoc.render_book(path)
    body = markdown[markdown.index('<a id="brewdoc-sheet-'):]
    # THEN generated headers do not consume the first source row as a table header
    assert (body, tally["sheets"], tally["tables"]) == (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Bob\'s Inputs"\n\n'
        'Source range: C5:F7\n\n| Row | C | D | E | F |\n| --- | --- | --- | --- | --- |\n'
        '| 5 | C5: offset | D5: #DIV/0! | E5: [blank] | F5: [missing cached result] |\n'
        '| 6 |  |  |  |  |\n| 7 |  | D7: 42.0 |  |  |\n', 1, 1,
    ), "every populated or metadata cell must retain its literal address beside the value"


def test_column_aa_far_row_and_large_sparse_ranges_do_not_allocate_blank_rectangle(tmp_path):
    # GIVEN widely separated values that would require billions of empty table cells
    rows = ('<row r="5"><c r="C5" t="inlineStr"><is><t>offset</t></is></c></row>'
            '<row r="100"><c r="AA100"><v>42</v></c></row>'
            '<row r="1048576"><c r="XFD1048576"><v>7</v></c></row>')
    path = fidelity_book(tmp_path / "sparse.xlsx", rows=rows, label="Inputs")
    with zipfile.ZipFile(path) as archive:
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="XFD1048576">' in worksheet, (
        "the source must contain a real far corner rather than only a declared large dimension")
    # WHEN source records are projected without allocating the dense empty rectangle
    markdown, _tally = brewdoc.render_book(path)
    # THEN original coordinates survive with explicit sparse gap disclosure
    assert markdown[markdown.index('<a id="brewdoc-sheet-'):] == (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Inputs"\n\n'
        'Source range: C5:XFD1048576\n\n'
        'Empty row and column gaps are omitted; source addresses are preserved.\n\n'
        '| Row | C | AA | XFD |\n| --- | --- | --- | --- |\n'
        '| 5 | C5: offset |  |  |\n| 100 |  | AA100: 42.0 |  |\n'
        '| 1048576 |  |  | XFD1048576: 7.0 |\n'
    ), "large empty spans must remain bounded without renumbering populated source cells"


def test_service_reuses_independent_inspection_and_saves_exact_report_with_readable_output(tmp_path, monkeypatch):
    # GIVEN one exact addressed value and an independently constructed inspection report
    path = fidelity_book(tmp_path / "source.xlsx", rows='<row r="100"><c r="AA100"><v>42</v></c></row>', label="Inputs")
    report = brewdoc.inspect_document(path)
    assert report["schema"] == "brewdoc.inspection/1", "inspection must exist independently of rendering"
    monkeypatch.setattr("brewdoc.sheets._workbook_cell_findings",
                        lambda source, existing: tuple(item for item in existing["findings"]
                                                      if item["document"] == existing["documents"][0]["id"]))
    # WHEN an existing report enters the private renderer and normal service output transaction
    private = _render_book(path, None, _inspection_report=report)
    markdown_out, report_out = tmp_path / "source.md", tmp_path / "inspection.json"
    code, receipt, markdown = brewdoc.run(path, markdown_out,
                                        artifact_outputs={"inspection/report": report_out})
    artifact = next(item for item in receipt["artifacts"] if item["key"] == "inspection/report")
    # THEN renderer contracts, default evidence, output bytes and report references remain exact
    assert (len(private), code, markdown_out.read_bytes(), json.loads(report_out.read_bytes())) == (
        4, 0, markdown.encode("utf-8"), report,
    ), "normal conversion must reuse the report and atomically save its exact deterministic bytes"
    assert artifact == {
        "key": "inspection/report", "kind": "inspection-report", "availability": "available", "count": 1,
        "location": "source", "media_type": "application/json", "byte_size": len(report_out.read_bytes()),
        "sha256": hashlib.sha256(report_out.read_bytes()).hexdigest(), "out": str(report_out),
    }, "the default report reference must identify the actual requested output bytes"
    assert (receipt["inspection"]["schema"], receipt["inspection"]["artifact"],
            receipt["inspection"]["source_sha256"], sum(receipt["inspection"]["coverage"].values())) == (
        "brewdoc.inspection/1", "inspection/report", hashlib.sha256(path.read_bytes()).hexdigest(), 14,
    ), "additive receipt inspection must identify source bytes and every capability status"
    assert re.findall(r"AA100: 42\.0", markdown) == ["AA100: 42.0"], (
        "default inspected conversion must include the addressed cached value in the readable sheet")


def test_merge_metadata_preserves_source_range_without_inventing_covered_values(tmp_path):
    # GIVEN a source merge with a single explicit origin and no covered cell values
    path = fidelity_book(tmp_path / "merged.xlsx", rows='<row r="5"><c r="C5" t="inlineStr"><is><t>joined</t></is></c></row>', label="Inputs")
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    worksheet = next(data for name, data in members if name == "xl/worksheets/sheet1.xml")
    assert b'<c r="C5"' in worksheet, "source origin must exist before merge metadata is added"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            replacement = data.replace(b"</worksheet>", b'<mergeCells><mergeCell ref="C5:D6"/></mergeCells></worksheet>')
            archive.writestr(name, replacement)
    # WHEN normal rendering accounts for the original merge extent
    markdown, _tally = brewdoc.render_book(path)
    # THEN covered coordinates stay empty while the full source range remains explicit
    assert markdown[markdown.index('<a id="brewdoc-sheet-'):] == (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Inputs"\n\nSource range: C5:D6\n\n'
        '| Row | C | D |\n| --- | --- | --- |\n| 5 | C5: joined |  |\n| 6 |  |  |\n'
    ), "merge metadata must retain its source extent without manufacturing populated covered cells"


@pytest.mark.parametrize("version,bof_id,formula_id", [(2, 0x0009, 6), (3, 0x0209, 0x0206),
                                                      (4, 0x0409, 0x0406)])
def test_unnamed_legacy_biff_uses_generated_display_without_inventing_source_identity(
        tmp_path, version, bof_id, formula_id):
    # GIVEN the valid standalone BIFF2-4 CFB source used by the native reader regressions
    from test_biff import compound_book, record

    header = struct.pack("<HH", 0, 0x10)
    cell = struct.pack("<HH", 4, 2) + bytes(3 if version == 2 else 2)
    tokens = bytes.fromhex("4404c002")
    formula = cell + struct.pack("<d", 42) + bytes(1 if version == 2 else 2)
    formula += struct.pack("<B" if version == 2 else "<H", len(tokens)) + tokens
    source = (record(bof_id, header) + record(formula_id, formula)
              + record(0x0005 if version == 2 else 0x0205, cell + b"\7\1") + record(0x000A))
    path = tmp_path / ("unnamed-biff%d.xls" % version)
    path.write_bytes(compound_book(source))
    with CalamineWorkbook.from_path(str(path)) as book:
        names = book.sheet_names
    report = brewdoc.inspect_document(path)
    assert (names, [(item["location"]["sheet"], item["location"]["qualified_cell"],
                     item["location"]["sheet_name_state"]) for item in report["findings"]
                    if item["kind"] == "cell"]) == (
        [], [(None, None, "not_stored"), (None, None, "not_stored")],
    ), "the actual source and installed reader must expose no stored source name or Calamine alias"
    # WHEN public rendering and normal conversion use the source ordinal for the unnamed sheet
    markdown, tally = brewdoc.render_book(path)
    code, receipt, normal = brewdoc.run(path)
    label = "[unnamed BIFF sheet at byte 0]"
    # THEN the display is explicitly generated while original inspection identities remain absent
    assert markdown[markdown.index('<a id="brewdoc-sheet-'):] == (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "\\[unnamed BIFF sheet at byte 0\\]"\n\n'
        'Source sheet name: not stored. The heading is a generated display label.\n\n'
        'Source range: C5:C5\n\n| Row | C |\n| --- | --- |\n| 5 | C5: #DIV/0! |\n'
    ), "a missing source sheet name requires an explicitly generated heading instead of the literal name None"
    selected = brewdoc.render_book(path, sheets=(label,))
    assert (selected, tally["sheets"], code, receipt["file_ok"], receipt["unit_keys"],
            "C5: #DIV/0!" in normal, brewdoc.inspect_document(path)) == (
        (markdown, tally), 1, 0, True, ["sheet/000001"], True, report,
    ), "readable aliases must not be promoted into original source names or erase recoverable addressed cells"
