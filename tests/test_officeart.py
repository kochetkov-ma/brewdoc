"""Synthetic OfficeArt and BIFF object evidence checks."""

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from brewdoc._inspection_common import InspectionContext
from brewdoc._officeart import inspect_biff_object, inspect_officeart


def record(kind, payload, *, instance=0, version=0):
    return struct.pack("<HHI", instance << 4 | version, kind, len(payload)) + payload


def test_shape_hyperlink_and_description_keep_source_offsets():
    # GIVEN a shape with actual pihlShape and description properties.
    target = "https://example.org/shape"
    encoded = (target + "\0").encode("utf-16le")
    link = bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b")
    link += struct.pack("<III", 2, 0x101, len(encoded) // 2) + encoded
    description = "Shape alt\0".encode("utf-16le")
    props = struct.pack("<HIHI", 0x8381, len(description), 0x8382, len(link))
    data = record(0xF004, record(0xF00B, props + description + link, instance=2, version=3), version=15)
    context = InspectionContext()
    # WHEN the assembled drawing is inspected.
    inspect_officeart(data, context, "doc", location={"stream": "Workbook", "record_offset": 100})
    # THEN both meanings and assembled offsets are retained.
    assert [(item["kind"], item["target"], item["text"]) for item in context.findings] == [
        ("shape-description", None, "Shape alt"), ("shape-hyperlink", target, None)
    ], "shape properties should decode by their verified IDs"
    assert context.findings[1]["location"]["record_offset"] == 100, "physical BIFF origin must remain unchanged"
    assert context.findings[1]["location"]["officeart_offset"] == 8, "nested offset is relative to assembled drawing"


def test_fbse_embedded_png_retains_exact_image_bytes():
    # GIVEN a BLIP within an FBSE rather than a container record.
    image = b"\x89PNG\r\n\x1a\nsynthetic"
    blip = record(0xF01E, b"U" * 16 + b"\xff" + image, instance=0x6E0)
    fbse = bytes(20) + struct.pack("<III", len(blip), 1, 0) + bytes(4)
    data = record(0xF007, fbse + blip, version=2)
    context = InspectionContext()
    # WHEN the BLIP store entry is inspected.
    inspect_officeart(data, context, "doc")
    # THEN image framing is removed without altering the payload.
    finding, = context.findings
    assert context.payloads[finding["artifact"]] == image, "retrieved PNG must match source exactly"
    assert finding["details"]["sha256"] == hashlib.sha256(image).hexdigest(), "image hash must identify payload bytes"
    assert finding["location"]["officeart_payload_offset"] == 69, "payload offset includes FBSE and BLIP headers"


@pytest.mark.parametrize("data,reason", [
    (b"x", "truncated OfficeArt header"),
    (struct.pack("<HHI", 0, 0xF01E, 100), "truncated OfficeArt payload"),
    (record(0xFFFF, b"https://not-a-parsed-link"), "unknown OfficeArt record"),
])
def test_malformed_and_unknown_drawings_report_incomplete_coverage(data, reason):
    # GIVEN malformed or semantically unknown drawing bytes.
    context = InspectionContext()
    # WHEN passive inspection runs.
    inspect_officeart(data, context, "doc")
    # THEN no target is guessed from arbitrary byte patterns.
    assert context.findings == [], "opaque bytes must not become semantic findings"
    assert reason in context.coverage[("doc", "links")]["reason"], "missing coverage must explain the parser boundary"


def test_nesting_limit_stops_before_deeper_inventory():
    # GIVEN nested containers exceeding the configured bound.
    data = record(0xF004, record(0xF004, record(0xF00A, bytes(8)), version=15), version=15)
    context = InspectionContext(limits={"nesting": 1})
    # WHEN the nesting allowance is exhausted.
    inspect_officeart(data, context, "doc")
    # THEN the refusal is recorded without descending.
    assert [item["limit"] for item in context.exhausted] == ["nesting"], "container stack needs the shared nesting bound"
    assert len(context.components) == 2, "deeper shape must not be admitted"


def test_object_macro_name_and_control_source_are_passive_evidence():
    # GIVEN FtCmo, FtMacro, picture flags and FtPictFmla with a control stream offset.
    cmo = struct.pack("<HHHHH", 0x15, 18, 8, 7, 0) + bytes(12)
    macro_body = struct.pack("<HI", 5, 0) + b"\x23" + struct.pack("<I", 3) + b"\0"
    macro = struct.pack("<HH", 4, len(macro_body)) + macro_body
    flags = struct.pack("<HHH", 8, 2, 0x20)
    formula_body = struct.pack("<HI", 5, 0) + b"\x02" + bytes(4) + b"\x03\0\0"
    picture_body = struct.pack("<H", len(formula_body)) + formula_body + struct.pack("<II", 123, 45)
    picture = struct.pack("<HH", 9, len(picture_body)) + picture_body
    data = cmo + macro + flags + picture + bytes(4)
    context = InspectionContext()
    # WHEN the object subrecords are parsed.
    inspect_biff_object(data, context, "doc")
    # THEN indexed macro identity and Ctls source are recovered without execution.
    assert [(item["kind"], item["target"]) for item in context.findings] == [
        ("biff-control", None), ("object-macro", "defined-name-index:3"),
        ("object-data-source", "Ctls")
    ], "object actions and storage references need structured recovery"
    assert context.findings[-1]["details"]["stream_offset"] == 123, "Ctls offset must come from FtPictFmla"
    assert context.findings[-1]["details"]["byte_size"] == 45, "Ctls size must come from FtPictFmla"


def test_payload_limit_refuses_before_retaining_drawing():
    # GIVEN a drawing larger than its payload allowance.
    context = InspectionContext(limits={"payload_bytes": 1})
    # WHEN source preservation is attempted.
    inspect_officeart(record(0xF004, b"", version=15), context, "doc")
    # THEN no payload is retained and coverage explains the limit.
    assert context.payloads == {}, "oversized raw evidence must not be retained"
    assert context.exhausted[0]["limit"] == "payload_bytes", "shared payload allowance must be enforced"


def test_complex_pib_contains_retrievable_blip():
    # GIVEN an inline BLIP in a verified complex pib property.
    image = b"synthetic jpeg"
    blip = record(0xF01D, bytes(16) + b"\xff" + image, instance=0x46A)
    data = record(0xF00B, struct.pack("<HI", 0x8104, len(blip)) + blip, instance=1, version=3)
    context = InspectionContext()
    # WHEN the property is inspected.
    inspect_officeart(data, context, "doc")
    # THEN its exact image bytes are available.
    finding, = context.findings
    assert context.payloads[finding["artifact"]] == image, "complex pib must expose its embedded image"


def test_metafile_decompression_obeys_declared_decoded_size():
    # GIVEN a compressed EMF BLIP with a valid OfficeArtMetafileHeader.
    image = b"synthetic EMF" * 8
    compressed = zlib.compress(image)
    header = struct.pack("<I", len(image)) + bytes(24) + struct.pack("<I", len(compressed)) + b"\x00\xfe"
    data = record(0xF01A, bytes(16) + header + compressed, instance=0x3D4)
    context = InspectionContext()
    # WHEN the bounded decompressor reads the framed payload.
    inspect_officeart(data, context, "doc")
    # THEN the raw drawing and decoded metafile are separately retained.
    finding, = context.findings
    assert context.payloads[finding["artifact"]] == image, "metafile payload must be reconstructed exactly"
    assert context.consumed["decoded_bytes"] == len(data) + len(image), "decoded bytes must use operation-wide accounting"


def test_shape_script_is_recovered_without_interpretation():
    # GIVEN a framed wzScript property rather than arbitrary script-looking bytes.
    script = "alert('synthetic');"
    encoded = (script + "\0").encode("utf-16le")
    data = record(0xF00B, struct.pack("<HI", 0x838E, len(encoded)) + encoded, instance=1, version=3)
    context = InspectionContext()
    # WHEN property evidence is decoded.
    inspect_officeart(data, context, "doc")
    # THEN exact script source is retained as passive evidence.
    assert [(item["capability"], item["text"]) for item in context.findings] == [
        ("scripts", script)
    ], "shape script should be exposed without evaluation"
    finding, = context.findings
    assert context.payloads[finding["artifact"]] == encoded, "script artifact must preserve exact UTF16LE bytes"
    assert finding["details"]["sha256"] == hashlib.sha256(encoded).hexdigest(), "script hash must describe source bytes"


@pytest.mark.parametrize("limit", ["decoded_bytes", "retained_bytes", "records"])
def test_drawing_uses_operation_wide_resource_limits(limit):
    # GIVEN an exhausted cumulative allowance.
    context = InspectionContext(limits={limit: 0})
    # WHEN even one empty shape container is admitted.
    inspect_officeart(record(0xF004, b"", version=15), context, "doc")
    # THEN the shared context records the refusal.
    assert context.exhausted[0]["limit"] == limit, "drawing parser must use the operation-wide counter"
    assert context.findings == [], "resource refusal must not invent semantic results"


@pytest.mark.parametrize("name", ["calamine-any_sheets.xls", "calamine-date.xls", "calamine-merge_cells.xls",
                                   "calamine-xls_formula.xls", "census-st-est00int-01.xls"])
def test_public_workbook_drawing_metadata_does_not_hide_native_content(name):
    # GIVEN the public source bytes, including their original drawing records.
    from brewdoc._compound import read_compound
    from brewdoc._biff import CAPABILITIES, inspect_biff_stream

    path = Path(__file__).parent / "fixtures" / "xls" / name
    data = path.read_bytes()
    context = InspectionContext()
    document = context.document(data, name, parent=None, location={"kind": "source"})
    streams = read_compound(data, context, document)
    workbook = next(item for item in streams if item["name"] in ("Workbook", "Book"))
    # WHEN the original workbook stream is inspected with its native companions.
    inspect_biff_stream(workbook["data"], workbook["name"], context, document, streams=streams)
    # THEN known native drawing style data leaves requested content fully inspected.
    assert {cap: context.coverage[(document, cap)]["status"] for cap in CAPABILITIES} == dict.fromkeys(
        CAPABILITIES, "complete"), "public native records must not inherit false unknown-style failures"


def test_source_shaped_split_menu_colors_have_typed_roles():
    # GIVEN exact F11E bytes from calamine-date.xls Workbook offset 1140.
    data = bytes.fromhex("40001ef1100000000d0000080c00000817000008f7000010")
    context = InspectionContext()
    # WHEN the four MSOCR values are inspected.
    inspect_officeart(data, context, "doc", location={"record_offset": 1070, "officeart_source_offset": 66})
    # THEN source color indexes retain roles without unrelated content failures.
    colors, = [item for item in context.components if item["kind"] == "officeart-split-menu-colors"]
    assert colors["details"]["colors"] == [
        {"role": "fill", "red": 13, "green": 0, "blue": 0, "scheme_index": True},
        {"role": "line", "red": 12, "green": 0, "blue": 0, "scheme_index": True},
        {"role": "shadow", "red": 23, "green": 0, "blue": 0, "scheme_index": True},
        {"role": "3d", "red": 247, "green": 0, "blue": 0, "scheme_index": False}
    ], "F11E colors need the documented order and scheme bit"
    assert context.coverage == {}, "style data must not invent missing link or script coverage"


@pytest.mark.parametrize("data", [record(0xF11E, bytes(12), instance=4),
                                    record(0xF00B, struct.pack("<HI", 0x8197, 1) + b"x", instance=1, version=3),
                                    record(0xF00B, struct.pack("<HI", 0x83FF, 0), instance=1, version=3)])
def test_known_style_lengths_and_unknown_required_properties_are_not_suppressed(data):
    # GIVEN invalid style framing or an unknown complex property.
    context = InspectionContext()
    # WHEN the helper inspects the source.
    inspect_officeart(data, context, "doc")
    # THEN the parser keeps explicit incomplete evidence.
    assert context.coverage[("doc", "inventory")]["status"] == "unavailable", "invalid or unknown records need explicit inventory diagnostics"


def test_checkbox_state_scroll_defaults_and_linked_cell_are_decoded():
    # GIVEN documented native control subrecords and a linked absolute cell.
    cmo = struct.pack("<HHHHH", 0x15, 18, 11, 3, 0) + bytes(12)
    check = struct.pack("<HHHHHH", 0x12, 8, 2, 65, 0, 1)
    scroll = struct.pack("<HHIhhhhhhhH", 0x0C, 20, 0, 5, 0, 10, 1, 2, 0, 12, 1)
    tokens = b"\x24" + struct.pack("<HH", 2, 1)
    formula = struct.pack("<HI", len(tokens), 0) + tokens + b"\0"
    link = struct.pack("<HH", 0x14, len(formula)) + formula
    context = InspectionContext()
    # WHEN the OBJ payload is inspected without constructing a control.
    inspect_biff_object(cmo + check + scroll + link + bytes(4), context, "doc")
    # THEN native values and the linked reference remain available.
    values = {item["kind"]: item["details"] for item in context.findings}
    assert values["checkbox-state"]["checked"] == 2, "mixed checkbox state must not be coerced to bool"
    assert values["scroll-control"]["value"] == 5, "native scroll value must survive"
    assert values["scroll-control"]["maximum"] == 10, "native scroll bounds must survive"
    assert values["object-linked-formula"]["formula"] == "$B$3", "linked control cell must be decoded from tokens"


def test_native_list_items_and_selected_values_are_recovered():
    # GIVEN FtLbsData with two uncontinued strings and multiple-selection bytes.
    cmo = struct.pack("<HHHHH", 0x15, 18, 18, 5, 0) + bytes(12)
    body = bytes(2) + struct.pack("<HHHH", 2, 2, 0x12, 0) + b"\1\0\0A\1\0\0B" + b"\0\1"
    context = InspectionContext()
    # WHEN list subrecords are decoded.
    inspect_biff_object(cmo + struct.pack("<HH", 0x13, len(body)) + body, context, "doc")
    # THEN order, selected indexes and literal values are preserved.
    finding, = [item for item in context.findings if item["kind"] == "list-control"]
    assert finding["details"]["items"] == ["A", "B"], "list strings must retain source order"
    assert finding["details"]["selected_indexes"] == [2], "selection flags must remain one-based native indexes"


def test_fill_image_url_and_gradient_are_typed_without_partial_coverage():
    # GIVEN nonempty fill BLIP, its declared URL, and an IMsoArray gradient.
    image = b"synthetic fill"
    blip = record(0xF01E, bytes(16) + b"\xff" + image, instance=0x6E0)
    name = "https://example.org/fill\0".encode("utf-16le")
    gradient = struct.pack("<HHHIIII", 2, 2, 8, 0x112233, 0, 0x445566, 65536)
    props = struct.pack("<HIHIHIHI", 0x8186, len(blip), 0x8187, len(name), 0x0188, 2, 0x8197, len(gradient))
    context = InspectionContext()
    # WHEN structured fill properties are decoded.
    inspect_officeart(record(0xF00B, props + blip + name + gradient, instance=4, version=3), context, "doc")
    # THEN image bytes, URL and numeric color positions remain exact.
    finding, = [item for item in context.findings if item["kind"] == "officeart-blip"]
    assert context.payloads[finding["artifact"]] == image, "fill image must use the same exact BLIP retrieval"
    assert [item["target"] for item in context.findings if item["kind"] == "fill-blip-name"] == [
        "https://example.org/fill"], "declared URL flags must determine target interpretation"
    assert [item["details"] for item in context.components if item["kind"] == "officeart-gradient-color"] == [
        {"color": 0x112233, "position": 0.0}, {"color": 0x445566, "position": 1.0}
    ], "gradient fields must follow IMsoArray and MSOSHADECOLOR layouts"
    assert context.coverage == {}, "known fill metadata must not degrade unrelated capabilities"


@pytest.mark.parametrize("body", [struct.pack("<HHH", 0x12, 2, 1),
                                    struct.pack("<HHHHHH", 0x12, 8, 3, 0, 0, 0)])
def test_invalid_native_control_lengths_or_values_remain_incomplete(body):
    # GIVEN a malformed checkbox subrecord.
    context = InspectionContext()
    # WHEN native fields are parsed.
    inspect_biff_object(body + bytes(4), context, "doc")
    # THEN invalid controls remain explicitly incomplete.
    assert context.coverage[("doc", "forms")]["status"] == "unavailable", "malformed native controls must not be treated as decoded"
