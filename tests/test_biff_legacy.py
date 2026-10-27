"""Version-aware legacy BIFF source fields use exact synthetic bytes."""

import struct

import pytest

from brewdoc._biff_legacy import (legacy_cached_values, legacy_codepage, legacy_defined_name, legacy_external_name,
                                 legacy_extern_sheet, legacy_formula, parse_legacy_cell)
from brewdoc._inspection_common import InspectionContext, InspectionLimit


@pytest.mark.parametrize("version,tokens,reason", [
    (2, b"\x1a" + bytes(6), "truncated legacy BIFF field"),
    (3, b"\x1a" + bytes(10) + b"\x1e\x2a\0", "unmatched legacy BIFF tSheet"),
    (4, b"\x1b" + bytes(4), "unmatched legacy BIFF tEndSheet"),
    (2, b"\x1a" + bytes(7) + b"\x1e\x2a\0\x1b" + bytes(2), "truncated legacy BIFF field"),
])
def test_legacy_sheet_context_rejects_truncation_and_unpaired_framing(version, tokens, reason):
    # GIVEN a native context wrapper whose declared pairing cannot be accounted for
    assert tokens[0] in (0x1A, 0x1B), "the fixture must contain an original legacy wrapper token"
    # WHEN the passive decoder checks source structure
    with pytest.raises(ValueError, match=reason):
        legacy_formula(tokens, version=version, codepage="cp1252", sheet_contexts=[])
    # THEN source framing is refused before any resolved binding can be returned
    assert tokens.hex().startswith(("1a", "1b")), "the decoder must leave its immutable source intact"


def test_legacy_sheet_context_requires_explicit_unknown_binding_consumer():
    # GIVEN a paired legacy wrapper with no verified interpretation of its binding bytes
    source = b"\x1a" + bytes(7) + b"\x1e\x2a\0\x1b" + bytes(3)
    # WHEN a caller requests a resolved formula without accepting unknown context evidence
    with pytest.raises(ValueError, match="sheet-context binding is undocumented"):
        legacy_formula(source, version=2, codepage="cp1252")
    # THEN an implicit local formula cannot replace its original unresolved scope
    assert len(source) == 15, "the refusal must concern the complete paired source wrapper"


@pytest.mark.parametrize("identifier,source,expected", [
    (1252, b"caf\xe9", "café"), (10000, b"caf\x8e", "café"),
    (32768, b"caf\x8e", "café"), (32769, b"caf\xe9", "café"),
    (932, b"\x93\x8c\x8b\x9e", "東京"), (28591, b"caf\xe9", "café"),
])
def test_legacy_declared_codepages_use_excel_aliases_without_silent_fallback(identifier, source, expected):
    # GIVEN an original native string with a declared Excel codepage
    assert source.hex() in ("636166e9", "6361668e", "938c8b9e"), "the fixture must retain exact encoded source bytes"
    # WHEN the declared numeric codepage selects its standard-library codec
    actual = source.decode(legacy_codepage(identifier))
    # THEN Macintosh aliases and multibyte text retain their original characters
    assert actual == expected, "unsupported cp10000-style Python aliases must not discard recoverable text"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_formula_decodes_byte_strings_and_reference_flag_locations(version):
    # GIVEN source CP1252 text and a relative C5 reference in the legacy row flags
    tokens = b"\x17\x04caf\xe9\x44" + struct.pack("<HB", 0xC004, 2) + b"\x08"
    assert tokens[2:6] == b"caf\xe9", "the source must contain original codepage bytes"
    # WHEN a legacy formula is normalized without evaluation
    result = legacy_formula(tokens, version=version, codepage="cp1252")
    # THEN text and original cell flags remain exact
    assert result == '("café"&C5)', "legacy references must not use the BIFF8 column flag layout"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_relative_shared_references_sign_extend_native_row_and_column_widths(version):
    # GIVEN native negative row and column offsets from cell D6
    tokens = b"\x2c" + struct.pack("<HB", 0xFFFF, 0xFF)
    assert len(tokens) == 4, "legacy cell references use three payload bytes"
    # WHEN the source relative reference is reconstructed
    result = legacy_formula(tokens, version=version, codepage="cp1252", row=5, column=3)
    # THEN both signed source offsets resolve against the consuming address
    assert result == "C5", "relative offsets must use 14-bit rows and 8-bit columns"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_relative_references_wrap_at_the_original_grid_not_biff8_rows(version):
    # GIVEN a relative source offset one row and column before A1
    tokens = b"\x2c" + struct.pack("<HB", 0xFFFF, 0xFF)
    assert tokens.hex() == "2cffffff", "both original signed offset fields must contain minus one"
    # WHEN the reference is interpreted at the top-left source cell
    actual = legacy_formula(tokens, version=version, codepage="cp1252", row=0, column=0)
    # THEN wrapping uses the legacy 16384-row and 256-column grid
    assert actual == "IV16384", "legacy wrapping must not manufacture BIFF8 row 65536"


@pytest.mark.parametrize("version,tail", [(2, b"\x03"), (3, b""), (4, b""), (5, b"menudeschs")])
def test_legacy_names_preserve_version_headers_and_descriptive_fields(version, tail):
    # GIVEN a name header for its declared version and a three-byte integer formula
    tokens = b"\x1e\x2a\x00"
    heads = {
        2: bytes((2, 2, 0, 4, 3)),
        3: struct.pack("<HBBH", 9, 0, 4, 3),
        4: struct.pack("<HBBH", 9, 0, 4, 3),
        5: struct.pack("<HBBHHHBBBB", 9, 0, 4, 3, 2, 1, 4, 4, 1, 1),
    }
    source = heads[version] + b"Name" + tokens + tail
    assert source.endswith(tokens + tail), "the source must retain exact native formula and suffix bytes"
    # WHEN name fields are decoded by source version
    actual = legacy_defined_name(source, version, "cp1252")
    # THEN descriptive fields and the original token bytes remain separate
    expected = {
        "name": "Name", "flags": 2 if version == 2 else 9, "macro_type": 2 if version == 2 else 0,
        "shortcut": 0, "scope": 1 if version == 5 else 0, "extern_index": 2 if version == 5 else 0,
        "tokens": tokens, "menu": "menu" if version == 5 else "",
        "description": "desc" if version == 5 else "", "help_topic": "h" if version == 5 else "",
        "status_bar": "s" if version == 5 else "",
    }
    assert actual == expected, "legacy names must not consume the BIFF8 fourteen-byte header in every version"


@pytest.mark.parametrize("payload,version,expected", [
    (b"\x06\x03Inputs", 5, {"kind": "internal-sheet", "target": None, "sheet": "Inputs", "original": "\x03Inputs"}),
    (b"\x01\x02", 5, {"kind": "caller-sheet", "target": None, "sheet": None, "original": "\x02"}),
    (b"\x01:", 5, {"kind": "add-in", "target": None, "sheet": None, "original": ":"}),
    (b"\x0d\x01sub\x03book.xls", 4, {"kind": "external", "target": "sub\\book.xls", "sheet": None,
                                        "original": "\x01sub\x03book.xls"}),
])
def test_legacy_external_sheet_retains_original_encoding_and_static_target(payload, version, expected):
    # GIVEN one original byte-counted local link-table item
    assert payload[1:].decode("cp1252") == expected["original"], "the source reference bytes must match the declared original encoding"
    # WHEN its declared legacy link representation is decoded
    actual = legacy_extern_sheet(payload, version, "cp1252")
    # THEN original evidence remains beside any statically decoded target
    assert actual == expected, "caller-dependent references must not acquire an invented workbook path"


def test_biff5_3d_and_external_name_tokens_keep_embedded_source_indices():
    # GIVEN BIFF5 internal sheet and external name tokens with their native padding
    internal = b"\x3a" + struct.pack("<h", -1) + bytes(8) + struct.pack("<HHHB", 1, 1, 0xC004, 2)
    external = b"\x39" + struct.pack("<h", 1) + bytes(8) + struct.pack("<H", 1) + bytes(12)
    assert (len(internal), len(external)) == (18, 25), "the native token widths must match BIFF5"
    # WHEN original sheet and external-name tables provide exact source identities
    actual = (
        legacy_formula(internal, version=5, codepage="cp1252", sheets=("Main", "Bob's input")),
        legacy_formula(external, version=5, codepage="cp1252", external_names={(0, 1): "'[book.xls]Inputs'!Rate"}),
    )
    # THEN formulas retain the original scope and apostrophe escaping
    assert actual == ("'Bob''s input'!C5", "'[book.xls]Inputs'!Rate"), "BIFF5 must not decode name padding as BIFF8 token data"


def test_biff5_internal_namex_keeps_explicit_sheet_instead_of_global_guess():
    # GIVEN an explicitly scoped internal name and a native negative sheet-table index
    tokens = b"\x39" + struct.pack("<h", -1) + bytes(8) + struct.pack("<H", 1) + bytes(12)
    assert len(tokens) == 25, "the BIFF5 internal name token must retain its native field widths"
    # WHEN source sheet and defined-name tables provide their identities
    actual = legacy_formula(tokens, version=5, codepage="cp1252", names=("Rate",), external=("'Inputs'",))
    # THEN explicit scope remains in the reconstructed source expression
    assert actual == "'Inputs'!Rate", "internal NameX must not silently become an unqualified global name"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_dde_cache_preserves_typed_values_native_dimensions_and_shared_limit(version):
    # GIVEN source DDE results containing a number, codepage text, error and explicit blank
    source = b"\x04\x01\x00\x01" + struct.pack("<d", 42) + b"\x02\x04caf\xe9"
    source += b"\x10\x07" + bytes(7) + b"\x00" + bytes(8)
    context = InspectionContext()
    assert source[:3] == b"\x04\x01\x00", "legacy DDE dimensions must retain four columns and one row"
    # WHEN the cached source is decoded without contacting its service
    actual = legacy_cached_values(source, version, "cp1252", context=context)
    # THEN all value types and exact error tokens survive independently
    assert actual == [[
        {"value": 42.0, "value_type": "number", "error": None},
        {"value": "café", "value_type": "string", "error": None},
        {"value": "#DIV/0!", "value_type": "error", "error": "#DIV/0!"},
        {"value": None, "value_type": "blank", "error": None},
    ]], "legacy cache metadata must not use decremented BIFF8 dimensions"
    assert (context.consumed["records"], context.consumed["decoded_bytes"]) == (
        4, 5,
    ), "cache expansion and decoded text must charge the shared operation"


def test_legacy_dde_cache_refuses_before_expanding_beyond_shared_record_budget():
    # GIVEN a source cache with two values and allowance for only one semantic record
    source = b"\x02\x01\x00\x00" + bytes(8) + b"\x00" + bytes(8)
    context = InspectionContext(limits={"records": 1})
    assert context.consumed["records"] == 0, "the shared counter must start unused"
    # WHEN the native dimensions would exceed the cumulative allowance
    with pytest.raises(InspectionLimit, match="records limit exceeded"):
        legacy_cached_values(source, 5, "cp1252", context=context)
    # THEN refusal occurs before a semantic cache value is admitted
    assert context.consumed["records"] == 0, "oversized cache dimensions must not partially allocate their record set"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_array_dimensions_and_codepage_strings_are_not_biff8_counts(version):
    # GIVEN a two-column one-row array containing a number and a codepage string
    tokens = b"\x20" + bytes(6 if version == 2 else 7)
    extra = struct.pack("<BH", 2, 1) + b"\x01" + struct.pack("<d", 7) + b"\x02\x04caf\xe9"
    assert extra[:3] == b"\x02\x01\x00", "legacy dimensions store actual counts rather than decremented counts"
    # WHEN the array is recovered without evaluating its enclosing formula
    actual = legacy_formula(tokens, extra, version=version, codepage="cp1252")
    # THEN the original dimensions and decoded characters remain exact
    assert actual == '{7,"café"}', "array source counts and native text must survive normalization"


@pytest.mark.parametrize("version,kind", [(2, 0x0006), (3, 0x0206), (4, 0x0406), (5, 0x0006)])
def test_legacy_formula_cells_keep_cache_and_version_specific_token_start(version, kind):
    # GIVEN an AA100 formula with native cell attributes and a cached numeric result
    tokens = b"\x1e\x2a\x00"
    source = struct.pack("<HH", 99, 26) + bytes(3 if version == 2 else 2) + struct.pack("<d", 42)
    source += bytes(1 if version == 2 else 2) + (bytes(4) if version == 5 else b"")
    source += (bytes((len(tokens),)) if version == 2 else struct.pack("<H", len(tokens))) + tokens
    assert source.endswith(tokens), "the native formula token source must remain exact"
    # WHEN the declared record layout is read
    actual = parse_legacy_cell(kind, source, version, "cp1252")
    # THEN source address, numeric cache and token bytes remain distinct
    assert actual == {
        "row": 99, "column": 26, "value": 42.0, "value_type": "number", "formula": None,
        "cached_value_state": "present", "error": None, "tokens": tokens, "extra": b"",
    }, "legacy cell layouts must not infer missing caches from an incorrect token offset"


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_legacy_values_keep_error_blank_and_non_ascii_strings_separate(version):
    # GIVEN addressed native errors, explicit blank cells and a CP1252 label
    prefix = struct.pack("<HH", 4, 2) + bytes(3 if version == 2 else 2)
    label = prefix + (b"\x04" if version == 2 else b"\x04\x00") + b"caf\xe9"
    assert prefix[:4] == b"\x04\x00\x02\x00", "the source address must remain C5"
    # WHEN each source cell is decoded before cached-value coercion
    actual = [parse_legacy_cell(kind, payload, version, "cp1252") for kind, payload in (
        (0x0005, prefix + b"\x07\x01"), (0x0001, prefix), (0x0004, label))]
    # THEN blanks, errors and literal text retain different source states
    assert [(item["value"], item["value_type"], item["error"], item["cached_value_state"])
            for item in actual] == [
        ("#DIV/0!", "error", "#DIV/0!", "not_formula"), (None, "blank", None, "blank"),
        ("café", "string", None, "not_formula"),
    ], "legacy cell evidence must preserve error identity and explicit source blanks"


@pytest.mark.parametrize("payload,version,expected", [
    (b"\x04Rate\x03\x00\x1e\x2a\x00", 2, ("Rate", 0, 0, b"\x03\x00\x1e\x2a\x00")),
    (b"\x02\x00\x04Rate\x03\x00\x1e\x2a\x00", 3, ("Rate", 2, 0, b"\x03\x00\x1e\x2a\x00")),
    (b"\x02\x00\x01\x00\x00\x00\x04Rate\x03\x00\x1e\x2a\x00", 5,
     ("Rate", 2, 1, b"\x03\x00\x1e\x2a\x00")),
])
def test_legacy_external_names_keep_definition_or_cache_bytes_without_utf16_flags(payload, version, expected):
    # GIVEN a byte-string external name and its original following definition
    assert payload.endswith(b"\x1e\x2a\x00"), "the source definition must retain its exact integer token"
    # WHEN the record is read using its version-specific prefix
    actual = legacy_external_name(payload, version, "cp1252")
    # THEN scope, flags and undecorated source bytes remain available to the caller
    assert (actual["name"], actual["flags"], actual["scope"], actual["definition"]) == expected, "external name parsing must not consume a nonexistent Unicode flags byte"


@pytest.mark.parametrize("operation,reason", [
    (lambda: legacy_formula(b"\x44\x01", version=5, codepage="cp1252"), "truncated"),
    (lambda: legacy_formula(b"\x1e\x2a\x00", version=5, codepage="cp1252", max_characters=2), "size limit"),
    (lambda: legacy_defined_name(bytes((0, 0, 0, 1, 1)) + b"N\x16\x02", 2, "cp1252"), "inconsistent"),
    (lambda: legacy_extern_sheet(b"\x03abcZ", 5, "cp1252"), "unvisited"),
    (lambda: parse_legacy_cell(0x0004, bytes(7) + b"\x04a", 2, "cp1252"), "truncated"),
    (lambda: legacy_formula(b"\x77", version=5, codepage="cp1252"), "unsupported"),
])
def test_legacy_malformed_unknown_and_exhausted_input_refuses_instead_of_empty_success(operation, reason):
    # GIVEN a source operation with an invalid or exhausted explicit boundary
    assert callable(operation) is True, "the fixture must provide one bounded source operation"
    # WHEN the reader reaches that exact boundary
    with pytest.raises(ValueError, match=reason):
        operation()
    # THEN no successful empty recovered value can escape the refusal
