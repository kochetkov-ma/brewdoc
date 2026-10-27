"""Exact inline image source boundaries over admitted PDF content segments."""

import base64
import zlib

import pytest
from pdfminer.psparser import LIT
from pdfminer.pdfinterp import PDFContentParser
from pdfminer.pdftypes import PDFStream

from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc._pdf_content import ContentError, iter_content


LOCATION = {"kind": "pdf", "object_id": 4}


def _parse(data, *, context=None, colors=None, decode=None, version=(1, 7)):
    segments = [{"source": {"object_id": index + 4}, "data": part}
                for index, part in enumerate(data if isinstance(data, list) else [data])]
    return list(iter_content(segments, context or InspectionContext(), LOCATION,
                            resolve_color=lambda name: (colors or {}).get(name),
                            version=version, decode=decode))


def test_binary_inline_preserves_lf_and_split_source_spans():
    # GIVEN a binary ID separator CR and a genuine first payload LF
    source = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID\r\n\x00\x0a EI /Im Do"
    start = source.index(b"ID") + 3
    assert source[start:start + 3] == b"\n\x00\n", "sample bytes include both genuine LF bytes"
    # WHEN the payload spans two original Contents members
    records = _parse([source[:start + 1], source[start + 1:]])
    # THEN the exact encoded samples and local source offsets survive
    assert records[0]["payload"] == b"\n\x00\n", "binary image samples must never be trimmed"
    assert records[0]["payload_span"] == [start, start + 3], "logical sample extent must be exact"
    assert records[0]["payload_segments"] == [
        {"source": {"object_id": 4}, "segment_ordinal": 1, "span": [start, start + 1]},
        {"source": {"object_id": 5}, "segment_ordinal": 2, "span": [0, 2]},
    ], "split Contents must retain original segment identities"
    assert [(item["operator"], item["operator_ordinal"]) for item in records] == [
        ("BI", 1), ("Do", 2)], "inline ID/EI are one operator occurrence"


def test_named_color_space_and_internal_ei_sample_bytes_are_preserved():
    # GIVEN a named Indexed colour space with marker-like samples
    source = b"BI /W 4 /H 1 /CS /Named /BPC 8 ID  EI  EI"
    assert source.count(b"EI") == 2, "one marker-like EI belongs to image data"
    # WHEN resource interpretation supplies one indexed component
    records = _parse(source, colors={"Named": [LIT("Indexed"), LIT("DeviceRGB"), 255, b""]})
    # THEN framing follows dimensions and repeated records are deterministic
    assert records[0]["payload"] == b" EI ", "internal EI and ending whitespace are samples"
    assert _parse(source, colors={"Named": [LIT("Indexed"), LIT("DeviceRGB"), 255, b""]}) == records, "repeated parsing must preserve every record"


def test_flate_extent_uses_eof_before_following_operators():
    # GIVEN a compressed image followed by real content
    payload = zlib.compress(b" EI \x00")
    source = b"BI /W 5 /H 1 /CS /G /BPC 8 /F /Fl ID " + payload + b" EI Q"
    assert payload.endswith(b"\x02\xd4\x00\xcf"), "the fixture has a fixed compressed checksum"
    # WHEN outer framing measures the actual zlib EOF
    records = _parse(source, decode=lambda attrs, data, context, location: zlib.decompress(data))
    # THEN all encoded bytes survive separately from the following operator
    assert records[0]["payload"] == payload, "the zlib terminator belongs to the payload"
    assert records[1]["operator"] == "Q", "framing must resume at the real following operator"


def _bits(value):
    return int(value.ljust((len(value) + 7) // 8 * 8, "0"), 2).to_bytes((len(value) + 7) // 8, "big")


def _image(payload, *, codec=b"CCF", params=b"", width=8):
    return (b"BI /W " + str(width).encode() + b" /H 1 /CS /G /BPC 1 /F /" + codec
            + b" /DP << " + params + b" >> ID " + payload + b" EI Q")


def test_native_inline_loss_reproduces_against_installed_parser():
    # GIVEN the original source bytes whose trailing LF is a real RGB component
    payload = b"\xff\x00\x0a"
    source = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID " + payload + b"EI Q"
    assert source.index(payload) == 32, "the measured payload start is byte 32"
    # WHEN the pinned native parser applies its inline EI heuristic
    native = PDFContentParser([PDFStream({}, source)]).nextobject()[1]
    # THEN the existing defect remains reproduced independently of our helper
    assert native.rawdata == b"\xff\x00", "native inline extraction removes the genuine final LF"
    assert _parse(source)[0]["payload"] == payload, "measured source framing preserves the sample LF that also precedes EI"


@pytest.mark.parametrize(("bits", "params"), [
    ("1" + "000000000001" * 2, b"/K -1 /Columns 8"),
    ("10000000" + "000000000001" * 2, b"/K -7 /Columns 8 /EncodedByteAlign true"),
    ("1", b"/K -1 /Columns 8 /Rows 1 /EndOfBlock false"),
    ("0000000000011", b"/K -1 /Columns 8 /Rows 1 /EndOfBlock false /EndOfLine true"),
    ("0000001111" + "1111111" + "00000010" + "000000000001" * 2, b"/K -1 /Columns 8"),
    ("10011", b"/K 0 /Columns 8 /Rows 1 /EndOfBlock false"),
    ("000000000001" + "10011" + "000000000001" * 6, b"/K 0 /Columns 8 /EndOfLine true"),
    ("10011" + "000000000001" * 6, b"/K 0 /Columns 8 /Rows 99"),
    ("0000000000011" + "10011" + "0000000000010" + "1"
     + "0000000000011" * 6, b"/K 2 /Columns 8 /Rows 99 /EndOfLine true"),
])
def test_ccitt_group3_and_group4_have_exact_physical_extents(bits, params):
    # GIVEN real native white-row codes and RTC/EOFB or explicit Rows termination
    payload = _bits(bits)
    source = _image(payload, params=params)
    assert len(payload) == (len(bits) + 7) // 8, "the fixture retains its final physical padding byte"
    # WHEN framing consumes rows without native printing or hidden byte skips
    records = _parse(source)
    # THEN termination overrides Rows where required and preserves every encoded byte
    assert records[0]["payload"] == payload, "CCITT extent includes the real terminator and final padding"
    assert [record["operator"] for record in records] == ["BI", "Q"], "encoded bits cannot absorb the following Q"


def test_dct_multiple_scans_ignore_app_eoi_stuffing_and_restart_markers():
    # GIVEN EOI-like APP bytes, stuffed entropy, restart, fill and a second scan
    payload = (b"\xff\xd8\xff\xe1\x00\x08abc\xff\xd9x"
               b"\xff\xda\x00\x02\x01\xff\x00\xd9\xff\xd0\x02"
               b"\xff\xff\xda\x00\x02\x03\xff\xd9")
    source = _image(payload, codec=b"DCT")
    assert payload.count(b"\xff\xd9") == 2, "APP bytes contain a false EOI before the actual terminator"
    # WHEN DCT framing follows marker lengths and entropy transitions
    records = _parse(source)
    # THEN every scan and original marker byte belongs to the encoded payload
    assert records[0]["payload"] == payload, "only the actual EOI after both scans terminates the image"
    assert records[1]["operator"] == "Q", "DCT framing must stop before the next content operator"


@pytest.mark.parametrize("encoded", [
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAgBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APf6/9k=",
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wgALCAABAAgBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAABv/aAAgBAQAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oACAEBAAAAEP8A/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=",
])
def test_real_baseline_and_progressive_jpeg_bytes_have_exact_extents(encoded):
    # GIVEN synthetic 8 by 1 white grayscale JPEGs generated once with Pillow
    payload = base64.b64decode(encoded)
    source = b"BI /W 8 /H 1 /CS /G /BPC 8 /F /DCT ID " + payload + b" EI Q"
    assert len(payload) in (332, 281), "baseline and progressive fixtures retain their full original bytes"
    # WHEN framing uses JPEG syntax without importing a pixel decoder
    records = _parse(source)
    # THEN real baseline and progressive scans stop at the actual source EOI
    assert records[0]["payload"] == payload, "both real JPEG representations must remain byte-identical"


@pytest.mark.parametrize(("payload", "params"), [
    (bytes.fromhex("00135140"), b"/K 0 /Columns 8 /Rows 1 /EndOfBlock false /EndOfLine true"),
    (bytes.fromhex("26a280080080"), b"/K -1 /Columns 8"),
])
def test_real_group3_and_group4_strip_bytes_preserve_nonwhite_rows(payload, params):
    # GIVEN synthetic 8 by 1 TIFF strip bytes generated once with libtiff via Pillow
    source = _image(payload, params=params)
    assert payload.hex() in ("00135140", "26a280080080"), "nonwhite run fixtures retain physical strip bytes"
    # WHEN native run tables and 2D state consume the original encoded rows
    records = _parse(source)
    # THEN meaningful nonwhite data supports both required fax modes
    assert records[0]["payload"] == payload, "Group 3 and Group 4 strip framing must preserve exact encoded data"


@pytest.mark.parametrize(("payload", "codec", "decoded"), [
    (b"20 45 49 20 00>", b"AHx", b" EI \x00"),
    (b"z~>", b"A85", b"\x00" * 4),
    (b"\x04 EI \x00\x80", b"RL", b" EI \x00"),
    (_bits("100000000" + "000100000" + "100000001"), b"LZW", b" "),
])
def test_outer_filter_terminators_preserve_encoded_source(payload, codec, decoded):
    # GIVEN permitted encoded data containing real filter framing
    source = (b"BI /W " + str(len(decoded)).encode() + b" /H 1 /CS /G /BPC 8 /F /"
              + codec + b" ID " + payload + b" EI Q")
    assert type(payload) is bytes, "encoded filter source is immutable byte evidence"
    # WHEN the injected decoder validates admitted intermediate transformations
    records = _parse(source, decode=lambda *args: decoded)
    # THEN the artifact retains the outer encoded representation including its terminator
    assert records[0]["payload"] == payload, "decoded samples must never replace the encoded payload"
    assert records[1]["operator"] == "Q", "outer framing must stop at the real codec terminator"


@pytest.mark.parametrize(("source", "reason"), [
    (b"BI /W 1 /H 1 /CS /Missing /BPC 8 ID x EI", "ColorSpace resource"),
    (b"BI /W 0 /H 1 /CS /G /BPC 8 ID x EI", "dimensions"),
    (b"BI /W 1 /H 1 /IM true /BPC 8 ID x EI", "BitsPerComponent"),
    (b"BI /W 1 /H 1 /IM true /CS /G ID x EI", "mask must not"),
    (b"BI /W 1 /H 1 /CS /G /BPC 8 IDx x EI", "dictionary key"),
    (b"BI /W 1 /H 1 /CS /G /BPC 8 ID x Q", "following EI"),
    (b"BI /W 1 /H 1 /CS /G /BPC 8 ID xEI", "whitespace and EI"),
    (b"BI /W 1 BI /H 1 ID x EI", "dictionary key"),
    (b"BI 7 1 ID x EI", "dictionary key"),
    (b"ID x EI", "unexpected content operator"),
    (b"EI", "unexpected content operator"),
    (b"[1 >> Do", "invalid container"),
    (b"<< 7 1 >> Do", "dictionary keys"),
    (_image(b"abc", codec=b"JPXDecode"), "forbidden"),
    (_image(b"abc", codec=b"JBIG2Decode"), "forbidden"),
    (_image(b"\xff\xd8\xff\xda\x00\x01", codec=b"DCT"), "marker length"),
    (_image(b"\xff\xd8\xff\xd9", codec=b"DCT"), "no scan"),
    (_image(b"\x80", params=b"/K -1 /Columns 8"), "CCITT"),
    (_image(b"\x80", params=b"/K -1 /Columns 8 /EndOfBlock false"), "Rows is required"),
    (_image(b"\x80", params=b"/K -1 /Columns 8 /DamagedRowsBeforeError 1"), "damaged-row"),
    (_image(b"\xff", params=b"/K -1 /Columns 8 /Rows 1 /EndOfBlock false /EncodedByteAlign true"), "nonzero fill"),
    (_image(_bits("0000011"), params=b"/K -1 /Columns 8 /Rows 1 /EndOfBlock false"), "vertical run"),
    (_image(b"\xff", params=b"/K 2 /Columns 8 /Rows 1 /EndOfBlock false /EndOfLine true"), "EOL"),
    (b"BI /W 1 /H 1 /CS /G /BPC 8 /F /Fl ID BAD EI", "Flate framing"),
])
def test_malformed_framing_keeps_a_measured_ambiguous_span(source, reason):
    # GIVEN malformed source that cannot establish an exact inline artifact
    assert type(source) is bytes, "malformed bytes remain the original container evidence"
    # WHEN framing cannot prove its source extent
    with pytest.raises(ContentError, match=reason) as failure:
        _parse(source)
    # THEN the refusal carries measured logical offsets for caller coverage diagnostics
    assert type(failure.value.span) is list, "caller receives a half-open measured span with the refusal"


@pytest.mark.parametrize(("source", "limits", "limit"), [
    (b"/" + b"x" * 200 + b" Do", {"payload_bytes": 32}, "payload_bytes"),
    (b"(" + b"x" * 200 + b") Tj", {"payload_bytes": 32}, "payload_bytes"),
    (b"[[[[1]]]] Do", {"nesting": 3}, "nesting"),
    (b"[1 2 3 4 5] Do", {"records": 5}, "records"),
    (_image(b"\x80", params=b"/K -1 /Columns 1000000000"), {}, "payload_bytes"),
    (_image(b"\x80", params=b"/K -1 /Columns 5000"), {"decoded_bytes": 2000}, "decoded_bytes"),
])
def test_token_container_and_fax_row_limits_refuse_before_growth(source, limits, limit):
    # GIVEN source exceeding one allowance in the caller's existing context
    context = InspectionContext(limits=limits)
    assert context.consumed["decoded_bytes"] == 0, "the shared context starts with no decoder work"
    # WHEN the helper reaches the next token, nesting, record or row allocation
    with pytest.raises(InspectionLimit, match=limit):
        _parse(source, context=context)
    # THEN budget evidence remains on that same operation context
    assert context.exhausted[0]["limit"] == limit, "no sibling budget may hide the actual refusal"


def test_operator_tokens_and_operands_cross_segments_with_plain_exact_records():
    # GIVEN one resource name and one Do token split across physical content members
    source = [b"/I", b"m D", b"o"]
    assert b"".join(source) == b"/Im Do", "the logical operator has two split lexical tokens"
    # WHEN the segment cursor preserves token state without inserting whitespace
    records = _parse(source)
    # THEN the record carries plain values and all original decoded source spans
    assert records == [{
        "operator": "Do", "operator_ordinal": 1, "operands": ["/Im"], "resource_name": "Im",
        "span": [0, 6], "span_basis": "decoded-stream", "segments": [
            {"source": {"object_id": 4}, "segment_ordinal": 1, "span": [0, 2]},
            {"source": {"object_id": 5}, "segment_ordinal": 2, "span": [0, 3]},
            {"source": {"object_id": 6}, "segment_ordinal": 3, "span": [0, 1]},
        ],
    }], "consumer records must not fabricate a single physical stream offset"


@pytest.mark.parametrize(("color", "payload"), [
    ([LIT("Separation"), LIT("Spot"), LIT("DeviceRGB"), {}], b"x"),
    ([LIT("ICCBased"), {"N": 3}], b"xyz"),
    ([LIT("DeviceN"), [LIT("A"), LIT("B")], LIT("DeviceRGB"), {}], b"xy"),
    ([LIT("CalGray"), {}], b"x"),
    ([LIT("Lab"), {}], b"xyz"),
])
def test_named_colour_component_evidence_controls_unfiltered_extent(color, payload):
    # GIVEN the current resource interpretation's declared native colour space
    source = b"BI /W 1 /H 1 /CS /Named /BPC 8 ID " + payload + b" EI"
    assert type(color) is list, "resource declarations retain their native PDF array form"
    # WHEN component count follows the actual named declaration
    records = _parse(source, colors={"Named": color})
    # THEN one component is never silently replaced by an assumed RGB size
    assert records[0]["payload"] == payload, "colour components determine the exact packed sample extent"


def test_pdf2_abbreviated_keys_take_precedence_and_mask_uses_one_bit_samples():
    # GIVEN full keys that conflict with PDF 2.0 abbreviated keys
    source = b"BI /Width 100 /W 8 /Height 20 /H 1 /IM true ID \xff EI"
    assert source.count(b"/W") == 2, "both full and short width keys are present"
    # WHEN the checked effective version selects the adopted abbreviation rule
    records = _parse(source, version=(2, 0))
    # THEN mask defaults and the selected dimensions yield one exact encoded byte
    assert records[0]["payload"] == b"\xff", "the specified one-bit mask must use the abbreviated dimensions"
    assert records[0]["attributes"]["Width"] == 8, "plain attributes expose the chosen interpretation"


def test_filter_chain_validates_terminal_dct_and_keeps_outer_ascii_bytes():
    # GIVEN two filters whose original source representation is ASCIIHex
    jpeg = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wgALCAABAAgBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAABv/aAAgBAQAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oACAEBAAAAEP8A/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=")
    encoded = jpeg.hex().encode() + b">"
    source = b"BI /W 8 /H 1 /CS /G /BPC 8 /F [/AHx /DCT] /DP [null null] ID " + encoded + b" EI"
    calls, context = [], InspectionContext()
    assert len(jpeg) == 281, "the terminal image is the genuine progressive JPEG fixture"
    # WHEN the caller validates the outer transform using the same cumulative context
    def decode(attrs, data, active_context, location):
        calls.append((attrs["DecodeParms"], active_context is context, location["operator"]))
        result = bytes.fromhex(data[:-1].decode())
        active_context.charge("decoded_bytes", len(result), location)
        return result

    records = _parse(source, context=context, decode=decode)
    # THEN the helper proves the terminal image extent without replacing encoded evidence
    assert calls == [([None, None], True, "BI")], "decoder parameters, context and source location must be passed exactly"
    assert records[0]["payload"] == encoded, "ASCII source remains the exact artifact after terminal DCT validation"


def test_repeated_inline_images_have_distinct_source_spans_and_deterministic_cost():
    # GIVEN two equal payloads in distinct physical operator occurrences
    image = b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI"
    source = image + b" " + image
    first_context, second_context = InspectionContext(), InspectionContext()
    assert len(image) == 34, "the source fixture freezes each operator's full extent"
    # WHEN both passes use independent operation contexts with the same allowances
    first, second = _parse(source, context=first_context), _parse(source, context=second_context)
    # THEN equal payload bytes preserve distinct occurrences and deterministic accounting
    assert first == second, "all repeated operator and source records must remain deterministic"
    assert [(item["operator_ordinal"], item["payload_span"]) for item in first] == [
        (1, [30, 31]), (2, [65, 66]),
    ], "equal bytes must not erase distinct inline source occurrences"
    assert first_context.consumed == second_context.consumed, "shared allowance counters must repeat exactly"


def test_filtered_inline_validation_is_required_and_checks_sample_size():
    # GIVEN a real Flate representation with one grayscale sample
    payload = zlib.compress(b"x")
    source = b"BI /W 1 /H 1 /CS /G /BPC 8 /F /Fl ID " + payload + b" EI"
    assert zlib.decompress(payload) == b"x", "the encoded source has exactly one declared sample"
    # WHEN no bounded caller decoder is supplied, or its validated output has a wrong extent
    with pytest.raises(ContentError, match="requires bounded decoder"):
        _parse(source)
    with pytest.raises(ContentError, match="size differs"):
        _parse(source, decode=lambda *args: b"xx")
    # THEN framing cannot publish an exact artifact after incomplete validation


def test_fax_and_filter_work_share_preconsumed_context_capacity():
    # GIVEN the same valid Group 4 source with most cumulative capacity already used
    source = _image(_bits("1" + "000000000001" * 2), params=b"/K -1 /Columns 8")
    context = InspectionContext(limits={"decoded_bytes": 2000})
    context.charge("decoded_bytes", 1990, LOCATION)
    assert context.remaining("decoded_bytes") == 10, "only the caller's remaining work capacity is available"
    # WHEN token or row framing reaches the inherited operation limit
    with pytest.raises(InspectionLimit, match="decoded_bytes"):
        _parse(source, context=context)
    # THEN cumulative usage is preserved rather than renewed in a helper-local budget
    assert context.exhausted[0]["maximum"] == 2000, "the original operation allowance governs every framing stage"


@pytest.mark.parametrize("numeric", [b"1e3", b"1garbage", b"1.2.3", b"1-2"])
def test_inline_dimensions_cannot_discard_a_native_numeric_token_tail(numeric):
    # GIVEN a single PDF lexical number that native parsing splits into multiple tokens
    source = b"BI /W " + numeric + b" /H 1 /CS /G /BPC 8 ID x EI"
    assert source[6:6 + len(numeric)] == numeric, "the malformed width stays one source lexical extent"
    # WHEN the helper requires one complete legal PDF number interpretation
    with pytest.raises(ContentError, match="numeric token|native token") as failure:
        _parse([source[:8], source[8:]])
    # THEN no prefix number may fabricate an exact one-byte inline image
    assert failure.value.span[0] == 0, "the refused interpretation remains located at its original BI"
    assert failure.value.segments == [
        {"source": {"object_id": 4}, "segment_ordinal": 1, "span": [0, 8]},
        {"source": {"object_id": 5}, "segment_ordinal": 2, "span": [0, len(numeric) - 2]},
    ], "malformed numeric tails must retain each original source member location"


@pytest.mark.parametrize("endline", [b"true", b"false"])
def test_genuine_group3_eol_fill_does_not_get_discarded_at_row_alignment(endline):
    # GIVEN the independent reviewer's real four-row Group 3 strip with EOL fill bits
    payload = bytes.fromhex("00011f1f1d000135c7c7c0013543e3e380011f1f1d00")
    source = (b"BI /W 8 /H 4 /CS /G /BPC 1 /F /CCF /DP << /K 0 /Columns 8 /Rows 4 "
              b"/EndOfLine " + endline + b" /EndOfBlock false /EncodedByteAlign true >> ID " + payload + b" EI Q")
    assert len(payload) == 22, "the reproduced strip retains all four encoded rows and fill bytes"
    # WHEN row framing honours alignment of encoded rows containing their EOL prefix
    records = _parse(source)
    # THEN exact encoded evidence survives and the next content operator remains separate
    assert records[0]["payload"] == payload, "valid Group 3 EOL fill belongs to the measured physical extent"
    assert records[1]["operator"] == "Q", "EOL alignment must not absorb following source content"


@pytest.mark.parametrize("k", [1, 2, 3, 37])
def test_genuine_mixed_group3_is_identical_for_every_positive_k_interpretation(k):
    # GIVEN the reviewer's genuine mixed Group 3 strip, interpreted under positive K
    payload = bytes.fromhex("0018f8f8e8004924a0033543e3e38009a494")
    source = (b"BI /W 8 /H 4 /CS /G /BPC 1 /F /CCF /DP << /K " + str(k).encode()
              + b" /Columns 8 /Rows 4 /EndOfLine true /EndOfBlock false >> ID " + payload + b" EI Q")
    assert len(payload) == 18, "the mixed-mode strip remains byte-identical for each positive K value"
    # WHEN per-line encoding tags determine one-dimensional versus two-dimensional coding
    records = _parse(source)
    # THEN K magnitude cannot reject a valid mixed-mode representation or change its extent
    assert records[0]["payload"] == payload, "all positive K values must retain the same genuine mixed strip"
    assert records[1]["operator"] == "Q", "positive K must not change following operator provenance"


@pytest.mark.parametrize("endline", [b"true", b"false"])
def test_genuine_aligned_mixed_group3_preserves_eol_fill_and_line_tags(endline):
    # GIVEN the same 32-pixel pattern with libtiff Group 3 two-dimensional and fill flags
    payload = bytes.fromhex("00018f8f8e8001249280019aa1f1f1c001349280")
    source = (b"BI /W 8 /H 4 /CS /G /BPC 1 /F /CCF /DP << /K 1 /Columns 8 /Rows 4 "
              b"/EndOfLine " + endline + b" /EndOfBlock false /EncodedByteAlign true >> ID " + payload + b" EI Q")
    assert len(payload) == 20, "the mixed aligned strip retains its exact encoded extent"
    # WHEN EOL fill precedes each line's one-dimensional/two-dimensional tag
    records = _parse(source)
    # THEN mixed mode and alignment compose without dropping a tag or a physical byte
    assert records[0]["payload"] == payload, "both EOL declarations must preserve the genuine mixed aligned strip"
    assert records[1]["operator"] == "Q", "the measured mixed strip must stop before the next operator"


@pytest.mark.parametrize("number", [b"+1", b"01", b"1.", b".5", b"-.5"])
def test_complete_pdf_decimal_tokens_keep_native_numeric_interpretations(number):
    # GIVEN one complete legal decimal token followed by a normal operator
    source = number + b" w"
    assert b"e" not in number.lower(), "the legal fixture uses PDF decimal syntax without exponents"
    # WHEN the bounded lexical extent has exactly one native token interpretation
    records = _parse(source)
    # THEN native numeric values survive the strict full-token check
    assert records[0]["operands"] == [float(number)], "valid decimal tokens must preserve their native numeric value"
