"""Exact compound-file allocation and occurrence regressions."""

import struct

import pytest

from brewdoc._compound import read_compound
from brewdoc._inspection_common import InspectionContext


def compound_fixture(*, cycle=False, orphan=False):
    """Build one root, one mini stream and explicit allocation tables."""
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHH", header, 24, 62, 3, 65534, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, 2, 1, 0xFFFFFFFE, 0)
    struct.pack_into("<109I", header, 76, 0, *([0xFFFFFFFF] * 108))
    fat = [0xFFFFFFFD, 0xFFFFFFFE, 0xFFFFFFFE, 0xFFFFFFFE] + [0xFFFFFFFF] * 124
    directory = bytearray(512)
    for offset, name, kind, start, size in ((0, "Root Entry", 5, 3, 64), (128, "Code", 2, 0, 3)):
        encoded = (name + "\0").encode("utf-16le")
        directory[offset:offset + len(encoded)] = encoded
        struct.pack_into("<HBBIII", directory, offset + 64, len(encoded), kind, 1,
                         0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into("<IQ", directory, offset + 116, start, size)
    struct.pack_into("<I", directory, 76, 0xFFFFFFFF if orphan else 1)
    mini = [0 if cycle else 0xFFFFFFFE] + [0xFFFFFFFF] * 127
    return bytes(header) + struct.pack("<128I", *fat) + directory + struct.pack("<128I", *mini) + b"abc" + bytes(509)


def test_mini_stream_retains_exact_bytes():
    # GIVEN a small stream in the root mini stream.
    context = InspectionContext()
    # WHEN all directory occurrences are inspected.
    entries = read_compound(compound_fixture(), context, "d")
    # THEN its original bytes and hierarchy survive.
    assert [(item["path"], item["data"]) for item in entries] == [("", None), ("Code", b"abc")], "all entries and bytes must survive"
    assert list(context.payloads.values()) == [b"abc"], "stream artifact must contain exact bytes"


def test_cycle_is_explicit_unreadable():
    # GIVEN a mini FAT cycle beyond the declared stream length.
    context = InspectionContext()
    # WHEN the full chain is accounted.
    entries = read_compound(compound_fixture(cycle=True), context, "d")
    # THEN cycle is not hidden by a short stream length.
    assert entries[1]["data"] is None, "cyclic stream must be unreadable"
    assert context.coverage[("d", "inventory")]["status"] == "unavailable", "cycle must degrade coverage"


def test_orphan_entry_is_retained_with_problem():
    # GIVEN an unreachable stream directory entry.
    context = InspectionContext()
    # WHEN the directory array is inspected.
    entries = read_compound(compound_fixture(orphan=True), context, "d")
    # THEN allocated orphan bytes remain available.
    assert entries[1]["data"] == b"abc", "orphan stream bytes must survive"
    assert "orphan" in context.coverage[("d", "inventory")]["reason"], "orphan must have explicit diagnostic"


@pytest.mark.parametrize("limit", ["decoded_bytes", "records", "retained_bytes"])
def test_shared_budget_refusal_degrades_inventory(limit):
    # GIVEN a budget too small for this compound file.
    context = InspectionContext(limits={limit: 0})
    # WHEN allocation and retained payloads are admitted.
    read_compound(compound_fixture(), context, "d")
    # THEN resource refusal is explicit.
    assert context.coverage[("d", "inventory")]["status"] == "unavailable", "budget refusal must degrade inventory"
    assert [entry["limit"] for entry in context.exhausted] == [limit], "the exhausted budget must be identified"


def test_version_four_uses_full_header_sector():
    # GIVEN a version four compound file with 4096-byte sectors.
    original = compound_fixture()
    header = bytearray(original[:512])
    struct.pack_into("<H", header, 26, 4)
    struct.pack_into("<H", header, 30, 12)
    struct.pack_into("<I", header, 40, 1)
    fat = original[512:1024] + b"\xff" * 3584
    directory = original[1024:1536] + bytes(3584)
    mini = original[1536:2048] + b"\xff" * 3584
    source = bytes(header) + bytes(3584) + fat + directory + mini + original[2048:] + bytes(3584)
    context = InspectionContext()
    # WHEN offsets use the complete header sector.
    entries = read_compound(source, context, "d")
    # THEN the mini stream is unchanged.
    assert entries[1]["data"] == b"abc", "version four sector offsets must account for padding"
    assert context.coverage[("d", "inventory")]["status"] == "complete", "valid version four inventory must complete"


def test_crosslinked_stream_is_unreadable():
    # GIVEN two directory entries claiming the same mini sector.
    source = bytearray(compound_fixture())
    source[1280:1408] = source[1152:1280]
    source[1280:1288] = "Twin".encode("utf-16le")
    struct.pack_into("<I", source, 1152 + 72, 2)
    context = InspectionContext()
    # WHEN both occurrences are read.
    entries = read_compound(bytes(source), context, "d")
    # THEN the second allocation is refused, not silently reused.
    assert [entry["data"] for entry in entries] == [None, b"abc", None], "crosslinked stream must remain inventoried and unreadable"
    assert "cross-linked" in entries[2]["reason"], "allocation overlap must be explained"


def test_extended_difat_accounts_for_all_fat_sectors():
    # GIVEN 110 FAT sectors requiring one DIFAT extension sector.
    original = compound_fixture()
    header = bytearray(original[:512])
    struct.pack_into("<II", header, 44, 110, 111)
    struct.pack_into("<I", header, 60, 112)
    struct.pack_into("<II", header, 68, 110, 1)
    struct.pack_into("<109I", header, 76, *range(109))
    fat = [0xFFFFFFFD] * 110 + [0xFFFFFFFC] + [0xFFFFFFFE] * 3 + [0xFFFFFFFF] * (110 * 128 - 114)
    difat = [109] + [0xFFFFFFFF] * 126 + [0xFFFFFFFE]
    directory = bytearray(original[1024:1536])
    struct.pack_into("<I", directory, 116, 113)
    source = bytes(header) + struct.pack("<14080I", *fat) + struct.pack("<128I", *difat) + directory + original[1536:]
    context = InspectionContext()
    # WHEN FAT addresses extend beyond the header's 109 entries.
    entries = read_compound(source, context, "d")
    # THEN all chains resolve without omitting the final FAT sector.
    assert entries[1]["data"] == b"abc", "extended DIFAT stream must decode exactly"
    assert context.coverage[("d", "inventory")]["status"] == "complete", "valid DIFAT extension must be completely inventoried"


def nested_storage_fixture(depth=80):
    """Build a directory-only CFB with a valid deep storage hierarchy."""
    sectors = (depth + 4) // 4
    header = bytearray(compound_fixture()[:512])
    struct.pack_into("<II", header, 60, 0xFFFFFFFE, 0)
    fat = [0xFFFFFFFD] + list(range(2, sectors + 1)) + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    directory = bytearray(sectors * 512)
    for number in range(depth + 1):
        offset = number * 128
        name = "Root Entry" if number == 0 else "a" * 31
        encoded = (name + "\0").encode("utf-16le")
        directory[offset:offset + len(encoded)] = encoded
        struct.pack_into("<HBBIII", directory, offset + 64, len(encoded),
                         5 if number == 0 else 1, 1, 0xFFFFFFFF, 0xFFFFFFFF,
                         number + 1 if number < depth else 0xFFFFFFFF)
        struct.pack_into("<IQ", directory, offset + 116, 0xFFFFFFFE, 0)
    return bytes(header) + struct.pack("<128I", *fat) + directory


def test_nested_paths_charge_actual_growth_before_concatenation():
    # GIVEN 80 deep storages whose derived paths require 103600 bytes.
    from brewdoc._compound import _Compound

    data = nested_storage_fixture()
    context = InspectionContext(limits={"decoded_bytes": 12288, "report_bytes": 8192})
    reader = _Compound(data, context, {"kind": "compound"})
    entries = reader.entries()
    before = context.consumed["decoded_bytes"]
    records_before = context.consumed["records"]
    assert (len(data), len(entries), before) == (11776, 84, 11264), "small source must reproduce the reviewed allocation boundary"
    # WHEN hierarchy paths are admitted under the remaining 1024 bytes.
    paths = reader.paths(entries, lambda reason, where=None: None)
    actual = sum(len(path.encode("utf-8")) for path in paths.values())
    # THEN every materialized path byte is charged and exhaustion is located.
    assert context.consumed["decoded_bytes"] - before == actual, "derived paths must charge exact UTF-8 growth before allocation"
    assert actual <= 1024, "deep paths cannot allocate beyond the remaining shared allowance"
    assert context.consumed["records"] - records_before == 8, "each attempted path occurrence must charge shared traversal work"
    assert [(item["limit"], item["location"]["entry_id"]) for item in context.exhausted] == [
        ("decoded_bytes", 8)], "the first path beyond the allowance must retain its exact directory identity"


def test_path_exhaustion_preserves_known_directory_boundary():
    # GIVEN the same valid deep source with a small shared path allowance.
    context = InspectionContext(limits={"decoded_bytes": 12288})
    # WHEN full inventory reaches the bounded path boundary.
    entries = read_compound(nested_storage_fixture(), context, "d")
    # THEN known entries survive with incomplete, explicitly located inventory coverage.
    assert len(entries) == 81, "path refusal must not silently discard allocated directory entries"
    assert entries[-1]["name"] == "a" * 31, "original directory metadata must survive failed derived hierarchy growth"
    assert context.coverage[("d", "inventory")]["status"] == "unavailable", "path exhaustion cannot produce complete inventory"


def test_deep_valid_hierarchy_has_no_arbitrary_depth_rejection():
    # GIVEN valid 80-level storage nesting and sufficient shared budgets.
    from brewdoc._compound import _Compound

    context = InspectionContext()
    reader = _Compound(nested_storage_fixture(), context, {"kind": "compound"})
    entries = reader.entries()
    before = context.consumed["decoded_bytes"]
    # WHEN actual derived growth is fully affordable.
    paths = reader.paths(entries, lambda reason, where=None: None)
    # THEN valid hierarchy depth is accepted and exact growth is charged.
    assert (len(paths), len(paths[80].encode("utf-8")), context.consumed["decoded_bytes"] - before) == (
        81, 2559, 103600), "budget admission must replace arbitrary depth rejection"
    assert context.exhausted == [], "sufficient resources must not fabricate a limit failure"
