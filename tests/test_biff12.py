"""Synthetic BIFF12 source records and existing licensed XLSB regression inputs."""

import hashlib
import itertools
import json
import os
import socket
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from brewdoc import inspect_document, read_document_artifact, run
from brewdoc import _inspection_workbook as workbook_inspection
from brewdoc import sheets as workbook_rendering
from brewdoc._biff12 import _rich_string, inspect_biff12, iter_biff12
from brewdoc._excel_formula import decode_formula
from brewdoc._inspection_common import InspectionContext, InspectionLimit


def _record(kind, data=b""):
    header = bytes([kind]) if kind < 128 else bytes([(kind & 127) | 128, kind >> 7])
    size = len(data)
    length = []
    while size >= 128:
        length.append((size & 127) | 128)
        size >>= 7
    return header + bytes(length + [size]) + data


def _wide(text):
    return struct.pack("<I", len(text)) + text.encode("utf-16le")


def _formula(tokens, extra=b""):
    return struct.pack("<I", len(tokens)) + tokens + struct.pack("<I", len(extra)) + extra


def _cell(column, data):
    return struct.pack("<II", column, 0) + data


def _row(row, *, hidden=False):
    return _record(0, struct.pack("<I", row) + b"\0" * 7 + bytes([16 * hidden]) + b"\0" * 5)


def _members(sheet_records, *, names=b"", supporting=b"", references=b"", extra=()):
    sheet_name = "Bob's input"
    workbook = (_record(131) + _record(143)
                + _record(156, struct.pack("<II", 2, 1) + b"\xff" * 4 + _wide("Module1"))
                + _record(156, struct.pack("<II", 1, 2) + _wide("r1") + _wide(sheet_name))
                + _record(144) + _record(357) + supporting
                + _record(362, struct.pack("<IIii", 1, 0, 1, 1)) + references + names + _record(132))
    relationships = (b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     b'<Relationship Id="r1" Type="worksheet" Target="worksheets/sheet1.bin"/>'
                     b'</Relationships>')
    if any(part == "xl/sharedStrings.bin" for _ordinal, part, _source in extra):
        relationships = relationships.replace(b'</Relationships>', b'<Relationship Id="sst" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            b'Target="sharedStrings.bin"/></Relationships>')
    sheet_relationships = (b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                           b'<Relationship Id="link" Type="hyperlink" Target="https://example.invalid/exact?a=1#f" '
                           b'TargetMode="External"/></Relationships>')
    types = '<Types><Override PartName="/xl/workbook.bin" ContentType="application/vnd.ms-excel.sheet.binary.macroEnabled.main"/>'
    types += '<Override PartName="/xl/worksheets/sheet1.bin" ContentType="application/vnd.ms-excel.worksheet"/>'
    for _ordinal, part, _source in extra:
        kind = next((value for prefix, value in (("xl/externalLinks/", "externalLink"), ("xl/tables/", "table"),
                    ("xl/pivotCache/", "pivotCacheDefinition"), ("xl/metadata.bin", "sheetMetadata"),
                    ("xl/sharedStrings.bin", "sharedStrings"))
                     if part.startswith(prefix) and part.endswith(".bin")), None)
        if kind:
            types += '<Override PartName="/' + part + '" ContentType="application/vnd.ms-excel.' + kind + '"/>'
    types += '</Types>'
    return ((1, "xl/workbook.bin", workbook), (2, "xl/_rels/workbook.bin.rels", relationships),
            (3, "xl/worksheets/sheet1.bin", _record(129) + sheet_records + _record(130)),
            (4, "xl/worksheets/_rels/sheet1.bin.rels", sheet_relationships), *extra,
            (len(extra) + 5, "[Content_Types].xml", types.encode()),
            (len(extra) + 6, "_rels/.rels", b'<Relationships><Relationship Id="root" '
             b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
             b'Target="xl/workbook.bin"/></Relationships>'))


def _inspect(members, *, limits=None):
    context = InspectionContext(limits=limits)
    document = context.document(b"synthetic-xlsb", "sample.xlsb", parent=None, location={"kind": "source"})
    inspect_biff12(b"synthetic-xlsb", "sample.xlsb", context, document, members=members)
    return context


def test_biff12_preserves_offset_error_blank_cells_hidden_metadata_names_and_exact_targets():
    # GIVEN native records include a module sheet before a hidden source worksheet
    tokens = b"\x1e\x2a\x00"
    named = _record(39, struct.pack("<IBI", 10, 0, 0xFFFFFFFF)
                    + _wide("Auto_Open") + _formula(tokens) + b"\xff" * 20)
    records = (_record(148, struct.pack("<IIII", 4, 99, 2, 26)) + _row(4, hidden=True)
               + _record(6, _cell(2, _wide("offset")))
               + _record(3, _cell(3, b"\x07")) + _record(1, _cell(4, b""))
               + _record(60, struct.pack("<IIIIH", 2, 3, 2048, 0, 1))
               + _record(176, struct.pack("<IIII", 4, 4, 2, 3))
               + _record(494, struct.pack("<IIII", 4, 4, 2, 2)
                         + _wide("link") + _wide("fragment") + _wide("tooltip") + _wide("display"))
               + _row(99) + _record(5, _cell(26, struct.pack("<d", 42))))
    members = _members(records, names=named)
    assert members[0][2][:2] == b"\x83\x01", "the synthetic workbook begins with native BrtBeginBook"
    # WHEN binary inspection visits source occurrences without a readable selection
    context = _inspect(members)
    cells = [(item["location"]["qualified_cell"], item["details"])
             for item in context.findings if item["kind"] == "cell"]
    # THEN exact addresses, error tokens and declared blanks remain distinct
    assert cells == [
        ("'Bob''s input'!C5", {"value": "offset", "value_type": "string", "formula": None,
          "cached_value_state": "not_formula", "error": None, "merge_origin": "C5",
          "merge_range": "C5:D5", "style_reference": 0}),
        ("'Bob''s input'!D5", {"value": "#DIV/0!", "value_type": "error", "formula": None,
          "cached_value_state": "not_formula", "error": "#DIV/0!", "merge_origin": "C5",
          "merge_range": "C5:D5", "style_reference": 0}),
        ("'Bob''s input'!E5", {"value": None, "value_type": "blank", "formula": None,
          "cached_value_state": "not_formula", "error": None, "merge_origin": None,
          "merge_range": None, "style_reference": 0}),
        ("'Bob''s input'!AA100", {"value": 42.0, "value_type": "number", "formula": None,
          "cached_value_state": "not_formula", "error": None, "merge_origin": None,
          "merge_range": None, "style_reference": 0}),
    ], "native cells must preserve full addresses, explicit blanks, errors and merge origins"
    selected = [(item["kind"], item["details"]) for item in context.findings
                if item["kind"] in ("module-sheet", "macro-name", "columns")]
    assert selected == [
        ("module-sheet", {"sheet": "Module1", "sheet_ordinal": 1, "state": "veryHidden"}),
        ("macro-name", {"xlm": True, "vba": False, "macro": True, "formula": "42",
                        "trailing_strings": [None, None, None, None, None]}),
        ("columns", {"first_column": 3, "last_column": 4, "hidden": True, "flags": "0100"}),
    ], "module ordinals, XLM defined names and hidden columns must survive"
    links = [(item["target"], item["details"]) for item in context.findings if item["kind"] == "hyperlink"]
    assert links == [("https://example.invalid/exact?a=1#f", {
        "range": "C5:C5", "fragment": "fragment", "tooltip": "tooltip", "display": "display",
        "relationship_id": "link"})], "hyperlinks retain original relationship target and separate fragment"


@pytest.mark.parametrize("group_type", [426, 427], ids=["array", "shared"])
def test_biff12_resolves_shared_and_array_formula_anchors_with_exact_source_artifacts(group_type):
    # GIVEN two formula cells point at one explicit shared or array definition
    reference = b"\x01" + struct.pack("<I", 4)
    extra = struct.pack("<I", 2)
    group = struct.pack("<IIII", 4, 5, 2, 2) + b"\0" * (group_type == 426)
    definition = _formula(b"\x1e\x07\x00")
    records = (_row(4) + _record(9, _cell(2, struct.pack("<dH", 7, 0) + _formula(reference, extra)))
               + _record(group_type, group + definition) + _row(5)
               + _record(11, _cell(2, b"\x07\0\0" + _formula(reference, extra))))
    members = _members(records)
    assert len(definition) == 11, "the source group has one literal integer token and empty auxiliary bytes"
    # WHEN native formula groups resolve without evaluating workbook code
    context = _inspect(members)
    cells = [(item["location"]["cell"], item["details"]["formula"], item["details"]["error"],
              item["details"]["cached_value_state"]) for item in context.findings if item["kind"] == "cell"]
    # THEN both cells recover the group formula while retaining distinct caches
    assert cells == [("C5", "7", None, "present"), ("C6", "7", "#DIV/0!", "present")], (
        "shared/array anchors must resolve and error caches must remain error tokens")
    artifacts = [context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "formula"]
    assert artifacts == [_formula(reference, extra), _formula(reference, extra)], (
        "formula artifacts preserve the original tokens rather than replacing them with reconstructed source")


@pytest.mark.parametrize("source,reason", [
    (b"\x80", "invalid BIFF12 record type"),
    (b"\x80\x80", "invalid BIFF12 record type"),
    (b"\x01\x80", "truncated BIFF12 length"),
    (b"\x01\x80\x80\x80\x80", "unterminated BIFF12 length"),
    (b"\x01\x03\x00", "truncated BIFF12 record"),
])
def test_biff12_rejects_truncated_and_unterminated_record_framing(source, reason):
    # GIVEN a malformed bounded record header or payload
    context = InspectionContext()
    document = context.document(source, "bad.xlsb", parent=None, location={"kind": "source"})
    assert len(source) <= 5, "the malformed source fits one tiny record boundary"
    # WHEN the native reader validates framing before payload allocation
    with pytest.raises(ValueError, match=reason):
        tuple(iter_biff12(source, context, document, {"kind": "biff12"}))
    # THEN the failed record never becomes a complete component
    assert context.components == [], "truncated records cannot become admitted components"


def test_biff12_retains_unknown_record_locations_and_never_claims_complete_semantics():
    # GIVEN an unknown source record after an addressed numeric cell
    records = _row(4) + _record(5, _cell(2, struct.pack("<d", 42))) + _record(16_383, b"opaque")
    assert records[-6:] == b"opaque", "unknown source bytes must be present at the end of the record"
    # WHEN inspection preserves opaque source evidence and degrades affected coverage
    context = _inspect(_members(records))
    unknown = [item for item in context.findings if item["kind"] == "opaque-biff12-record"]
    # THEN the report names the exact source boundary rather than claiming an empty complete scan
    assert [(item["location"]["part"], item["location"]["record_type"],
             item["details"]["byte_size"]) for item in unknown] == [
        ("xl/worksheets/sheet1.bin", 16_383, 6)], "unknown records must remain located opaque evidence"
    assert context.coverage[("document/000001", "cells")]["status"] == "partial", (
        "an unknown semantic record cannot leave recovered cell coverage complete")


def test_biff12_uses_the_operation_record_budget_before_admitting_records():
    # GIVEN one operation has capacity for its source document but no binary records
    context = InspectionContext(limits={"records": 1})
    document = context.document(b"x", "limited.xlsb", parent=None, location={"kind": "source"})
    assert context.consumed["records"] == 1, "the source document already consumes the shared record allowance"
    # WHEN a workbook record would exceed the same operation allowance
    with pytest.raises(InspectionLimit, match="records limit exceeded"):
        tuple(iter_biff12(_record(131), context, document, {"kind": "biff12"}))
    # THEN no independent per-reader allowance hides the exhaustion
    assert context.components == [], "an over-limit BIFF12 component must not be admitted"


@pytest.mark.parametrize("tokens,expected", [
    (b"\x24" + struct.pack("<IH", 4, 2), "$C$5"),
    (b"\x23" + struct.pack("<I", 1), "InputName"),
    (b"\x3a" + struct.pack("<HIH", 0, 4, 2), "'Bob''s input'!$C$5"),
    (b"\x25" + struct.pack("<IIHH", 4, 5, 2, 3), "$C$5:$D$6"),
    (b"\x1e\x01\0\x1e\x02\0\x03", "(1+2)"),
    (b"\x1e\x01\0\x1e\x02\0\x0c", "(1>=2)"),
    (b"\x1e\x01\0\x1e\x02\0\x0d", "(1>2)"),
    (b"\x1e\x01\0\x1e\x02\0\x22\x02\x04\0", "SUM(1,2)"),
    (b"\x1e\x01\0\x21\x14\0", "SQRT(1)"),
    (b"\x2c" + struct.pack("<IH", 0xFFFFFFFF, 0xFFFF), "B4"),
])
def test_biff12_reconstructs_literals_references_names_functions_and_relative_group_tokens(tokens, expected):
    # GIVEN a bounded native formula expression with explicit names and sheet scopes
    assert len(tokens) <= 16_384, "the test expression fits the BIFF12 source token maximum"
    # WHEN the passive reader reconstructs its source meaning without evaluation
    result = decode_formula(tokens, b"", dialect="biff12", row=4, column=2,
                            names=["InputName"], external=["'Bob''s input'"])
    # THEN source references and function identities are preserved exactly
    assert result == expected, "the expression must retain its correct addresses, operators and names"


@pytest.mark.corpus
@pytest.mark.parametrize("filename,expected_count", [
    ("calamine-date.xlsb", 6), ("calamine-issue127.xlsb", 0), ("calamine-issues.xlsb", 27),
])
def test_biff12_public_inputs_are_deterministic_and_preserve_exact_component_hashes(filename, expected_count):
    # GIVEN an existing licensed public XLSB input and every package occurrence
    path = Path(__file__).parent / "fixtures" / "xlsb" / filename
    with zipfile.ZipFile(path) as archive:
        members = tuple((index, info.filename, archive.read(info))
                        for index, info in enumerate(archive.infolist(), 1))
    assert path.suffix == ".xlsb", "the public fixture exercises native binary workbook records"
    # WHEN two fresh inspections consume identical source members
    first, second = _inspect(members), _inspect(members)
    cells = [item for item in first.findings if item["kind"] == "cell"]
    # THEN source order and hashes remain deterministic without rewritten expected document output
    assert len(cells) == expected_count, "the fixture's addressed native cell count must remain exact"
    assert first.report({}) == second.report({}), "repeated inspection must have identical records, counters and IDs"
    assert first.coverage[("document/000001", "cells")]["status"] == "complete", (
        "recognized formatting and opaque printer settings cannot falsely degrade native cell extraction")
    assert {capability: first.coverage[("document/000001", capability)]["status"] for capability in (
        "cells", "formulas", "names", "links", "external_references", "hidden_structures", "macros", "forms",
        "attachments")} == {"cells": "complete", "formulas": "complete", "names": "complete", "links": "complete",
        "external_references": "complete", "hidden_structures": "complete", "macros": "complete",
        "forms": "complete", "attachments": "complete"}, (
        "the public source corpus must complete every native semantic requirement available in these fixtures")
    source = members[0][2]
    assert first.components[0]["sha256"] == hashlib.sha256(source[3:3]).hexdigest(), (
        "the empty initial workbook record has the SHA-256 of its exact payload")


@pytest.mark.parametrize("link_type,source1,source2,scope,expected", [
    (0, "remote", None, 1, "'[https://source.invalid/book.xlsb?exact=1]Remote'!Rate"),
    (1, "service", "topic", 0, "'service|topic'!Rate"),
    (2, "remote", "Excel.Sheet", 0, "'[https://source.invalid/book.xlsb?exact=1]'!Rate"),
], ids=["external-workbook-name", "DDE-server-topic-item", "OLE-source-item"])
def test_biff12_external_names_resolve_exact_source_tables_and_namex(link_type, source1, source2, scope, expected):
    # GIVEN one source external-link table and a cell using its first named item
    external = (_record(360, struct.pack("<H", link_type) + _wide(source1)
                        + (b"\xff" * 4 if source2 is None else _wide(source2)))
                + _record(359, struct.pack("<I", 1) + _wide("Remote"))
                + _record(577, _wide("Rate")) + _record(586, struct.pack("<HIB", 0, scope, 1))
                + _record(587) + _record(588))
    remote_rels = (b'<Relationships><Relationship Id="remote" Target="https://source.invalid/book.xlsb?exact=1" '
                   b'TargetMode="External"/></Relationships>')
    tokens = b"\x39" + struct.pack("<HI", 1, 1)
    cells = (_row(4) + _record(9, _cell(2, struct.pack("<dH", 42, 0) + _formula(tokens)))
             + _record(639, struct.pack("<IIIH", 1, 3, 9, 3)
                       + _wide("Excel.Sheet") + _formula(tokens)))
    parts = _members(cells, supporting=_record(355, _wide("external")),
                     references=_record(362, struct.pack("<IIii", 1, 1, -2, -2)), extra=(
                         (5, "xl/externalLinks/externalLink1.bin", external),
                         (6, "xl/externalLinks/_rels/externalLink1.bin.rels", remote_rels)))
    augmented = parts[1][2].replace(b"</Relationships>",
        b'<Relationship Id="external" Target="externalLinks/externalLink1.bin"/></Relationships>')
    parts = (parts[0], (2, parts[1][1], augmented), *parts[2:])
    assert tokens == b"\x39\x01\0\x01\0\0\0", "the native NameX indexes external XTI1 and name1"
    # WHEN the static reader joins source link tables to the exact NameX occurrence
    context = _inspect(parts)
    formulas = [item["details"]["formula"] for item in context.findings if item["kind"] == "cell"]
    # THEN the formula source names its exact workbook or DDE/OLE source and item
    assert formulas == [expected], "NameX must resolve source identities without placeholders or fetching"
    linked = [item["text"] for item in context.findings if item["kind"] == "linked-ole-object"]
    assert linked == [expected], "linked OLE source formulas must resolve the same exact NameX source"
    assert context.coverage[("document/000001", "external_references")]["status"] == "complete", (
        "all external name/source table records in the synthetic document must be inspected")


def test_biff12_controls_objects_and_drawing_relationships_retain_payload_artifacts():
    # GIVEN a control, embedded object and drawing each reference an admitted package member
    control = _record(644, struct.pack("<I", 7) + _wide("control") + _wide("Button7"))
    embedded = _record(639, struct.pack("<IIIH", 1, 0, 8, 2) + _wide("Package.Object") + _wide("object"))
    drawing = _record(550, _wide("drawing"))
    members = _members(control + embedded + drawing, extra=(
        (5, "xl/activeX/activeX1.xml", b"<control/>") ,
        (6, "xl/embeddings/object1.bin", b"opaque object bytes"),
        (7, "xl/drawings/drawing1.xml", b"<drawing/>")))
    relations = members[3][2].replace(b"</Relationships>",
        b'<Relationship Id="control" Target="../activeX/activeX1.xml"/>'
        b'<Relationship Id="object" Target="../embeddings/object1.bin"/>'
        b'<Relationship Id="drawing" Target="../drawings/drawing1.xml"/></Relationships>')
    members = (*members[:3], (4, members[3][1], relations), *members[4:])
    context = InspectionContext()
    document = context.document(b"synthetic", "sample.xlsb", parent=None, location={"kind": "source"})
    artifact = context.payload(document, "package-member", {"part": "xl/embeddings/object1.bin"},
                               b"opaque object bytes", "application/octet-stream")
    context.component(document, "package-member", {"part": "xl/embeddings/object1.bin", "member_ordinal": 6},
                      data=b"opaque object bytes", artifact=artifact, disposition="opaque")
    assert context.payloads[artifact] == b"opaque object bytes", "the package owns the exact admitted object payload"
    # WHEN the binary reader resolves control/object/drawing relationship occurrences
    inspect_biff12(b"synthetic", "sample.xlsb", context, document, members=members)
    objects = [(item["kind"], item["target"], item["artifact"])
               for item in context.findings if item["kind"] in ("activex-control", "ole-object", "drawing")]
    # THEN binary findings link to original destinations and reuse exact package payloads
    assert objects == [("activex-control", "../activeX/activeX1.xml", None),
                       ("ole-object", "../embeddings/object1.bin", artifact),
                       ("drawing", "../drawings/drawing1.xml", None)], (
        "controls and object records must preserve relationship occurrences and existing artifact identity")
    actions = [item["details"] for item in context.findings if item["kind"] == "ole-load-action"]
    assert actions == [{"automatic_load": True, "linked": False, "update_mode": 0,
                        "shape_id": 8, "progid": "Package.Object"}], (
        "the original automatic object-load action must be visible without running it")


def test_biff12_recovers_dde_item_cache_arrays_with_values_errors_blanks_and_source_names():
    # GIVEN a DDE source caches five distinct native value types for one item
    source = (_record(360, struct.pack("<H", 1) + _wide("server") + _wide("topic"))
              + _record(577, _wide("item")) + _record(586, b"\x02\0\0\0\0\0\x01")
              + _record(578, struct.pack("<II", 1, 5)) + _record(580, struct.pack("<d", 42))
              + _record(581, b"\x07") + _record(582, _wide("source text"))
              + _record(583) + _record(584, b"\x01") + _record(579) + _record(587) + _record(588))
    assert source[:2] == b"\xe8\x02", "the source begins with native external-link BrtBeginSupBook"
    # WHEN all row-major cached values are inspected without contacting the DDE server
    context = _inspect(_members(b"", extra=((5, "xl/externalLinks/external1.bin", source),)))
    cached = [(item["details"]["name"], item["details"]["row"], item["details"]["column"],
               item["details"]["value"], item["details"]["value_type"])
              for item in context.findings if item["kind"] == "external-name-cache"]
    # THEN none of the distinct native values collapse to an empty string
    assert cached == [("item", 1, 1, 42.0, "number"), ("item", 1, 2, "#DIV/0!", "error"),
                      ("item", 1, 3, "source text", "string"), ("item", 1, 4, None, "blank"),
                      ("item", 1, 5, True, "boolean")], "DDE item caches preserve source addresses and types"
    actions = [item["details"] for item in context.findings if item["kind"] == "dde-item"]
    assert actions == [{"service": "server", "topic": "topic", "item": "item", "flags": 2}], (
        "DDE source identities and notification flags must remain explicit passive findings")


def test_biff12_recovers_external_name_reference_formula_and_addressed_cached_cells():
    # GIVEN a remote defined name has a native small reference and a cached cell error
    reference = b"\x3a" + struct.pack("<hhHH", 0, 0, 4, 2)
    source = (_record(360, struct.pack("<H", 0) + _wide("remote") + b"\xff" * 4)
              + _record(359, struct.pack("<I", 1) + _wide("Remote"))
              + _record(577, _wide("Rate")) + _record(585, struct.pack("<I", len(reference)) + reference)
              + _record(586, struct.pack("<HIB", 0, 0, 0)) + _record(587)
              + _record(363, struct.pack("<IB", 0, 0)) + _record(366, struct.pack("<I", 4))
              + _record(370, struct.pack("<IB", 2, 7)) + _record(364) + _record(588))
    relationships = (b'<Relationships><Relationship Id="remote" Target="https://source.invalid/book.xlsb" '
                     b'TargetMode="External"/></Relationships>')
    assert len(reference) == 9, "ExtPtgRef3D uses source small coordinates rather than cell-formula widths"
    # WHEN native external formulas and cached cell records are joined with their source scope
    context = _inspect(_members(b"", extra=(
        (5, "xl/externalLinks/external1.bin", source),
        (6, "xl/externalLinks/_rels/external1.bin.rels", relationships))))
    named = [item["text"] for item in context.findings if item["kind"] == "external-name-formula"]
    cells = [item["details"] for item in context.findings if item["kind"] == "external-cell"]
    # THEN source names, errors and original locations remain recoverable
    assert named == ["'[https://source.invalid/book.xlsb]Remote'!$C$5"], (
        "external defined-name formulas retain their own native token layout and source scope")
    assert cells == [{"sheet": "Remote", "cell": "C5", "value": "#DIV/0!", "value_type": "error",
                      "qualified_cell": "'[https://source.invalid/book.xlsb]Remote'!C5"}], (
        "external cached cell records must preserve native error tokens beside exact addressed scope")


def test_biff12_malformed_validation_extension_preserves_unaffected_cells():
    # GIVEN a malformed known validation follows a normal addressed cell
    records = _row(4) + _record(5, _cell(2, struct.pack("<d", 42))) + _record(1053, b"rule-extension")
    assert records[-14:] == b"rule-extension", "the source includes the exact extension metadata bytes"
    # WHEN validation decoding rejects the actual truncated source
    context = _inspect(_members(records))
    unknown = [item["details"] for item in context.findings if item["kind"] == "opaque-biff12-record"]
    # THEN known malformed validation is distinct from an unknown source record
    assert unknown == [], "a known validation parser must report its native framing failure directly"
    assert (context.coverage[("document/000001", "cells")]["status"],
            context.coverage[("document/000001", "forms")]["status"]) == ("complete", "unavailable"), (
        "unrelated cell extraction may complete while malformed required validation metadata stays unavailable")


@pytest.mark.parametrize("source,expected_status", [
    (_record(577, _wide("orphan")), "partial"),
    (_record(360, struct.pack("<H", 1) + _wide("server") + _wide("topic")), "partial"),
    ((_record(360, struct.pack("<H", 1) + _wide("server") + _wide("topic"))
     + _record(577, _wide("item")) + _record(578, struct.pack("<II", 1, 2))
     + _record(580, struct.pack("<d", 42)) + _record(579) + _record(587) + _record(588)), "partial"),
], ids=["missing-source-header", "missing-end-boundary", "missing-cached-array-value"])
def test_biff12_malformed_external_tables_never_become_complete_empty_reports(source, expected_status):
    # GIVEN missing source framing or a declared cached array with fewer values
    assert len(source) > 0, "the test provides actual malformed external source records"
    # WHEN inspection attempts every admitted external occurrence
    context = _inspect(_members(b"", extra=((5, "xl/externalLinks/external1.bin", source),)))
    coverage = context.coverage[("document/000001", "external_references")]
    # THEN an explicit located failure remains after later success updates
    assert coverage["status"] == expected_status, "malformed required source tables cannot claim completeness"
    assert sorted({location["part"] for location in coverage["locations"]}) == [
        "xl/externalLinks/external1.bin"], "the failure identifies the affected source part"


def test_biff12_malformed_formula_group_keeps_independently_recoverable_cell_values():
    # GIVEN a valid addressed value precedes an unreadable shared-formula group
    records = _row(4) + _record(5, _cell(2, struct.pack("<d", 42))) + _record(427, b"\0")
    assert records[-1:] == b"\0", "the malformed group contains one actual unreadable source byte"
    # WHEN group metadata fails independently of the source cell record
    context = _inspect(_members(records))
    cells = [(item["location"]["cell"], item["details"]["value"])
             for item in context.findings if item["kind"] == "cell"]
    # THEN the source cell remains recoverable while the group failure stays explicit
    assert cells == [("C5", 42.0)], "malformed optional group metadata must not discard readable native cell values"
    assert context.coverage[("document/000001", "formulas")]["status"] == "unavailable", (
        "an unreadable shared group must never be counted as complete formula extraction")


def test_biff12_discovered_controls_and_dde_sources_never_fetch_or_execute(monkeypatch):
    # GIVEN passive control and DDE findings name destinations and handlers
    source = (_record(360, struct.pack("<H", 1) + _wide("command-server") + _wide("topic"))
              + _record(577, _wide("item")) + _record(586, b"\0" * 6 + b"\x01")
              + _record(587) + _record(588))
    control = _record(644, struct.pack("<I", 7) + _wide("link") + _wide("PassiveControl"))
    parts = _members(control, extra=((5, "xl/externalLinks/external1.bin", source),))
    assert parts[3][2].count(b"https://example.invalid/exact?a=1#f") == 1, (
        "the control's source relationship contains one exact external destination")

    def refuse(*args, **kwargs):
        """Fail any attempt to acquire resources or start a process."""
        raise AssertionError("static binary inspection attempted network or process execution")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "system", refuse)
    # WHEN inspection records the original control and DDE source
    context = _inspect(parts)
    controls = [item["target"] for item in context.findings if item["kind"] == "activex-control"]
    actions = [item["details"] for item in context.findings if item["kind"] == "dde-item"]
    # THEN exact evidence is recovered despite forbidden network/process capabilities
    assert (controls, actions) == (["https://example.invalid/exact?a=1#f"], [{
        "service": "command-server", "topic": "topic", "item": "item", "flags": 0}]), (
        "discovered destinations and executable service names are evidence rather than instructions")


def _table_source():
    table = struct.pack("<IIII", 4, 7, 2, 2) + struct.pack("<IIII", 0, 7, 1, 1) + b"\0" * 32
    table += _wide("Sales") + _wide("Sales") + b"\xff" * 16
    column = struct.pack("<IIIIII", 1, 9, 0, 0, 0, 0) + _wide("Amount") + _wide("Amount") + b"\xff" * 16
    return (_record(343, table) + _record(347, column)
            + _record(351, b"\0" + _formula(b"\x1e\x02\0"))
            + _record(352, b"\0" + _formula(b"\x1e\x03\0")) + _record(348) + _record(344))


def test_biff12_structured_table_references_and_table_formulas_use_admitted_source_names():
    # GIVEN a cell references an admitted binary table's first source column
    token = b"\x18\x19" + struct.pack("<HHIHH", 0, 1, 7, 0, 0)
    cells = _row(4) + _record(9, _cell(2, struct.pack("<dH", 42, 0) + _formula(token)))
    members = _members(cells, extra=((5, "xl/tables/table1.bin", _table_source()),))
    rels = members[3][2].replace(b"</Relationships>",
        b'<Relationship Id="table" Target="../tables/table1.bin"/></Relationships>')
    members = (*members[:3], (4, members[3][1], rels), *members[4:])
    assert len(token) == 14, "the structured source token includes original XTI/table/column identities"
    # WHEN the reader joins token IDs to source table metadata before decoding formulas
    context = _inspect(members)
    cells = [item["details"]["formula"] for item in context.findings if item["kind"] == "cell"]
    tables = [(item["kind"], item["text"]) for item in context.findings
              if item["kind"] in ("calculated-column-formula", "total-row-formula")]
    # THEN both referenced names and separate table formulas remain recoverable
    assert (cells, tables) == (["Sales[Amount]"], [("calculated-column-formula", "2"),
                                                   ("total-row-formula", "3")]), (
        "table references and formulas must use actual source identities rather than opaque tokens")


def test_biff12_pivot_formula_resolves_following_native_pname_field_references():
    # GIVEN a calculated pivot field references source field0 through a scoped PName
    plain = struct.pack("<HIHIII", 4, 0, 0, 0, 0, 0) + _wide("Revenue")
    tokens = b"\x18\x1d" + struct.pack("<I", 0) + b"\x1e\x02\0\x03"
    calculated = struct.pack("<HIHIII", 256, 0, 0, 0, 0, 0) + _wide("Calculated") + _formula(tokens)
    pivot = (_record(183, plain) + _record(184) + _record(183, calculated)
             + _record(255, struct.pack("<IBB", 0, 255, 0)) + _record(256) + _record(184))
    assert len(tokens) == 10, "the pivot formula names its following PName0 before adding a literal"
    # WHEN the reader reconstructs the native calculated field after resolving its local PNames
    context = _inspect(_members(b"", extra=((5, "xl/pivotCache/pivotCacheDefinition1.bin", pivot),)))
    formulas = [item["text"] for item in context.findings if item["kind"] == "pivot-formula"]
    # THEN the exact source cache field name survives without a fabricated label
    assert formulas == ["('Revenue'+2)"], "PivotName indexes must resolve against their source-scoped field names"


def test_biff12_validation_formulas_and_prompt_text_retain_original_ranges_and_flags():
    # GIVEN a native validation rule applies two literal bounds to C5:D6
    flags = 1 | 256 | (1 << 18) | (1 << 19)
    rule = (struct.pack("<II", flags, 1) + struct.pack("<IIII", 4, 5, 2, 3)
            + _wide("Error") + _wide("Outside bounds") + _wide("Prompt") + _wide("Use 1..10")
            + _formula(b"\x1e\x01\0") + _formula(b"\x1e\x0a\0"))
    assert flags == 786689, "the source enables blank values plus prompt and error messages"
    # WHEN inspection reads native validation metadata independently of cached values
    context = _inspect(_members(_record(64, rule)))
    rules = [item["details"] for item in context.findings if item["kind"] == "data-validation"]
    # THEN original ranges, limits, prompts and source flag values remain explicit
    assert rules == [{"ranges": ["C5:D6"], "flags": flags, "value_type": 1, "operator": 0,
                      "allow_blank": True, "show_prompt": True, "show_error": True,
                      "error_title": "Error", "error": "Outside bounds", "prompt_title": "Prompt",
                      "prompt": "Use 1..10", "formulas": ["1", "10"]}], (
        "validation formulas and metadata must be extracted without testing or executing the rule")


def test_biff12_external_alternate_urls_and_refresh_flags_keep_original_relationship_targets():
    # GIVEN a native source record carries drive/item IDs and two alternate URL relationships
    source = (_record(360, struct.pack("<H", 1) + _wide("service") + _wide("topic"))
              + _record(5108, _wide("drive-id") + _wide("item-id") + _wide("absolute") + _wide("relative"))
              + _record(588))
    relations = (b'<Relationships><Relationship Id="absolute" Target="https://source.invalid/Book.xlsb?exact=1" '
                 b'TargetMode="External"/><Relationship Id="relative" Target="../Book.xlsb" '
                 b'TargetMode="External"/></Relationships>')
    assert source.count("drive-id".encode("utf-16le")) == 1, "the source contains one native drive identity"
    # WHEN inspection reads alternate source relationships and the original refresh flag
    context = _inspect(_members(b"", names=_record(5099, b"\0\0\0\0\x01"), extra=(
        (5, "xl/externalLinks/external1.bin", source),
        (6, "xl/externalLinks/_rels/external1.bin.rels", relations))))
    urls = [(item["target"], item["details"]["drive_id"], item["details"]["item_id"])
            for item in context.findings if item["kind"] == "external-alternate-url"]
    refresh = [item["details"]["automatic_refresh"] for item in context.findings
               if item["kind"] == "external-refresh-action"]
    # THEN exact original destinations and automatic refresh behavior remain passive evidence
    assert (urls, refresh) == ([("https://source.invalid/Book.xlsb?exact=1", "drive-id", "item-id"),
                               ("../Book.xlsb", "drive-id", "item-id")], [True]), (
        "alternate URL relationships and source refresh flags must not be discarded or normalized")


def test_biff12_cell_and_value_metadata_join_following_native_cell_and_source_flags():
    # GIVEN cell/value metadata indexes refer to one native MDX metadata type and member
    metadata = (_record(332) + _record(334, struct.pack("<I", 1))
                + _record(335, struct.pack("<II", 5, 42) + _wide("XLMDX")) + _record(336)
                + _record(59, _wide("[Measure].[Source]")) + _record(54, struct.pack("<II", 0, 0))
                + _record(58, struct.pack("<IB", 0, 1)) + _record(55)
                + _record(337, struct.pack("<II", 1, 1)) + _record(51, struct.pack("<IiI", 1, 1, 0))
                + _record(338) + _record(337, struct.pack("<II", 1, 0))
                + _record(51, struct.pack("<IiI", 1, 1, 0)) + _record(338) + _record(333))
    cells = (_row(4) + _record(49, struct.pack("<i", 1)) + _record(50, struct.pack("<i", 1))
             + _record(5, _cell(2, struct.pack("<d", 42))))
    assert metadata.count("XLMDX".encode("utf-16le")) == 1, "one exact native metadata type defines the source flags"
    # WHEN the reader joins source metadata blocks to the following addressed cell
    context = _inspect(_members(cells, extra=((5, "xl/metadata.bin", metadata),)))
    findings = [item for item in context.findings if item["kind"] == "cell"]
    cell = findings[0]
    bound = cell["details"].get("metadata", {})
    # THEN metadata indexes retain the source type/version/member and original C5 location
    assert (cell["location"]["cell"], sorted(bound)) == ("C5", ["cell", "value"]), (
        "cell and value metadata must bind to the next original addressed source cell")
    assert [(key, bound[key]["index"], bound[key]["entries"][0]["type_name"],
             bound[key]["entries"][0]["type_flags"], bound[key]["entries"][0]["metadata_id"],
             bound[key]["entries"][0]["mdx_members"][0]["text"],
             bound[key]["entries"][0]["mdx_members"][0]["flags"]) for key in sorted(bound)] == [
        ("cell", 1, "XLMDX", 5, 42, "[Measure].[Source]", 1),
        ("value", 1, "XLMDX", 5, 42, "[Measure].[Source]", 1)], (
        "metadata joins must preserve exact source flags, type identity and MDX text")


def test_biff12_macro_name_extensions_preserve_source_argument_indexes_and_descriptions():
    # GIVEN a native macro-name extension declares two original argument descriptions
    extension = (b"\0" * 4 + struct.pack("<I", 2) + _wide("Macro1")
                 + struct.pack("<I", 0) + _wide("Source value")
                 + struct.pack("<I", 2) + _wide("Target address"))
    assert extension[:8] == b"\0\0\0\0\x02\0\0\0", "FRTBlank and description count use native fixed widths"
    # WHEN static inspection decodes the complete macro metadata source
    context = _inspect(_members(b"", names=_record(1036, extension)))
    macros = [(item["text"], item["details"]) for item in context.findings
              if item["kind"] == "macro-name-extension"]
    # THEN sparse argument indexes and literal descriptions survive without loading macro code
    assert macros == [("Macro1", {"arguments": [
        {"argument_index": 0, "description": "Source value"},
        {"argument_index": 2, "description": "Target address"}]})], (
        "macro argument metadata must retain original indexes rather than renumbering descriptions")


@pytest.mark.parametrize("support,scope,expression", [
    (358, "caller-sheet", "CALLER_SHEET!$A$1"), (667, "add-in", "ADDIN_SCOPE!$A$1"),
])
def test_biff12_named_runtime_scopes_are_symbolic_typed_evidence_not_guessed_urls(support, scope, expression):
    # GIVEN a named formula contains a source-declared runtime-dependent supporting link
    token = b"\x3a" + struct.pack("<HIH", 1, 0, 0)
    named = _record(39, struct.pack("<IBI", 0, 0, 0xFFFFFFFF)
                    + _wide("RuntimeSource") + _formula(token) + b"\xff" * 4)
    members = _members(b"", names=named, supporting=_record(support),
                       references=_record(362, struct.pack("<IIii", 1, 1, -2, -2)))
    assert token[:3] == b"\x3a\x01\0", "the native token references the second source XTI occurrence"
    # WHEN the reader decodes source semantics without evaluating the caller or loading an add-in
    context = _inspect(members)
    findings = [item for item in context.findings if item["kind"] == "formula"]
    # THEN the expression has typed runtime context and no fabricated original destination
    assert [(item["text"], item["details"]["context_required"]) for item in findings] == [(
        expression, [{"token_offset": 0, "scope_index": 1, "scope_kind": scope,
                      "context_required": True, "original_target": None}])], (
        "known runtime-dependent source operations are distinct from unsupported native bytes")
    assert context.coverage[("document/000001", "formulas")]["status"] == "complete", (
        "fully decoded symbolic source semantics do not require runtime formula evaluation")


def test_biff12_metadata_reference_failures_do_not_invent_cells_or_suppress_cached_values():
    # GIVEN a real cell references a source metadata block that is absent from the package
    cells = _row(4) + _record(49, struct.pack("<i", 7)) + _record(5, _cell(2, struct.pack("<d", 42)))
    assert cells.count(struct.pack("<i", 7)) == 1, "the source declares metadata index7 explicitly"
    # WHEN independent cell inspection cannot resolve that metadata source
    context = _inspect(_members(cells))
    values = [(item["location"]["cell"], item["details"]["value"])
              for item in context.findings if item["kind"] == "cell"]
    # THEN recoverable cached values survive while unknown metadata stays visibly incomplete
    assert values == [("C5", 42.0)], "missing metadata must not fabricate a replacement cell or erase the actual cache"
    assert context.coverage[("document/000001", "cells")]["status"] == "partial", (
        "source-addressed values cannot make missing required metadata appear complete")


@pytest.mark.parametrize("part,source,expected", [
    ("xl/tables/table1.xml", b'<table id="7" name="Sales" displayName="Sales" ref="C5:C8">'
     b'<tableColumns><tableColumn id="1" name="Amount"><calculatedColumnFormula>1+2</calculatedColumnFormula>'
     b'<totalsRowFormula>SUM(Sales[Amount])</totalsRowFormula></tableColumn></tableColumns></table>',
     [("calculated-column-formula", "1+2"), ("total-row-formula", "SUM(Sales[Amount])")]),
    ("xl/pivotCache/pivotCacheDefinition1.xml", b'<pivotCacheDefinition><cacheFields>'
     b'<cacheField name="Calculated" formula="Revenue*2"/></cacheFields>'
     b'<calculatedItems><calculatedItem field="0" formula="Dairy*115%"/></calculatedItems></pivotCacheDefinition>',
     [("pivot-formula", "Revenue*2"), ("pivot-formula", "Dairy*115%")]),
])
def test_biff12_package_xml_table_and_pivot_formulas_are_inspected_outside_binary_projection(part, source, expected):
    # GIVEN an admitted XML representation carries source formulas outside worksheet binary cells
    assert source.startswith(b"<"), "the package member contains original XML rather than reconstructed source"
    # WHEN independent XLSB inspection visits every table and pivot metadata representation
    context = _inspect(_members(b"", extra=((5, part, source),)))
    formulas = [(item["kind"], item["text"]) for item in context.findings if item["capability"] == "formulas"]
    # THEN original formula text is preserved even when its part is not a readable worksheet
    assert formulas == expected, "formula extraction cannot be narrowed to binary worksheet cell records"


def test_biff12_rich_string_cells_preserve_full_text_and_native_run_metadata():
    # GIVEN a source cell contains rich Unicode text and two native font runs
    rich = b"\x01" + _wide("Full β text") + struct.pack("<IHHHH", 2, 0, 1, 5, 2)
    assert rich[:1] == b"\x01", "the native RichStr flag explicitly enables formatting runs"
    # WHEN independent cell inspection reads the complete source RichStr rather than dropping the record
    context = _inspect(_members(_row(4) + _record(62, _cell(2, rich))))
    cells = [(item["location"]["cell"], item["details"]["value"], item["details"].get("rich_text"))
             for item in context.findings if item["kind"] == "cell"]
    # THEN all source characters and native run boundaries remain recoverable
    assert cells == [("C5", "Full β text", {"flags": 1, "coordinate_unit": "utf16-code-units",
        "runs": [{"character": 0, "font_index": 1}, {"character": 5, "font_index": 2}],
        "phonetic": None, "phonetic_runs": []})], "rich-text cells must retain full source text and run metadata"


def test_biff12_extended_validation_recovers_frt_ranges_formulas_prompts_and_original_artifact_bytes():
    # GIVEN newer validation uses FRT cce/cb-before-tokens framing instead of CellParsedFormula order
    first = struct.pack("<III", 2, 3, 0) + b"\x1e\x01\0"
    second = struct.pack("<III", 2, 3, 0) + b"\x1e\x0a\0"
    header = (struct.pack("<IIII", 6, 1, 2, 1) + struct.pack("<IIII", 4, 5, 2, 3)
              + struct.pack("<I", 2) + first + second)
    flags = 1 | (3 << 24)
    source = header + struct.pack("<I", flags) + _wide("Error") + _wide("Outside") + _wide("Prompt") + _wide("Use 1..10")
    assert first[:12] == b"\x02\0\0\0\x03\0\0\0\0\0\0\0", "the future formula preserves flags/cce/cb native order"
    # WHEN the reader decodes the extended validation header and source formula containers
    context = _inspect(_members(_record(1053, source)))
    rules = [item["details"] for item in context.findings if item["kind"] == "data-validation14"]
    artifacts = [context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "formula"]
    # THEN source ranges/formulas are recovered and exact original FRT bytes remain retrievable
    assert ([(item["ranges"], item["formulas"], item["flags"], item["prompt"]) for item in rules], artifacts) == (
        [(["C5:D6"], ["1", "10"], flags, "Use 1..10")], [first, second]), (
        "extended validation must preserve original FRT formula bytes instead of rewriting their framing")


def test_biff12_calculated_pivot_items_resolve_native_cache_item_references():
    # GIVEN a calculated item references the first literal cache item in a source field
    field = struct.pack("<HIHIII", 4, 0, 0, 0, 0, 0) + _wide("Product")
    tokens = b"\x18\x1d" + struct.pack("<I", 0) + b"\x1e\x02\0\x05"
    pivot = (_record(183, field) + _record(24, _wide("Dairy")) + _record(24, _wide("Meat"))
             + _record(184) + _record(245, b"\xff" * 4 + _formula(tokens))
             + _record(255, struct.pack("<IBB", 0xFFFFFFFF, 255, 0))
             + _record(259, struct.pack("<BIi", 0, 0, 0)) + _record(260) + _record(256) + _record(246))
    assert len(tokens) == 10, "the source formula names PName0 and multiplies it by literal2"
    # WHEN the reader joins a calculated item to its native field/cache item instead of inventing a label
    context = _inspect(_members(b"", extra=((5, "xl/pivotCache/pivotCacheDefinition1.bin", pivot),)))
    formulas = [item["text"] for item in context.findings if item["kind"] == "pivot-item-formula"]
    refs = [(item["details"]["field"], item["details"]["item"], item["details"]["flags"])
            for item in context.findings if item["kind"] == "pivot-item-reference"]
    # THEN actual source names and item flags survive as passive formula evidence
    assert (formulas, refs) == (["('Product'['Dairy']*2)"], [("Product", "Dairy", 0)]), (
        "calculated pivot item operands must resolve exact source field/item identities")


def test_biff12_rich_value_metadata_preserves_native_type_keys_values_and_cell_binding():
    # GIVEN a source value metadata block refers to one generic rich value with native structure keys
    metadata = (_record(332) + _record(335, struct.pack("<II", 5, 42) + _wide("XLRICHVALUE"))
                + _record(339, struct.pack("<I", 1) + _wide("XLRICHVALUE")) + _record(52)
                + _record(5002, b"\0" * 8) + _record(5003) + _record(53) + _record(340)
                + _record(337, struct.pack("<II", 1, 0)) + _record(51, struct.pack("<IiI", 1, 1, 0))
                + _record(338) + _record(333))
    structures = b'<rvStructures><s t="_custom"><k n="Title" t="s"/><k n="Count" t="i"/></s></rvStructures>'
    values = b'<rvData><rv s="0"><v>Native source</v><v>7</v></rv></rvData>'
    cells = _row(4) + _record(50, struct.pack("<i", 1)) + _record(5, _cell(2, struct.pack("<d", 42)))
    assert b'k n="Title" t="s"' in structures, "the original rich schema supplies the literal key and native type"
    # WHEN future-value metadata is joined to its actual rich XML payload and addressed cell
    context = _inspect(_members(cells, extra=((5, "xl/metadata.bin", metadata),
        (6, "xl/richData/rdrichvaluestructure.xml", structures), (7, "xl/richData/rdrichvalue.xml", values))))
    cell = [item for item in context.findings if item["kind"] == "cell"][0]
    rich = cell["details"].get("metadata", {}).get("value", {}).get("entries", [{}])[0].get("rich_value")
    # THEN generic native properties are preserved without inventing application-specific semantics
    assert (cell["location"]["cell"], rich) == ("C5", {
        "index": 0, "structure_index": 0, "type": "_custom", "properties": [
            {"name": "Title", "native_type": "s", "source_text": "Native source", "value": "Native source"},
            {"name": "Count", "native_type": "i", "source_text": "7", "value": 7}]}), (
        "rich value extraction must retain native keys, lexical values, typed scalars and original cell scope")
    assert context.coverage[("document/000001", "cells")]["status"] == "complete", (
        "recognized generic rich properties are statically recovered without application-specific interpretation")


def test_biff12_data_table_operation_preserves_inputs_range_flags_and_cached_cell_values():
    # GIVEN native what-if table members have numeric caches and a separate TABLE operation
    source = struct.pack("<IIIIIIIIB", 4, 5, 2, 2, 0, 0, 0, 1, 19)
    records = (_row(4) + _record(5, _cell(2, struct.pack("<d", 42))) + _record(428, source)
               + _row(5) + _record(3, _cell(2, b"\x07")))
    assert len(source) == 33, "the TABLE source has its native range, two input coordinates and flags"
    # WHEN passive inspection joins actual cached cells by the declared membership range
    context = _inspect(_members(records))
    cells = [(item["location"]["cell"], item["details"]["formula"],
              item["details"]["value"], item["details"]["cached_value_state"])
             for item in context.findings if item["kind"] == "cell"]
    operations = [item for item in context.findings if item["kind"] == "data-table-formula"]
    # THEN source operations and cached errors remain distinct from runtime evaluation
    assert cells == [("C5", "TABLE(A1,B1)", 42.0, "present"),
                     ("C6", "TABLE(A1,B1)", "#DIV/0!", "present")], (
        "native data table cells must retain TABLE inputs without calculating their cached results")
    assert [(item["details"]["range"], item["details"]["flags"], context.payloads[item["artifact"]])
            for item in operations] == [("C5:C6", 19, source)], (
        "original TABLE flags, range and exact native bytes must remain retrievable")
    assert context.coverage[("document/000001", "formulas")]["status"] == "complete", (
        "known native TABLE operations are recoverable without unsupported PtgTbl assumptions")


def test_biff12_rich_string_phonetics_preserve_source_and_pronunciation_coordinates():
    # GIVEN one source character has an explicit pronunciation and native font flags
    source = b"\x02" + _wide("漢") + _wide("kan") + struct.pack("<IHHHHH", 1, 0, 0, 1, 2, 3)
    assert source[:1] == b"\x02", "the phonetic flag is independently set from rich formatting runs"
    # WHEN the source RichStr is decoded with native UTF-16 coordinates
    context = _inspect(_members(_row(4) + _record(62, _cell(2, source))))
    values = [(item["details"]["value"], item["details"]["rich_text"])
              for item in context.findings if item["kind"] == "cell"]
    # THEN source and pronunciation text remain separate literal values
    assert values == [("漢", {"flags": 2, "coordinate_unit": "utf16-code-units", "runs": [],
        "phonetic": "kan", "phonetic_runs": [{"phonetic_character": 0, "source_character": 0,
        "source_length": 1, "font_index": 2, "flags": 3}]})], (
        "phonetic text must not replace or erase the original source cell text")


@pytest.mark.parametrize("source", [
    b"\x01" + _wide("abc") + struct.pack("<IHH", 1, 3, 0),
    b"\x01" + _wide("abc") + struct.pack("<IHHHH", 2, 1, 0, 0, 0),
    b"\x02" + _wide("abc") + _wide("x") + struct.pack("<IHHHHH", 1, 0, 2, 2, 0, 0),
], ids=["run-past-source", "unordered-runs", "phonetic-past-source"])
def test_biff12_invalid_rich_string_coordinates_never_become_complete_empty_cells(source):
    # GIVEN native run indexes are out of range or unordered
    assert len(source) > 5, "the malformed record contains a real native RichStr header and run body"
    # WHEN semantic decoding rejects the source coordinate claim
    context = _inspect(_members(_row(4) + _record(62, _cell(2, source))))
    # THEN coverage stays unavailable rather than inventing a normal blank source cell
    assert [item for item in context.findings if item["kind"] == "cell"] == [], (
        "unreadable rich text must never be replaced by a fabricated cell")
    assert context.coverage[("document/000001", "cells")]["status"] == "unavailable", (
        "failed rich-text extraction must remain visibly incomplete")


def test_biff12_rich_runs_charge_the_existing_shared_record_budget_before_growth():
    # GIVEN two native runs exceed the operation's remaining shared allowance
    source = b"\x01" + _wide("abc") + struct.pack("<IHHHH", 2, 0, 1, 2, 1)
    context = InspectionContext(limits={"records": 1})
    assert context.consumed["records"] == 0, "the operation starts with precisely one available record slot"
    # WHEN a source reader would create two run metadata entries
    with pytest.raises(InspectionLimit, match="records limit exceeded"):
        _rich_string(source, 0, context, {"kind": "biff12"})
    # THEN no separate reader allowance or partially admitted entry hides the exhaustion
    assert context.consumed["records"] == 0, "the whole native run count must be checked before admission"


@pytest.mark.parametrize("token", [b"\x01" + struct.pack("<I", 4), b"\x02" + struct.pack("<I", 4)],
                         ids=["missing-shared-definition", "unsupported-legacy-PtgTbl"])
def test_biff12_data_table_does_not_invent_support_for_missing_or_invalid_anchor_definitions(token):
    # GIVEN a native TABLE exists but the formula's separate token definition is absent or unsupported
    source = struct.pack("<IIIIIIIIB", 4, 5, 2, 2, 0, 0, 0, 1, 3)
    cells = _row(4) + _record(9, _cell(2, struct.pack("<dH", 42, 0) + _formula(token, struct.pack("<I", 2))))
    assert len(token) == 5, "the source formula contains its own exact anchor/token bytes"
    # WHEN the TABLE source operation is preserved without pretending it defines PtgExp or PtgTbl
    context = _inspect(_members(cells + _record(428, source)))
    values = [(item["details"]["value"], item["details"]["formula"])
              for item in context.findings if item["kind"] == "cell"]
    # THEN the recoverable cache survives while the independent formula token failure stays sticky
    assert values == [(42.0, None)], "TABLE metadata must not manufacture a missing shared formula definition"
    assert context.coverage[("document/000001", "formulas")]["status"] == "partial", (
        "a later successfully recovered TABLE source cannot erase a formula decoding failure")


@pytest.mark.parametrize("flags,item_index,expected,complete", [
    (0, 7, None, "unavailable"), (3, -1, "PIVOT_ITEM('Product',-1,3)", "complete"),
], ids=["missing-cache-item", "runtime-relative-item"])
def test_biff12_pivot_item_operands_preserve_dynamic_scope_and_reject_absent_literal_items(
        flags, item_index, expected, complete):
    # GIVEN a native PName contains either an absent absolute item or an explicit runtime-relative item
    field = struct.pack("<HIHIII", 4, 0, 0, 0, 0, 0) + _wide("Product")
    pivot = (_record(183, field) + _record(24, _wide("Dairy")) + _record(184)
             + _record(245, b"\xff" * 4 + _formula(b"\x18\x1d" + struct.pack("<I", 0)))
             + _record(255, struct.pack("<IBB", 0xFFFFFFFF, 255, 0))
             + _record(259, struct.pack("<BIi", flags, 0, item_index))
             + _record(260) + _record(256) + _record(246))
    assert pivot.endswith(_record(246)), "the source item collection has a real closing native boundary"
    # WHEN the reader resolves only literal identities actually present in the cache
    context = _inspect(_members(b"", extra=((5, "xl/pivotCache/pivotCacheDefinition1.bin", pivot),)))
    formulas = [item["text"] for item in context.findings if item["kind"] == "pivot-item-formula"]
    # THEN runtime dependency stays symbolic and missing literal targets cannot become fabricated names
    assert formulas == ([expected] * int(expected is not None)), (
        "exact cache item identity or explicit runtime operation must replace guessed resolved names")
    assert context.coverage[("document/000001", "formulas")]["status"] == complete, (
        "fully decoded dynamic source operands and unreadable absent source indexes have distinct coverage")


@pytest.mark.parametrize("source", [
    b'<rvData count="2"><rv s="0"><v>x</v></rv></rvData>',
    b'<rvData><rv s="7"><v>x</v></rv></rvData>',
], ids=["native-count-mismatch", "absent-native-structure"])
def test_biff12_rich_value_source_count_and_structure_failures_remain_explicit(source):
    # GIVEN a rich value source has an invalid count or references a nonexistent native schema
    assert b"<rv" in source, "the invalid source is an actual admitted rich value XML member"
    # WHEN the reader inspects original rich properties independently of displayed sheet content
    context = _inspect(_members(b"", extra=((5, "xl/richData/rdrichvalue.xml", source),)))
    # THEN no readable sheet can hide the unresolved required rich value evidence
    assert context.coverage[("document/000001", "cells")]["status"] == "unavailable", (
        "native rich value count and reference failures cannot produce a complete empty report")


def test_biff12_known_dynamic_array_metadata_binds_source_flags_to_the_actual_cell():
    # GIVEN known dynamic-array future metadata has one native block and a source cell reference
    metadata = (_record(332) + _record(335, struct.pack("<II", 5, 42) + _wide("XLDAPR"))
                + _record(339, struct.pack("<I", 1) + _wide("XLDAPR")) + _record(52)
                + _record(4096, b"\0" * 4) + _record(4097) + _record(53) + _record(340)
                + _record(337, struct.pack("<II", 1, 1)) + _record(51, struct.pack("<IiI", 1, 1, 0))
                + _record(338) + _record(333))
    assert metadata.count(_wide("XLDAPR")) == 2, "native type and future-store names agree exactly"
    # WHEN the future block is joined to the following actual source cell
    cells = _row(4) + _record(49, struct.pack("<i", 1)) + _record(5, _cell(2, struct.pack("<d", 42)))
    context = _inspect(_members(cells, extra=((5, "xl/metadata.bin", metadata),)))
    entry = [item for item in context.findings if item["kind"] == "cell"][0]["details"]["metadata"]["cell"]["entries"][0]
    # THEN dynamic status, native source flags and record locations remain addressed evidence
    assert (entry["dynamic_array"], entry["type_name"], entry["type_flags"], entry["record_location"]["record_type"]) == (
        True, "XLDAPR", 5, 4096), "known future metadata must preserve source type and native flag provenance"
    assert context.coverage[("document/000001", "cells")]["status"] == "complete", (
        "fully recovered dynamic-array source metadata does not require runtime spill evaluation")


def test_biff12_pivot_cache_run_and_additional_item_caption_retain_native_values():
    # GIVEN a field stores compact strings and a decorated item with a literal caption
    field = struct.pack("<HIHIII", 4, 0, 0, 0, 0, 0) + _wide("Product")
    compact = struct.pack("<HI", 2, 2) + _wide("Dairy") + _wide("Meat")
    decorated = _wide("Drink") + struct.pack("<H", 4) + _wide("Literal caption") + struct.pack("<I", 0)
    source = _record(183, field) + _record(191, compact) + _record(192) + _record(31, decorated) + _record(184)
    assert compact[:6] == b"\x02\0\x02\0\0\0", "the compact run declares two native string items"
    # WHEN the original cache representation is inspected without a rendered PivotTable
    context = _inspect(_members(b"", extra=((5, "xl/pivotCache/pivotCacheDefinition1.bin", source),)))
    items = [item["details"] for item in context.findings if item["kind"] == "pivot-cache-item"]
    # THEN compressed cache entries and additional literal text remain independently recoverable
    assert [item["value"] for item in items] == ["Dairy", "Meat", "Drink"], (
        "compact Pivot cache runs must contribute every native source item in order")
    assert items[2].get("additional_info") == {"flags": 4, "ghost": False, "calculated": False,
        "caption": "Literal caption", "member_property_indexes": []}, (
        "additional cache item captions are recoverable source text rather than opaque formatting")


def test_biff12_pivot_cache_source_keeps_external_target_and_exact_original_range():
    # GIVEN a native cache source stores an external relationship and literal worksheet range
    source = b"\0\0\x03" + _wide("Inputs") + _wide("remote") + struct.pack("<IIII", 4, 99, 2, 26)
    relationships = (b'<Relationships><Relationship Id="remote" TargetMode="External" '
                     b'Target="https://source.invalid/exact.xlsb?x=1#frag"/></Relationships>')
    assert source[:3] == b"\0\0\x03", "the source selects external-workbook and sheet scope explicitly"
    # WHEN original Pivot source metadata is recovered from its own admitted part
    context = _inspect(_members(b"", extra=(
        (5, "xl/pivotCache/pivotCacheDefinition1.bin", _record(187, source) + _record(188)),
        (6, "xl/pivotCache/_rels/pivotCacheDefinition1.bin.rels", relationships))))
    sources = [item for item in context.findings if item["kind"] == "pivot-cache-source"]
    # THEN original destination and offset source coordinates survive without opening the destination
    assert [(item["target"], item["details"]["sheet"], item["details"]["range"], item["details"]["flags_hex"])
            for item in sources] == [
        ("https://source.invalid/exact.xlsb?x=1#frag", "Inputs", "C5:AA100", "000003")], (
        "Pivot cache source metadata must retain its original target, sheet, range and native source flags")


def _public_variant(tmp_path, part, payload, content_type):
    path = tmp_path / "variant.xlsb"
    fixture = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(path, "w") as output:
        output.writestr(part, payload)
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == "[Content_Types].xml":
                raw = raw.replace(b"</Types>", ('<Override PartName="/' + part + '" ContentType="'
                                  + content_type + '"/></Types>').encode())
            output.writestr(info, raw)
    return path


@pytest.mark.corpus
def test_biff12_public_report_finalizes_all_fourteen_root_capabilities_after_actual_source_scan():
    # GIVEN the unchanged licensed workbook has recoverable native source strings
    path = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    assert path.stat().st_size == 19226, "the regression uses the unchanged public XLSB source fixture"
    # WHEN the public API completes independent native inspection
    report = inspect_document(path)
    # THEN successful text scanning finalizes alongside every other applicable root capability
    assert {item["capability"]: item["status"] for item in report["coverage"]
            if item["document"] == "document/000001"} == dict.fromkeys((
        "inventory", "text", "links", "scripts", "actions", "forms", "attachments", "images", "cells",
        "formulas", "names", "external_references", "hidden_structures", "macros"), "complete"), (
        "real shared-string findings must not coexist with unfinalized root text coverage")


@pytest.mark.corpus
def test_biff12_opaque_binary_member_does_not_enter_the_native_record_reader(tmp_path):
    # GIVEN an otherwise unchanged public workbook includes an explicitly opaque binary member
    path = _public_variant(tmp_path, "xl/unused.bin", b"opaque", "application/octet-stream")
    assert path.is_file(), "the modified public package exists before independent inspection"
    # WHEN content type and relationships determine native admission
    report = inspect_document(path)
    # THEN opaque bytes remain physical package evidence without invented native record failures
    assert {item["capability"]: item["status"] for item in report["coverage"]
            if item["document"] == "document/000001" and item["capability"] in ("cells", "formulas", "text")} == {
                "cells": "complete", "formulas": "complete", "text": "complete"}, (
        "an arbitrary .bin suffix cannot make declared opaque evidence corrupt native workbook coverage")
    assert [item for item in report["components"] if item["kind"] == "biff12-record"
            and item["location"].get("part") == "xl/unused.bin"] == [], (
        "opaque payloads must not be interpreted as native source records")
    physical = [item for item in report["components"] if item["location"].get("part") == "xl/unused.bin"]
    assert [(item["byte_size"], item["sha256"], read_document_artifact(path, item["artifact"]))
            for item in physical] == [(6, hashlib.sha256(b"opaque").hexdigest(), b"opaque")], (
        "opaque members must remain exactly retrievable package evidence after native admission is narrowed")


@pytest.mark.corpus
def test_biff12_orphan_sst_is_inspected_separately_without_corrupting_active_source_cache(tmp_path):
    # GIVEN a separately typed unreferenced SST precedes the workbook's actual shared string table
    source = _record(159, struct.pack("<II", 1, 1)) + _record(19, b"\0" + _wide("ORPHAN-STRING")) + _record(160)
    path = _public_variant(tmp_path, "xl/orphanStrings.bin", source, "application/vnd.ms-excel.sharedStrings")
    assert source.count("ORPHAN-STRING".encode("utf-16le")) == 1, "the orphan contains one exact literal string"
    # WHEN source SST indices are bound only to the workbook's actual sharedStrings relationship
    report = inspect_document(path)
    cells = [item["details"] for item in report["findings"] if item["kind"] == "cell"
             and item["location"].get("sheet") == "issue2" and item["location"].get("cell") == "B1"]
    orphans = [item for item in report["findings"] if item["kind"] == "shared-string"
               and item["location"].get("part") == "xl/orphanStrings.bin"]
    # THEN calamine cannot mask a corrupted source cache and orphan source text remains inspectable
    assert [(item["value"], item.get("source_cached_value")) for item in cells] == [("a", "a")], (
        "an orphan table must never shift the actual workbook's shared-string index0")
    assert [(item["text"], item["details"].get("scope")) for item in orphans] == [("ORPHAN-STRING", "unreferenced")], (
        "orphan native text must retain its own explicit source scope instead of being ignored")
    assert [item["status"] for item in report["coverage"] if item["document"] == "document/000001"
            and item["capability"] == "cells"] == ["partial"], (
        "the malformed extra shared-string source must remain an explicit cardinality failure")


def test_biff12_malformed_active_sst_entry_keeps_later_indices_and_text_failure_sticky():
    # GIVEN entry0 is malformed but entry1 has valid source text referenced by C5
    source = (_record(159, struct.pack("<II", 2, 2)) + _record(19, b"\0\x01\0\0\0\xff")
              + _record(19, b"\0" + _wide("SECOND")) + _record(160))
    assert source.count("SECOND".encode("utf-16le")) == 1, "the valid sibling is source SST index1"
    # WHEN inspection preserves original string indexing despite the earlier native decoding failure
    context = _inspect(_members(_row(4) + _record(7, _cell(2, struct.pack("<I", 1))),
                               extra=((5, "xl/sharedStrings.bin", source),)))
    # THEN the later valid string retains its actual cell and prior text failure remains visible
    assert [(item["location"]["cell"], item["details"]["value"])
            for item in context.findings if item["kind"] == "cell"] == [("C5", "SECOND")], (
        "failed source entries must reserve their original SST index rather than shifting later caches")
    assert context.coverage[("document/000001", "text")]["status"] == "partial", (
        "successful sibling text must not overwrite an actual earlier native text extraction failure")


@pytest.mark.corpus
@pytest.mark.parametrize("replacement", [
    b'Target="absent.bin"',
    b'Target="sharedStrings.bin"/><Relationship Id="otherSst" '
    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
    b'Target="sharedStrings.bin"',
], ids=["missing-active-target", "duplicate-active-relationships"])
def test_biff12_public_missing_or_ambiguous_sst_relationship_cannot_invent_source_cache(tmp_path, replacement):
    # GIVEN a public package has a missing or multiply referenced active SST source
    fixture = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    path = tmp_path / "ambiguous.xlsb"
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(path, "w") as output:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == "xl/_rels/workbook.bin.rels":
                raw = raw.replace(b'Target="sharedStrings.bin"', replacement)
            output.writestr(info, raw)
    with zipfile.ZipFile(path) as package:
        changed = package.read("xl/_rels/workbook.bin.rels")
    assert changed.count(replacement) == 1, "the source relationship bytes contain the exact intentional mutation"
    # WHEN the public API refuses ambiguous native SST ownership
    report = inspect_document(path)
    # THEN no first-table guess can populate the affected source-cache cell
    assert [item for item in report["findings"] if item["kind"] == "cell"
            and item["location"].get("sheet") == "issue2" and item["location"].get("cell") == "B1"] == [], (
        "unresolved native shared-string ownership cannot become a fabricated calamine-backed source cache")
    assert [item["status"] for item in report["coverage"] if item["document"] == "document/000001"
            and item["capability"] == "cells"] == ["partial"], (
        "missing or ambiguous required string sources must remain incomplete after other cells recover")


@pytest.mark.corpus
def test_biff12_public_duplicate_sst_member_occurrences_remain_separate_and_ambiguous(tmp_path):
    # GIVEN the actual active SST part appears twice as distinct physical package occurrences
    fixture = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    path = tmp_path / "duplicate.xlsb"
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(path, "w") as output:
        shared = source.read("xl/sharedStrings.bin")
        for info in source.infolist():
            output.writestr(info, source.read(info))
        with pytest.warns(UserWarning, match="Duplicate name: 'xl/sharedStrings.bin'"):
            output.writestr("xl/sharedStrings.bin", shared)
    with zipfile.ZipFile(path) as package:
        ordinals = [index for index, part in enumerate(package.namelist(), 1) if part == "xl/sharedStrings.bin"]
    assert len(ordinals) == 2, "the modified source contains precisely two physical active-table occurrences"
    # WHEN native inspection rejects ambiguous part ownership while retaining both source tables
    report = inspect_document(path)
    entries = [item for item in report["findings"] if item["kind"] == "shared-string"
               and item["location"].get("part") == "xl/sharedStrings.bin" and item["details"]["string_index"] == 0]
    # THEN every duplicate occurrence retains local indices and no active source cache is guessed
    assert [(item["location"]["member_ordinal"], item["text"], item["details"]["scope"])
            for item in entries] == [(index, "a", "unreferenced") for index in ordinals], (
        "duplicate native tables must remain distinct located source evidence without a first/last winner")
    assert [item for item in report["findings"] if item["kind"] == "cell"
            and item["location"].get("sheet") == "issue2" and item["location"].get("cell") == "B1"] == [], (
        "duplicate active-table occurrences cannot provide an unambiguous native source cache")
    assert [item["status"] for item in report["coverage"] if item["document"] == "document/000001"
            and item["capability"] == "cells"] == ["partial"], (
        "recoverable duplicate text cannot conceal malformed active SST cardinality")


def _renamed_public_sst(tmp_path):
    path = tmp_path / "renamed.xlsb"
    fixture = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(path, "w") as output:
        for info in source.infolist():
            raw = source.read(info)
            name = info.filename
            if name in ("[Content_Types].xml", "xl/_rels/workbook.bin.rels"):
                raw = raw.replace(b"sharedStrings.bin", b"sharedStrings.native")
            if name == "xl/sharedStrings.bin":
                name = "xl/sharedStrings.native"
            output.writestr(name, raw)
    return path


@pytest.mark.corpus
def test_biff12_public_native_sst_filename_survives_optional_calamine_panic_with_exact_source_evidence(tmp_path):
    # GIVEN only the declared SST part name and its actual relationship target change
    path = _renamed_public_sst(tmp_path)
    with zipfile.ZipFile(path) as source:
        renamed = source.read("xl/sharedStrings.native")
    fixture = Path(__file__).parent / "fixtures" / "xlsb" / "calamine-issues.xlsb"
    with zipfile.ZipFile(fixture) as source:
        original = source.read("xl/sharedStrings.bin")
    assert renamed == original, "the valid declared native SST retains every original source byte"
    # WHEN independent inspection finishes despite an optional binding projection limitation
    first, second = inspect_document(path), inspect_document(path)
    cells = [item for item in first["findings"] if item["kind"] == "cell"]
    failures = [item for item in first["findings"] if item["kind"] == "typed-cache-unavailable"]
    # THEN exact native cells survive and only optional enrichment is disclosed as unavailable
    assert first == second, "native source reports and optional enrichment diagnostics must remain deterministic"
    assert (len(cells), [(item["details"]["value"], item["details"]["shared_string_index"])
            for item in cells if item["location"].get("sheet") == "issue2"
            and item["location"]["cell"] == "B1"]) == (27, [("a", 0)]), (
        "a valid relationship-selected SST must retain native addressed cell/cache evidence through a binding failure")
    assert [item["details"]["reason"] for item in failures] == [
        "PanicException: index out of bounds: the len is 0 but the index is 3"], (
        "the optional binding failure must be disclosed without converting native extraction into an empty report")
    assert {item["status"] for item in first["coverage"] if item["document"] == "document/000001"} == {"complete"}, (
        "optional typed-cache enrichment cannot falsely degrade complete native source capabilities")


@pytest.mark.corpus
def test_biff12_calamine_panic_fallback_matches_native_only_cells_and_original_sst_artifact(tmp_path, monkeypatch):
    # GIVEN the valid renamed source triggers the installed binding's optional cache limitation
    path = _renamed_public_sst(tmp_path)
    with zipfile.ZipFile(path) as package:
        original = package.read("xl/sharedStrings.native")
    assert hashlib.sha256(original).hexdigest() == "b6670d888da682addd2534fedf0267efd22f39b3b01f814e8d4747e73308d607", (
        "the regression retains the exact licensed native SST payload")
    # WHEN fallback and explicitly native-only inspection read the same original source
    fallback = inspect_document(path)
    monkeypatch.setattr(workbook_inspection, "_cached_values", lambda *_args: {})
    native_only = inspect_document(path)
    source = [item for item in fallback["components"] if item["location"].get("part") == "xl/sharedStrings.native"
              and item["kind"] == "package-member"][0]
    # THEN optional failure cannot partly overwrite native source values or original payload bytes
    assert [item for item in fallback["findings"] if item["kind"] in ("cell", "shared-string")] == [
        item for item in native_only["findings"] if item["kind"] in ("cell", "shared-string")], (
        "optional enrichment failure must produce the same addressed cells and SST metadata as native-only extraction")
    assert read_document_artifact(path, source["artifact"]) == original, (
        "native fallback retains exact original SST bytes through the public artifact API")


@pytest.mark.parametrize("failure", [KeyboardInterrupt("stop"), SystemExit(7), GeneratorExit("stop"),
                                    BaseException("foreign control")],
                         ids=["keyboard", "system-exit", "generator-exit", "unrelated-base-exception"])
def test_optional_cache_boundary_never_catches_process_control_or_unrelated_base_exceptions(monkeypatch, failure):
    # GIVEN optional cache loading raises a process control exception rather than the binding's panic class
    context = _inspect(_members(_row(4) + _record(5, _cell(2, struct.pack("<d", 42)))))
    original = list(context.findings)
    assert context.coverage[("document/000001", "cells")]["status"] == "complete", (
        "the native source was fully inspected before optional cache enrichment")
    class InterruptedWorkbook:
        """Raise the supplied control object at the binding boundary."""
        @staticmethod
        def from_filelike(_source):
            """Emulate interruption before any typed cache is read."""
            raise failure
    monkeypatch.setattr(workbook_inspection, "CalamineWorkbook", InterruptedWorkbook)
    # WHEN optional enrichment enters the narrowly selected binding exception boundary
    with pytest.raises(type(failure)) as raised:
        workbook_inspection._cached_values(b"native source", context, "document/000001")
    # THEN process control propagates unchanged and no fabricated reader failure is published
    assert raised.value is failure, "the exact process control exception must propagate to its caller"
    assert context.findings == original, "unrelated BaseExceptions must not become optional cache diagnostics"


@pytest.mark.corpus
def test_biff12_optional_enrichment_failure_does_not_hide_actual_unknown_native_records(tmp_path):
    # GIVEN a renamed-SST package also contains a genuinely unknown native worksheet record
    original = _renamed_public_sst(tmp_path)
    path = tmp_path / "unknown-native.xlsb"
    marker = _record(16383, b"future-source")
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(path, "w") as output:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == "xl/worksheets/sheet1.bin":
                raw = raw[:-len(_record(130))] + marker + _record(130)
            output.writestr(info, raw)
    with zipfile.ZipFile(path) as package:
        changed = package.read("xl/worksheets/sheet1.bin")
    assert changed.endswith(marker + _record(130)), "the source contains a real unknown native record before its valid end"
    # WHEN independent native scanning and optional enrichment report their own evidence
    report = inspect_document(path)
    unknown = [item for item in report["findings"] if item["kind"] == "opaque-biff12-record"]
    # THEN recovered native cells survive while their actual unsupported source remains incomplete
    assert [(item["location"]["part"], item["location"]["record_type"]) for item in unknown] == [
        ("xl/worksheets/sheet1.bin", 16383)], "optional fallback must preserve exact native failure locations"
    assert (len([item for item in report["findings"] if item["kind"] == "cell"]),
            [item["status"] for item in report["coverage"] if item["document"] == "document/000001"
             and item["capability"] == "cells"]) == (27, ["partial"]), (
        "binding diagnostics cannot falsely upgrade an actual native semantic extraction failure")


def test_optional_cache_failure_discards_partial_typed_overlay_without_degrading_native_values(monkeypatch):
    # GIVEN two native sheets exist and typed enrichment succeeds only for the first
    context = _inspect(_members(_row(4) + _record(5, _cell(2, struct.pack("<d", 42)))))
    context.finding("document/000001", "cells", "cell", {"kind": "workbook-cell", "sheet": "Other",
        "sheet_ordinal": 3, "cell": "C5"}, value=43, value_type="number")
    assert [item["details"]["value"] for item in context.findings if item["kind"] == "cell"] == [42.0, 43], (
        "native source values precede optional typed enrichment")
    class CachedSheet:
        """Supply one typed value before another sheet fails."""
        start = (4, 2)
        end = (4, 2)
        def to_python(self):
            """Return the deliberately distinct typed value."""
            return [[999]]
    class PartialWorkbook:
        """Fail on the second sheet after a successful first projection."""
        sheet_names = ["Bob's input", "Other"]
        @classmethod
        def from_filelike(cls, _source):
            """Open the controlled partial projection."""
            return cls()
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def get_sheet_by_name(self, name):
            """Reject the second source sheet with a binding-style lookup failure."""
            return {"Bob's input": CachedSheet()}[name]
    monkeypatch.setattr(workbook_inspection, "CalamineWorkbook", PartialWorkbook)
    # WHEN the binding fails after returning one typed value
    typed = workbook_inspection._cached_values(b"source", context, "document/000001")
    failures = [item["details"] for item in context.findings if item["kind"] == "typed-cache-unavailable"]
    # THEN optional enrichment is atomic and the disclosed failure cannot invalidate recovered native evidence
    assert typed == {}, "an incomplete optional typed projection must not partly overwrite native source cells"
    assert failures == [{"reason": "KeyError: 'Other'", "reader": "python-calamine", "optional": True}], (
        "ordinary binding errors must disclose the same explicit optional-enrichment status as a native panic")
    assert context.coverage[("document/000001", "cells")]["status"] == "complete", (
        "an optional overlay failure must not falsely invalidate native source cell coverage")


@pytest.mark.corpus
def test_biff12_readable_panic_refusal_preserves_requested_report_vba_source_and_native_payload(tmp_path):
    # GIVEN valid original inspection succeeds and requested evidence is independent of readable calamine
    source = _renamed_public_sst(tmp_path)
    report = inspect_document(source)
    script = next(item["key"] for item in report["artifacts"] if item["kind"] == "vba_source")
    native = next(item["artifact"] for item in report["components"] if item["kind"] == "package-member"
                  and item["location"].get("part") == "xl/sharedStrings.native")
    script_bytes, native_bytes = read_document_artifact(source, script), read_document_artifact(source, native)
    output, saved, code_path, native_path = (tmp_path / name for name in ("out.md", "report.json", "module.bas", "sst.bin"))
    output.write_bytes(b"previous Markdown\r\n")
    assert len(native_bytes) == 164, "the requested native payload is the original declared SST source"
    # WHEN readable conversion refuses its binding panic through the existing service artifact transaction
    code, receipt, markdown = run(source, output, artifact_outputs={
        "inspection/report": saved, script: code_path, native: native_path})
    # THEN only requested evidence commits and failed Markdown remains byte-exact
    assert (code, receipt["file_ok"], markdown, output.read_bytes(), json.loads(saved.read_bytes()),
            code_path.read_bytes(), native_path.read_bytes()) == (
        1, False, "", b"previous Markdown\r\n", report, script_bytes, native_bytes), (
        "a native readable panic must refuse Markdown while preserving exact requested report, script and source artifacts")
    assert receipt["reason"] == ("spreadsheet unreadable: %s: PanicException: " % source
        + "index out of bounds: the len is 0 but the index is 3"), (
        "the public refusal must identify the original binding panic without leaking a BaseException")


@pytest.mark.corpus
@pytest.mark.parametrize("invalid", ["unknown-key", "collision", "directory-target"])
def test_biff12_readable_panic_preservation_keeps_artifact_key_and_target_validation(tmp_path, invalid):
    # GIVEN native evidence exists but one requested artifact assignment is invalid
    source = _renamed_public_sst(tmp_path)
    report = inspect_document(source)
    key = next(item["artifact"] for item in report["components"] if item["kind"] == "package-member"
               and item["location"].get("part") == "xl/sharedStrings.native")
    output, saved, native, directory = (tmp_path / name for name in ("out.md", "report.json", "sst.bin", "bad-target"))
    output.write_bytes(b"previous Markdown\r\n")
    saved.write_bytes(b"previous report")
    native.write_bytes(b"previous native")
    directory.mkdir()
    assignments = {"unknown-key": {"inspection/report": saved, "inspection/missing": native},
                   "collision": {"inspection/report": output, key: native},
                   "directory-target": {"inspection/report": saved, key: directory}}
    expected = {"unknown-key": "unknown artifact key: inspection/missing",
                "collision": "Markdown and artifact outputs collide: %s" % output,
                "directory-target": "output target is not a regular file: %s" % directory}
    original_source = source.read_bytes()
    assert directory.is_dir(), "the invalid target includes a real preexisting directory"
    # WHEN the unchanged service validation handles requested artifacts around readable refusal
    code, receipt, markdown = run(source, output, artifact_outputs=assignments[invalid])
    # THEN no invalid assignment may commit even a valid companion artifact
    assert (code, receipt["reason"], markdown, output.read_bytes(), saved.read_bytes(), native.read_bytes(),
            source.read_bytes(), sorted(item.name for item in tmp_path.iterdir())) == (
        1, expected[invalid], "", b"previous Markdown\r\n", b"previous report", b"previous native",
        original_source, ["bad-target", "out.md", "renamed.xlsb", "report.json", "sst.bin"]), (
        "panic normalization cannot bypass artifact key/target validation or modify existing source and destinations")


@pytest.mark.corpus
def test_biff12_readable_panic_artifact_transaction_rolls_back_partial_publication(tmp_path, monkeypatch):
    # GIVEN report and native-source destinations already contain independently preserved bytes
    source = _renamed_public_sst(tmp_path)
    report = inspect_document(source)
    key = next(item["artifact"] for item in report["components"] if item["kind"] == "package-member"
               and item["location"].get("part") == "xl/sharedStrings.native")
    output, saved, native = (tmp_path / name for name in ("out.md", "report.json", "sst.bin"))
    output.write_bytes(b"previous Markdown\r\n")
    saved.write_bytes(b"previous report")
    native.write_bytes(b"previous native")
    assert (saved.read_bytes(), native.read_bytes()) == (b"previous report", b"previous native"), (
        "both existing sidecars precede the artifact-only output transaction")
    def fail_replace(*_args):
        """Reject the second destination after the first sidecar has changed."""
        raise OSError("injected XLSB artifact replace failure")
    replacements = itertools.chain((os.replace, fail_replace), itertools.repeat(os.replace))
    monkeypatch.setattr(os, "replace", lambda first, second: next(replacements)(first, second))
    # WHEN native readable refusal reaches an artifact-only transaction whose second replace fails
    code, receipt, markdown = run(source, output, artifact_outputs={"inspection/report": saved, key: native})
    # THEN existing artifacts and Markdown are restored and all temporary transaction files disappear
    assert (code, receipt["reason"], markdown, output.read_bytes(), saved.read_bytes(), native.read_bytes(),
            sorted(item.name for item in tmp_path.iterdir())) == (
        1, "output write failed: could not replace %s: injected XLSB artifact replace failure" % native,
        "", b"previous Markdown\r\n", b"previous report", b"previous native",
        ["out.md", "renamed.xlsb", "report.json", "sst.bin"]), (
        "a binding refusal cannot weaken atomic artifact publication or leave partial evidence and leaked stages")


@pytest.mark.parametrize("failure", [KeyboardInterrupt("stop"), SystemExit(7), GeneratorExit("stop"),
                                    BaseException("foreign control")],
                         ids=["keyboard", "system-exit", "generator-exit", "unrelated-base-exception"])
def test_readable_workbook_boundary_never_catches_process_control_or_unrelated_base_exceptions(monkeypatch, failure):
    # GIVEN readable workbook opening is interrupted by caller control rather than a native binding panic
    class InterruptedWorkbook:
        """Raise caller control before opening any workbook resource."""
        @staticmethod
        def from_path(_source):
            """Propagate the supplied control object unchanged."""
            raise failure
    monkeypatch.setattr(workbook_rendering, "CalamineWorkbook", InterruptedWorkbook)
    assert type(failure).__module__ == "builtins", "the control is unrelated to the loaded native panic class"
    # WHEN the readable context manager opens its binding boundary
    with pytest.raises(type(failure)) as raised:
        with workbook_rendering._workbook(Path("control.xlsb")):
            pytest.fail("caller control must stop workbook opening before readable projection")
    # THEN native panic normalization does not swallow any other BaseException type
    assert raised.value is failure, "the exact caller control object must propagate through readable conversion"
