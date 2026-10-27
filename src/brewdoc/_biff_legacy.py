"""Checked BIFF2-BIFF5 source fields and passive formula normalization."""

from __future__ import annotations

import codecs
import math
import struct

from brewdoc._excel_formula import decode_formula

ERRORS = {0: "#NULL!", 7: "#DIV/0!", 15: "#VALUE!", 23: "#REF!",
          29: "#NAME?", 36: "#NUM!", 42: "#N/A", 43: "#GETTING_DATA"}
_VERSIONS = (2, 3, 4, 5)


class _Fields:
    """Consume exact legacy fields without repairing truncated source bytes."""

    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    def take(self, size: int) -> bytes:
        """Consume one exact byte range or refuse its declared length."""
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("truncated legacy BIFF field at byte %d" % self.offset)
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def number(self, kind: str):
        """Read one checked little-endian source scalar."""
        return struct.unpack("<" + kind, self.take(struct.calcsize("<" + kind)))[0]

    def text(self, codepage: str, count: int | None = None, *, short=True) -> str:
        """Decode the declared byte-string length without replacement characters."""
        count = self.number("B" if short else "H") if count is None else count
        return self.take(count).decode(codepage)

    def finish(self) -> None:
        """Reject a successful parse that leaves source fields unvisited."""
        if self.offset != len(self.data):
            raise ValueError("unvisited legacy BIFF bytes at byte %d" % self.offset)


def _version(version: int) -> None:
    if version not in _VERSIONS:
        raise ValueError("unsupported legacy BIFF version: %s" % version)


def legacy_row(payload: bytes) -> dict:
    """Preserve BIFF2 ROW fields whose layout predates hidden and outline flags."""
    source = _Fields(payload)
    row, first, last, height = (source.number("H") for _ in range(4))
    source.take(2)
    defaults, offset = source.number("B"), source.number("H")
    if defaults not in (0, 1) or first > last or last > 256 or row > 16383:
        raise ValueError("invalid BIFF2 ROW fields")
    attributes = source.take(3).hex() if defaults else None
    style = source.number("H") if defaults else None
    source.finish()
    return {"row": row, "first_column": first, "last_column_exclusive": last,
            "height_twips": height & 0x7FFF, "default_height": bool(height & 0x8000),
            "has_default_attributes": bool(defaults), "cell_attributes_hex": attributes,
            "xf_index": style, "first_cell_relative_offset": offset,
            "hidden": None, "hidden_state": "not_stored", "outline_level": None,
            "outline_state": "not_stored"}


def biff_array_definition(payload: bytes, version: int) -> dict:
    """Read native ARRAY layouts without treating old flags as BIFF8 padding."""
    if version not in (2, 3, 4, 5, 8):
        raise ValueError("unsupported BIFF ARRAY version")
    source = _Fields(payload)
    bounds = tuple(source.number(code) for code in ("H", "H", "B", "B"))
    if bounds[0] > bounds[1] or bounds[2] > bounds[3] or version < 8 and bounds[1] > 16383:
        raise ValueError("invalid BIFF ARRAY range")
    flags = source.number("B" if version == 2 else "H")
    reserved = source.take(4).hex() if version >= 5 else None
    tokens = source.take(source.number("B" if version == 2 else "H"))
    extra = source.take(len(payload) - source.offset)
    return {"bounds": bounds, "flags": flags, "reserved_hex": reserved,
            "tokens": tokens, "extra": extra}


def biff_table_definition(payload: bytes, version: int, kind: int) -> dict:
    """Read native TABLEOP fields and retain unused and deleted input evidence."""
    if version not in (2, 3, 4, 5, 8):
        raise ValueError("unsupported BIFF data-table version")
    source = _Fields(payload)
    bounds = tuple(source.number(code) for code in ("H", "H", "B", "B"))
    if (bounds[0] > bounds[1] or bounds[2] > bounds[3] or not bounds[0] or not bounds[2]
            or version < 8 and bounds[1] > 16383):
        raise ValueError("invalid BIFF data-table range")
    flags = source.number("H")
    if version == 2:
        if kind not in (0x0036, 0x0037) or (flags & 0xFF) not in (0, 1):
            raise ValueError("invalid BIFF2 data-table record or recalculation flag")
        two = kind == 0x0037
        if not two and flags >> 8 not in (0, 1):
            raise ValueError("invalid BIFF2 data-table orientation")
        row_input = bool(flags >> 8) and not two
    else:
        if kind != 0x0236:
            raise ValueError("invalid native data-table record version")
        two, row_input = bool(flags & 8), bool(flags & 4)
    first = source.number("H"), source.number("H")
    second = (source.number("H"), source.number("H")) if two or version != 2 else None
    source.finish()
    deleted = (bool(flags & 16), bool(flags & 32)) if version == 8 else (False, False)
    for index, coordinate in enumerate((first, second) if two else (first,)):
        if deleted[index] and coordinate != (65535, 65535):
            raise ValueError("invalid deleted BIFF data-table input sentinel")
        if not deleted[index] and (coordinate[1] > 255 or coordinate[0] > (65535 if version == 8 else 16383)):
            raise ValueError("invalid BIFF data-table input coordinate")
    return {"bounds": bounds, "flags": flags, "two_variable": two,
            "row_input": row_input, "first_input": first, "second_input": second,
            "first_input_deleted": deleted[0], "second_input_deleted": deleted[1]}


def legacy_codepage(identifier: int) -> str:
    """Resolve declared Excel aliases without replacing unsupported encodings."""
    aliases = {1200: "utf-16le", 10000: "mac_roman", 10006: "mac_greek",
               10007: "mac_cyrillic", 10010: "mac_romanian", 10029: "mac_latin2",
               10079: "mac_iceland", 10081: "mac_turkish", 10082: "mac_croatian",
               20127: "ascii", 32768: "mac_roman", 32769: "cp1252", 65000: "utf-7",
               65001: "utf-8"}
    name = aliases.get(identifier, "iso8859-%d" % (identifier - 28590)
                       if 28591 <= identifier <= 28599 else "cp%d" % identifier)
    return codecs.lookup(name).name


def legacy_defined_name(payload: bytes, version: int, codepage: str) -> dict:
    """Preserve version-specific names, formula bytes and descriptive source strings."""
    _version(version)
    source = _Fields(payload)
    if version == 2:
        flags, macro_type, shortcut, count, length = (source.number("B") for _ in range(5))
        scope, extern_index, lengths = 0, 0, (0, 0, 0, 0)
    else:
        flags, shortcut, count, length = (source.number(kind) for kind in ("H", "B", "B", "H"))
        macro_type = 0
        extern_index, scope = (source.number("H"), source.number("H")) if version == 5 else (0, 0)
        lengths = tuple(source.number("B") for _ in range(4)) if version == 5 else (0, 0, 0, 0)
    name = source.text(codepage, count)
    tokens = source.take(length)
    descriptions = {key: source.text(codepage, size) for key, size in zip(
        ("menu", "description", "help_topic", "status_bar"), lengths)}
    if version == 2 and source.number("B") != length:
        raise ValueError("inconsistent BIFF2 defined-name formula length")
    source.finish()
    return {"name": name, "flags": flags, "macro_type": macro_type, "shortcut": shortcut,
            "scope": scope, "extern_index": extern_index, "tokens": tokens, **descriptions}


def legacy_extern_sheet(payload: bytes, version: int, codepage: str) -> dict:
    """Decode the original link-table name without inventing its current document path."""
    _version(version)
    source = _Fields(payload)
    count = source.number("B")
    own = source.data[source.offset:source.offset + 1] == b"\x03" and version == 5
    original = source.text(codepage, count + int(own))
    source.finish()
    if original == ":":
        return {"kind": "add-in", "target": None, "sheet": None, "original": original}
    if original in ("\x00", "\x02"):
        return {"kind": "caller-sheet", "target": None, "sheet": None, "original": original}
    if version == 5 and original.startswith("\x03"):
        return {"kind": "internal-sheet", "target": None, "sheet": original[1:], "original": original}
    if version == 5 and original == "\x04":
        return {"kind": "current-workbook", "target": None, "sheet": None, "original": original}
    encoded = original.startswith("\x01")
    value = original[1:] if encoded else original
    output, position = [], 0
    while position < len(value):
        character = value[position]
        position += 1
        if encoded and character == "\x05":
            if position >= len(value):
                raise ValueError("truncated legacy external URL length")
            size = ord(value[position])
            position += 1
            if position + size > len(value):
                raise ValueError("truncated legacy external URL")
            output.append(value[position:position + size])
            position += size
        elif encoded and character == "\x01":
            if position >= len(value):
                raise ValueError("truncated legacy external drive")
            drive = value[position]
            position += 1
            output.append("\\\\" if drive == "@" else drive + ":\\")
        elif encoded and character in ("\x02", "\x03", "\x04"):
            output.append({"\x02": "\\", "\x03": "\\", "\x04": "..\\"}[character])
        elif encoded and character in ("\x06", "\x08"):
            raise ValueError("legacy external path depends on Excel installation directories")
        elif encoded and character == "\x09" and version == 4:
            return {"kind": "internal-sheet", "target": None, "sheet": value[position:], "original": original}
        else:
            output.append(character)
    target = "".join(output)
    return {"kind": "external", "target": target, "sheet": None, "original": original}


def legacy_external_name(payload: bytes, version: int, codepage: str) -> dict:
    """Retain external-name identity, scope and exact definition/cache source bytes."""
    _version(version)
    source = _Fields(payload)
    flags = source.number("H") if version >= 3 else 0
    prefix = source.take(4) if version == 5 else b""
    label = source.text(codepage)
    return {"name": label, "flags": flags, "scope": int.from_bytes(prefix[:2], "little"),
            "storage_id": int.from_bytes(prefix, "little"),
            "definition": source.take(len(payload) - source.offset)}


def legacy_cached_values(payload: bytes, version: int, codepage: str, *, context=None,
                         location=None) -> list[list[dict]]:
    """Recover typed DDE/OLE source caches with native nondecremented dimensions."""
    _version(version)
    source = _Fields(payload)
    columns, rows = source.number("B"), source.number("H")
    columns = columns or 256
    count = columns * rows
    if not rows or count > len(payload) - source.offset:
        raise ValueError("invalid legacy cached-value dimensions")
    location = location or {"kind": "legacy-biff-cache"}
    if context is not None:
        context.check("report_bytes", context.consumed["report_bytes"] + count * 64, location)
        context.charge("records", count, location)
    output = []
    for _ in range(rows):
        values = []
        for _ in range(columns):
            kind, error = source.number("B"), None
            if kind == 0:
                source.take(8)
                value, value_type = None, "blank"
            elif kind == 1:
                value, value_type = source.number("d"), "number"
                if not math.isfinite(value):
                    raise ValueError("non-finite legacy cached number")
            elif kind == 2:
                size = source.number("B")
                if context is not None:
                    context.check("decoded_bytes", context.consumed["decoded_bytes"] + size * 4, location)
                value, value_type = source.text(codepage, size), "string"
                if context is not None:
                    context.charge("decoded_bytes", len(value.encode("utf-8")), location)
            elif kind == 4:
                value = source.number("B")
                source.take(7)
                if value not in (0, 1):
                    raise ValueError("invalid legacy cached Boolean")
                value, value_type = bool(value), "boolean"
            elif kind == 16:
                code = source.number("B")
                source.take(7)
                if code not in ERRORS:
                    raise ValueError("unknown legacy cached error")
                value, value_type, error = ERRORS[code], "error", ERRORS[code]
            else:
                raise ValueError("unknown legacy cached-value type: %d" % kind)
            values.append({"value": value, "value_type": value_type, "error": error})
        output.append(values)
    source.finish()
    return output


def _cell_reference(source: _Fields, *, relative=False, row=0, column=0) -> bytes:
    encoded_row, encoded_column = source.number("H"), source.number("B")
    flags = encoded_row & 0xC000
    target_row, target_column = encoded_row & 0x3FFF, encoded_column
    if relative and flags & 0x8000:
        target_row = (row + target_row - (0x4000 if target_row & 0x2000 else 0)) % 16384
    if relative and flags & 0x4000:
        target_column = (column + target_column - (256 if target_column & 128 else 0)) % 256
    return struct.pack("<HH", target_row, target_column | flags)


def _area_reference(source: _Fields, *, relative=False, row=0, column=0) -> bytes:
    first_row, last_row, first_column, last_column = (source.number(kind) for kind in ("H", "H", "B", "B"))
    first = _cell_reference(_Fields(struct.pack("<HB", first_row, first_column)),
                            relative=relative, row=row, column=column)
    last = _cell_reference(_Fields(struct.pack("<HB", last_row, last_column)),
                           relative=relative, row=row, column=column)
    return first[:2] + last[:2] + first[2:] + last[2:]


def _legacy_array(source: _Fields, codepage: str, maximum: int) -> bytes:
    columns, rows = source.number("B"), source.number("H")
    columns = columns or 256
    if not rows or columns * rows > len(source.data) - source.offset:
        raise ValueError("invalid legacy array dimensions")
    if maximum < 3:
        raise ValueError("legacy BIFF normalized formula size limit exceeded")
    output = bytearray(struct.pack("<BH", columns - 1, rows - 1))
    for _ in range(columns * rows):
        kind = source.number("B")
        if kind == 2:
            text = source.text(codepage)
            value = struct.pack("<HB", len(text), 1) + text.encode("utf-16le")
        elif kind in (0, 1, 4, 16):
            value = source.take(8)
        else:
            raise ValueError("unknown legacy array value type: %d" % kind)
        if len(output) + len(value) + 1 > maximum:
            raise ValueError("legacy BIFF normalized formula size limit exceeded")
        output.append(kind)
        output.extend(value)
    return bytes(output)


def legacy_formula(tokens: bytes, extra: bytes = b"", *, version: int, codepage: str,
                   row=0, column=0, names=(), external=(), external_names=None, sheets=(),
                   max_characters=1_048_576, sheet_contexts=None) -> str:
    """Normalize documented legacy fields, then reuse the shared passive RPN decoder."""
    _version(version)
    source, auxiliary = _Fields(tokens), _Fields(extra)
    output, trailing, scopes = bytearray(), bytearray(), list(external)
    pending_contexts = []
    name_refs = dict(external_names or {})
    while source.offset < len(tokens):
        raw = source.number("B")
        base = (raw & 31) | 32 if 32 <= raw < 128 else raw
        output.append(raw)
        if raw == 0x1A and version <= 4:
            output.pop()
            header = source.take(7 if version == 2 else 10)
            pending_contexts.append((source.offset, header))
        elif raw == 0x1B and version <= 4:
            output.pop()
            if not pending_contexts:
                raise ValueError("unmatched legacy BIFF tEndSheet")
            start, header = pending_contexts.pop()
            inner = tokens[start:source.offset - 1]
            footer = source.take(3 if version == 2 else 4)
            if sheet_contexts is None:
                raise ValueError("legacy BIFF sheet-context binding is undocumented")
            sheet_contexts.append({"header_hex": header.hex(), "footer_hex": footer.hex(),
                                   "inner_token_hex": inner.hex(), "token_offset": start - len(header) - 1,
                                   "binding_state": "unavailable", "binding_reason": "undocumented legacy sheet-context fields"})
        elif raw in range(3, 0x17):
            pass
        elif raw == 0x17:
            text = source.text(codepage)
            output.extend(struct.pack("<BB", len(text), 1) + text.encode("utf-16le"))
        elif raw in (0x1C, 0x1D, 0x1E, 0x1F):
            output.extend(source.take({0x1C: 1, 0x1D: 1, 0x1E: 2, 0x1F: 8}[raw]))
        elif base in (0x21, 0x22, 0x38):
            argc = source.number("B") if base in (0x22, 0x38) else None
            function = source.number("B" if version <= 3 else "H")
            if base == 0x38:
                output[-1] = (raw & 0x60) | 0x22
                function |= 0x8000
            if argc is not None:
                output.append(argc)
            output.extend(struct.pack("<H", function))
        elif base == 0x23:
            index = source.number("H")
            source.take({2: 5, 3: 8, 4: 8, 5: 12}[version])
            output.extend(struct.pack("<I", index))
        elif base in (0x24, 0x2C):
            if base == 0x2C:
                output[-1] -= 8
            output.extend(_cell_reference(source, relative=base == 0x2C, row=row, column=column))
        elif base in (0x25, 0x2D):
            if base == 0x2D:
                output[-1] -= 8
            output.extend(_area_reference(source, relative=base == 0x2D, row=row, column=column))
        elif base in (0x2A, 0x2B):
            source.take(3 if base == 0x2A else 6)
            output.extend(bytes(4 if base == 0x2A else 8))
        elif base == 0x39 and version == 5:
            sheet_index = source.number("h")
            source.take(8)
            index = source.number("H")
            source.take(12)
            key = (abs(sheet_index) - 1, index)
            if sheet_index < 0 and key not in name_refs:
                if not 1 <= index <= len(names):
                    raise ValueError("unresolved legacy internal name")
                if not 0 <= key[0] < len(scopes) or not isinstance(scopes[key[0]], str):
                    raise ValueError("unresolved legacy internal name sheet scope")
                name_refs[key] = scopes[key[0]] + "!" + names[index - 1]
            output.extend(struct.pack("<HI", *key))
        elif base in (0x3A, 0x3B, 0x3C, 0x3D) and version == 5:
            sheet_index = source.number("h")
            source.take(8)
            first, last = source.number("H"), source.number("H")
            if sheet_index < 0:
                if first == 0xFFFF or last == 0xFFFF:
                    scope = "#REF!"
                elif first > last or last >= len(sheets):
                    raise ValueError("unresolved legacy internal sheet range")
                else:
                    scope = "'" + (sheets[first] + (":" + sheets[last] if first != last else "")).replace("'", "''") + "'"
                scopes.append(scope)
                index = len(scopes) - 1
            else:
                index = sheet_index - 1
            if not 0 <= index < len(scopes):
                raise ValueError("unresolved legacy external sheet index")
            output.extend(struct.pack("<H", index))
            if base in (0x3A, 0x3B):
                output.extend(_cell_reference(source) if base == 0x3A else _area_reference(source))
            else:
                source.take(3 if base == 0x3C else 6)
                output.extend(bytes(4 if base == 0x3C else 8))
        elif base == 0x20:
            source.take(6 if version == 2 else 7)
            output.extend(bytes(7))
            trailing.extend(_legacy_array(auxiliary, codepage,
                                          max_characters - len(output) - len(trailing)))
        elif base in (0x26, 0x27, 0x28):
            source.take(3 if version == 2 else 4)
            length = source.number("B" if version == 2 else "H")
            output.extend(bytes(4) + struct.pack("<H", length))
            if base == 0x26:
                count = auxiliary.number("H")
                if len(output) + len(trailing) + 2 + count * 8 > max_characters:
                    raise ValueError("legacy BIFF normalized formula size limit exceeded")
                trailing.extend(struct.pack("<H", count))
                for _ in range(count):
                    trailing.extend(_area_reference(auxiliary))
        elif base == 0x29:
            output.extend(struct.pack("<H", source.number("B" if version == 2 else "H")))
        elif raw == 0x19:
            flags = source.number("B")
            count = source.number("B" if version == 2 else "H")
            output.extend(struct.pack("<BH", flags, count))
            if flags == 4:
                for _ in range(count + 1):
                    output.extend(struct.pack("<H", source.number("B" if version == 2 else "H")))
        elif raw == 1:
            output.extend(struct.pack("<HH", source.number("H"), source.number("B" if version == 2 else "H")))
        else:
            raise ValueError("unsupported BIFF%d formula token 0x%02X" % (version, raw))
        if len(output) + len(trailing) > max_characters:
            raise ValueError("legacy BIFF normalized formula size limit exceeded")
    if pending_contexts:
        raise ValueError("unmatched legacy BIFF tSheet")
    auxiliary.finish()
    return decode_formula(bytes(output), bytes(trailing), dialect="biff8", row=row, column=column,
                          names=names, external=scopes, external_names=name_refs,
                          max_characters=max_characters)


def parse_legacy_cell(kind: int, payload: bytes, version: int, codepage: str) -> dict:
    """Retain native addressed values and separate cached errors, blanks and formula bytes."""
    _version(version)
    source = _Fields(payload)
    row, column = source.number("H"), source.number("H")
    if row >= 16384 or column >= 256:
        raise ValueError("legacy BIFF cell exceeds its source grid")
    source.take(3 if version == 2 else 2)
    result = {"row": row, "column": column, "value": None, "value_type": "blank",
              "formula": None, "cached_value_state": "not_formula", "error": None,
              "tokens": None, "extra": b""}
    if kind in (0x0001, 0x0201):
        result["cached_value_state"] = "blank"
    elif kind == 0x0002 and version == 2:
        result.update(value=source.number("H"), value_type="number")
    elif kind in (0x0003, 0x0203):
        value = source.number("d")
        if not math.isfinite(value):
            raise ValueError("non-finite legacy BIFF number")
        result.update(value=value, value_type="number")
    elif kind in (0x0004, 0x0204):
        result.update(value=source.text(codepage, short=version == 2), value_type="string")
    elif kind in (0x0005, 0x0205):
        value, error = source.number("B"), source.number("B")
        if error not in (0, 1) or error and value not in ERRORS or not error and value not in (0, 1):
            raise ValueError("invalid legacy BIFF Boolean/error value")
        result.update(value=ERRORS[value] if error else bool(value), value_type="error" if error else "boolean",
                      error=ERRORS[value] if error else None)
    elif kind in (0x0006, 0x0206, 0x0406):
        cached = source.take(8)
        if cached[-2:] != b"\xff\xff" or version == 2:
            value = struct.unpack("<d", cached)[0]
            if not math.isfinite(value):
                raise ValueError("non-finite legacy formula cache")
            result.update(value=value, value_type="number", cached_value_state="present")
        else:
            value_kind, value = cached[0], cached[2]
            if value_kind == 0:
                result.update(value_type="string", cached_value_state="missing")
            elif value_kind == 1 and value in (0, 1):
                result.update(value=bool(value), value_type="boolean", cached_value_state="present")
            elif value_kind == 2 and value in ERRORS:
                result.update(value=ERRORS[value], value_type="error", error=ERRORS[value], cached_value_state="present")
            elif value_kind == 3:
                result.update(value="", value_type="string", cached_value_state="present")
            else:
                raise ValueError("invalid legacy BIFF formula cache type")
        source.take(1 if version == 2 else 2)
        if version == 5:
            source.take(4)
        length = source.number("B" if version == 2 else "H")
        result["tokens"] = source.take(length)
        result["extra"] = source.take(len(payload) - source.offset)
    else:
        raise ValueError("unsupported legacy BIFF cell record 0x%04X" % kind)
    source.finish()
    return result
