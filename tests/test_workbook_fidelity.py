import copy
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from python_calamine import CalamineWorkbook

import brewdoc
from brewdoc import common
from brewdoc.sheets import _sheet_grid
from test_contract import SCHEMA_TWO
from test_spreadsheets import (DROPPED, SHEET_RECEIPT, VBA_KEY, formula_ref, readable_markdown,
                               rendering_receipt, rendering_result, tally)


FIXTURES = Path(__file__).parent / "fixtures"
OMISSIONS = "\n".join("- " + item for item in SHEET_RECEIPT["not_carried"])
FORMULA_TABLE = (
    "| Key | Kind | Availability | Count | Location | Media type | Bytes | SHA-256 |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| formula/sheet/000001 | formula | available | 1 | [sheet/000001](#brewdoc-sheet-000001) | "
    "application/json | not applicable | not applicable |")
VBA_HASH = "d4d33fa604e2072a9a4828c116e0b4b28984ea7d4ebfa9ead6f85685dc20ac2c"
VBA_REF = brewdoc.ArtifactRef(VBA_KEY, "vba-project", "available", 1, "#brewdoc-metadata",
                            "application/vnd.ms-office.vbaProject", 17920, VBA_HASH)
VBA_TABLE = (
    "| Key | Kind | Availability | Count | Location | Media type | Bytes | SHA-256 |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| vba/project/000001 | vba-project | available | 1 | [metadata](#brewdoc-metadata) | "
    "application/vnd.ms-office.vbaProject | 17920 | " + VBA_HASH + " |")
SYNTHETIC_ROWS = (
    '<row r="2"><c r="A2" t="inlineStr"><is><t>café 東京 Привет</t></is></c>'
    '<c r="B2" t="inlineStr"><is><t>A\u00a0B</t></is></c>'
    '<c r="C2" t="inlineStr"><is><t>left|right</t></is></c>'
    '<c r="D2" t="inlineStr"><is><t>first\nsecond</t></is></c></row>'
    '<row r="3"><c r="B3" s="1"><v>45351</v></c>'
    '<c r="C3" s="2"><v>45351.989583333336</v></c>'
    '<c r="D3" s="3"><v>0.043091707824074</v></c>'
    '<c r="E3" s="4"><v>1.000023148148148</v></c>'
    '<c r="F3"><v>-0.0</v></c><c r="G3"><f>2+3</f><v>5</v></c></row>'
    '<row r="5"><c r="B5"><v>7</v></c></row>')
SYNTHETIC_BODY = (
    '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "東京"\n\n'
    'Source range: A2:G5\n\n| Row | A | B | C | D | E | F | G |\n'
    '| --- | --- | --- | --- | --- | --- | --- | --- |\n'
    '| 2 | A2: café 東京 Привет | B2: A B | C2: left\\|right | D2: first second |  |  |  |\n'
    '| 3 |  | B3: 2024-02-29 | C3: 2024-02-29T23:45:00 | D3: 01:02:03.124000 | '
    'E3: 1 day, 0:00:02 | F3: -0.0 | G3: 5.0 |\n'
    '| 4 |  |  |  |  |  |  |  |\n| 5 |  | B5: 7.0 |  |  |  |  |  |\n')


def workbook_document(path, body, contents, *, source_units=1, ordinal=1,
                      formula="unavailable", formula_count="unknown", vba="unavailable",
                      vba_count="unknown", artifacts="None.", drops=None):
    """Declare complete workbook Markdown with source identity and UTF-8 body identity."""
    source, encoded = path.read_bytes(), body.encode("utf-8")
    markdown = SCHEMA_TWO.format(
        name=path.name, suffix=path.suffix,
        source_bytes=len(source), source_sha256=hashlib.sha256(source).hexdigest(),
        body_bytes=len(encoded), body_sha256=hashlib.sha256(encoded).hexdigest(),
        route="sheet", unit="sheet", count=1, keys="sheet/%06d" % ordinal,
        pages=0, sheets=1, chapters=0, tables=1, text_regions=0,
        formula=formula, formula_count=formula_count, vba=vba, vba_count=vba_count,
        omissions=OMISSIONS, contents=contents, body=body)
    markdown = markdown.replace("| Source unit count | 1 |", "| Source unit count | %d |" % source_units)
    markdown = markdown.replace("## Artifacts\n\nNone.", "## Artifacts\n\n" + artifacts)
    markdown = markdown.replace("| VBA source module capability | available |", "| VBA source module capability | unavailable |")
    markdown = markdown.replace("| VBA source module count | 1 |", "| VBA source module count | unknown |")
    for key, count in (drops or {}).items():
        label = key.replace("_", " ")
        markdown = markdown.replace("| Dropped %s | 0 |" % label, "| Dropped %s | %d |" % (label, count))
    return markdown


def sheet_receipt(path, **fields):
    """Declare every unchanged receipt field, with explicit accepted deltas."""
    return {**SHEET_RECEIPT, "source": path.name, "file_ok": True, "out": None,
            "reason": "sheet rendered: 1 sheets, 1 tables, 0 text regions, 0 column splits",
            "units": 1, "tables": 1, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
            "unit_keys": ["sheet/000001"], **fields}


def fidelity_book(path, rows=SYNTHETIC_ROWS, label="東京"):
    """Write a small styled workbook with addressed values and passive cached formulas."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels", '<Relationships xmlns="%s"><Relationship Id="r1" '
                         'Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % (package_ns, office))
        archive.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets>'
                         '<sheet name="%s" sheetId="1" r:id="r1"/></sheets></workbook>' % (ns, office, escape(label)))
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="%s">'
                         '<Relationship Id="r1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>'
                         '<Relationship Id="r2" Type="%s/styles" Target="styles.xml"/></Relationships>' % (package_ns, office, office))
        archive.writestr("xl/styles.xml", '<styleSheet xmlns="%s"><numFmts count="3">'
                         '<numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss.000000"/>'
                         '<numFmt numFmtId="165" formatCode="hh:mm:ss.000000"/>'
                         '<numFmt numFmtId="166" formatCode="[h]:mm:ss"/></numFmts>'
                         '<cellXfs count="5"><xf numFmtId="0"/><xf numFmtId="14"/>'
                         '<xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="166"/>'
                         '</cellXfs></styleSheet>' % ns)
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (ns, rows))
    return path


def synthetic_expectations(path):
    """Declare complete Unicode output, receipt and formula payload from synthetic cells."""
    markdown = workbook_document(path, SYNTHETIC_BODY, '- [Sheet 1: "東京"](#brewdoc-sheet-000001)',
                                 formula="available", formula_count=1, artifacts=FORMULA_TABLE, drops={"nbsp": 1})
    receipt = sheet_receipt(path, dropped={**DROPPED, "nbsp": 1}, artifacts=[formula_ref(1, 1).to_dict()])
    formula = {"formulas": [{"attributes": {}, "cell": "G3", "formula": "2+3", "sheet": "東京"}],
               "schema": "brewdoc.formulas/1", "sheet": {"key": "sheet/000001", "name": "東京", "ordinal": 1},
               "source": {"bytes": path.stat().st_size, "name": path.name, "suffix": ".xlsx",
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    payload = (json.dumps(formula, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    return markdown, receipt, payload


@pytest.mark.corpus
def test_public_ods_datetime_keeps_the_actual_time_and_microsecond_time_cells():
    # GIVEN licensed ODS exposing a date, an actual datetime and two time values
    path = FIXTURES / "ods/calamine-date.ods"
    with CalamineWorkbook.from_path(str(path)) as book:
        parsed = book.get_sheet_by_name("Sheet1").to_python(skip_empty_area=False)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), parsed) == (
        "d65f3198bc73f2fad356e1b097215fc2f9a833fb802638217e5adc27fbba0466",
        [[date(2021, 1, 1), 15.0], [datetime(2021, 1, 1, 10, 10, 10), 16.0],
         [time(10, 10, 10), 17.0], [time(10, 10, 10, 123456), 18.0]],
    ), "the licensed source must expose a real datetime rather than an inferred display format"
    body = ('<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Sheet1"\n\n'
            'Source range: A1:B4\n\n| Row | A | B |\n| --- | --- | --- |\n'
            '| 1 | A1: 2021-01-01 | B1: 15.0 |\n| 2 | A2: 2021-01-01T10:10:10 | B2: 16.0 |\n'
            '| 3 | A3: 10:10:10 | B3: 17.0 |\n| 4 | A4: 10:10:10.123456 | B4: 18.0 |\n')
    expected = workbook_document(path, body, '- [Sheet 1: "Sheet1"](#brewdoc-sheet-000001)')
    # WHEN public render and service APIs project the source values
    actual = brewdoc.render_book(path), rendering_result(brewdoc.run(path))
    # THEN complete output preserves the date/time distinction with unchanged receipt and tally
    assert actual == ((expected, tally(1, 1)), (0, sheet_receipt(path), expected)), (
        "ODS datetime must retain full ISO time without changing date, time, counts or metadata")


@pytest.mark.corpus
@pytest.mark.parametrize(("relative", "digest", "vba", "vba_count", "artifact_table", "references"), [
    ("ods/calamine-issues.ods", "35b60d34e30e80b9931b5001ae9d8ff6ce0ac719983af8197a00ee59e01f07df",
     "unavailable", "unknown", "None.", ()),
    ("xlsb/calamine-issues.xlsb", "06c648de5529e022ef399a2b6ebc227040e9fdf71a0780e19e614f556bdb53d7",
     "available", 1, VBA_TABLE, (VBA_REF,)),
], ids=["ods", "xlsb"])
def test_public_unicode_cells_keep_all_source_symbols_and_full_source_ordinals(
        relative, digest, vba, vba_count, artifact_table, references):
    # GIVEN licensed sheets exposing distinct ordinary Unicode characters
    path = FIXTURES / relative
    with CalamineWorkbook.from_path(str(path)) as book:
        rows = book.get_sheet_by_name("spc_chrs").to_python(skip_empty_area=False)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), rows) == (
        digest, [["&"], ["<"], [">"], ["aaa ' aaa"], ['"'], ["☺"], ["֍"], ["àâéêèçöïî«»"]],
    ), "the fixture must expose exact Unicode symbols before conversion"
    body = ('<a id="brewdoc-sheet-000006"></a>\n## Sheet 6: "spc_chrs"\n\n'
            'Source range: A1:A8\n\n| Row | A |\n| --- | --- |\n'
            '| 1 | A1: & |\n| 2 | A2: &lt; |\n| 3 | A3: > |\n| 4 | A4: aaa \' aaa |\n'
            '| 5 | A5: " |\n| 6 | A6: ☺ |\n| 7 | A7: ֍ |\n| 8 | A8: àâéêèçöïî«» |\n')
    expected = workbook_document(path, body, '- [Sheet 6: "spc_chrs"](#brewdoc-sheet-000006)',
                                 source_units=6, ordinal=6, vba=vba, vba_count=vba_count, artifacts=artifact_table)
    receipt = sheet_receipt(path, unit_keys=["sheet/000006"], selected_sheets=["spc_chrs"],
                            artifacts=[item.to_dict() for item in references])
    # WHEN only the Unicode sheet is selected using the public APIs
    actual = (brewdoc.list_book_artifacts(path, sheets=("spc_chrs",)),
              brewdoc.render_book(path, sheets=("spc_chrs",)), rendering_result(brewdoc.run(path, sheets=("spc_chrs",))))
    # THEN source ordinal, complete artifacts, Unicode content and all loss counters remain exact
    assert actual == (references, (expected, tally(1, 1)), (0, receipt, expected)), (
        "ordinary Unicode must survive without replacement while source ordinals and VBA inventory remain exact")


@pytest.mark.corpus
def test_public_xlsb_unicode_work_preserves_every_opaque_vba_byte():
    # GIVEN the licensed XLSB project member and its recorded passive payload identity
    path = FIXTURES / "xlsb/calamine-issues.xlsb"
    with zipfile.ZipFile(path) as archive:
        project = archive.read("xl/vbaProject.bin")
    assert (len(project), hashlib.sha256(project).hexdigest()) == (17920, VBA_HASH), (
        "the opaque source project must match recorded fixture identity")
    # WHEN a Unicode sheet selection accompanies public project retrieval
    actual = brewdoc.read_book_artifact(path, VBA_KEY, sheets=("spc_chrs",))
    # THEN every binary byte remains passive and identical
    assert actual == project, "workbook Unicode support must not decode or rewrite opaque VBA bytes"


@pytest.mark.parametrize(("rows", "expected", "losses"), [
    ([[date(2024, 2, 29), datetime(2024, 2, 29), datetime(2024, 2, 29, 23, 45, 6, 123456),
       time(1, 2, 3, 123456), timedelta(days=1, seconds=2), -0.0, True, None]],
     [["2024-02-29", "2024-02-29T00:00:00", "2024-02-29T23:45:06.123456",
       "01:02:03.123456", "1 day, 0:00:02", "-0.0", "true"]], {}),
    ([["café 東京 Привет", "A\u00a0B", "co\u00adoperate", "\x01", "(cid:7)", "ﬃ", "\ue001", "‘a’ – ½"]],
     [["café 東京 Привет", "A B", "cooperate", "", "", "ffi", "-", "‘a’ – ½"]],
     {"nbsp": 1, "soft_hyphens": 1, "control_chars": 1, "cid_survivors": 1, "ligatures": 1, "pua_glyphs": 1}),
    ([[None, "", "left"], [], [7], ["", "right", "\x01"]],
     [["", "", "left"], [], ["7"], ["", "right", ""]], {"control_chars": 1}),
], ids=["temporal-types", "unicode-and-named-cleanup", "ragged-blanks"])
def test_typed_projection_keeps_full_temporal_values_unicode_cleanup_and_caller_rows(rows, expected, losses):
    # GIVEN caller-owned typed values and a fresh tally
    rows = copy.deepcopy(rows)
    original = copy.deepcopy(rows)
    counts = common.new_tally()
    assert counts == tally(0, 0), "projection must start with no inherited losses or unit counts"
    # WHEN workbook cell projection receives the actual typed objects
    grid = _sheet_grid(rows, counts)
    # THEN exact values, full tally, blanks and caller input are preserved
    assert (grid, counts, rows) == (expected, {**tally(0, 0), "dropped": {**DROPPED, **losses}}, original), (
        "workbook projection must preserve exposed precision and ordinary Unicode while retaining named cleanup")


def test_synthetic_public_outputs_keep_full_temporal_grid_unicode_labels_and_cached_formula(tmp_path):
    # GIVEN native parser values from styled addressed synthetic cells and preserved blank rows
    path = fidelity_book(tmp_path / "book.xlsx")
    with CalamineWorkbook.from_path(str(path)) as book:
        rows = book.get_sheet_by_name("東京").to_python(skip_empty_area=False)
    assert rows == [[""] * 7, ["café 東京 Привет", "A\u00a0B", "left|right", "first\nsecond", "", "", ""],
                    ["", date(2024, 2, 29), datetime(2024, 2, 29, 23, 45), time(1, 2, 3, 124000),
                     timedelta(days=1, seconds=2), -0.0, 5.0], [""] * 7, ["", 7.0, "", "", "", "", ""]], (
        "preconditions must preserve actual binding types, exposed .124000 precision and cached value 5.0")
    markdown, receipt, payload = synthetic_expectations(path)
    out, formula_out = tmp_path / "book.md", tmp_path / "formula.json"
    written_receipt = {**receipt, "out": str(out), "selected_sheets": ["東京"],
                       "artifacts": [formula_ref(1, 1).to_dict(str(formula_out))]}
    # WHEN render, service and artifact APIs publish the selected native values
    rendered = brewdoc.render_book(path)
    result = brewdoc.run(path, out, sheets=("東京",), artifact_outputs={"formula/sheet/000001": formula_out})
    artifact = brewdoc.read_book_artifact(path, "formula/sheet/000001")
    # THEN complete UTF-8 Markdown, receipts, cached values and escaped JSON artifacts agree
    assert (rendered, rendering_result(result, out), readable_markdown(out.read_text()).encode("utf-8"), formula_out.read_bytes(), artifact) == (
        (markdown, {**tally(1, 1), "dropped": {**DROPPED, "nbsp": 1}}),
        (0, written_receipt, markdown), markdown.encode("utf-8"), payload, payload,
    ), "Unicode publication must preserve full grid, single pipe escaping, ordinals and ASCII-escaped formula bytes"


def test_ascii_configured_cli_publishes_exact_utf8_workbook_bytes(tmp_path):
    # GIVEN Unicode source content and a process configured with ASCII stdout
    path = fidelity_book(tmp_path / "book.xlsx")
    markdown, receipt, _payload = synthetic_expectations(path)
    environment = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    assert environment["PYTHONIOENCODING"] == "ascii", "the CLI must exercise an ASCII-configured output stream"
    # WHEN the CLI entry module emits receipt and workbook Markdown
    process = subprocess.run([sys.executable, "-m", "brewdoc.cli", str(path)], env=environment,
                             capture_output=True, timeout=10, check=False)
    # THEN receipt JSON stays escaped while Markdown uses exact UTF-8 without BOM
    line, body = process.stdout.split(b"\n", 1)
    actual = (rendering_receipt(json.loads(line)), readable_markdown(body.decode("utf-8")))
    assert (process.returncode, actual, process.stderr) == (0, (receipt, markdown), b""), (
        "ASCII stream configuration must not corrupt or refuse workbook Unicode Markdown")


def test_ascii_date_only_workbook_keeps_complete_prior_output(tmp_path):
    # GIVEN one ASCII-labelled date-only sheet with no formula
    path = fidelity_book(tmp_path / "book.xlsx", '<row r="1"><c r="A1" s="1"><v>45351</v></c></row>', "Data")
    with CalamineWorkbook.from_path(str(path)) as book:
        rows = book.get_sheet_by_name("Data").to_python(skip_empty_area=False)
    assert rows == [[date(2024, 2, 29)]], "the control must expose a date rather than midnight datetime"
    body = ('<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Data"\n\nSource range: A1:A1\n\n'
            '| Row | A |\n| --- | --- |\n| 1 | A1: 2024-02-29 |\n')
    markdown = workbook_document(path, body, '- [Sheet 1: "Data"](#brewdoc-sheet-000001)', formula="available", formula_count=0)
    out = tmp_path / "book.md"
    # WHEN a date-only ASCII workbook is rendered and written by public APIs
    actual = brewdoc.render_book(path), rendering_result(brewdoc.run(path, out), out)
    # THEN every prior ASCII output byte, receipt field and tally remains unchanged
    assert (actual, readable_markdown(out.read_text()).encode("ascii")) == (((markdown, tally(1, 1)),
                                         (0, sheet_receipt(path, out=str(out)), markdown)), markdown.encode("ascii")), (
        "the workbook fidelity change must preserve complete ASCII/date-only behavior")


def test_shared_default_cell_and_sanitizer_policies_remain_ascii_date_only():
    # GIVEN shared formatters called without workbook opt-in
    counts = common.new_tally()
    assert counts == tally(0, 0), "the shared control must begin without prior losses"
    # WHEN the legacy default formats a datetime and ordinary Unicode cell text
    actual = common.cell_text(datetime(2024, 2, 29, 23, 45)), common.sanitise("café 東京", counts)
    # THEN non-workbook defaults retain exact date-only and ASCII behavior
    assert (actual, counts) == (("2024-02-29", "cafe ??"),
                               {**tally(0, 0), "dropped": {**DROPPED, "non_ascii_replaced": 2}}), (
        "workbook fidelity must use explicit opt-in and preserve shared default callers")
