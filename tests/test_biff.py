"""Passive BIFF regressions use native records and complete CFB sources."""

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from brewdoc._inspection_common import InspectionContext


def record(kind, payload=b""):
    """Frame one BIFF record with its exact source length."""
    return struct.pack("<HH", kind, len(payload)) + payload


def stream(sheet_records, *, globals_records=b"", sheet_name="Bob's input", sheet_type=0,
           visibility=0, version=0x0600):
    """Compose globals and one addressed sheet with a correct source offset."""
    bof = record(0x0809, struct.pack("<HH", version, 5) + b"\x00" * 12)
    name = bytes((len(sheet_name),)) + (b"\0" if version == 0x0600 else b"") + sheet_name.encode("latin1")
    bound = record(0x0085, struct.pack("<IBB", 0, visibility, sheet_type) + name)
    prefix = bof + globals_records + bound + record(0x000A)
    bound = record(0x0085, struct.pack("<IBB", len(prefix), visibility, sheet_type) + name)
    prefix = bof + globals_records + bound + record(0x000A)
    return prefix + record(0x0809, struct.pack("<HH", version, 0x40 if sheet_type == 1 else 0x10)
                           + b"\x00" * 12) + sheet_records + record(0x000A)


def inspect(data, **limits):
    """Inspect bytes through the reader's public internal contract."""
    from brewdoc._biff import inspect_biff_stream

    context = InspectionContext(limits=limits)
    document = context.document(data, "book.xls", parent=None, location={"kind": "source"})
    inspect_biff_stream(data, "Workbook", context, document)
    return context


def compound_book(source):
    """Frame one admitted legacy Book stream as a complete standard CFB source."""
    header, directory = bytearray(512), bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHH", header, 24, 62, 3, 65534, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    struct.pack_into("<109I", header, 76, 0, *([0xFFFFFFFF] * 108))
    for position, name, kind, start, size in ((0, "Root Entry", 5, 0xFFFFFFFE, 0), (128, "Book", 2, 2, 4096)):
        encoded = (name + "\0").encode("utf-16le")
        directory[position:position + len(encoded)] = encoded
        struct.pack_into("<HBBIII", directory, position + 64, len(encoded), kind, 1,
                         0xFFFFFFFF, 0xFFFFFFFF, 1 if position == 0 else 0xFFFFFFFF)
        struct.pack_into("<IQ", directory, position + 116, start, size)
    allocation = [0xFFFFFFFD, 0xFFFFFFFE, 3, 4, 5, 6, 7, 8, 9, 0xFFFFFFFE] + [0xFFFFFFFF] * 118
    return bytes(header) + struct.pack("<128I", *allocation) + bytes(directory) + source + bytes(4096 - len(source))


def inspect_native_book(version, sheet_records, **limits):
    """Inspect a complete CFB Book with the declared native BIFF version."""
    from brewdoc._biff import inspect_biff_stream
    from brewdoc._compound import read_compound

    bof = {2: 0x0009, 3: 0x0209, 4: 0x0409, 5: 0x0809, 8: 0x0809}[version]
    source = record(bof, struct.pack("<HH", {5: 0x0500, 8: 0x0600}.get(version, 0), 0x10))
    data = compound_book(source + sheet_records + record(0x000A))
    context = InspectionContext(limits=limits)
    document = context.document(data, "native.xls", parent=None, location={"kind": "source"})
    streams = read_compound(data, context, document)
    book = next(item for item in streams if item["name"] == "Book")
    inspect_biff_stream(book["data"], "Book", context, document, streams=streams)
    return context


def native_formula(version, row, column, tokens, value=42):
    """Frame a formula using its original version's cell header and token count."""
    header = struct.pack("<HH", row, column) + bytes(3 if version == 2 else 2)
    payload = header + struct.pack("<d", value) + bytes(1 if version == 2 else 2)
    payload += bytes(4) if version >= 5 else b""
    payload += struct.pack("<B" if version == 2 else "<H", len(tokens)) + tokens
    return record({3: 0x0206, 4: 0x0406}.get(version, 6), payload)


def biff8_name(label, tokens, extra=b""):
    """Frame the separate rgce and rgcb source fields of one native defined name."""
    header = struct.pack("<HBBHHHBBBB", 0, 0, len(label), len(tokens), 0, 0, 0, 0, 0, 0)
    return record(0x0018, header + b"\0" + label.encode() + tokens + extra)


@pytest.mark.parametrize("version", [0x0700, 0x0007])
def test_unknown_bof_version_retains_framed_source_without_admitting_guessed_semantics(version):
    # GIVEN an unsupported modern BOF value followed by bytes shaped like a known formula
    source = stream(native_formula(8, 4, 2, b"\x1e\x2a\0"), version=version)
    # WHEN physical records are inventoried before semantic dialect admission
    context = inspect(source)
    # THEN no modern cell or formula is fabricated from the unknown source version
    assert cell_details(context) == [], "an unknown BOF version must not select the modern cell layout"
    assert [(key[1], value["status"]) for key, value in context.coverage.items()
            if key[1] in ("cells", "formulas", "macros")] == [
        ("cells", "unavailable"), ("formulas", "unavailable"), ("macros", "unavailable")], "unsupported dialect semantics cannot be complete"
    failures = [item for item in context.findings if item["kind"] == "unsupported-biff-version"]
    assert [(item["location"]["record_offset"], item["details"]["version"])
            for item in failures] == [(0, version), (46, version)], "every original unsupported BOF must remain located"
    assert context.payloads[failures[0]["artifact"]] == source[:20], "the exact framed unsupported BOF must remain retrievable"
    assert [item["location"]["record_type"] for item in context.components if item["kind"] == "biff-record"] == [
        "0x0809", "0x0085", "0x000A", "0x0809", "0x0006", "0x000A"], "semantic refusal must retain the complete original physical record inventory"


@pytest.mark.parametrize("version", [5, 8])
@pytest.mark.parametrize("index,label", [(0, "Consolidate_Area"), (3, "Extract"), (4, "Database"),
                                        (5, "Criteria"), (6, "Print_Area"), (8, "Recorder"),
                                        (9, "Data_Form"), (12, "Sheet_Title")])
def test_builtin_name_indexes_and_text_aliases_resolve_identically_for_native_consumers(version, index, label):
    # GIVEN indexed and textual source aliases plus a native PtgName consumer
    tokens = b"\x1e\x2a\0"
    labels = (bytes((index,)), label.encode())
    headers = [struct.pack("<HBBHHHBBBB", 0x20, 0, len(value), len(tokens), 0, 1, 0, 0, 0, 0)
               for value in labels]
    names = [record(0x18, header + (b"\0" if version == 8 else b"") + value + tokens)
             for header, value in zip(headers, labels)]
    reference = b"\x23" + (struct.pack("<I", 1) if version == 8 else struct.pack("<H", 1) + bytes(12))
    # WHEN both source forms pass through their declared native version
    contexts = [inspect(stream(native_formula(version, 4, 2, reference), globals_records=name,
                               version=0x0600 if version == 8 else 0x0500)) for name in names]
    # THEN original alias identity and the referencing expression agree without placeholders
    assert [[(item["text"], item["details"]["formula"], item["details"]["scope"])
             for item in context.findings if item["kind"] == "defined-name"] for context in contexts] == [
        [(label, "42", 1)], [(label, "42", 1)]], "every documented indexed built-in must equal its permitted textual alias"
    assert [[item[1]["formula"] for item in cell_details(context)] for context in contexts] == [[label], [label]], "PtgName must use the decoded source identity in both native dialects"
    assert [[context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "defined-name"]
            for context in contexts] == [[tokens], [tokens]], "alias normalization must preserve original formula artifacts"


@pytest.mark.parametrize("version", [5, 8])
def test_unknown_builtin_index_is_located_incomplete_evidence_with_original_formula_bytes(version):
    # GIVEN a native built-in name index absent from the documented table
    tokens = b"\x1e\x2a\0"
    payload = struct.pack("<HBBHHHBBBB", 0x20, 0, 1, len(tokens), 0, 1, 0, 0, 0, 0)
    payload += (b"\0" if version == 8 else b"") + b"\xff" + tokens
    source = stream(b"", globals_records=record(0x18, payload), version=0x0600 if version == 8 else 0x0500)
    # WHEN source inspection encounters an unsupported indexed identity
    context = inspect(source)
    # THEN the diagnostic label cannot receive complete named-expression coverage
    assert {key[1]: value["status"] for key, value in context.coverage.items() if key[1] in ("names", "formulas")} == {
        "names": "partial", "formulas": "unavailable"}, "unknown built-in indexes must remain explicitly incomplete"
    assert [(item["text"], context.payloads[item["artifact"]]) for item in context.findings if item["kind"] == "defined-name"] == [
        ("builtin-name-0xFF", tokens)], "diagnostic source identity and original formula bytes must survive refusal"
    assert [(item["byte_size"], item["sha256"]) for item in context.components
            if item["kind"] == "biff-record" and item["location"]["record_offset"] == 20] == [
        (len(payload) + 4, hashlib.sha256(record(0x18, payload)).hexdigest())], "the original unknown indexed-name record must remain exactly inventoried"


@pytest.mark.parametrize("version", [2, 3, 4, 5, 8])
def test_supported_bof_dialects_keep_native_formula_semantics_complete(version):
    # GIVEN a complete CFB Book using one of the explicitly supported native BOF dialects
    tokens = b"\x1e\x2a\0"
    # WHEN semantic admission selects that dialect before decoding the original record
    context = inspect_native_book(version, native_formula(version, 4, 2, tokens))
    # THEN native semantics and original expression bytes remain available under complete coverage
    assert [(item[1]["formula"], item[1]["value"]) for item in cell_details(context)] == [("42", 42.0)], "supported BOF controls must keep their own native cell and formula layouts"
    assert {key[1]: value["status"] for key, value in context.coverage.items()
            if key[1] in ("cells", "formulas", "macros")} == {
        "cells": "complete", "formulas": "complete", "macros": "complete"}, "explicitly supported dialects must remain complete"
    assert [context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "formula"] == [tokens], "supported controls must retain exact native formula artifacts"


def test_defined_array_name_preserves_auxiliary_expression_and_complete_artifact():
    # GIVEN the native NameParsedFormula array constant and its separate auxiliary values
    tokens, extra = b"\x40" + bytes(7), b"\0\0\0\1" + struct.pack("<d", 42)
    source = stream(b"", globals_records=biff8_name("ArrayName", tokens, extra))
    # WHEN original defined-name fields are inspected without formula evaluation
    context = inspect(source)
    names = [item for item in context.findings if item["kind"] == "defined-name"]
    # THEN both the expression and every original formula byte remain retrievable
    assert [item["details"]["formula"] for item in names] == ["{42}"], "named arrays require their native rgcb bytes"
    assert [context.payloads[item["artifact"]] for item in names] == [tokens + extra], "named formula artifacts must include tokens and auxiliary source"


def test_defined_external_alias_uses_source_external_name_context():
    # GIVEN a defined name whose native NameX refers to an inspected external name
    target, label = "https://example.invalid/Book.xls", "Budget"
    supporting = struct.pack("<HH", 1, len(target)) + b"\0" + target.encode() + b"\x06\0\0Inputs"
    external = struct.pack("<HHH", 0, 1, 0) + bytes((len(label), 0)) + label.encode() + b"\0\0"
    xti, tokens = struct.pack("<HHhh", 1, 0, 0, 0), b"\x39" + struct.pack("<HI", 0, 1)
    source = stream(b"", globals_records=record(0x01AE, supporting) + record(0x0023, external)
                    + record(0x0017, xti) + biff8_name("ExternalAlias", tokens))
    # WHEN the named-expression consumer resolves the original supporting link table
    context = inspect(source)
    # THEN the existing external source identity reaches the formula decoder
    assert [(item["text"], item["details"]["formula"]) for item in context.findings if item["kind"] == "defined-name"] == [
        ("ExternalAlias", "'[https://example.invalid/Book.xls]Inputs'!Budget")], "NameX aliases must use the same source context as cell formulas"


@pytest.mark.parametrize("kind", [0x0221, 0x04BC])
def test_shared_and_array_followers_outside_definition_keep_cache_without_guessed_expression(kind):
    # GIVEN a source definition for C5:C6 and an invalid follower at C100
    anchor = b"\1" + struct.pack("<HH", 4, 2)
    prefix = struct.pack("<HHBB", 4, 5, 2, 2) + bytes(6 if kind == 0x0221 else 2)
    definition = prefix + struct.pack("<H", 3) + b"\x1e\x2a\0"
    source = stream(native_formula(8, 4, 2, anchor) + record(kind, definition)
                    + native_formula(8, 99, 2, anchor, 17))
    # WHEN passive anchor resolution checks each consuming cell's original coordinates
    context = inspect(source)
    # THEN invalid coordinates remain unresolved while their exact cache and tokens survive
    assert [(item[0], item[1]["formula"], item[1]["value"]) for item in cell_details(context)] == [
        ("'Bob''s input'!C5", "42", 42.0), ("'Bob''s input'!C100", None, 17.0)], "out-of-range followers must not inherit a valid anchor expression"
    assert [value["status"] for key, value in context.coverage.items() if key[1] == "formulas"] == ["partial"], "contradictory source membership cannot be complete"
    assert [(key[1], value["status"]) for key, value in context.coverage.items()
            if key[1] in ("cells", "macros", "external_references")] == [
        ("cells", "complete"), ("external_references", "complete"), ("macros", "complete")], "bad array membership must not invalidate unrelated inspected source capabilities"


@pytest.mark.parametrize("kind", [0x0221, 0x04BC])
def test_shared_and_array_definitions_report_missing_required_consumers(kind):
    # GIVEN a definition requiring two uses but only its first native Formula record
    anchor = b"\1" + struct.pack("<HH", 4, 2)
    suffix = bytes(6) if kind == 0x0221 else b"\0\2"
    definition = struct.pack("<HHBB", 4, 5, 2, 2) + suffix + struct.pack("<H", 3) + b"\x1e\x2a\0"
    # WHEN the native definition's declared membership is reconciled after the source scan
    context = inspect(stream(native_formula(8, 4, 2, anchor) + record(kind, definition)))
    # THEN recovered content remains available while incomplete source membership is explicit
    assert [(key[1], value["status"], value["reason"]) for key, value in context.coverage.items() if key[1] == "formulas"] == [
        ("formulas", "partial", "BIFF shared/array definition lacks required formula cells")], "absent required consumers must prevent complete formula coverage"
    assert [value["status"] for key, value in context.coverage.items() if key[1] == "macros"] == ["complete"], "a normal worksheet's missing formula does not imply incomplete macro inspection"


@pytest.mark.parametrize("kind", [0x0221, 0x04BC])
def test_xlm_missing_shared_or_array_consumers_make_macro_inspection_incomplete(kind):
    # GIVEN an XLM macro sheet whose ARRAY range or shared cUse requires an absent C6
    anchor = b"\1" + struct.pack("<HH", 4, 2)
    suffix = bytes(6) if kind == 0x0221 else b"\0\2"
    definition = struct.pack("<HHBB", 4, 5, 2, 2) + suffix + b"\3\0\x1e\x2a\0"
    # WHEN source membership is reconciled on the actual native macro sheet
    context = inspect(stream(native_formula(8, 4, 2, anchor) + record(kind, definition), sheet_type=1))
    # THEN both affected capabilities are partial while recovered caches and operands survive
    assert [(key[1], value["status"], value["reason"]) for key, value in context.coverage.items()
            if key[1] in ("formulas", "macros")] == [
        ("formulas", "partial", "BIFF shared/array definition lacks required formula cells"),
        ("macros", "partial", "BIFF shared/array definition lacks required formula cells"),
    ], "an absent required XLM formula also leaves macro inspection incomplete"
    assert [(item[1]["formula"], item[1]["value"], item[1]["cached_value_state"])
            for item in cell_details(context)] == [("42", 42.0, "present")], "the recovered XLM cell and cache must remain available"
    assert [context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "xlm-formula"] == [anchor], "the original macro operand must remain retrievable"


@pytest.mark.parametrize("duplicate", ["definition", "cell"])
def test_duplicate_array_definitions_or_consumers_keep_raw_bytes_and_refuse_ambiguous_expression(duplicate):
    # GIVEN a single-cell array with duplicated definition or duplicated native consumer
    anchor = b"\1" + struct.pack("<HH", 4, 2)
    definition = record(0x0221, struct.pack("<HHBB", 4, 4, 2, 2) + bytes(6) + b"\3\0\x1e\x2a\0")
    first = native_formula(8, 4, 2, anchor)
    additions = {"definition": definition, "cell": native_formula(8, 4, 2, anchor, 17)}
    # WHEN all original record occurrences are retained before membership resolution
    context = inspect(stream(first + definition + additions[duplicate]))
    # THEN duplicate source cells retain their caches without an invented winning expression
    expected = {"definition": [(None, 42.0)], "cell": [(None, 42.0), (None, 17.0)]}
    assert [(item[1]["formula"], item[1]["value"]) for item in cell_details(context)] == expected[duplicate], "ambiguous duplicate records must not silently select one formula"
    assert [context.payloads[item["artifact"]] for item in context.findings if item["kind"] == "formula"] == [anchor] * len(expected[duplicate]), "every original anchor operand must remain retrievable"


def test_valid_sparse_shared_range_uses_declared_count_instead_of_rectangle_area():
    # GIVEN two shared consumers at the boundaries of C5:C100 with unused interior cells
    anchor = b"\1" + struct.pack("<HH", 4, 2)
    definition = record(0x04BC, struct.pack("<HHBBBBH", 4, 99, 2, 2, 0, 2, 3) + b"\x1e\x2a\0")
    # WHEN the source cUse count and both inclusive boundaries are inspected
    context = inspect(stream(native_formula(8, 4, 2, anchor) + definition + native_formula(8, 99, 2, anchor)))
    # THEN absent rectangle interiors remain valid for a shared rather than array formula
    assert [item[1]["formula"] for item in cell_details(context)] == ["42", "42"], "shared range boundary cells must resolve normally"
    assert [value["status"] for key, value in context.coverage.items() if key[1] == "formulas"] == ["complete"], "shared formulas require cUse consumers rather than every rectangle position"


def test_legacy_name_descriptions_do_not_become_formula_auxiliary_bytes():
    # GIVEN a BIFF5 Lbl with separate declared menu, description, help and status strings
    tokens = b"\x1e\x2a\0"
    payload = struct.pack("<HBBHHHBBBB", 0, 0, 4, len(tokens), 0, 0, 4, 4, 1, 1)
    source = stream(b"", globals_records=record(0x0018, payload + b"Name" + tokens + b"menudeschs"), version=0x0500)
    # WHEN version-specific source framing separates expression and trailing metadata
    context = inspect(source)
    names = [item for item in context.findings if item["kind"] == "defined-name"]
    # THEN descriptive fields survive and the dedicated formula artifact remains exact
    assert [(item["details"]["formula"], item["details"]["menu"], item["details"]["description"],
             item["details"]["help_topic"], item["details"]["status_bar"])
            for item in names] == [("42", "menu", "desc", "h", "s")], "nonformula strings must follow their original legacy lengths"
    assert [context.payloads[item["artifact"]] for item in names] == [tokens], "name descriptions are not formula auxiliary data"


@pytest.mark.parametrize("version", [2, 3, 4, 5, 8])
def test_native_array_definition_resolves_version_specific_source_fields(version):
    # GIVEN native array records with different flags and formula-count offsets
    anchor = b"\1" + struct.pack("<HB" if version == 2 else "<HH", 4, 2)
    definition = struct.pack("<HHBB", 4, 5, 2, 2) + bytes(1 if version == 2 else 2)
    definition += bytes(4) if version >= 5 else b""
    definition += struct.pack("<B" if version == 2 else "<H", 3) + b"\x1e\x2a\0"
    source = (native_formula(version, 4, 2, anchor) + record(0x0021 if version == 2 else 0x0221, definition)
              + native_formula(version, 5, 2, anchor, 7))
    # WHEN the original Book stream is inspected independently of cached rendering
    context = inspect_native_book(version, source)
    # THEN each array cell retains its literal expression and source cache
    assert [(item["text"], item["location"]["cell"]) for item in context.findings if item["kind"] == "formula"] == [("42", "C5"), ("42", "C6")], "old ARRAY layouts must resolve their own native anchor"
    assert [(item["details"]["value"], item["details"]["cached_value_state"]) for item in context.findings if item["kind"] == "cell"] == [(42.0, "present"), (7.0, "present")], "array inspection must preserve cached values"


@pytest.mark.parametrize("version,kind,flags,inputs,expected", [
    (2, 0x0036, 0, (0, 0), "TABLE(,$A$1)"),
    (2, 0x0036, 0x0100, (0, 0), "TABLE($A$1,)"),
    (2, 0x0037, 0, (0, 0, 1, 0), "TABLE($A$1,$A$2)"),
    (3, 0x0236, 4, (0, 0, 0, 0), "TABLE($A$1,)"),
    (4, 0x0236, 0, (0, 0, 0, 0), "TABLE(,$A$1)"),
    (5, 0x0236, 8, (0, 0, 1, 0), "TABLE($A$1,$A$2)"),
    (8, 0x0236, 8, (0, 0, 1, 0), "TABLE($A$1,$A$2)"),
    (8, 0x0236, 0x14, (65535, 65535, 0, 0), "TABLE(#REF!,)"),
])
def test_native_table_operation_retains_source_inputs_and_caches(version, kind, flags, inputs, expected):
    # GIVEN a native tTbl operand and original data-table definition
    anchor = b"\2" + struct.pack("<HB" if version == 2 else "<HH", 4, 2)
    definition = struct.pack("<HHBBH", 4, 5, 2, 3, flags) + struct.pack("<" + "H" * len(inputs), *inputs)
    source = (native_formula(version, 4, 2, anchor) + record(kind, definition)
              + native_formula(version, 4, 3, anchor, 17) + native_formula(version, 5, 2, anchor, 19)
              + native_formula(version, 5, 3, anchor, 7))
    # WHEN inspection joins the source definition without calculating the operation
    context = inspect_native_book(version, source)
    formulas = [item for item in context.findings if item["kind"] == "formula"]
    cells = [item for item in context.findings if item["kind"] == "cell"]
    # THEN expression, inputs, flags, location and independent caches survive
    assert [item["text"] for item in formulas] == [expected] * 4, "native table operations must resolve to static source expressions"
    assert [item["details"]["representation"] for item in formulas] == ["native_data_table"] * 4, "table expressions must distinguish native operations from ordinary RPN"
    assert [item["details"]["value"] for item in cells] == [42.0, 17.0, 19.0, 7.0], "inspection must not recalculate source caches"
    assert [item["details"]["data_table"]["flags"] for item in cells] == [flags] * 4, "original operation flags must remain available"
    assert [item["details"]["data_table"]["range"] for item in cells] == ["C5:D6"] * 4, "table source bounds must stay addressed"
    assert context.coverage[("document/000001", "formulas")]["status"] == "complete", "valid native table operations must complete their source formula coverage"


def test_biff2_row_preserves_native_height_defaults_without_inventing_hidden_bits():
    # GIVEN a BIFF2 ROW whose source layout has no hidden or outline flags
    payload = struct.pack("<HHHHHBH", 4, 2, 3, 0, 0, 1, 0x1234) + b"\x01\x02\x03" + struct.pack("<H", 7)
    # WHEN the original Book stream is inspected
    context = inspect_native_book(2, record(0x0008, payload))
    rows = [item for item in context.findings if item["kind"] == "row"]
    # THEN source absence is explicit and native height and style evidence remain exact
    assert [(item["location"]["row"], item["details"]) for item in rows] == [(5, {
        "first_column": "C", "last_column_exclusive": "D", "height_twips": 0,
        "default_height": False, "has_default_attributes": True, "cell_attributes_hex": "010203",
        "xf_index": 7, "first_cell_relative_offset": 0x1234, "hidden": None,
        "hidden_state": "not_stored", "outline_level": None, "outline_state": "not_stored",
    })], "BIFF2 row semantics must follow its native layout"


@pytest.mark.parametrize("version", [2, 3, 4])
def test_legacy_sheet_context_preserves_paired_unknown_bytes_and_independent_targets(version):
    # GIVEN documented wrapper widths whose payload binding fields remain undocumented
    prefix, suffix = bytes(range(7 if version == 2 else 10)), bytes(range(3 if version == 2 else 4))
    inner = bytes.fromhex("4404c002")
    tokens = b"\x1a" + prefix + inner + b"\x1b" + suffix
    source = record(0x0017, b"\x0bexample.xls") + native_formula(version, 4, 2, tokens)
    # WHEN the native reader recovers inner syntax without guessing external indexes
    context = inspect_native_book(version, source)
    cells = [item for item in context.findings if item["kind"] == "cell"]
    formulas = [item for item in context.findings if item["kind"] == "formula"]
    # THEN exact framing and independent targets survive beside unavailable binding evidence
    assert [(item["details"]["formula"], item["details"]["recovered_inner_expression"]) for item in cells] == [(None, "C5")], "unknown sheet binding must not become a resolved local reference"
    assert formulas[0]["details"]["legacy_sheet_contexts"] == [{
        "header_hex": prefix.hex(), "footer_hex": suffix.hex(), "inner_token_hex": inner.hex(),
        "token_offset": 0, "binding_state": "unavailable", "binding_reason": "undocumented legacy sheet-context fields",
    }], "undocumented bytes must remain retrievable as typed unknown source context"
    assert [item["target"] for item in context.findings if item["kind"] == "legacy-supporting-link"] == ["example.xls"], "unknown formula binding must not discard independently decoded link targets"
    assert context.payloads[formulas[0]["artifact"]] == tokens, "formula artifacts must retain exact source wrapper bytes"
    assert [context.coverage[("document/000001", capability)]["status"] for capability in ("formulas", "external_references")] == ["partial", "partial"], "unknown binding may never report complete formula inspection"


@pytest.mark.parametrize("version,source,reason", [
    (2, record(0x0008, bytes(12)), "truncated legacy BIFF field"),
    (2, record(0x0021, struct.pack("<HHBB", 4, 5, 2, 2) + b"\0\3\x1e"), "truncated legacy BIFF field"),
    (3, record(0x0236, bytes(16)), "invalid BIFF data-table range"),
    (4, native_formula(4, 4, 2, b"\2\4\0\2\0"), "unresolved BIFF data-table anchor C5"),
    (5, record(0x0236, struct.pack("<HHBBHHHHH", 4, 5, 2, 3, 0, 0, 256, 0, 0)), "invalid BIFF data-table input coordinate"),
    (8, native_formula(8, 4, 2, b"\2\4\0\2\0\0"), "invalid BIFF data-table operand length"),
])
def test_native_legacy_definition_damage_is_located_and_never_complete(version, source, reason):
    # GIVEN native source whose row, array or table definition cannot be fully read
    assert len(source) >= 4, "the regression must contain an original framed source record"
    # WHEN scanning accounts for the damaged native evidence
    context = inspect_native_book(version, source)
    coverage = context.coverage[("document/000001", "formulas")]
    # THEN an explicit source location and reason survive without a false complete report
    assert (coverage["status"] in ("partial", "unavailable"), reason in coverage["reason"]) == (True, True), "damaged native definitions must remain explicit coverage failures"
    assert [location["stream"] for location in coverage["locations"]] == ["Book"], "the failed capability must identify its original stream"


def test_native_table_artifacts_locations_and_repeated_reports_are_exact():
    # GIVEN two cached table cells and one literal original operation definition
    tokens = b"\2\4\0\2\0"
    definition = struct.pack("<HHBBHHHHH", 4, 5, 2, 3, 8, 0, 0, 1, 0)
    source = (native_formula(8, 4, 2, tokens) + record(0x0236, definition)
              + native_formula(8, 4, 3, tokens, 17) + native_formula(8, 5, 2, tokens, 19)
              + native_formula(8, 5, 3, tokens, 7))
    # WHEN two independent native inspections account for the same original bytes
    first, second = inspect_native_book(8, source), inspect_native_book(8, source)
    operation = next(item for item in first.findings if item["kind"] == "data-table")
    # THEN exact payload bytes and addressed source fields stay deterministic
    assert (first.findings, first.coverage, first.artifacts, first.payloads) == (second.findings, second.coverage, second.artifacts, second.payloads), "repeated reports must retain deterministic ordering, identities and bytes"
    assert first.payloads[operation["artifact"]] == definition, "TABLE source artifacts must retain every original definition byte"
    assert (operation["location"]["record_offset"], operation["details"]["first_input"], operation["details"]["second_input"]) == (39, [0, 0], [1, 0]), "source definition location and raw input indexes must remain exact"
    assert first.coverage[("document/000001", "formulas")]["status"] == "complete", "fully supported native table syntax must finish formula coverage"


def test_native_legacy_record_exhaustion_remains_explicit_and_deterministic():
    # GIVEN a CFB BIFF2 row stream with its shared allowance exhausted at the ROW record
    from brewdoc._compound import read_compound

    source = record(0x0008, struct.pack("<HHHHHBH", 4, 2, 3, 300, 0, 0, 0))
    data = compound_book(record(0x0009, struct.pack("<HH", 0, 0x10)) + source + record(0x000A))
    precondition = InspectionContext()
    document = precondition.document(data, "native.xls", parent=None, location={"kind": "source"})
    read_compound(data, precondition, document)
    assert precondition.consumed["records"] == 19, "current CFB allocation and source inventory must precede BIFF with nineteen record charges"
    # WHEN the compound and native readers consume the same shared allowance
    first, second = inspect_native_book(2, source, records=20), inspect_native_book(2, source, records=20)
    sufficient = inspect_native_book(2, source, records=25)
    # THEN exhaustion cannot become a complete empty report
    assert (first.exhausted, first.coverage, first.payloads) == (second.exhausted, second.coverage, second.payloads), "shared-budget failure evidence must repeat exactly"
    assert [(item["location"]["record_type"], item["location"]["record_offset"])
            for item in first.components if item["kind"] == "biff-record"] == [("0x0009", 0)], "the twentieth admitted record charge must inventory BOF before ROW is refused"
    assert first.exhausted == [{"limit": "records", "maximum": 20, "location": {
        "kind": "biff-record", "stream": "Book", "record_offset": 8, "record_type": "0x0008",
    }}], "native inspection must report its exact exhausted cumulative allowance and source location"
    assert first.coverage[("document/000001", "hidden_structures")]["status"] == "unavailable", "unreached row evidence must not be reported complete"
    assert sufficient.consumed["records"] == 25, "the complete native source must account for all twenty-five record operations"
    assert [(item["location"]["row"], item["details"]["first_column"], item["details"]["last_column_exclusive"])
            for item in sufficient.findings if item["kind"] == "row"] == [(5, "C", "D")], "a sufficient allowance must recover the exact row and source column boundaries"
    assert sufficient.coverage[("document/000001", "hidden_structures")]["status"] == "complete", "the sufficient allowance must finish native structure inspection"


@pytest.mark.parametrize("version", [3, 4])
def test_native_legacy_row_keeps_source_hidden_and_outline_bits(version):
    # GIVEN a native ROW with outline level three and hidden bit five
    payload = struct.pack("<HHHHHHI", 4, 2, 3, 300, 0, 0, 0x123)
    # WHEN the original CFB Book is inspected with its declared version
    context = inspect_native_book(version, record(0x0208, payload))
    # THEN source flags remain different from the BIFF2 layout without such fields
    assert [(item["location"]["row"], item["details"]) for item in context.findings if item["kind"] == "row"] == [(5, {
        "hidden": True, "outline_level": 3,
    })], "BIFF3 and BIFF4 ROW records must retain their actual hidden and outline bits"


@pytest.mark.parametrize("tail,reason", [
    (b"", "BIFF data-table range lacks required formula cells"),
    (record(0x0236, struct.pack("<HHBBHHHHH", 4, 5, 2, 3, 8, 0, 0, 1, 0)), "duplicate BIFF data-table anchor"),
])
def test_native_table_missing_cells_and_duplicate_anchors_remain_explicit(tail, reason):
    # GIVEN a declared four-cell table with only its first formula or a repeated definition
    definition = struct.pack("<HHBBHHHHH", 4, 5, 2, 3, 8, 0, 0, 1, 0)
    source = native_formula(8, 4, 2, b"\2\4\0\2\0") + record(0x0236, definition) + tail
    # WHEN source recovery retains all original records without overwriting evidence
    context = inspect_native_book(8, source)
    coverage = context.coverage[("document/000001", "formulas")]
    # THEN malformed table structures cannot receive complete coverage
    assert (coverage["status"], reason in coverage["reason"]) == ("partial", True), "missing formula cells and repeated native definitions must stay explicit"
    assert [item["details"]["value"] for item in context.findings if item["kind"] == "cell"] == [42.0], "malformed table declarations must not destroy the source cache"


@pytest.mark.parametrize("version", [3, 5, 8])
@pytest.mark.parametrize("cache,string_record,expected", [
    (bytes.fromhex("020007000000ffff"), False, ("#DIV/0!", "error", "present", "#DIV/0!")),
    (bytes.fromhex("000000000000ffff"), True, ("café", "string", "present", None)),
    (bytes.fromhex("000000000000ffff"), False, (None, "string", "missing", None)),
])
def test_native_table_retains_errors_strings_and_missing_source_caches(version, cache, string_record, expected):
    # GIVEN a one-cell native table whose source cache has a specific stored state
    tokens = b"\2\4\0\2\0"
    formula = native_formula(version, 4, 2, tokens).replace(struct.pack("<d", 42), cache, 1)
    definition = struct.pack("<HHBBHHHHH", 4, 4, 2, 2, 4, 0, 0, 0, 0)
    strings = {3: b"\4\0caf\xe9", 5: b"\4\0caf\xe9", 8: b"\4\0\0caf\xe9"}
    suffix = {True: record(0x0207, strings[version]), False: b""}[string_record]
    # WHEN the definition appears between the formula and its optional STRING cache
    context = inspect_native_book(version, formula + record(0x0236, definition) + suffix)
    cells = [item["details"] for item in context.findings if item["kind"] == "cell"]
    # THEN the table operation does not erase distinctions in original cached evidence
    assert [(item["value"], item["value_type"], item["cached_value_state"], item["error"]) for item in cells] == [expected], "native TABLE inspection must retain typed errors, decoded strings and missing source results"
    assert [item["formula"] for item in cells] == ["TABLE($A$1,)"], "cache state must not interfere with the native source operation"


@pytest.mark.parametrize("version,bof_id,formula_id", [(2, 0x0009, 6), (3, 0x0209, 0x0206),
                                                      (4, 0x0409, 0x0406), (5, 0x0809, 6)])
def test_native_legacy_biff_cells_keep_source_reference_widths_and_error_values(version, bof_id, formula_id):
    # GIVEN native version-specific formula and error records in an original workbook stream
    header = struct.pack("<HH", 0x0500 if version == 5 else 0, 0x10)
    cell = struct.pack("<HH", 4, 2) + bytes(3 if version == 2 else 2)
    tokens = bytes.fromhex("4404c002")
    formula = cell + struct.pack("<d", 42) + bytes(1 if version == 2 else 2) + (bytes(4) if version == 5 else b"")
    formula += struct.pack("<B" if version == 2 else "<H", len(tokens)) + tokens
    error = cell + b"\7\1"
    source = record(bof_id, header) + record(formula_id, formula) + record(0x0005 if version == 2 else 0x0205, error) + record(0x000A)
    # WHEN the native scanner selects the source version's passive decoder
    from brewdoc._compound import read_compound
    from brewdoc._biff import inspect_biff_stream

    data = compound_book(source)
    context = InspectionContext()
    document = context.document(data, "legacy.xls", parent=None, location={"kind": "source"})
    streams = read_compound(data, context, document)
    book = next(item for item in streams if item["name"] == "Book")
    inspect_biff_stream(book["data"], "Book", context, document, streams=streams)
    # THEN old row flags remain C5 and error bytes remain typed
    assert [(item["text"], item["details"]["token_hex"]) for item in context.findings if item["kind"] == "formula"] == [
        ("C5", "4404c002")], "legacy formulas must decode their original field widths"
    assert [item[1]["error"] for item in cell_details(context)] == [None, "#DIV/0!"], "source errors must survive all admitted BIFF versions"
    assert [(item["location"]["sheet"], item["location"]["qualified_cell"], item["location"]["cell"],
             item["location"]["sheet_name_state"], item["location"]["display_sheet"])
            for item in context.findings if item["kind"] == "cell"] == [
        (None, None, "C5", "not_stored", "[unnamed BIFF sheet at byte 0]"),
        (None, None, "C5", "not_stored", "[unnamed BIFF sheet at byte 0]"),
    ], "a display identity must not become an original source sheet name"
    assert [(item["details"]["hidden"], item["details"]["hidden_state"])
            for item in context.findings if item["kind"] == "sheet"] == [(None, "not_stored")], "absent source visibility must not be invented as visible"


def test_biff_frtwrapper_exposes_inner_chart_text_with_both_physical_offsets():
    # GIVEN a source chart label wrapped as a future record
    inner = record(0x100D, b"\0\0\5\0Title")
    wrapped = struct.pack("<HH", 0x0851, 0) + inner
    data = stream(record(0x0851, wrapped), sheet_type=2)
    # WHEN the wrapper is inspected independently of its chart rendering
    context = inspect(data)
    labels = [item for item in context.findings if item["kind"] == "chart-text"]
    # THEN source text and physical wrapper provenance remain exact
    assert [item["text"] for item in labels] == ["Title"], "wrapped chart text must be recovered"
    assert [item["location"]["record_offset"] - item["location"]["wrapper_offset"] for item in labels] == [8], "the inner record header begins after outer header and FrtHeaderOld"


def test_biff_chart_xml_and_compressed_properties_cross_native_future_records():
    # GIVEN original chart XML and typed properties split across future continuation headers
    xml = b'<a:txPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Hidden</a:t></a:txPr>'
    payload = struct.pack("<H", 0x08A5) + bytes(14) + struct.pack("<I", len(xml)) + xml
    compressor = zlib.compressobj(wbits=-15)
    compressed = compressor.compress(xml) + compressor.flush()
    chain = bytes((0, 0, 2, 0)) + struct.pack("<BBHI", 7, 0, 0x33, len(compressed)) + compressed
    properties = struct.pack("<H", 0x089E) + bytes(10) + struct.pack("<I", len(chain)) + chain + bytes(4)
    data = stream(record(0x08A5, payload[:29]) + record(0x087F, struct.pack("<H", 0x087F) + bytes(10) + payload[29:])
                  + record(0x089E, properties[:25]) + record(0x089F, struct.pack("<H", 0x089F) + bytes(10) + properties[25:]))
    # WHEN native record fragments are assembled without their continuation framing
    context = inspect(data)
    # THEN both literal XML sources survive as exact artifacts and expose hidden text
    assert [context.payloads[item["key"]] for item in context.artifacts if item["kind"] == "chart-xml"] == [xml, xml], "chart XML artifacts must contain exact source content after decoding only its declared compression"
    assert [item["text"] for item in context.findings if item["kind"] == "chart-text"] == ["Hidden", "Hidden"], "continued and compressed source alternatives must both be inspected"


def test_biff5_native_names_local_external_links_hidden_sheet_and_cached_string():
    # GIVEN a hidden BIFF5 sheet with a name, local external name and cached string formula
    label = b"Budget"
    name = record(0x0018, struct.pack("<HBBHHHBBBB", 1, 0, len(label), 3, 0, 0, 0, 0, 0, 0) + label + b"\x1e\x2a\0")
    target = b"book.xls"
    externals = record(0x0017, bytes((len(target),)) + target)
    externals += record(0x0023, struct.pack("<HI", 0, 0) + b"\4Rate\0\0")
    tokens = b"\x39" + struct.pack("<h", 1) + bytes(8) + struct.pack("<H", 1) + bytes(12)
    cached = b"\0\0\0\0\0\0\xff\xff"
    formula = struct.pack("<HHH", 4, 2, 0) + cached + bytes(6) + struct.pack("<H", len(tokens)) + tokens
    bof = record(0x0809, struct.pack("<HH", 0x0500, 5) + bytes(12))
    bound = record(0x0085, struct.pack("<IBB", 0, 1, 0) + b"\6Inputs")
    prefix = bof + name + bound + record(0x000A)
    bound = record(0x0085, struct.pack("<IBB", len(prefix), 1, 0) + b"\6Inputs")
    data = bof + name + bound + record(0x000A) + record(0x0809, struct.pack("<HH", 0x0500, 0x10) + bytes(12))
    data += externals + record(6, formula) + record(0x0207, b"\4\0caf\xe9") + record(0x000A)
    # WHEN native source tables resolve the exact local external index
    context = inspect(data)
    # THEN names, cached text, visibility and external scopes remain source-derived
    assert [(item["location"]["qualified_cell"], item["text"]) for item in context.findings if item["kind"] == "formula"] == [
        ("'Inputs'!C5", "'book.xls'!Rate")], "BIFF5 local external names must resolve without BIFF8 padding assumptions"
    assert [(item[1]["value"], item[1]["cached_value_state"]) for item in cell_details(context)] == [("café", "present")], "legacy cached strings must retain the declared codepage"
    assert [(item["text"], item["details"]["formula"]) for item in context.findings if item["kind"] == "defined-name"] == [("Budget", "42")], "legacy defined names must preserve source formulas"
    assert [item["details"]["hidden"] for item in context.findings if item["kind"] == "sheet"] == ["hidden"], "original hidden visibility must survive"


def test_biff4_sheetheader_preserves_real_name_without_inventing_visibility():
    # GIVEN a BIFF4 source sheet header preceding its native worksheet substream
    child = record(0x0409, struct.pack("<HH", 0, 0x10))
    child += record(0x0203, struct.pack("<HHHd", 4, 2, 0, 42)) + record(0x000A)
    source = record(0x0409, struct.pack("<HH", 0, 0x100))
    source += record(0x008F, struct.pack("<I", len(child)) + b"\6Inputs") + child + record(0x000A)
    # WHEN source identities are read from SHEETHDR rather than generated display aliases
    context = inspect(source)
    # THEN the actual name qualifies addresses while absent visibility remains absent
    assert [item[0] for item in cell_details(context)] == ["'Inputs'!C5"], "the original header name must qualify its cell"
    assert [(item["details"]["hidden"], item["details"]["hidden_state"])
            for item in context.findings if item["kind"] == "sheet"] == [(None, "not_stored")], "SHEETHDR does not encode a visibility bit"


def test_biff_xmltk_double_preserves_exact_numeric_override():
    # GIVEN a logarithmic-axis maximum stored as an XmlTkDouble
    chain = bytes((0, 0, 1, 0)) + struct.pack("<BBHI d", 3, 0, 0x55, 0, 123.5)
    payload = struct.pack("<H", 0x089E) + bytes(10) + struct.pack("<I", len(chain)) + chain + bytes(4)
    # WHEN the typed source property is inspected without chart rendering
    context = inspect(stream(record(0x089E, payload), sheet_type=2))
    # THEN the exact source numeric value and token identity remain available
    assert [(item["location"]["property_tag"], item["details"]["value"])
            for item in context.findings if item["kind"] == "chart-property-number"] == [(0x55, 123.5)], "XmlTkDouble must preserve its eight-byte source value"


def cell_details(context):
    """Return source-ordered cell payloads with exact qualified addresses."""
    return [(item["location"]["qualified_cell"], item["details"])
            for item in context.findings if item["capability"] == "cells" and item["kind"] == "cell"]


def test_biff_comment_text_joins_encoding_and_formatting_segments_at_source_cell():
    # GIVEN a cell note linked to one rich text object with two character segments
    cmo = record(0x0015, struct.pack("<HHH", 25, 7, 0) + b"\0" * 12) + record(0)
    txo = struct.pack("<HH", 0x12, 0) + b"\0" * 6 + struct.pack("<HHH", 3, 16, 0) + b"\0\0"
    runs = struct.pack("<HHIHHI", 0, 2, 0, 3, 0, 0)
    note = struct.pack("<HHHH", 4, 2, 2, 7) + struct.pack("<HB", 3, 0) + b"Ann\0"
    data = stream(record(0x005D, cmo) + record(0x01B6, txo) + record(0x003C, b"\0A")
                  + record(0x003C, b"\1" + "βC".encode("utf-16le"))
                  + record(0x003C, runs[:8]) + record(0x003C, runs[8:]) + record(0x001C, note))
    # WHEN source records are inspected without opening controls
    context = inspect(data)
    comments = [item for item in context.findings if item["kind"] == "comment"]
    # THEN author, content and original address survive independently of rendering
    assert [(item["location"]["qualified_cell"], item["text"], item["details"]["author"])
            for item in comments] == [("'Bob''s input'!C5", "AβC", "Ann")], "the note must resolve its exact object and cell"


def test_biff_imdata_artifact_excludes_native_wrapper_and_tracks_continued_bytes():
    # GIVEN an exact DIB payload split across an IMDATA and CONTINUE record
    image = struct.pack("<IHHHH", 12, 1, 1, 1, 24) + b"\x11\x22\x33\0"
    data = stream(record(0x007F, struct.pack("<HHI", 9, 1, len(image)) + image[:9])
                  + record(0x003C, image[9:]))
    # WHEN inspection retains the native image bytes
    context = inspect(data)
    images = [item for item in context.findings if item["kind"] == "legacy-image"]
    # THEN the artifact contains the image itself and its physical source segments
    assert [context.payloads[item["artifact"]] for item in images] == [image], "image artifacts must exclude BIFF headers"
    assert [item["details"]["sha256"] for item in images] == [hashlib.sha256(image).hexdigest()], "image hashes must identify exact source bytes"


def test_biff_chart_records_preserve_labels_formulas_and_cache_without_sheet_cells():
    # GIVEN a chart with literal label, source formula and cached error
    label = record(0x100D, b"\0\0\5\0Title")
    formula = b"\x3a" + struct.pack("<HHH", 0, 4, 2)
    brai = record(0x1051, struct.pack("<BBHHH", 1, 2, 0, 0, len(formula)) + formula)
    external = record(0x01AE, struct.pack("<HH", 1, 0x0401)) + record(0x0017, struct.pack("<HHHH", 1, 0, 0, 0))
    data = stream(label + brai + record(0x1065, b"\1\0")
                  + record(0x0205, struct.pack("<HHHBB", 0, 0, 0, 7, 1)),
                  globals_records=external, sheet_type=2)
    # WHEN chart data is inspected separately from worksheet values
    context = inspect(data)
    # THEN source identities and errors survive with no invented worksheet address
    assert [(item["kind"], item["text"]) for item in context.findings
            if item["kind"] in ("chart-text", "chart-formula")] == [
        ("chart-text", "Title"), ("chart-formula", "'Bob''s input'!$C$5")], "chart labels and source formulas must be recovered"
    assert cell_details(context) == [], "chart cache positions must not become worksheet addresses"
    assert [item["details"]["error"] for item in context.findings if item["kind"] == "chart-cache"] == ["#DIV/0!"], "chart errors must remain typed"


@pytest.mark.parametrize("native", [
    record(0x007F, struct.pack("<HHI", 9, 1, 99) + b"short"),
    record(0x01B6, b"\0" * 10 + struct.pack("<HHH", 2, 16, 0)) + record(0x003C, b"\1A"),
    record(0x01B6, b"\0" * 10 + struct.pack("<HHH", 1, 16, 0)) + record(0x003C, b"\0A") + record(0x003C, b"short"),
])
def test_biff_malformed_image_or_text_never_receives_complete_coverage(native):
    # GIVEN a truncated image, UTF-16 code unit or formatting-run source
    data = stream(native)
    # WHEN inspection processes each admitted native record
    context = inspect(data)
    # THEN failure evidence remains sticky despite final stream completion
    assert [value["status"] for key, value in context.coverage.items() if key[1] == "text"] == ["unavailable"], "malformed content must remain explicitly incomplete"


@pytest.mark.corpus
def test_public_xls_chart_retains_source_formulas_and_exact_cache_positions():
    # GIVEN the unchanged public chart sheet corpus with two three-point series
    from brewdoc._compound import read_compound
    from brewdoc._biff import inspect_biff_stream

    path = Path(__file__).parent / "fixtures/xls/calamine-any_sheets.xls"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == "efb61c9a60b826b18b251cd74bb80819fcf8eb566e74ef6130709dcd2f7e0590", "the chart source must match the licensed corpus"
    # WHEN native chart records are inspected through the existing compound inventory
    context = InspectionContext()
    document = context.document(data, path.name, parent=None, location={"kind": "source"})
    streams = read_compound(data, context, document)
    workbook = next(item for item in streams if item["name"] == "Workbook")
    inspect_biff_stream(workbook["data"], workbook["name"], context, document, streams=streams)
    # THEN exact chart references and cache coordinates survive independently of rendering
    assert [item["text"] for item in context.findings if item["kind"] == "chart-formula" and item["text"]] == [
        "'Visible'!$A$1:$A$3", "'Visible'!$B$1:$B$3"], "the native chart must retain both original source ranges"
    assert [(item["location"]["chart_offset"], item["details"]["cache_index"], item["details"]["point_index"],
             item["details"]["series_index"], item["details"]["value"])
            for item in context.findings if item["kind"] == "chart-cache"] == [
        (16896, 1, 0, 0, 1.0), (16896, 1, 0, 1, 2.0), (16896, 1, 1, 0, 3.0),
        (16896, 1, 1, 1, 4.0), (16896, 1, 2, 0, 5.0), (16896, 1, 2, 1, 6.0),
    ], "cache points must use source chart and series positions"


def test_biff_cells_keep_offset_error_blank_formula_cache_and_merge():
    # GIVEN explicit C5 error, AA100 formula, metadata blank and a merged range
    cells = record(0x0205, struct.pack("<HHHBB", 4, 2, 0, 7, 1))
    tokens = b"\x1e\x2a\x00"
    formula = struct.pack("<HHH", 99, 26, 0) + struct.pack("<d", 42) + b"\x00" * 6
    cells += record(0x0006, formula + struct.pack("<H", len(tokens)) + tokens)
    cells += record(0x0201, struct.pack("<HHH", 5, 2, 0))
    cells += record(0x00E5, struct.pack("<HHHHH", 1, 4, 5, 2, 3))
    data = stream(cells)
    assert data.count(tokens) == 1, "fixture must retain the exact integer formula token bytes"
    # WHEN every source record is inspected without calamine coercion
    context = inspect(data)
    # THEN errors and explicit blanks remain distinct and the sparse formula keeps AA100
    assert [(address, item["value"], item["value_type"], item["formula"],
             item["cached_value_state"], item["error"], item["merge_range"])
            for address, item in cell_details(context)] == [
        ("'Bob''s input'!C5", "#DIV/0!", "error", None, "not_formula", "#DIV/0!", "C5:D6"),
        ("'Bob''s input'!AA100", 42.0, "number", "42", "present", None, None),
        ("'Bob''s input'!C6", None, "blank", None, "blank", None, "C5:D6"),
    ], "native cell evidence must preserve addresses, types, caches and merge metadata"


@pytest.mark.parametrize(("tail", "reason"), [
    (b"\x03", "truncated BIFF record header"),
    (struct.pack("<HH", 0x0203, 12) + b"x", "truncated BIFF record payload"),
    (record(0x7777, b"unknown"), "unsupported BIFF record 0x7777"),
    (record(0x002F, b"encrypted"), "encrypted BIFF content"),
])
def test_biff_malformed_unknown_and_encrypted_records_cannot_complete_empty(tail, reason):
    # GIVEN a source whose valid prefix does not account for its final record
    data = stream(b"") + tail
    assert data.endswith(tail), "fixture must contain the exact unaccounted source bytes"
    # WHEN passive scanning reaches the malformed or unsupported evidence
    context = inspect(data)
    # THEN cells and formula coverage retain a located explicit failure
    assert [(context.coverage[("document/000001", capability)]["status"],
             reason in context.coverage[("document/000001", capability)]["reason"])
            for capability in ("cells", "formulas")] == [
        ("unavailable", True), ("unavailable", True),
    ], "unreadable binary semantics must never become a complete empty report"


def test_biff_hidden_xlm_names_external_links_and_original_target():
    # GIVEN hidden XLM cells, an auto-run name, a supporting link and a URL moniker
    tokens = b"\x1e\x07\x00"
    label = struct.pack("<HBBHHHBBBB", 0x000A, 0, 9, len(tokens), 0, 0, 0, 0, 0, 0)
    label += b"\x00Auto_Open" + tokens
    target = "https://example.invalid/hidden?x=1#part"
    url = (target + "\x00").encode("utf-16le")
    moniker = bytes.fromhex("e0c9ea79f9bace118c8200aa004ba90b")
    hyperlink = struct.pack("<HHHH", 4, 4, 2, 2) + bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b")
    hyperlink += struct.pack("<II", 2, 3) + moniker + struct.pack("<I", len(url)) + url
    row = struct.pack("<HHHHHHBBBB", 4, 2, 3, 255, 0, 0, 0x20, 1, 0, 0)
    column = struct.pack("<HHHHHH", 2, 2, 1000, 0, 1, 0)
    path = "\x03external.xls"
    supporting = struct.pack("<HH", 1, len(path)) + b"\x00" + path.encode("latin1")
    supporting += struct.pack("<H", 6) + b"\x00Inputs"
    data = stream(record(0x0208, row) + record(0x007D, column) + record(0x01B8, hyperlink),
                  globals_records=record(0x0018, label) + record(0x01AE, supporting),
                  sheet_type=1, visibility=2)
    assert url in data, "fixture must retain an exact UTF-16 hyperlink destination"
    # WHEN hidden and name-bearing records are scanned without readable filtering
    context = inspect(data)
    # THEN source locations, original targets and auto-run identity remain explicit
    assert [(item["kind"], item["target"], item["text"])
            for item in context.findings if item["capability"] in ("links", "names")] == [
        ("defined-name", None, "Auto_Open"), ("hyperlink", target, None),
    ], "defined names and exact hyperlink targets must be retained in source order"
    assert [(item["kind"], item["details"].get("hidden"))
            for item in context.findings if item["capability"] == "hidden_structures"] == [
        ("sheet", "very_hidden"), ("row", True), ("column", True),
    ], "hidden sheet, row and column metadata must remain independent evidence"


def test_biff_shared_formulas_preserve_cache_absence_and_relative_cell_locations():
    # GIVEN a shared relative formula and a string cache with no following STRING record
    anchor = b"\x01" + struct.pack("<HH", 4, 3)
    cache = b"\x00" * 6 + b"\xff\xff"
    first = struct.pack("<HHH", 4, 3, 0) + cache + b"\x00" * 6
    second = struct.pack("<HHH", 5, 3, 0) + struct.pack("<d", 1) + b"\x00" * 6
    relative = b"\x2c" + struct.pack("<HH", 0, 0xFFFF)
    shared = struct.pack("<HHBBBBH", 4, 5, 3, 3, 0, 2, len(relative)) + relative
    data = stream(record(0x0006, first + struct.pack("<H", len(anchor)) + anchor)
                  + record(0x04BC, shared)
                  + record(0x0006, second + struct.pack("<H", len(anchor)) + anchor))
    assert data.count(anchor) == 2, "both source formulas must use the same shared anchor"
    # WHEN the scanner resolves the relative shared definition against each source cell
    context = inspect(data)
    # THEN absent string caches and present numeric caches remain separate
    assert [(address, details["formula"], details["value"], details["cached_value_state"])
            for address, details in cell_details(context)] == [
        ("'Bob''s input'!D5", "C5", None, "missing"),
        ("'Bob''s input'!D6", "C6", 1.0, "present"),
    ], "shared formula references must use the consuming cell's original address"


def test_biff_record_limit_stops_with_explicit_coverage_and_deterministic_payloads():
    # GIVEN a valid workbook with a named resource allowance below its record count
    data = stream(record(0x0203, struct.pack("<HHHd", 4, 2, 0, 42)))
    assert len(data) == 89, "the synthetic workbook must retain its bounded exact size"
    # WHEN scanning cannot inventory its next source record
    first, second = inspect(data, records=3), inspect(data, records=3)
    # THEN both operations retain the same explicit exhausted budget and open cell coverage
    assert (first.exhausted, first.coverage, first.payloads) == (
        second.exhausted, second.coverage, second.payloads,
    ), "repeated limit failures must preserve deterministic evidence"
    assert [(row["limit"], row["maximum"]) for row in first.exhausted] == [
        ("records", 3),
    ], "the exact cumulative record allowance must be reported"


@pytest.mark.corpus
def test_public_xls_formula_fixture_keeps_native_tokens_and_external_sheet_names():
    # GIVEN the unchanged licensed calamine XLS formula fixture
    from brewdoc._compound import read_compound
    from brewdoc._biff import inspect_biff_stream

    path = Path(__file__).parent / "fixtures/xls/calamine-xls_formula.xls"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        "553f0417bacbb34c7755cbe7334ecc63f850cb099d02af503143a9c679278a87"
    ), "the source identity must match the existing licensed corpus"
    # WHEN one CFB inventory hands the original Workbook bytes to BIFF inspection
    context = InspectionContext()
    document = context.document(data, path.name, parent=None, location={"kind": "source"})
    streams = read_compound(data, context, document)
    workbook = next(item for item in streams if item["name"] == "Workbook")
    inspect_biff_stream(workbook["data"], workbook["name"], context, document)
    # THEN formula reconstruction retains native source tokens and exact cross-sheet identities
    assert [(item["location"]["qualified_cell"], item["text"], item["details"]["token_hex"])
            for item in context.findings if item["kind"] == "formula"] == [
        ("'Sheet1'!A2", "(A1*2)", "44000000c01e020005"),
        ("'Sheet1'!A3", "(2*'Sheet2'!A1)", "1e02005a0000000000c005"),
        ("'Sheet1'!A4", "(A1+'Sheet2'!A1)", "44000000c05a0000000000c003"),
    ], "the licensed source must expose its three exact formulas without evaluation"


def test_biff_external_name_source_resolves_namex_formula_without_placeholder():
    # GIVEN one external defined name referenced through the original XTI index
    target, label = "https://example.invalid/Book.xls", "Budget"
    supporting = struct.pack("<HH", 1, len(target)) + b"\x00" + target.encode()
    supporting += b"\x06\x00\x00Inputs"
    external_name = struct.pack("<HHH", 0, 1, 0) + bytes((len(label), 0)) + label.encode() + b"\x00\x00"
    xti = struct.pack("<HHhh", 1, 0, 0, 0)
    tokens = b"\x39" + struct.pack("<HI", 0, 1)
    formula = struct.pack("<HHHd", 4, 3, 0, 42) + b"\x00" * 6
    data = stream(record(0x0006, formula + struct.pack("<H", len(tokens)) + tokens),
                  globals_records=record(0x01AE, supporting) + record(0x0023, external_name) + record(0x0017, xti))
    assert tokens in data, "fixture must retain the exact native NameX indices"
    # WHEN passive inspection joins the supporting link, external name and source formula
    context = inspect(data)
    # THEN the cell formula retains the real target, sheet scope and external name
    assert [(address, details["formula"]) for address, details in cell_details(context)] == [
        ("'Bob''s input'!D5", "'[https://example.invalid/Book.xls]Inputs'!Budget"),
    ], "NameX reconstruction must use original external-name evidence"


def test_biff_file_moniker_keeps_parent_depth_unicode_path_and_fragment():
    # GIVEN a file moniker with a Unicode path and an internal fragment
    ansi, unicode_path = b"fallback.xls\x00", "Δ Inputs.xls".encode("utf-16le")
    moniker = bytes.fromhex("0303000000000000c000000000000046")
    body = bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b") + struct.pack("<II", 2, 9)
    body += moniker + struct.pack("<HI", 2, len(ansi)) + ansi + bytes(24)
    body += struct.pack("<IIH", len(unicode_path) + 6, len(unicode_path), 3) + unicode_path
    fragment = "Inputs!C5\x00".encode("utf-16le")
    body += struct.pack("<I", len(fragment) // 2) + fragment
    data = stream(record(0x01B8, struct.pack("<HHHH", 4, 4, 2, 2) + body))
    assert unicode_path in data, "fixture must retain the original Unicode destination bytes"
    # WHEN HLink delegates to the existing bounded IHlink parser
    context = inspect(data)
    # THEN parent prefixes and fragments remain inert original target text
    assert [item["target"] for item in context.findings if item["kind"] == "hyperlink"] == [
        "../../Δ Inputs.xls#Inputs!C5",
    ], "file monikers must retain Unicode paths, parent depth and fragment"


def test_biff_dde_cache_retains_service_topic_item_and_typed_errors():
    # GIVEN a DDE supporting link with one cached Boolean and one cached error
    target, label = "AcmeService\x03Topic", "Item"
    supporting = struct.pack("<HH", 0, len(target)) + b"\x00" + target.encode()
    cached = b"\x01\x00\x00\x04\x01" + bytes(7) + b"\x10\x07" + bytes(7)
    external_name = bytes(6) + bytes((len(label), 0)) + label.encode() + cached
    data = stream(b"", globals_records=record(0x01AE, supporting) + record(0x0023, external_name))
    assert cached in data, "fixture must preserve the exact typed cache bytes"
    # WHEN native records are inspected without opening the DDE service
    context = inspect(data)
    # THEN service/topic/item and cached error identity remain structured evidence
    finding = next(item for item in context.findings if item["kind"] == "external-name")
    assert (finding["target"], finding["details"].get("service"), finding["details"].get("topic"),
            finding["text"], finding["details"].get("cached_values")) == (
        target, "AcmeService", "Topic", "Item",
        [[{"value": True, "value_type": "boolean", "error": None},
          {"value": "#DIV/0!", "value_type": "error", "error": "#DIV/0!"}]],
    ), "DDE cache errors must remain distinct from blank and missing results"


def test_biff_continued_drawing_exposes_shape_link_with_physical_segment_provenance():
    # GIVEN a shape hyperlink whose OfficeArt bytes cross a native CONTINUE boundary
    target = "https://example.invalid/picture"
    encoded = (target + "\x00").encode("utf-16le")
    link = bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b")
    link += struct.pack("<III", 2, 0x101, len(encoded) // 2) + encoded
    prop = struct.pack("<HI", 0x8382, len(link)) + link
    drawing = struct.pack("<HHI", 0x13, 0xF00B, len(prop)) + prop
    boundary = 17
    data = stream(record(0x00EC, drawing[:boundary]) + record(0x003C, drawing[boundary:]))
    assert data.count(encoded) == 1, "fixture must retain one exact destination across source records"
    # WHEN the drawing parser receives assembled bytes and their original segment map
    context = inspect(data)
    # THEN semantic target and physical source segments remain independently exact
    finding = next(item for item in context.findings if item["kind"] == "shape-hyperlink")
    assert (finding["target"], [(segment["assembled_offset"], segment["byte_size"])
                                for segment in finding["location"]["segments"]]) == (
        target, [(0, boundary), (boundary, len(drawing) - boundary)],
    ), "assembled OfficeArt offsets must never be mislabeled as physical source offsets"


def test_biff_headers_and_footers_keep_even_and_first_page_text():
    # GIVEN independent odd, even and first-page header/footer source strings
    first, even = "First page", "Even page"
    extra = struct.pack("<HH", 0x089C, 0) + bytes(8) + bytes(16)
    extra += struct.pack("<HHHHH", 3, len(even), 0, len(first), 0)
    extra += struct.pack("<H", len(even)) + b"\x00" + even.encode()
    extra += struct.pack("<H", len(first)) + b"\x00" + first.encode()
    data = stream(record(0x0014, b"\x03\x00\x00Odd") + record(0x089C, extra))
    assert first.encode() in data, "fixture must retain independent first-page text"
    # WHEN native supplementary source text is inspected
    context = inspect(data)
    # THEN page-specific source strings survive without formatting normalization
    assert [(item["kind"], item["text"]) for item in context.findings if item["capability"] == "text"] == [
        ("header", "Odd"), ("header-even", "Even page"), ("header-first", "First page"),
    ], "all independent header variants must remain visible supplementary content"


def test_biff_control_actions_resolve_names_and_retrieve_exact_ctls_range():
    # GIVEN an OBJ macro name and a bounded control data reference to a CFB stream
    from brewdoc._biff import inspect_biff_stream

    tokens = b"\x1e\x07\x00"
    label = struct.pack("<HBBHHHBBBB", 0x000A, 0, 9, len(tokens), 0, 0, 0, 0, 0, 0)
    label += b"\x00Auto_Open" + tokens
    cmo = struct.pack("<HHHHH", 0x15, 18, 8, 7, 0) + bytes(12)
    macro_body = struct.pack("<HI", 5, 0) + b"\x23" + struct.pack("<I", 1) + b"\x00"
    macro = struct.pack("<HH", 4, len(macro_body)) + macro_body
    flags = struct.pack("<HHH", 8, 2, 0x20)
    formula = struct.pack("<HI", 5, 0) + b"\x02" + bytes(4) + b"\x03\x00\x00"
    payload = b"exact control bytes"
    body = struct.pack("<H", len(formula)) + formula + struct.pack("<II", 3, len(payload))
    obj = cmo + macro + flags + struct.pack("<HH", 9, len(body)) + body + bytes(4)
    data = stream(record(0x005D, obj), globals_records=record(0x0018, label))
    streams = ({"name": "Ctls", "path": "Ctls", "kind": "stream", "data": b"abc" + payload + b"tail",
                "location": {"kind": "compound", "path": "Ctls"}},)
    assert streams[0]["data"][3:3 + len(payload)] == payload, "source range must contain exact control bytes"
    # WHEN the native object reader receives already inventoried CFB streams
    context = InspectionContext()
    document = context.document(data, "book.xls", parent=None, location={"kind": "source"})
    inspect_biff_stream(data, "Workbook", context, document, streams=streams)
    # THEN macro identity is resolved and control bytes remain passive retrievable evidence
    assert [item["target"] for item in context.findings if item["kind"] == "object-macro-name"] == [
        "Auto_Open",
    ], "object macro references must resolve against original defined-name ordinals"
    finding = next(item for item in context.findings if item["kind"] == "object-data")
    assert context.payloads[finding["artifact"]] == payload, "control source slices must be byte-identical"


@pytest.mark.parametrize(("column", "reference"), [(2, "$C$5"), (0xC002, "R[4]C[2]")])
def test_biff_external_defined_formula_keeps_actual_target_and_restricted_reference(column, reference):
    # GIVEN a native external-name definition carrying a real sheet pair and absolute cell
    target, label = "external.xls", "Budget"
    supporting = struct.pack("<HH", 1, len(target)) + b"\x00" + target.encode() + b"\x06\x00\x00Inputs"
    formula = b"\x3a" + struct.pack("<HHHH", 0, 0, 4, column)
    external_name = struct.pack("<HHH", 0, 1, 0) + bytes((len(label), 0)) + label.encode()
    external_name += struct.pack("<H", len(formula)) + formula
    data = stream(b"", globals_records=record(0x01AE, supporting) + record(0x0023, external_name))
    assert formula in data, "the external definition must retain its exact native reference bytes"
    # WHEN ExtNameParsedFormula is translated through the shared passive decoder
    context = inspect(data)
    # THEN its original target and source range remain explicit
    finding = next(item for item in context.findings if item["kind"] == "external-name")
    assert (finding["target"], finding["text"], finding["details"]["formula"]) == (
        "external.xls", "Budget", "'[external.xls]Inputs'!" + reference,
    ), "external defined-name formulas must use their actual source workbook and sheet"


def test_biff_sst_and_cached_string_integrate_original_continue_boundaries():
    # GIVEN two source strings that change encoding across native continuation records
    sst = struct.pack("<IIHB", 1, 1, 3, 0) + b"A"
    follow = b"\x01" + "βC".encode("utf-16le")
    tokens = b"\x1e\x07\x00"
    raw = struct.pack("<HHH", 5, 3, 0) + bytes(6) + b"\xff\xff" + bytes(6)
    cells = record(0x00FD, struct.pack("<HHHI", 4, 2, 0, 0))
    cells += record(0x0006, raw + struct.pack("<H", len(tokens)) + tokens)
    cells += record(0x0207, b"\x03\x00\x00A") + record(0x003C, follow)
    data = stream(cells, globals_records=record(0x00FC, sst) + record(0x003C, follow))
    assert data.count(follow) == 2, "each native string must retain its own encoding transition"
    # WHEN globals and formula caches use the boundary-aware shared string helpers
    context = inspect(data)
    # THEN both values retain the source Unicode and complete native cell coverage
    assert [(address, details["value"]) for address, details in cell_details(context)] == [
        ("'Bob''s input'!C5", "AβC"), ("'Bob''s input'!D6", "AβC"),
    ], "continued source strings must keep their own encoding state"
    assert context.coverage[("document/000001", "cells")]["status"] == "complete", (
        "fully consumed continuation records must not leave cell coverage open")
