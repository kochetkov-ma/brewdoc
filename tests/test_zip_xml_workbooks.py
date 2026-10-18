import hashlib
import io
import json
import zipfile

import pytest

import brewdoc
from brewdoc import common, sheets
from test_contract import schema_two
from test_output import tree_state
from test_spreadsheets import (CALC, FORMULA_KEYS, SHEET_RECEIPT, VBA_KEY, exact,
                               formula_book, formula_ref, refused, tally, vba_book,
                               vba_ref, vba_relationship)


OPERATION_LIMITS = (
    ("consumed_bytes", 3104),
    ("total_elements", 56),
    ("total_characters", 1038),
)


def set_limits(monkeypatch, **overrides):
    """Apply small operation budgets without requiring new baseline symbols."""
    monkeypatch.setattr(common, "ZIP_XML_LIMITS", {
        "members": 4096, "member_bytes": 33554432, "consumed_bytes": 268435456,
        "document_elements": 2097152, "total_elements": 4194304, "depth": 16384,
        "document_characters": 33554432, "total_characters": 67108864, **overrides,
    }, raising=False)


def calc_formulas():
    """Declare every formula, including the empty shared follower."""
    return (
        brewdoc.CellFormula("Calc", "A1", "SUM(Inputs!A1:A2)", ()),
        brewdoc.CellFormula("Calc", "B1", "Inputs!A1*2",
                            (("ref", "B1:B2"), ("si", "7"), ("t", "shared"))),
        brewdoc.CellFormula("Calc", "B2", "", (("si", "7"), ("t", "shared"))),
        brewdoc.CellFormula("Calc", "C3", 'IF(A1<4,"café","")', (("aca", "1"), ("bx", "0"))),
    )


def expected_formulas(path):
    """Build immutable expected formulas from the synthetic source identity."""
    return (brewdoc.FormulaArtifact(FORMULA_KEYS[1], path.name,
                                   hashlib.sha256(path.read_bytes()).hexdigest(),
                                   2, "Calc", calc_formulas()),)


def expected_payload(path):
    """Declare the complete serialized Calc artifact independently of its reader."""
    formulas = [
        {"attributes": {}, "cell": "A1", "formula": "SUM(Inputs!A1:A2)", "sheet": "Calc"},
        {"attributes": {"ref": "B1:B2", "si": "7", "t": "shared"}, "cell": "B1",
         "formula": "Inputs!A1*2", "sheet": "Calc"},
        {"attributes": {"si": "7", "t": "shared"}, "cell": "B2", "formula": "", "sheet": "Calc"},
        {"attributes": {"aca": "1", "bx": "0"}, "cell": "C3",
         "formula": 'IF(A1<4,"café","")', "sheet": "Calc"},
    ]
    payload = {
        "formulas": formulas, "schema": "brewdoc.formulas/1",
        "sheet": {"key": "sheet/000002", "name": "Calc", "ordinal": 2},
        "source": {"bytes": path.stat().st_size, "name": path.name, "suffix": ".xlsx",
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def expected_render(path):
    """Declare the full selected-sheet Markdown, including artifact inventory."""
    markdown = schema_two(
        path, CALC, formula="available", formula_count=4, vba="unavailable", vba_count="unknown",
        name="book.xlsx", suffix=".xlsx", route="sheet", unit="sheet", count=1,
        keys="sheet/000002", pages=0, sheets=1, chapters=0, tables=1, text_regions=0,
        omissions="\n".join("- " + item for item in SHEET_RECEIPT["not_carried"]),
        contents='- [Sheet 2: "Calc"](#brewdoc-sheet-000002)')
    markdown = markdown.replace("| Source unit count | 1 |", "| Source unit count | 3 |")
    artifact_table = (
        "| Key | Kind | Availability | Count | Location | Media type | Bytes | SHA-256 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| formula/sheet/000002 | formula | available | 4 | [sheet/000002](#brewdoc-sheet-000002) | "
        "application/json | not applicable | not applicable |")
    return markdown.replace("## Artifacts\n\nNone.", "## Artifacts\n\n" + artifact_table), tally(1, 1)


def track_zip_resources(monkeypatch):
    """Observe actual archive and member lifetimes at the ZIP I/O boundary."""
    archives, streams = [], []
    original = zipfile.ZipFile

    class TrackedZipFile(original):
        """Record opened handles without altering ZIP reads."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            archives.append(self)

        def open(self, *args, **kwargs):
            """Record a member stream owned by the calling reader."""
            stream = super().open(*args, **kwargs)
            streams.append(stream)
            return stream

    monkeypatch.setattr(zipfile, "ZipFile", TrackedZipFile)
    return archives, streams


@pytest.mark.parametrize(("key", "limit"), OPERATION_LIMITS)
def test_formula_artifact_and_run_accept_the_complete_operation_limit(tmp_path, monkeypatch, key, limit):
    # GIVEN a valid Calc artifact whose inventory and retrieval together meet the budget
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, artifact_out = tmp_path / "book.md", tmp_path / "calc.json"
    markdown, _counts = expected_render(path)
    payload = expected_payload(path)
    assert brewdoc.read_book_artifact(path, FORMULA_KEYS[1], sheets=("Calc",)) == payload, (
        "the synthetic source must yield the independently declared complete artifact")
    set_limits(monkeypatch, **{key: limit})
    receipt = {
        **SHEET_RECEIPT, "file_ok": True, "out": str(markdown_out),
        "reason": "sheet rendered: 1 sheets, 1 tables, 0 text regions, 0 column splits",
        "units": 1, "tables": 1, "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": ["sheet/000002"], "selected_sheets": ["Calc"],
        "artifacts": [formula_ref(2, 4).to_dict(str(artifact_out))],
    }
    # WHEN two public operations each consume their inclusive complete-operation budget
    artifact = brewdoc.read_book_artifact(path, FORMULA_KEYS[1], sheets=("Calc",))
    result = brewdoc.run(path, markdown_out, sheets=("Calc",),
                         artifact_outputs={FORMULA_KEYS[1]: artifact_out})
    # THEN every payload byte, Markdown byte and receipt field remains exact
    assert (artifact, result, markdown_out.read_bytes(), artifact_out.read_bytes()) == (
        payload, (0, receipt, markdown), markdown.encode("ascii"), payload,
    ), "the inclusive budget must preserve complete output and reset between public operations"


@pytest.mark.parametrize(("key", "limit"), OPERATION_LIMITS)
def test_formula_artifact_budget_survives_inventory_and_archive_reopening(tmp_path, monkeypatch, key, limit):
    # GIVEN individually legal XML members and a budget one below the two-pass total
    path = formula_book(tmp_path / "book.xlsx")
    assert brewdoc.list_book_artifacts(path, sheets=("Calc",)) == (formula_ref(2, 4),), (
        "inventory must expose the real selected formula artifact before limiting it")
    set_limits(monkeypatch, **{key: limit - 1})
    archives, streams = track_zip_resources(monkeypatch)
    reason = "OOXML %s limit exceeded: xl/worksheets/sheet2.xml: %d" % (key, limit - 1)
    # WHEN artifact retrieval rereads XML in a second archive
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)):
        brewdoc.read_book_artifact(path, FORMULA_KEYS[1], sheets=("Calc",))
    # THEN the aggregate refusal closes both archives and every opened member
    assert ([archive.fp is None for archive in archives], [stream.closed for stream in streams]) == (
        [True, True], [True] * 8,
    ), "retrieval refusal must close every real archive and member across both read phases"


@pytest.mark.parametrize(("key", "limit"), OPERATION_LIMITS)
@pytest.mark.parametrize("destination", ["calc.json", "new-parent/calc.json"], ids=["existing", "new-parent"])
def test_run_aggregate_refusal_preserves_outputs_and_creates_no_stages(
        tmp_path, monkeypatch, key, limit, destination):
    # GIVEN prior output bytes and a readable artifact requiring a second ZIP read phase
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, prior_artifact = tmp_path / "book.md", tmp_path / "calc.json"
    markdown_out.write_bytes(b"prior Markdown\n")
    prior_artifact.write_bytes(b"prior artifact\x00\xff")
    before = tree_state(tmp_path)
    assert brewdoc.list_book_artifacts(path, sheets=("Calc",)) == (formula_ref(2, 4),), (
        "the refusal case must start with a valid available formula artifact")
    set_limits(monkeypatch, **{key: limit - 1})
    archives, streams = track_zip_resources(monkeypatch)
    reason = "OOXML %s limit exceeded: xl/worksheets/sheet2.xml: %d" % (key, limit - 1)
    # WHEN a shared operation budget expires during artifact retrieval before output staging
    result = brewdoc.run(path, markdown_out, sheets=("Calc",),
                         artifact_outputs={FORMULA_KEYS[1]: tmp_path / destination})
    # THEN the exact refusal leaves prior bytes, directories, modes and resources intact
    assert (result, tree_state(tmp_path), [archive.fp is None for archive in archives],
            [stream.closed for stream in streams]) == (
        (1, refused(reason), ""), before, [True, True], [True] * 8,
    ), "aggregate refusal must preserve destinations and leave no new parent, temporary file or open handle"


@pytest.mark.parametrize(("key", "limit"), [("consumed_bytes", 1552), ("total_elements", 28),
                                           ("total_characters", 519)])
def test_many_legal_workbook_xml_members_share_one_formula_read_budget(tmp_path, monkeypatch, key, limit):
    # GIVEN one selected formula pass whose four individually legal members exceed its total by one
    path = formula_book(tmp_path / "book.xlsx")
    assert brewdoc.read_formulas(path, sheets=("Calc",)) == expected_formulas(path), (
        "the aggregate case must start with exact complete formulas from valid XML")
    set_limits(monkeypatch, **{key: limit - 1})
    reason = "OOXML %s limit exceeded: xl/worksheets/sheet2.xml: %d" % (key, limit - 1)
    # WHEN all selected XML parts are read during one public formula operation
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)) as refusal:
        brewdoc.read_formulas(path, sheets=("Calc",))
    # THEN legal individual parts cannot bypass the cumulative operation limit
    assert str(refusal.value) == reason, "aggregate refusal must name the exact exhausted budget and part"


@pytest.mark.parametrize(("call", "expected"), [
    (lambda path: brewdoc.read_formulas(path, sheets=("Calc",)), expected_formulas),
    (lambda path: brewdoc.list_book_artifacts(path, sheets=("Calc",)), lambda path: (formula_ref(2, 4),)),
    (lambda path: brewdoc.render_book(path, sheets=("Calc",)), expected_render),
], ids=["read-formulas", "list-artifacts", "render-book"])
def test_each_public_workbook_call_starts_with_fresh_exact_budgets(tmp_path, monkeypatch, call, expected):
    # GIVEN inclusive limits for one complete selected-sheet inventory pass
    path = formula_book(tmp_path / "book.xlsx")
    wanted = expected(path)
    assert call(path) == wanted, "the unbounded synthetic source must have exact expected output"
    set_limits(monkeypatch, consumed_bytes=1552, total_elements=28, total_characters=519)
    # WHEN the same public operation runs twice on the same source
    first, second = call(path), call(path)
    # THEN state is local to each call and all results remain complete
    assert (first, second) == (wanted, wanted), "public readers must reset all aggregate counters per call"


def test_vba_artifact_bytes_remain_exact_with_bounded_xml_and_member_reads(tmp_path, monkeypatch):
    # GIVEN a legal opaque VBA payload containing bytes that are not XML or text
    project = b"synthetic project\x00\xff\xfe"
    path = vba_book(tmp_path / "book.xlsm", vba_relationship("vbaProject.bin"),
                    parts=(("xl/vbaProject.bin", project),))
    assert brewdoc.list_book_artifacts(path) == (vba_ref(project),), (
        "the synthetic VBA member must be inventoried as exact opaque bytes")
    set_limits(monkeypatch)
    # WHEN bounded reads inventory and retrieve the same project across reopened archives
    first, second = brewdoc.read_book_artifact(path, VBA_KEY), brewdoc.read_book_artifact(path, VBA_KEY)
    # THEN no decoding, truncation or state leakage changes the project bytes
    assert (first, second) == (project, project), "bounded readers must preserve every opaque VBA byte"


def inject_vba_stream(monkeypatch, project):
    """Return controlled bytes at the member I/O edge while retaining truthful XML reads."""
    requested = []
    original = zipfile.ZipFile.open

    class ProjectStream(io.BytesIO):
        """Record read requests for the misleading VBA member."""

        def read(self, size=-1):
            """Record requested bounds without changing returned opaque bytes."""
            requested.append(size)
            return super().read(size)

    handlers = {"xl/vbaProject.bin": lambda *args, **kwargs: ProjectStream(project)}

    def open_member(package, name, *args, **kwargs):
        """Replace only the opaque project stream and retain real XML I/O."""
        part = getattr(name, "filename", name)
        return handlers.get(part, original)(package, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    return requested


def test_vba_actual_byte_limit_accepts_the_boundary_with_bounded_stream_reads(tmp_path, monkeypatch):
    # GIVEN declared three-byte metadata but sixteen actual opaque bytes at the ZIP stream edge
    path = vba_book(tmp_path / "book.xlsm", vba_relationship("vbaProject.bin"),
                    parts=(("xl/vbaProject.bin", b"bin"),))
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("xl/vbaProject.bin").file_size == 3, "declared metadata must understate actual bytes"
    monkeypatch.setattr(sheets, "MAX_VBA_PROJECT_BYTES", 16)
    set_limits(monkeypatch)
    project = b"\x00\xff" * 8
    requested = inject_vba_stream(monkeypatch, project)
    archives, streams = track_zip_resources(monkeypatch)
    # WHEN a public artifact operation inventories and retrieves a project exactly at its actual cap
    payload = brewdoc.read_book_artifact(path, VBA_KEY)
    # THEN every opaque byte survives, reads stay within cap plus probe, and all handles close
    assert (payload, all(0 < size <= 17 for size in requested),
            [archive.fp is None for archive in archives], [stream.closed for stream in streams]) == (
        project, True, [True] * 3, [True] * 10,
    ), "the actual VBA boundary must preserve bytes using bounded reads and closed resources"


def test_vba_actual_overrun_keeps_the_existing_diagnostic_before_returning_payload(tmp_path, monkeypatch):
    # GIVEN legal declared metadata hiding one byte beyond the stricter opaque VBA cap
    path = vba_book(tmp_path / "book.xlsm", vba_relationship("vbaProject.bin"),
                    parts=(("xl/vbaProject.bin", b"bin"),))
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("xl/vbaProject.bin").file_size == 3, "the case must bypass the declared-size check"
    monkeypatch.setattr(sheets, "MAX_VBA_PROJECT_BYTES", 16)
    set_limits(monkeypatch)
    requested = inject_vba_stream(monkeypatch, b"\x00\xff" * 8 + b"!")
    archives, streams = track_zip_resources(monkeypatch)
    reason = "VBA project part exceeds 16 bytes: xl/vbaProject.bin"
    # WHEN actual stream bytes exceed the VBA cap despite legal member metadata
    with pytest.raises(brewdoc.BrewdocError, match=exact(reason)):
        brewdoc.read_book_artifact(path, VBA_KEY)
    # THEN the stricter existing diagnostic wins and the refused member is closed
    assert (all(0 < size <= 17 for size in requested), [archive.fp is None for archive in archives],
            [stream.closed for stream in streams]) == (True, [True] * 2, [True] * 7), (
        "actual VBA refusal must use only cap-plus-probe reads and close inventory resources")
