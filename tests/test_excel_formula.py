"""Exact passive formula token regressions for both workbook dialects."""

import struct

import pytest

from brewdoc._biff import _formula
from brewdoc._excel_formula import CETAB, FTAB, FTAB_ARGC, decode_formula


@pytest.mark.parametrize(("token", "expected"), [(0x0C, "(2>=1)"), (0x0D, "(2>1)")])
def test_biff_comparators_follow_microsoft_token_identity(token, expected):
    # GIVEN numeric operands and a source comparator whose opcode is authoritative
    data = b"\x1e\x02\x00\x1e\x01\x00" + bytes((token,))
    assert len(data) == 7, "the comparator source must contain exactly two literals and one operator"
    # WHEN the BIFF decoder reconstructs source without calculating its value
    actual = _formula(data)
    # THEN greater and greater-or-equal preserve their distinct source opcodes
    assert actual == expected, "0x0C is PtgGe and 0x0D is PtgGt in Microsoft BIFF"


@pytest.mark.parametrize(("function", "arguments", "call_kind", "expected"), [
    (1, (1, 2, 3), "fixed", "IF(1,2,3)"),
    (110, (1, 2, 3, 4), "fixed", "EXEC(1,2,3,4)"),
    (336, (1, 2), "variable", "CONCATENATE(1,2)"),
])
def test_biff_function_metadata_preserves_fixed_and_variable_calls(function, arguments, call_kind, expected):
    # GIVEN exact source function IDs beyond the original decoder's guessed arities
    operands = b"".join(b"\x1e" + struct.pack("<H", value) for value in arguments)
    call = {"fixed": b"\x21", "variable": b"\x22" + bytes((len(arguments),))}[call_kind]
    data = operands + call + struct.pack("<H", function)
    assert data.endswith(struct.pack("<H", function)), "source function identity must remain exact"
    # WHEN the native token sequence is decoded passively
    actual = _formula(data)
    # THEN metadata supplies real names and arities without executing the call
    assert actual == expected, "fixed and variable function metadata must match the authoritative table"


def test_biff_xlm_command_table_is_distinct_from_function_table():
    # GIVEN command 0x0004 SAVE encoded with the CETAB flag
    data = b"\x22\x00\x04\x80"
    assert data[-2:] == b"\x04\x80", "the high function-table bit must remain set in source"
    # WHEN a command ID also used by SUM is decoded
    actual = _formula(data)
    # THEN the source remains a literal SAVE command instead of becoming SUM
    assert actual == "SAVE()", "XLM command IDs must use CETAB rather than Ftab"


@pytest.mark.parametrize("dialect", ["biff8", "biff12"])
def test_excel_external_name_and_udf_preserve_exact_target_and_source_identity(dialect):
    # GIVEN an externally qualified function name and exact native source IDs
    target = "'[https://example.invalid/Book.xls]Input'!Run"
    names = {(2, 3): target}
    tokens = b"\x39" + struct.pack("<HI", 2, 3) + b"\x1e\x07\x00\x22\x02\xff\x00"
    assert tokens[:7] == b"\x39\x02\x00\x03\x00\x00\x00", "NameX source identity must stay exact"
    # WHEN both dialects resolve the same external-name evidence without loading it
    actual = decode_formula(tokens, dialect=dialect, external_names=names)
    # THEN UDF reconstruction uses the original target rather than a placeholder
    assert actual == target + "(7)", "source-qualified UDF names must remain literal"


@pytest.mark.parametrize(("dialect", "reserved", "extra"), [
    ("biff8", 7, b"\x01\x01\x00" + b"\x01" + struct.pack("<d", 42)
     + b'\x02\x03\x00\x00a"b' + b"\x04\x01" + b"\x00" * 7
     + b"\x10\x07" + b"\x00" * 7),
    ("biff12", 14, struct.pack("<II", 2, 2) + b"\x00" + struct.pack("<d", 42)
     + b'\x01\x03\x00a\x00"\x00b\x00' + b"\x02\x01" + b"\x04\x07\x00\x00\x00"),
])
def test_excel_array_constants_preserve_typed_values_and_quoted_strings(dialect, reserved, extra):
    # GIVEN a two-by-two native array containing number, string, Boolean and error
    tokens = b"\x40" + b"\x00" * reserved
    assert extra.count(struct.pack("<d", 42)) == 1, "fixture must retain one exact numeric source value"
    # WHEN each dialect reads its own auxiliary array layout
    actual = decode_formula(tokens, extra, dialect=dialect)
    # THEN literals preserve array shape, quotes, Boolean type and exact error token
    assert actual == '{42,"a""b";TRUE,#DIV/0!}', "native array contents must remain source-faithful"


@pytest.mark.parametrize(("dialect", "ref", "expected"), [
    ("biff8", struct.pack("<HH", 65535, 65535), "B4"),
    ("biff12", struct.pack("<IH", 0xFFFFFFFF, 65535), "B4"),
    ("biff8", struct.pack("<HH", 65531, 65533), "IV65536"),
    ("biff12", struct.pack("<IH", 0xFFFFFFFB, 65533), "XFD1048576"),
])
def test_excel_relative_references_use_native_signed_width_and_wrap(dialect, ref, expected):
    # GIVEN relative offsets applied to the original C5 formula cell
    tokens = b"\x2c" + ref
    assert tokens[0] == 0x2C, "fixture must use an explicit relative-reference token"
    # WHEN relative row/column values are resolved using the format grid dimensions
    actual = decode_formula(tokens, dialect=dialect, row=4, column=2)
    # THEN negative offsets and grid wrapping retain the correct original cell
    assert actual == expected, "signed references must use the native dialect widths"


@pytest.mark.parametrize(("dialect", "area_size"), [("biff8", 8), ("biff12", 12)])
def test_excel_reference_error_tokens_keep_external_scope(dialect, area_size):
    # GIVEN one external broken area reference with a retained supporting-link identity
    tokens = b"\x3d\x00\x00" + b"\x00" * area_size
    assert len(tokens) == 3 + area_size, "the error reference must retain its complete source structure"
    # WHEN the source error is decoded without replacing its target
    actual = decode_formula(tokens, dialect=dialect, external=("'Inputs'",))
    # THEN the broken reference still identifies its original scope
    assert actual == "'Inputs'!#REF!", "external reference errors must keep their source scope"


@pytest.mark.parametrize(("tokens", "extra", "reason"), [
    (b"\x39\x02\x00\x03\x00\x00\x00", b"", "unresolved Excel external name 2:3"),
    (b"\x40" + b"\x00" * 7, b"\xff\xff\xff", "invalid or truncated Excel array dimensions"),
    (b"\x18\x19", b"", "unsupported Excel formula token 0x18"),
    (b"\x1e\x01", b"", "truncated Excel formula field at byte 1"),
    (b"\x1e\x01\x00", b"unvisited", "unconsumed Excel formula auxiliary bytes at byte 0"),
])
def test_excel_unresolved_malformed_and_unvisited_formula_source_is_not_accepted(tokens, extra, reason):
    # GIVEN exact malformed or unsupported source bytes
    assert type(tokens) is bytes, "the refusal case must preserve its native source bytes"
    # WHEN source reconstruction cannot account for the supplied evidence
    with pytest.raises(ValueError) as caught:
        decode_formula(tokens, extra, dialect="biff8")
    # THEN the failure identifies the missing or unreadable semantics
    assert str(caught.value) == reason, "unknown evidence must not produce a successful placeholder"


def test_excel_formula_output_is_bounded_before_retaining_reconstructed_source():
    # GIVEN a string source whose reconstruction exceeds a small explicit allowance
    tokens = b"\x17\x05\x00hello"
    assert len(tokens) == 8, "fixture must contain exactly five source string characters"
    # WHEN passive decoding reaches the explicit output boundary
    with pytest.raises(ValueError) as caught:
        decode_formula(tokens, dialect="biff8", max_characters=6)
    # THEN the limit refuses explicitly without truncating literal evidence
    assert str(caught.value) == "Excel formula output limit exceeded", "formula source must never be silently shortened"


def test_excel_full_metadata_preserves_function_and_command_names_without_placeholders():
    # GIVEN the pinned function metadata and normative command enumeration
    assert (len(FTAB), len(FTAB_ARGC), len(CETAB)) == (485, 485, 396), "both complete source tables must remain intact"
    # WHEN distant named entries and overlapping table IDs are read
    actual = (FTAB[110], FTAB_ARGC[110], FTAB[484], FTAB_ARGC[484], CETAB[4], CETAB[0x328])
    # THEN source identity and arity metadata stay distinct across tables
    assert actual == ("EXEC", 4, "AVERAGEIFS", 129, ("SAVE", 0, 0), ("HIDEALL.INKANNOTS", 0, 1)), (
        "function and command names must come from retained authoritative metadata")


@pytest.mark.parametrize("flags,first,last,expected", [
    (1, 0, 0, "Sales[Amount]"),
    (2, 0, 1, "Sales[[Amount]:[Rate]]"),
    (4, 0, 0, "Sales[#All]"),
    (65, 0, 0, "Sales[[#This Row],[Amount]]"),
    (25, 1, 1, "Sales[[#Headers],[#Data],[Rate]]"),
])
def test_excel_structured_references_use_native_table_column_and_row_selectors(flags, first, last, expected):
    # GIVEN a complete native PtgList and admitted source table names
    tokens = b"\x18\x19" + struct.pack("<HHIHH", 2, flags, 7, first, last)
    tables = {(2, 7): {"name": "Sales", "columns": ("Amount", "Rate")}}
    assert len(tokens) == 14, "PtgList retains exact native scope/table/column fields"
    # WHEN formula reconstruction resolves the explicit source table context
    actual = decode_formula(tokens, dialect="biff12", tables=tables)
    # THEN each encoded row/column selection keeps its original table identities
    assert actual == expected, "structured references must not invent table or column labels"


def test_excel_nonresident_table_reference_uses_exact_auxiliary_names_and_source_scope():
    # GIVEN an external structured reference carries its table and column in native auxiliary data
    tokens = b"\x18\x19" + struct.pack("<HHIHH", 0, 0x2000, 0, 0, 0)
    extra = (struct.pack("<BHH", 1, 0, 5) + "Sales".encode("utf-16le")
             + struct.pack("<HBBHH", 0, 1, 0, 2, 6) + "Amount".encode("utf-16le"))
    assert extra.endswith("Amount".encode("utf-16le")), "the original auxiliary source contains the column name"
    # WHEN source reconstruction consumes the complete native auxiliary structure
    actual = decode_formula(tokens, extra, dialect="biff12", external=("'[remote.xlsb]Sheet'",))
    # THEN both the exact external source scope and native table/column names remain literal
    assert actual == "'[remote.xlsb]Sheet'!Sales[Amount]", "external table references must preserve source names"


def test_excel_pivot_names_are_source_scoped_and_unresolved_indexes_refuse():
    # GIVEN a PtgSxName identifies the second source-scoped cache field
    tokens = b"\x18\x1d" + struct.pack("<I", 1)
    assert len(tokens) == 6, "the pivot name token carries one native index"
    # WHEN reconstruction receives actual source field identities
    actual = decode_formula(tokens, dialect="biff12", pivot_names=("Revenue", "Cost Total"))
    # THEN the exact source field name is quoted without substituting an invented label
    assert actual == "'Cost Total'", "pivot fields must resolve within the formula's own PName collection"
    with pytest.raises(ValueError, match="unresolved Excel pivot name 1"):
        decode_formula(tokens, dialect="biff12", pivot_names=("Revenue",))


@pytest.mark.parametrize("kind,expected", [("caller-sheet", "CALLER_SHEET!$A$1"), ("add-in", "ADDIN_SCOPE!$A$1")])
def test_excel_runtime_dependent_scopes_retain_typed_symbolic_operands_without_fabricated_targets(kind, expected):
    # GIVEN a source supporting-link type has no statically known runtime target
    tokens = b"\x3a" + struct.pack("<HIH", 0, 0, 0)
    details = []
    assert len(tokens) == 9, "the 3D source reference includes its actual native supporting-link index"
    # WHEN reconstruction decodes the native operation without calculating runtime context
    actual = decode_formula(tokens, dialect="biff12", external=({"kind": kind},), operand_details=details)
    # THEN symbolic source syntax has explicit unresolved-runtime provenance rather than an invented URL
    assert (actual, details) == (expected, [{"token_offset": 0, "scope_index": 0, "scope_kind": kind,
        "context_required": True, "original_target": None}]), "source-dependent scopes must remain typed and symbolic"


@pytest.mark.parametrize("tokens,tables,pivot,reason", [
    (b"\x18\x19" + struct.pack("<HHIHH", 0, 1, 7, 0, 0), {}, (), "unresolved Excel table 0:7"),
    (b"\x18\x19" + struct.pack("<HHIHH", 0, 1, 7, 1, 1),
     {(0, 7): {"name": "Sales", "columns": ("Amount",)}}, (), "invalid Excel structured-reference column bounds"),
    (b"\x18\x1d" + struct.pack("<I", 0), {}, (), "unresolved Excel pivot name 0"),
    (b"\x18\xff", {}, (), "unsupported Excel formula extension 0xFF"),
])
def test_excel_extended_tokens_never_accept_missing_source_metadata(tokens, tables, pivot, reason):
    # GIVEN missing, malformed or unsupported native extended-token evidence
    assert tokens[:1] == b"\x18", "every refusal concerns a real native extended token"
    # WHEN reconstruction cannot resolve the encoded source identity
    with pytest.raises(ValueError) as caught:
        decode_formula(tokens, dialect="biff12", tables=tables, pivot_names=pivot)
    # THEN failure names the actual missing source context without a successful placeholder
    assert str(caught.value) == reason, "unavailable metadata must remain an explicit formula failure"


@pytest.mark.parametrize("tokens,metadata", [
    (b"\x18\x19" + struct.pack("<HHIHH", 0, 1, 7, 0, 0),
     {"tables": {(0, 7): {"name": "Sales", "columns": ("x" * 1000,)}}}),
    (b"\x18\x1d" + struct.pack("<I", 0), {"pivot_names": ("x" * 1000,)}),
])
def test_excel_extended_source_names_obey_the_same_explicit_output_allowance(tokens, metadata):
    # GIVEN native metadata names exceed the caller's small formula-output allowance
    assert len(tokens) <= 14, "a small native token can reference independently large source metadata"
    # WHEN reconstruction checks admitted table or pivot names before output expansion
    with pytest.raises(ValueError) as caught:
        decode_formula(tokens, dialect="biff12", max_characters=32, **metadata)
    # THEN metadata references cannot bypass the formula-output boundary
    assert str(caught.value) == "Excel formula output limit exceeded", "all native operand paths share one output bound"
