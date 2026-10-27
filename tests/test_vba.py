"""MS-OVBA compression and malformed-input regressions."""

import pytest
import struct
from pathlib import Path
from zipfile import ZipFile
import hashlib
import io
import math

from brewdoc._vba import _module_directory, decompress_vba, inspect_vba
from brewdoc._compound import read_compound
from brewdoc._inspection_common import InspectionContext, InspectionLimit


def test_overlapping_copy_token():
    # GIVEN literal A followed by a back-reference of length six.
    encoded = bytes.fromhex("0103b002410300")
    # WHEN the chunk is decompressed.
    actual = decompress_vba(encoded)
    # THEN overlapping copies repeat the newly emitted byte.
    assert actual == b"AAAAAAA", "overlapping copies must follow OVBA semantics"


@pytest.mark.parametrize("encoded", [b"", b"\x01\x00", bytes.fromhex("0102b0010000")])
def test_malformed_compression_is_refused(encoded):
    # GIVEN incomplete or invalid compressed data.
    # WHEN decompression validates framing and references.
    with pytest.raises(ValueError):
        decompress_vba(encoded)
    # THEN no successful empty source is returned.


def test_copy_growth_checks_budget_first():
    # GIVEN a decoder allowance smaller than the copy output.
    context = InspectionContext(limits={"decoded_bytes": 3})
    # WHEN decompression would expand a back-reference.
    with pytest.raises(InspectionLimit):
        decompress_vba(bytes.fromhex("0103b002410300"), context)
    # THEN only the admitted literal was charged.
    assert context.consumed["decoded_bytes"] == 1, "copy must be refused before growth"


def dir_record(kind, value):
    return struct.pack("<HI", kind, len(value)) + value


def test_codepage_unicode_names_and_source_offset():
    # GIVEN module names in cp1251 with independent Unicode display names.
    source = (dir_record(3, struct.pack("<H", 1251)) + dir_record(15, b"\x01\x00")
              + dir_record(25, "Модуль".encode("cp1251"))
              + dir_record(71, "Модуль".encode("utf-16le"))
              + dir_record(26, b"Code") + dir_record(50, "Code".encode("utf-16le"))
              + dir_record(49, struct.pack("<I", 47)) + dir_record(43, b""))
    # WHEN metadata is read structurally.
    encoding, modules = _module_directory(source, InspectionContext(), {"kind": "vba"})
    # THEN the stream offset and declared codepage survive.
    assert encoding == "cp1251", "declared project codepage must be retained"
    assert [(item["name"], item["stream"], item["source_offset"]) for item in modules] == [("Модуль", "Code", 47)], "module metadata must follow record boundaries"


@pytest.mark.parametrize("source", [dir_record(0x7777, b""), dir_record(3, b"\xe4\x04")[:-1]])
def test_unknown_or_truncated_dir_is_refused(source):
    # GIVEN unknown or truncated dir records.
    # WHEN metadata is parsed.
    with pytest.raises(ValueError):
        _module_directory(source, InspectionContext(), {"kind": "vba"})
    # THEN no empty successful module inventory is synthesized.


def test_public_project_exact_source_and_compiled_prefix():
    # GIVEN the licensed calamine VBA fixture.
    path = Path(__file__).parent / "fixtures/xlsm/calamine-vba.xlsm"
    with ZipFile(path) as archive:
        data = archive.read("xl/vbaProject.bin")
    context = InspectionContext()
    # WHEN source and compiled content are inspected separately.
    inspect_vba(data, "vbaProject.bin", context, "d")
    # THEN every module and exact source bytes are retained.
    assert [entry["details"]["module_name"] for entry in context.findings] == ["ThisWorkbook", "Sheet1", "Sheet2", "Sheet3", "testVBA"], "all public fixture modules must be recovered"
    finding = context.findings[-1]
    expected = b'Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n'
    assert context.payloads[finding["artifact"]] == expected, "source artifact must preserve exact bytes and CRLF"
    raw_component = next(item for item in context.components if item["location"].get("path") == "VBA/testVBA")
    raw = context.payloads[raw_component["artifact"]]
    offset = finding["location"]["source_offset"]
    assert context.payloads[finding["details"]["compiled_prefix"]] == raw[:offset], "compiled prefix must exactly match original module bytes"
    assert context.payloads[finding["details"]["compressed_source"]] == raw[offset:], "source container must exactly match original module tail"
    assert context.coverage[("d", "macros")]["status"] == "complete", "available source and opaque compiled byte preservation must satisfy macro scope"


def test_raw_chunk_preserves_all_4096_bytes():
    # GIVEN an uncompressed OVBA chunk.
    source = bytes(range(256)) * 16
    # WHEN its raw chunk is admitted.
    actual = decompress_vba(b"\x01\xff\x3f" + source)
    # THEN the original bytes survive.
    assert actual == source, "raw chunks must preserve all bytes"


def test_copy_offset_width_changes_after_sixteen_literals():
    # GIVEN seventeen literals followed by a distance seventeen copy token.
    encoded = b"\x01\x15\xb0\x00abcdefgh\x00ijklmnop\x02q\x00\x80"
    # WHEN the offset field grows to five bits.
    actual = decompress_vba(encoded)
    # THEN a three-byte copy points to the first literal.
    assert actual == b"abcdefghijklmnopqabc", "copy-token width must derive from the current chunk position"


def test_missing_module_does_not_hide_later_source():
    # GIVEN a project whose first module stream is unavailable.
    path = Path(__file__).parent / "fixtures/xlsm/calamine-vba.xlsm"
    with ZipFile(path) as archive:
        data = archive.read("xl/vbaProject.bin")
    context = InspectionContext()
    entries = read_compound(data, context, "d")
    entries[2]["data"] = None
    # WHEN modules are inspected independently.
    inspect_vba(data, "vbaProject.bin", context, "d", streams=entries)
    # THEN later module source is recovered and coverage stays partial.
    assert [item["details"]["module_name"] for item in context.findings] == ["Sheet1", "Sheet2", "Sheet3", "testVBA"], "one failed module must not suppress later source"
    assert context.coverage[("d", "macros")]["status"] == "partial", "unavailable module must prevent completeness"


def literal_container(payload):
    """Encode one literal-only compressed chunk independently from the decoder."""
    body = b"".join(b"\0" + payload[offset:offset + 8] for offset in range(0, len(payload), 8))
    return b"\x01" + struct.pack("<H", 0xB000 | (len(body) - 1)) + body


@pytest.mark.parametrize("codepage,encoding,text", [(10006, "mac_greek", "Ω"),
                                                  (10004, "mac_arabic", "ش"),
                                                  (10010, "mac_romanian", "ă"),
                                                  (10029, "mac_latin2", "Ł"),
                                                  (10079, "mac_iceland", "ð"),
                                                  (10081, "mac_turkish", "ş"),
                                                  (10082, "mac_croatian", "ž"),
                                                  (28597, "iso8859-7", "Ω"),
                                                  (28591, "iso8859-1", "é"),
                                                  (28592, "iso8859-2", "Ł"),
                                                  (28593, "iso8859-3", "Ħ"),
                                                  (28594, "iso8859-4", "Ā"),
                                                  (28595, "iso8859-5", "Ж"),
                                                  (28596, "iso8859-6", "ش"),
                                                  (28598, "iso8859-8", "ש"),
                                                  (28599, "iso8859-9", "ş"),
                                                  (28603, "iso8859-13", "Ģ"),
                                                  (28605, "iso8859-15", "€"),
                                                  (37, "cp037", "é"),
                                                  (20273, "cp273", "ä"),
                                                  (20424, "cp424", "ש"),
                                                  (20932, "euc_jp", "日本語"),
                                                  (20936, "gb2312", "中文"),
                                                  (20127, "ascii", "A")])
def test_declared_standard_codepage_recovers_exact_available_source(codepage, encoding, text):
    # GIVEN a declared standard codepage with strict standard-library round-trip support.
    source = ('Attribute VB_Name = "M"\r\nSub F(): MsgBox "' + text + '": End Sub\r\n').encode(encoding)
    assert source.decode(encoding).encode(encoding) == source, "fixture codepage must strictly round-trip before inspection"
    directory = (dir_record(3, struct.pack("<H", codepage)) + dir_record(15, b"\x01\0")
                 + dir_record(25, "M".encode(encoding)) + dir_record(26, "M".encode(encoding))
                 + dir_record(49, struct.pack("<I", 3)) + dir_record(43, b""))
    streams = ({"name": "dir", "path": "VBA/dir", "kind": "stream", "data": literal_container(directory), "location": {"kind": "compound", "entry_id": 1}},
               {"name": "M", "path": "VBA/M", "kind": "stream", "data": b"raw" + literal_container(source), "location": {"kind": "compound", "entry_id": 2}})
    context = InspectionContext()
    # WHEN source recovery uses the exact numeric project codepage.
    inspect_vba(b"", "project.bin", context, "d", streams=streams)
    # THEN Unicode source and original compiled/source bytes remain separately available.
    assert [(item["text"], item["details"]["encoding"]) for item in context.findings] == [
        (source.decode(encoding), encoding)], "available declared codepage must recover exact Unicode module source"
    finding = context.findings[0]
    assert context.payloads[finding["artifact"]] == source, "source artifact must retain exact original encoded bytes"
    assert context.payloads[finding["details"]["compiled_prefix"]] == b"raw", "compiled prefix must remain independently exact"
    assert finding["details"]["codepage"] == codepage, "original numeric codepage provenance must remain explicit"


def test_unknown_numeric_codepage_keeps_raw_evidence_and_incomplete_coverage():
    # GIVEN an unknown project identifier and exact original stream evidence.
    directory = (dir_record(3, struct.pack("<H", 65535)) + dir_record(15, b"\x01\0")
                 + dir_record(25, b"M") + dir_record(26, b"M")
                 + dir_record(49, struct.pack("<I", 0)) + dir_record(43, b""))
    module = literal_container(b'Attribute VB_Name = "M"\r\n')
    streams = ({"name": "dir", "path": "VBA/dir", "kind": "stream", "data": literal_container(directory), "location": {"kind": "compound", "entry_id": 1}},
               {"name": "M", "path": "VBA/M", "kind": "stream", "data": module, "location": {"kind": "compound", "entry_id": 2}})
    context = InspectionContext()
    original = context.payload("d", "compound_stream", streams[1]["location"], module, "application/octet-stream")
    # WHEN strict codec lookup rejects the unknown identifier.
    inspect_vba(b"", "project.bin", context, "d", streams=streams)
    # THEN no replacement decoding or successful empty module claim is produced.
    assert context.findings == [], "unknown codepage must not invent decoded source"
    assert context.payloads[original] == module, "original encoded source container must remain retrievable"
    assert context.coverage[("d", "macros")]["status"] == "unavailable", "unknown identifier must keep macro recovery incomplete"


def encoded_public_project(codepage, encoding, text):
    """Repack the licensed project with its full dir and one strictly encoded source."""
    with ZipFile(Path(__file__).parent / "fixtures/xlsm/calamine-vba.xlsm") as archive:
        original = archive.read("xl/vbaProject.bin")
    entries = read_compound(original, InspectionContext(), "fixture")
    raw_dir = bytearray(decompress_vba(next(entry["data"] for entry in entries if entry["path"] == "VBA/dir")))
    assert (len(raw_dir), struct.unpack_from("<HIH", raw_dir, 30)) == (1086, (3, 2, 1252)), "full original dir codepage header/value offsets must be independently established"
    struct.pack_into("<H", raw_dir, 36, codepage)
    module = next(entry["data"] for entry in entries if entry["path"] == "VBA/testVBA")
    assert decompress_vba(module[785:]) == b'Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n', "the original compiled prefix boundary must reproduce the unchanged licensed source"
    source = ('Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "' + text + '"\r\nEnd Sub\r\n').encode(encoding)
    assert source.decode(encoding).encode(encoding) == source, "non-ASCII source must strictly round-trip before project inspection"
    replacement = {"VBA/dir": literal_container(raw_dir), "VBA/testVBA": module[:785] + literal_container(source)}
    fat = struct.unpack_from("<128I", original, 512)
    sector, chunks = 1, []
    while sector != 0xFFFFFFFE:
        chunks.append(original[(sector + 1) * 512:(sector + 2) * 512])
        sector = fat[sector]
    directory = bytearray(b"".join(chunks))
    mini_data, mini_fat = bytearray(), []
    for entry in entries:
        if entry["kind"] != "stream":
            continue
        payload = replacement.get(entry["path"], entry["data"])
        start = len(mini_fat)
        count = math.ceil(len(payload) / 64)
        mini_fat.extend([*range(start + 1, start + count), 0xFFFFFFFE])
        mini_data.extend(payload + bytes(count * 64 - len(payload)))
        struct.pack_into("<IQ", directory, entry["entry_id"] * 128 + 116, start, len(payload))
    mini_sectors = math.ceil(len(mini_fat) / 128)
    root_start = 1 + len(directory) // 512 + mini_sectors
    root_sectors = math.ceil(len(mini_data) / 512)
    book_start = root_start + root_sectors
    struct.pack_into("<IQ", directory, 116, root_start, len(mini_data))
    label = "Book\0".encode("utf-16le")
    directory[15 * 128:15 * 128 + len(label)] = label
    struct.pack_into("<HBBIII", directory, 15 * 128 + 64, len(label), 2, 1, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<IQ", directory, 15 * 128 + 116, book_start, 4096)
    struct.pack_into("<I", directory, 128 + 72, 15)
    table = [0xFFFFFFFD]
    for start, count in ((1, len(directory) // 512), (1 + len(directory) // 512, mini_sectors),
                         (root_start, root_sectors), (book_start, 8)):
        table.extend([*range(start + 1, start + count), 0xFFFFFFFE])
    table.extend([0xFFFFFFFF] * (128 - len(table)))
    header = bytearray(original[:512])
    struct.pack_into("<II", header, 60, 1 + len(directory) // 512, mini_sectors)
    mini_fat.extend([0xFFFFFFFF] * (mini_sectors * 128 - len(mini_fat)))
    from test_biff import record, stream

    book = stream(record(0x0203, struct.pack("<HHHd", 0, 0, 0, 42)))
    data = (bytes(header) + struct.pack("<128I", *table) + directory
            + struct.pack("<%dI" % len(mini_fat), *mini_fat)
            + mini_data + bytes(root_sectors * 512 - len(mini_data)) + book + bytes(4096 - len(book)))
    return data, source, module[:785], literal_container(source), bytes(raw_dir)


@pytest.mark.parametrize("suffix", [".xls", ".docx"])
@pytest.mark.parametrize("codepage,encoding,text", [(50220, "iso2022_jp", "日本語"),
                                                  (50221, "iso2022_jp_ext", "日本語ｱ"),
                                                  (50225, "iso2022_kr", "한국어")])
def test_full_original_dir_recovers_stateful_source_through_native_consumers(tmp_path, suffix, codepage, encoding, text):
    # GIVEN a full licensed dir with only its uint16 codepage value changed and exact source bytes.
    from brewdoc.inspection import _inspect_document

    project, source, prefix, compressed, directory = encoded_public_project(codepage, encoding, text)
    assert struct.unpack_from("<HIH", directory, 30) == (3, 2, codepage), "record offset30 and codepage value offset36 must remain distinct"
    path = tmp_path / ("native" + suffix)
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("word/embeddings/project.bin", project)
    payload = {".xls": project, ".docx": output.getvalue()}[suffix]
    source_context = InspectionContext()
    source_entries = read_compound(project, source_context, "original")
    assert source_context.coverage[("original", "inventory")]["status"] == "complete", "actual reconstructed CFB must validate before any macro expectation"
    assert decompress_vba(next(item["data"] for item in source_entries if item["path"] == "VBA/dir")) == directory, "the actual CFB must contain the full modified directory bytes"
    path.write_bytes(payload)
    # WHEN the real native XLS and package callback paths inspect the reconstructed CFB source.
    report, artifacts = _inspect_document(path)
    modules = [item for item in report["findings"] if item["kind"] == "vba_module"]
    # THEN all source and compiled bytes survive their exact project encoding.
    assert [item["details"]["module_name"] for item in modules] == ["ThisWorkbook", "Sheet1", "Sheet2", "Sheet3", "testVBA"], "all five real modules must recover through both consumers"
    finding = modules[-1]
    assert (finding["text"], finding["details"]["encoding"], finding["details"]["codepage"]) == (
        source.decode(encoding), encoding, codepage), "stateful source must preserve exact Unicode and decoder provenance"
    assert (artifacts[finding["artifact"]], artifacts[finding["details"]["compiled_prefix"]],
            artifacts[finding["details"]["compressed_source"]]) == (source, prefix, compressed), "source, compiled prefix and compressed container must remain independent exact artifacts"
    assert hashlib.sha256(artifacts[finding["artifact"]]).digest() == hashlib.sha256(source).digest(), "recovered source hash must match the original encoded bytes"
    raw = next(item for item in report["components"] if item["document"] == finding["document"]
               and item["kind"] == "compound_stream" and item["location"].get("path") == "VBA/testVBA")
    assert artifacts[raw["artifact"]] == prefix + compressed, "the full original module stream must remain retrievable beside its derived artifacts"


def test_iso2022_halfwidth_variants_are_not_aliased_to_each_other():
    # GIVEN distinct escape and shift representations for halfwidth Katakana.
    escape = b"\x1b(I1\x1b(B"
    shifted = b"\x0e1\x0f"
    assert escape.decode("iso2022_jp_ext") == "ｱ", "the exact 50221 escape sequence must decode as halfwidth Katakana"
    assert shifted.decode("iso2022_jp_ext") == "\x0e1\x0f", "the standard Japanese decoder does not implement 50222 shift semantics"
    # WHEN an actual full project declares the unsupported SO/SI identifier.
    project, _source, _prefix, _compressed, _directory = encoded_public_project(50222, "ascii", "\x0e1\x0f")
    context = InspectionContext()
    entries = read_compound(project, context, "d")
    inspect_vba(project, "project.bin", context, "d", streams=entries)
    # THEN unsupported semantics remain opaque rather than being decoded as literal control bytes.
    assert context.findings == [], "50222 must not inherit an incompatible Japanese decoder"
    assert context.coverage[("d", "macros")]["status"] == "unavailable", "unsupported shift variant must remain explicitly incomplete"
    original = next(item for item in entries if item["path"] == "VBA/testVBA")
    assert context.payloads[original["artifact"]] == original["data"], "unsupported variant must retain exact raw module evidence"
