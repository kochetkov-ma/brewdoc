"""Continued BIFF8 strings preserve encoding and noncharacter boundaries."""

import struct
from pathlib import Path

import pytest

from brewdoc._biff_strings import parse_sst, parse_xl_unicode_segments
from brewdoc._inspection_common import InspectionContext, InspectionLimit

LOCATION = {"kind": "biff-record", "stream": "Workbook", "record_offset": 0, "record_type": "0x00FC"}


@pytest.mark.parametrize(("segments", "expected"), [
    ([struct.pack("<IIHB", 1, 1, 3, 0) + b"A", b"\x01\xb2\x03C\x00"], ("AβC",)),
    ([struct.pack("<IIHB", 1, 1, 3, 1) + "β".encode("utf-16le"), b"\x00A\x80"], ("βA\x80",)),
    ([struct.pack("<IIHB", 1, 1, 2, 1) + b"=\xd8", b"\x01\x00\xde"], ("😀",)),
    ([struct.pack("<IIHB", 1, 1, 2, 0) + b"A", b"\x01", b"\x00B"], ("AB",)),
    ([struct.pack("<II", 2, 2) + b"\x00\x00\x00", b"\x01\x00\x00Z"], ("", "Z")),
    ([struct.pack("<II", 1, 1) + b"\x03\x00\x00A\x00B"], ("A\x00B",)),
])
def test_sst_character_continuations_change_encoding_without_losing_source_values(segments, expected):
    # GIVEN source segments retaining an original character or string boundary
    assert len(segments) in (1, 2, 3), "the fixture must preserve its exact native segment boundaries"
    # WHEN the shared string table consumes only real character continuation flags
    actual = parse_sst(segments, InspectionContext(), LOCATION)
    # THEN compressed high bytes, controls, empty strings and surrogate pairs survive
    assert actual == expected, "SST values must preserve all source characters and empty entries"


@pytest.mark.parametrize("split", [1, 3, 4, 7, 11])
def test_rich_and_extension_boundaries_do_not_consume_a_spurious_encoding_flag(split):
    # GIVEN one rich string whose formatting and phonetic extension span CONTINUE
    tail = b"\x00\x00\x01\x00" + b"\x01\x00\x02\x00\x00\x00\x00\x00"
    head = struct.pack("<IIHBHI", 2, 2, 1, 12, 1, 8) + b"A"
    segments = [head + tail[:split], tail[split:] + b"\x01\x00\x00Z"]
    assert len(tail) == 12, "formatting and extension data must retain the declared exact lengths"
    # WHEN the next character header follows the noncharacter bytes
    actual = parse_sst(segments, InspectionContext(), LOCATION)
    # THEN raw formatting/extension bytes and the next header remain correctly aligned
    assert actual == ("A", "Z"), "noncharacter continuations must not discard their first byte"


@pytest.mark.parametrize(("segments", "reason"), [
    ([b"\x01\x00"], "fixed fields split"),
    ([struct.pack("<II", 1, 1) + b"\x01", b"\x00\x00A"], "fixed fields split"),
    ([struct.pack("<IIHB", 1, 1, 1, 1) + b"A", b"\x01\x00"], "UTF-16 character split"),
    ([struct.pack("<IIHB", 1, 1, 2, 0) + b"A", b""], "missing BIFF continuation encoding flag"),
    ([struct.pack("<IIHB", 1, 1, 2, 0) + b"A"], "truncated continued BIFF string"),
    ([struct.pack("<IIHB", 1, 1, 0, 4) + struct.pack("<i", -1)], "negative BIFF string extension"),
    ([struct.pack("<IIHB", 1, 1, 0, 8) + b"\x01\x00"], "truncated BIFF string formatting"),
    ([struct.pack("<II", 1, 0) + b"unvisited"], "unvisited BIFF string bytes"),
    ([struct.pack("<ii", 0, -1)], "invalid BIFF shared string counts"),
])
def test_sst_malformed_boundaries_never_become_a_successful_empty_value(segments, reason):
    # GIVEN source bytes with a native malformed boundary or unsupported length
    assert type(segments[0]) is bytes, "the malformed fixture must remain an exact byte payload"
    # WHEN parsing reaches an unaccounted source boundary
    with pytest.raises(ValueError, match=reason):
        parse_sst(segments, InspectionContext(), LOCATION)
    # THEN the raised refusal prevents a falsely complete empty table


def test_cached_string_continuations_keep_the_original_empty_and_unicode_values():
    # GIVEN two source STRING values, one empty and one continued
    empty = [b"\x00\x00\x00"]
    continued = [struct.pack("<HB", 3, 0) + b"A", b"\x01\xb2\x03C\x00"]
    assert empty[0] == b"\x00\x00\x00", "empty cached STRING still has its full source header"
    # WHEN the cached-value reader consumes source boundaries
    actual = (parse_xl_unicode_segments(empty, InspectionContext(), LOCATION),
              parse_xl_unicode_segments(continued, InspectionContext(), LOCATION))
    # THEN an empty cache is distinct from missing bytes and Unicode survives
    assert actual == ("", "AβC"), "cached strings must retain empty versus missing and exact Unicode"


@pytest.mark.parametrize(("segments", "reason"), [
    ([b"\x00\x00"], "fixed fields split"),
    ([struct.pack("<HB", 32768, 0)], "exceeds 32767 characters"),
    ([b"\x01\x00\x00"], "truncated continued BIFF string"),
    ([b"\x00\x00\x00unvisited"], "unvisited BIFF string bytes"),
])
def test_cached_string_invalid_source_is_distinct_from_a_present_empty_cache(segments, reason):
    # GIVEN a missing header, excessive declared count or unaccounted cached bytes
    assert type(segments[0]) is bytes, "cached-source fixtures must retain their exact original bytes"
    # WHEN the standalone STRING decoder reaches invalid source evidence
    with pytest.raises(ValueError, match=reason):
        parse_xl_unicode_segments(segments, InspectionContext(), LOCATION)
    # THEN no invalid cache is returned as a valid empty string


def test_zero_string_table_and_reserved_flags_keep_their_defined_meaning():
    # GIVEN a zero-entry SST and strings with reserved bits that the format ignores
    empty = [struct.pack("<II", 0, 0)]
    reserved = [struct.pack("<IIHB", 1, 1, 1, 0xF2) + b"A"]
    assert len(empty[0]) == 8, "an empty SST still retains its complete count fields"
    # WHEN native source counts and the defined encoding bit are applied
    actual = (parse_sst(empty, InspectionContext(), LOCATION),
              parse_sst(reserved, InspectionContext(), LOCATION))
    # THEN a real empty table and a source with ignored reserved bits decode exactly
    assert actual == ((), ("A",)), "reserved bits must not invent unsupported data or lose valid strings"


@pytest.mark.parametrize(("limit", "maximum"), [("decoded_bytes", 7), ("records", 2), ("payload_bytes", 1)])
def test_sst_cumulative_limits_refuse_before_materializing_the_next_output(limit, maximum):
    # GIVEN two source strings and a cumulative allowance smaller than their decoded work
    segments = [struct.pack("<II", 2, 2) + b"\x01\x00\x00A\x01\x00\x00B"]
    context = InspectionContext(limits={limit: maximum})
    assert context.limits[limit] == maximum, "this parse must use the exact named allowance"
    # WHEN another decoded string would exceed the shared operation allowance
    with pytest.raises(InspectionLimit, match=limit):
        parse_sst(segments, context, LOCATION)
    # THEN the explicit source location and exhausted counter remain available
    assert [(row["limit"], row["maximum"], row["location"]) for row in context.exhausted] == [
        (limit, maximum, LOCATION),
    ], "the exact cumulative failure must retain its original record location"


@pytest.mark.corpus
def test_public_xls_sst_values_match_the_existing_cached_source_values():
    # GIVEN an unchanged public workbook with a native shared-string table
    from brewdoc._compound import read_compound

    path = Path(__file__).parent / "fixtures/xls/calamine-any_sheets.xls"
    context = InspectionContext()
    data = path.read_bytes()
    document = context.document(data, path.name, parent=None, location={"kind": "source"})
    entries = read_compound(data, context, document)
    workbook = next(entry["data"] for entry in entries if entry["name"] in ("Workbook", "Book"))
    records, offset = [], 0
    while offset + 4 <= len(workbook):
        kind, size = struct.unpack_from("<HH", workbook, offset)
        records.append((kind, workbook[offset + 4:offset + 4 + size]))
        offset += 4 + size
    source = next(payload for kind, payload in records if kind == 0x00FC)
    assert int.from_bytes(source[4:8], "little") == 1, "the public SST must contain its original one unique value"
    # WHEN its native source string table is decoded independently
    actual = parse_sst([source], context, LOCATION)
    # THEN the exact workbook source text survives without calamine coercion
    assert actual == ("This workbook contains 4 sheets: Visible, Hidden, VeryHidden and Chart",), \
        "the public shared string must match its licensed native bytes and cached Visible A5 value"
