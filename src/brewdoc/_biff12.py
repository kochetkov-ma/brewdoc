"""Passive BIFF12 records, source cells and workbook metadata from admitted parts."""

from __future__ import annotations

import math
import struct
import xml.etree.ElementTree as ET

from brewdoc._inspection_common import InspectionContext
from brewdoc._excel_formula import decode_formula
from brewdoc.common import _local_name, _resolve_ooxml_part, _xml_preflight

ERRORS = {0: "#NULL!", 7: "#DIV/0!", 15: "#VALUE!", 23: "#REF!", 29: "#NAME?",
          36: "#NUM!", 42: "#N/A", 43: "#GETTING_DATA"}
SHARED_STRINGS_TYPE = "application/vnd.ms-excel.sharedStrings"
OFFICE_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
SEMANTICS = ("text", "cells", "formulas", "names", "links", "external_references",
             "hidden_structures", "macros", "forms", "attachments")
FRAME_RECORDS = {35, 36, 37, 38, 40, 42, 128, 129, 130, 131, 132, 133, 134,
                 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146,
                 147, 151, 152, 153, 154, 155, 157, 158, 159, 160, 177, 178,
                 277, 353, 354, 390, 391, 396, 397, 422, 423, 424, 425, 480,
                 505, 506, 508, 509, 528, 529, 549, 553, 603, 604, 611, 612,
                 613, 614, 615, 616, 617, 618, 619, 620, 626, 627, 1131, 1132,
                 1142, 1143, 2096, 2097, 2098, 2099, 638, 640, 643, 645}
STYLE_RECORDS = {43, 44, 45, 46, 47, 48, 63, 278, 279, 476, 477,
                 478, 485, 535, 1024, 1025, 1045}


def _u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _address(row: int, column: int) -> str:
    if not 0 <= row < 1_048_576 or not 0 <= column < 16_384:
        raise ValueError("BIFF12 cell coordinates exceed the worksheet bounds")
    name = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name + str(row + 1)


def _wide(data: bytes, offset: int = 0, *, nullable: bool = False) -> tuple[str | None, int]:
    count = _u32(data, offset)
    if nullable and count == 0xFFFFFFFF:
        return None, offset + 4
    end = offset + 4 + count * 2
    if end > len(data):
        raise ValueError("truncated BIFF12 UTF-16 string")
    return data[offset + 4:end].decode("utf-16le"), end


def _range(data: bytes) -> tuple[str, tuple[int, int, int, int]]:
    first_row, last_row, first_col, last_col = struct.unpack_from("<IIII", data)
    if first_row > last_row or first_col > last_col:
        raise ValueError("reversed BIFF12 cell range")
    return (_address(first_row, first_col) + ":" + _address(last_row, last_col),
            (first_row, last_row, first_col, last_col))


def iter_biff12(data: bytes, context: InspectionContext, document: str,
                 location: dict):
    """Yield source records after validating complete variable-length framing."""
    offset = 0
    while offset < len(data):
        start = offset
        record_type = data[offset] & 127
        continuation = data[offset] & 128
        offset += 1
        if continuation:
            if offset >= len(data) or data[offset] & 128:
                raise ValueError("invalid BIFF12 record type at byte %d" % start)
            record_type |= data[offset] << 7
            offset += 1
        size = 0
        for shift in range(0, 28, 7):
            if offset >= len(data):
                raise ValueError("truncated BIFF12 length at byte %d" % start)
            byte = data[offset]
            offset += 1
            size |= (byte & 127) << shift
            if byte < 128:
                break
        else:
            raise ValueError("unterminated BIFF12 length at byte %d" % start)
        end = offset + size
        here = {**location, "record_offset": start, "record_type": record_type,
                "payload_offset": offset, "record_size": end - start}
        if end > len(data):
            raise ValueError("truncated BIFF12 record %d at byte %d" % (record_type, start))
        context.check("payload_bytes", size, here)
        context.charge("decoded_bytes", size, here)
        payload = data[offset:end]
        component = context.component(document, "biff12-record", here, data=payload,
                                      disposition="opaque", reason="passive binary source record")
        yield record_type, payload, here, component
        offset = end


def _formula_container(data: bytes, offset: int, *, allow_empty=False) -> tuple[bytes, bytes, int]:
    size = _u32(data, offset)
    if size > 16_384 or (size == 0 and not allow_empty) or offset + 8 + size > len(data):
        raise ValueError("invalid BIFF12 formula token length")
    end = offset + 4 + size
    extra_size = _u32(data, end)
    if end + 4 + extra_size > len(data):
        raise ValueError("truncated BIFF12 formula auxiliary data")
    return data[offset + 4:end], data[end + 4:end + 4 + extra_size], end + 4 + extra_size


def _frt_formula_container(data, offset):
    """Read the native FRT flags/cce/cb prefix without rewriting retained bytes."""
    flags, size, extra_size = struct.unpack_from("<III", data, offset)
    end = offset + 12 + size + extra_size
    if flags != 2 or not 0 < size <= 16_384 or end > len(data):
        raise ValueError("invalid or truncated native FRT formula container")
    start = offset + 12
    return data[start:start + size], data[start + size:end], end


def _small_reference(row, column):
    """Apply the native small grid before shared reference reconstruction."""
    if row >= 65_536 or (column & 0x3FFF) >= 256:
        raise ValueError("external small reference exceeds its native source grid")
    return decode_formula(b"\x24" + struct.pack("<IH", row, column), dialect="biff12")

def _relationships(members, context, document):
    relationships = []
    for ordinal, part, data in members:
        if data is None or not part.endswith(".rels"):
            continue
        location = {"kind": "package", "part": part, "member_ordinal": ordinal}
        try:
            _xml_preflight(data, part, context.zip_budget)
            root = ET.fromstring(data)
            owner = part.replace("/_rels/", "/")[:-5] if "/_rels/" in part else ""
            for index, relation in enumerate(root, 1):
                if _local_name(relation.tag) == "Relationship":
                    attributes = dict(relation.attrib)
                    attributes["inspection_location"] = {**location, "relationship_ordinal": index}
                    relationships.append((owner, attributes))
        except (ValueError, ET.ParseError) as exc:
            context.problem(document, ("links", "external_references"), str(exc), location)
    return relationships


def _targets(relationships, owner, key):
    return [item for part, item in relationships if part == owner and item.get("Id") == key]


def _resolve_relation(relationships, owner, key):
    matches = _targets(relationships, owner, key)
    if len(matches) != 1 or matches[0].get("TargetMode") == "External":
        raise ValueError("missing or ambiguous internal BIFF12 relationship: %s" % key)
    return _resolve_ooxml_part(owner, matches[0].get("Target", ""))


def _native_content_types(members, context, document):
    """Admit binary Excel source schemas from declarations rather than filename suffixes."""
    sources = [(ordinal, payload) for ordinal, part, payload in members if part == "[Content_Types].xml"]
    defaults, overrides = {}, {}
    if len(sources) != 1 or sources[0][1] is None:
        context.problem(document, SEMANTICS, "missing or duplicate XLSB content type source", {"kind": "package"})
        return {}
    ordinal, payload = sources[0]
    location = {"kind": "package", "part": "[Content_Types].xml", "member_ordinal": ordinal}
    try:
        _xml_preflight(payload, "[Content_Types].xml", context.zip_budget)
        root = ET.fromstring(payload)
        if _local_name(root.tag) != "Types":
            raise ValueError("invalid XLSB content type source root")
        for node in root:
            kind = _local_name(node.tag)
            if kind not in ("Default", "Override"):
                continue
            table = defaults if kind == "Default" else overrides
            key = node.attrib["Extension"].lower() if kind == "Default" else node.attrib["PartName"].lstrip("/")
            if key in table:
                raise ValueError("duplicate XLSB content type declaration: " + key)
            table[key] = node.attrib["ContentType"]
    except (ValueError, KeyError, ET.ParseError) as exc:
        context.problem(document, SEMANTICS, str(exc), location)
        return {}
    result = {}
    for _ordinal, part, _payload in members:
        declared = overrides.get(part, defaults.get(part.rsplit(".", 1)[-1].lower(), ""))
        if declared.startswith("application/vnd.ms-excel.") and not declared.endswith("+xml"):
            result[part] = declared
    return result


def _active_strings(parts, native_types, relationships, workbook_part, context, document):
    tables = [item for item in parts if native_types[item[0]] == SHARED_STRINGS_TYPE]
    occurrences = [item for owner, item in relationships if owner == workbook_part
                   and item.get("Type") == OFFICE_RELATIONSHIPS + "sharedStrings"]
    location = {"kind": "package", "part": workbook_part}
    if len(tables) > 1 or tables and not occurrences:
        context.problem(document, ("cells",), "extra or unreferenced native shared-string table", location)
    if not occurrences:
        return None
    if len(occurrences) != 1 or occurrences[0].get("TargetMode") == "External":
        context.problem(document, ("cells", "text"), "ambiguous native shared-string relationship", location)
        return None
    target = _resolve_ooxml_part(workbook_part, occurrences[0].get("Target", ""))
    matching = [item for item in tables if item[0] == target]
    if len(matching) != 1:
        context.problem(document, ("cells", "text"), "missing or duplicate native shared-string target", location)
        return None
    records = matching[0][2]
    if not records or records[0][0] != 159 or records[-1][0] != 160 or sum(item[0] == 159 for item in records) != 1:
        context.problem(document, ("cells", "text"), "ambiguous native shared-string table boundary", location)
        return None
    return matching[0][0], matching[0][1]


def _record_formula(data, offset, context, document, location, names, external,
                    *, row=0, column=0, groups=(), external_names=None,
                    tables=None, pivot_names=(), allow_empty=False, frt=False):
    tokens, extra, end = (_frt_formula_container(data, offset) if frt else
                          _formula_container(data, offset, allow_empty=allow_empty))
    location = {**location, "formula_offset": offset}
    artifact = context.payload(document, "formula-tokens", location, data[offset:end],
                               "application/octet-stream")
    original = tokens
    formula = None
    operand_details = []
    try:
        if not tokens:
            return None
        if tokens[:1] == b"\x01":
            anchor_row = _u32(tokens, 1)
            anchor_col = _u32(extra)
            matches = [item for item in groups if item[0][0] == anchor_row and item[0][2] == anchor_col]
            if len(matches) != 1:
                raise ValueError("missing or ambiguous shared/array formula group")
            bounds, tokens, extra = matches[0]
            if not bounds[0] <= row <= bounds[1] or not bounds[2] <= column <= bounds[3]:
                raise ValueError("shared/array formula cell lies outside its group")
        formula = decode_formula(tokens, extra, dialect="biff12", row=row, column=column,
                                 names=names, external=external, external_names=external_names,
                                 tables=tables, pivot_names=pivot_names, operand_details=operand_details)
    except (ValueError, struct.error, IndexError, KeyError) as exc:
        context.problem(document, ("formulas",), str(exc), location)
    context.finding(document, "formulas", "formula", location, text=formula, artifact=artifact,
                    tokens_hex=original.hex(), reconstruction="symbolic RPN source reconstruction"
                    if operand_details else "RPN source reconstruction",
                    **({"context_required": operand_details} if operand_details else {}),
                    **({"container_kind": "FRTFormula", "frt_flags": 2} if frt else {}))
    return formula


def _cell_value(kind, data, strings):
    if kind == 1:
        return None, "blank", 8
    if kind == 2:
        raw = _u32(data, 8)
        value = (struct.unpack("<i", data[8:12])[0] >> 2 if raw & 2 else
                 struct.unpack("<d", b"\0\0\0\0" + struct.pack("<I", raw & ~3))[0])
        return (value / 100 if raw & 1 else value), "number", 12
    if kind in (3, 11):
        return ERRORS[data[8]], "error", 9
    if kind in (4, 10):
        if data[8] not in (0, 1):
            raise ValueError("invalid BIFF12 Boolean value")
        return bool(data[8]), "boolean", 9
    if kind in (5, 9):
        value = struct.unpack_from("<d", data, 8)[0]
        if not math.isfinite(value):
            raise ValueError("non-finite BIFF12 numeric value")
        return value, "number", 16
    if kind in (6, 8):
        value, end = _wide(data, 8)
        return value, "string", end
    if kind == 7:
        index = _u32(data, 8)
        if index >= len(strings) or strings[index] is None:
            raise ValueError("invalid BIFF12 shared-string index %d" % index)
        return strings[index], "string", 12
    raise ValueError("unsupported BIFF12 cell record %d" % kind)


def _rich_string(data, offset, context, location):
    """Recover RichStr text and native UTF-16 run indexes with pre-growth bounds."""
    flags, characters = data[offset], _u32(data, offset + 1)
    if characters > 32_767:
        raise ValueError("rich string exceeds its native character limit")
    text, end = _wide(data, offset + 1)
    runs, phonetic_runs, phonetic = [], [], None
    if flags & 1:
        count = _u32(data, end)
        end += 4
        if count > 32_767 or end + count * 4 > len(data):
            raise ValueError("invalid or truncated rich string run count")
        context.charge("records", count, location)
        previous = -1
        for _index in range(count):
            character, font = struct.unpack_from("<HH", data, end)
            if not previous < character < characters:
                raise ValueError("rich string run indexes are invalid or unordered")
            runs.append({"character": character, "font_index": font})
            previous, end = character, end + 4
    if flags & 2:
        phonetic_characters = _u32(data, end)
        phonetic, end = _wide(data, end)
        count = _u32(data, end)
        end += 4
        if count > 32_767 or end + count * 10 > len(data):
            raise ValueError("invalid or truncated rich phonetic run count")
        context.charge("records", count, location)
        previous_phonetic, previous_source = -1, -1
        for _index in range(count):
            first, source, length, font, native_flags = struct.unpack_from("<HHHHH", data, end)
            if not previous_phonetic < first < phonetic_characters or not previous_source < source < characters:
                raise ValueError("rich phonetic run indexes are invalid or unordered")
            if source + length > characters:
                raise ValueError("rich phonetic run exceeds source string")
            phonetic_runs.append({"phonetic_character": first, "source_character": source,
                                  "source_length": length, "font_index": font, "flags": native_flags})
            previous_phonetic, previous_source, end = first, source, end + 10
    if end != len(data):
        raise ValueError("trailing bytes in native rich string")
    return text, {"flags": flags, "coordinate_unit": "utf16-code-units", "runs": runs,
                  "phonetic": phonetic, "phonetic_runs": phonetic_runs}


def _quote(text):
    return "'" + text.replace("'", "''") + "'"


def _table_scope(table, first=-2, last=-2):
    if table["type"] == 1:
        return _quote(table["service"] + "|" + table["topic"])
    if len(table["targets"]) != 1:
        raise ValueError("ambiguous or absent external source relationship")
    scope = "[" + table["targets"][0] + "]"
    if first >= 0:
        if not first <= last < len(table["sheets"]):
            raise ValueError("external sheet index lies outside the source table")
        scope += table["sheets"][first]
        if last != first:
            scope += ":" + table["sheets"][last]
    elif first != -2 or last != -2:
        raise ValueError("external sheet scope is invalid or missing")
    return _quote(scope)


def _external_expression(payload, table):
    size = _u32(payload)
    if len(payload) != size + 4:
        raise ValueError("invalid external defined-name expression size")
    if size == 0:
        return None
    tokens = payload[4:]
    kind = tokens[0]
    if kind == 28 and tokens == b"\x1c\x17":
        return "#REF!"
    if kind not in (58, 59, 60, 61):
        raise ValueError("invalid external defined-name expression token")
    if len(tokens) != (9 if kind in (58, 60) else 13):
        raise ValueError("invalid external defined-name expression width")
    first, last = struct.unpack_from("<hh", tokens, 1)
    scope = _table_scope(table, first, last)
    if kind in (60, 61):
        reference = "#REF!"
    elif kind == 58:
        row, column = struct.unpack_from("<HH", tokens, 5)
        reference = _small_reference(row, column)
    else:
        first_row, last_row, first_col, last_col = struct.unpack_from("<HHHH", tokens, 5)
        reference = _small_reference(first_row, first_col) + ":" + _small_reference(last_row, last_col)
    return scope + "!" + reference


def _cached_external_value(kind, payload):
    if kind == 580:
        if len(payload) != 8:
            raise ValueError("invalid external cached numeric width")
        value = struct.unpack_from("<d", payload)[0]
        if not math.isfinite(value):
            raise ValueError("non-finite external cached number")
        return value, "number"
    if kind == 581:
        if len(payload) != 1:
            raise ValueError("invalid external cached error width")
        return ERRORS[payload[0]], "error"
    if kind == 582:
        value, end = _wide(payload)
        if end != len(payload):
            raise ValueError("trailing bytes in external cached string")
        return value, "string"
    if kind == 583:
        if payload:
            raise ValueError("nonempty nil external cached value")
        return None, "blank"
    if kind == 584 and payload in (b"\0", b"\x01"):
        return bool(payload[0]), "boolean"
    raise ValueError("invalid external cached value record")


def _external_tables(parts, relationships, context, document):
    """Recover external workbook, DDE and OLE tables before joining NameX tokens."""
    tables = {}
    for part, ordinal, records in parts:
        if not any(kind == 360 for kind, *_rest in records):
            if "/externalLinks/" in part:
                context.problem(document, ("external_references", "names", "formulas"),
                                "external source table has no source declaration",
                                {"kind": "biff12", "part": part, "member_ordinal": ordinal})
            continue
        table = None
        current = None
        cache_sheet = None
        cache_row = None
        cache_count = 0
        cache_shape = None
        ended = False
        for kind, payload, location, component in records:
            try:
                if kind == 360:
                    if table is not None:
                        context.problem(document, ("external_references", "names", "formulas"),
                                        "multiple external source table declarations in one part", location)
                    link_type = struct.unpack_from("<H", payload)[0]
                    if link_type not in (0, 1, 2):
                        raise ValueError("invalid BIFF12 external source type")
                    first, end = _wide(payload, 2)
                    second, _end = _wide(payload, end, nullable=link_type == 0)
                    if link_type == 0 and second is not None:
                        raise ValueError("external workbook source requires a null second string")
                    targets = _targets(relationships, part, first) if link_type != 1 else []
                    table = {"type": link_type, "targets": [item["Target"] for item in targets],
                             "service": first if link_type == 1 else None,
                             "topic": second if link_type == 1 else None,
                             "progid": second if link_type == 2 else None,
                             "sheets": [], "names": [], "location": location, "part": part}
                    tables.setdefault(part, []).append(table)
                    if link_type != 1 and len(targets) != 1:
                        context.problem(document, ("external_references", "names", "formulas"),
                                        "external source target is missing or ambiguous", location)
                    for target in table["targets"] or [None]:
                        context.finding(document, "external_references", "external-source", location,
                                        component=component, target=target, source_type=link_type,
                                        service=table["service"], topic=table["topic"], progid=table["progid"])
                elif table is None and kind not in FRAME_RECORDS:
                    raise ValueError("external record precedes its source table")
                elif table is None:
                    continue
                elif kind == 359:
                    count = _u32(payload)
                    if count >= 65_535 or count > (len(payload) - 4) // 4:
                        raise ValueError("invalid external sheet-name count")
                    end = 4
                    for _index in range(count):
                        text, end = _wide(payload, end)
                        table["sheets"].append(text)
                    if end != len(payload):
                        raise ValueError("trailing bytes in external sheet-name table")
                    context.finding(document, "external_references", "external-sheet-names", location,
                                    names=list(table["sheets"]))
                elif kind == 577:
                    if current is not None:
                        raise ValueError("unclosed external defined-name record")
                    text, _end = _wide(payload)
                    current = {"name": text, "scope": 0, "formula": None, "flags": None,
                               "location": location, "component": component}
                    table["names"].append(current)
                elif kind == 586:
                    if current is None or len(payload) != 7:
                        raise ValueError("invalid external name properties")
                    flags = struct.unpack_from("<H", payload)[0]
                    current.update(flags=flags, scope=_u32(payload, 2) if table["type"] == 0 else 0)
                    context.finding(document, "external_references", "external-name-properties", location,
                                    name=current["name"], flags=flags, scope=current["scope"],
                                    want_advise=bool(flags & 2), want_picture=bool(flags & 4),
                                    ole=bool(flags & 8) if table["type"] == 1 else table["type"] == 2,
                                    icon=bool(flags & 32) if table["type"] == 2 else False)
                elif kind == 585:
                    if current is None:
                        raise ValueError("external name formula has no preceding name")
                    current["formula"] = _external_expression(payload, table)
                    context.finding(document, "formulas", "external-name-formula", location,
                                    text=current["formula"], name=current["name"], tokens_hex=payload[4:].hex())
                elif kind == 578:
                    if current is None or cache_shape is not None or len(payload) != 8:
                        raise ValueError("external value array has no name or invalid dimensions")
                    rows, columns = struct.unpack_from("<II", payload)
                    if not 1 <= rows <= 1_048_576 or not 1 <= columns <= 16_384:
                        raise ValueError("external cached array exceeds worksheet dimensions")
                    context.check("records", context.consumed["records"] + rows * columns, location)
                    cache_count, cache_shape = 0, (rows, columns)
                elif 580 <= kind <= 584:
                    if current is None or cache_shape is None:
                        raise ValueError("external cached value is outside its named array")
                    value, value_type = _cached_external_value(kind, payload)
                    if cache_count >= cache_shape[0] * cache_shape[1]:
                        raise ValueError("external cached array has too many values")
                    row, column = divmod(cache_count, cache_shape[1])
                    context.finding(document, "external_references", "external-name-cache", location,
                                    name=current["name"], row=row + 1, column=column + 1,
                                    value=value, value_type=value_type)
                    cache_count += 1
                elif kind == 579:
                    if cache_shape is None or cache_count != cache_shape[0] * cache_shape[1]:
                        raise ValueError("external cached array has missing values")
                    cache_shape = None
                elif kind == 587:
                    if current is None or cache_shape is not None:
                        raise ValueError("invalid external defined-name end boundary")
                    scope = current["scope"]
                    prefix = _table_scope(table, scope - 1, scope - 1) if scope else _table_scope(table)
                    current["qualified_name"] = prefix + "!" + current["name"]
                    context.finding(document, "names", "external-defined-name", current["location"],
                                    component=current["component"], text=current["name"],
                                    source_type=table["type"], scope=scope, formula=current["formula"],
                                    qualified_name=current["qualified_name"], flags=current["flags"])
                    if table["type"] == 1:
                        context.finding(document, "actions", "dde-item", current["location"],
                                        target=current["name"], service=table["service"], topic=table["topic"],
                                        item=current["name"], flags=current["flags"])
                    current = None
                elif kind == 363:
                    cache_sheet = _u32(payload)
                    if cache_sheet >= len(table["sheets"]):
                        raise ValueError("external cell cache sheet index is invalid")
                    context.finding(document, "external_references", "external-cell-cache", location,
                                    sheet=table["sheets"][cache_sheet], refresh_error=bool(payload[4] & 1))
                elif kind == 366:
                    cache_row = _u32(payload)
                    _address(cache_row, 0)
                elif 367 <= kind <= 371:
                    if cache_sheet is None or cache_row is None:
                        raise ValueError("external cached cell has no sheet or row")
                    column = _u32(payload)
                    address = _address(cache_row, column)
                    mapped = {367: 583, 368: 580, 369: 584, 370: 581, 371: 582}[kind]
                    value, value_type = _cached_external_value(mapped, payload[4:])
                    context.finding(document, "external_references", "external-cell", location,
                                    sheet=table["sheets"][cache_sheet], cell=address,
                                    target=table["targets"][0] if len(table["targets"]) == 1 else None,
                                    value=value, value_type=value_type,
                                    qualified_cell=_table_scope(table, cache_sheet, cache_sheet) + "!" + address)
                elif kind == 472:
                    context.finding(document, "external_references", "external-value-metadata", location,
                                    metadata_index=_u32(payload))
                elif kind == 364:
                    cache_sheet, cache_row = None, None
                elif kind == 588:
                    ended = True
                    if current is not None:
                        raise ValueError("external source ends with an unclosed name")
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("external_references", "names", "formulas"), str(exc), location)
        if table is not None and (not ended or current is not None or cache_shape is not None):
            context.problem(document, ("external_references", "names", "formulas"),
                            "external source is truncated before its end boundary", table["location"])
    return tables


def _related_occurrences(relationships, owner, relation, context, document, location, capabilities):
    matches = _targets(relationships, owner, relation)
    if len(matches) != 1:
        context.problem(document, capabilities, "binary object relationship missing or ambiguous", location)
    for match in matches:
        target = match.get("Target")
        if target is None:
            context.problem(document, capabilities, "binary object relationship has no target", location)
            continue
        resolved = None if match.get("TargetMode") == "External" else _resolve_ooxml_part(owner, target)
        if resolved is None:
            context.problem(document, capabilities,
                            "binary control/drawing/object requires an internal package relationship", location)
        components = [item for item in context.components if item["document"] == document
                      and item["location"].get("part") == resolved and item["artifact"] is not None]
        if resolved is not None and len(components) > 1:
            context.problem(document, capabilities, "binary object package target is ambiguous", location)
        yield (target, resolved, components[0]["artifact"] if len(components) == 1 else None,
               match["inspection_location"])


def _rich_values(members, relationships, context, document):
    """Preserve generic native rich schemas, lexical fields and typed scalar values."""
    structures, raw_values, resources = [], [], []
    seen = set()
    candidates = set()
    for owner, relation in relationships:
        kind = relation.get("Type", "")
        if "richvalue" in kind.lower() or "richdata" in kind.lower():
            if relation.get("TargetMode") != "External" and relation.get("Target"):
                candidates.add(_resolve_ooxml_part(owner, relation["Target"]))
    for ordinal, part, payload in members:
        if payload is None or not part.lower().endswith(".xml") or not (
                "/richdata/" in part.lower() or part in candidates):
            continue
        location = {"kind": "package", "part": part, "member_ordinal": ordinal}
        try:
            _xml_preflight(payload, part, context.zip_budget)
            root = ET.fromstring(payload)
            schema = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else None
            local = _local_name(root.tag)
            if local in ("rvStructures", "rvData"):
                if local in seen:
                    raise ValueError("duplicate native rich value source: " + local)
                seen.add(local)
            if local == "rvStructures":
                count = sum(_local_name(node.tag) == "s" for node in root)
                if "count" in root.attrib and int(root.attrib["count"]) != count:
                    raise ValueError("rich structure count does not match native records")
                context.charge("records", count, location)
                for index, node in enumerate(node for node in root if _local_name(node.tag) == "s"):
                    count = sum(_local_name(child.tag) == "k" for child in node)
                    context.charge("records", count, location)
                    keys = [{"name": child.attrib["n"], "native_type": child.get("t", "n"),
                             "attributes": dict(child.attrib)} for child in node
                            if _local_name(child.tag) == "k"]
                    structures.append({"type": node.attrib["t"], "keys": keys,
                                       "location": {**location, "structure_index": index},
                                       "schema": schema, "attributes": dict(node.attrib)})
            elif local == "rvData":
                count = sum(_local_name(node.tag) == "rv" for node in root)
                if "count" in root.attrib and int(root.attrib["count"]) != count:
                    raise ValueError("rich value count does not match native records")
                context.charge("records", count, location)
                for index, node in enumerate(node for node in root if _local_name(node.tag) == "rv"):
                    values = []
                    for child in node:
                        if _local_name(child.tag) != "v":
                            continue
                        text = "".join(child.itertext())
                        context.finding(document, "text", "rich-value-lexical", location, text=text,
                                        rich_value_index=index, property_ordinal=len(values) + 1)
                        values.append(text)
                    raw_values.append({"structure": int(node.attrib["s"]), "values": values,
                                       "location": {**location, "rich_value_index": index},
                                       "schema": schema, "attributes": dict(node.attrib)})
            else:
                for index, node in enumerate(root.iter(), 1):
                    fields = {"element": _local_name(node.tag), "attributes": dict(node.attrib),
                              "text": node.text, "tail": node.tail}
                    resources.append({"location": {**location, "element_ordinal": index},
                                      "schema": schema, "fields": fields})
                    context.finding(document, "cells", "rich-native-field", location,
                                    text=node.text, element_ordinal=index, schema=schema, fields=fields)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError, ET.ParseError) as exc:
            context.problem(document, ("cells",), str(exc), location)
    values, sources = [], []
    for index, record in enumerate(raw_values):
        try:
            source_index = record["structure"]
            if not 0 <= source_index < len(structures):
                raise ValueError("rich value references an absent source structure")
            structure = structures[source_index]
            if len(structure["keys"]) != len(record["values"]):
                raise ValueError("rich value field count does not match its source schema")
            properties = []
            for key, text in zip(structure["keys"], record["values"]):
                native_type = key["native_type"]
                value = text
                if native_type in ("i", "r", "a", "p"):
                    value = int(text)
                elif native_type == "n":
                    value = float(text)
                    if not math.isfinite(value):
                        raise ValueError("rich property contains a non-finite native number")
                elif native_type == "b":
                    if text not in ("0", "1", "false", "true"):
                        raise ValueError("rich property contains an invalid native Boolean")
                    value = text in ("1", "true")
                properties.append({"name": key["name"], "native_type": native_type,
                                   "source_text": text, "value": value})
            rich = {"index": index, "structure_index": source_index,
                    "type": structure["type"], "properties": properties}
            values.append(rich)
            sources.append({"value_location": record["location"], "value_schema": record["schema"],
                            "value_attributes": record["attributes"], "structure_location": structure["location"],
                            "structure_schema": structure["schema"], "structure_attributes": structure["attributes"],
                            "key_attributes": [item["attributes"] for item in structure["keys"]],
                            "native_resource_count": len(resources)})
            context.finding(document, "cells", "rich-value", record["location"], **rich)
            if rich["type"] == "_hyperlink":
                for item in properties:
                    if item["name"] == "Address" and item["native_type"] == "s":
                        context.finding(document, "links", "rich-value-hyperlink", record["location"],
                                        target=item["source_text"], rich_value_index=index)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            values.append(None)
            sources.append({"value_location": record["location"]})
            context.problem(document, ("cells",), str(exc), record["location"])
    return values, sources


def _metadata(parts, context, document, *, rich_values=(), rich_sources=()):
    """Join source metadata type/string blocks without applying them to fabricated cells."""
    result = {"cell": [], "value": []}
    sources = 0
    for part, _ordinal, records in parts:
        relevant = any(kind in (51, 58, 332, 335, 339, 4096, 5002) for kind, *_rest in records)
        if not relevant:
            continue
        sources += 1
        if sources > 1 or not any(kind == 332 for kind, *_rest in records):
            context.problem(document, ("cells", "names"),
                            "metadata source container is missing or duplicated",
                            {"kind": "biff12", "part": part, "member_ordinal": _ordinal})
        types, strings, mdx = [], [], []
        current_mdx = None
        stores, store, future = {}, None, None
        store_count = 0
        for kind, data, location, _component in records:
            try:
                if kind == 335:
                    flags, version = struct.unpack_from("<II", data)
                    name, _end = _wide(data, 8)
                    types.append({"type_name": name, "type_flags": flags,
                                  "metadata_id": version, "location": location})
                    context.finding(document, "names", "metadata-type", location, text=name,
                                    flags=flags, metadata_id=version)
                elif kind == 59:
                    strings.append(_wide(data)[0])
                elif kind == 54:
                    current_mdx = {"location": location, "members": [], "header_hex": data.hex()}
                    mdx.append(current_mdx)
                elif kind == 58:
                    index, flags = struct.unpack_from("<IB", data)
                    if current_mdx is None or index >= len(strings) or len(data) != 5:
                        raise ValueError("MDX member references a missing source string or group")
                    member = {"text": strings[index], "string_index": index, "flags": flags,
                              "cube_set": bool(flags & 1), "location": location}
                    current_mdx["members"].append(member)
                    context.finding(document, "names", "mdx-member", location, text=strings[index],
                                    string_index=index, flags=flags, cube_set=bool(flags & 1))
                elif kind == 55:
                    current_mdx = None
                elif kind == 339:
                    store_count = _u32(data)
                    store, _end = _wide(data, 4)
                    if store in stores or store_count < 1:
                        raise ValueError("duplicate future metadata store or invalid block count")
                    stores[store] = []
                elif kind == 52:
                    if store is None:
                        raise ValueError("future metadata block has no source store")
                    future = {"location": location}
                    stores[store].append(future)
                elif kind == 4096:
                    if future is None or data != b"\0" * 4:
                        raise ValueError("invalid native dynamic array metadata header")
                    future["dynamic_array"] = True
                    future["record_location"] = location
                elif kind == 5002:
                    if future is None or len(data) != 8 or data[:4] != b"\0" * 4:
                        raise ValueError("invalid native rich value block header")
                    index = _u32(data, 4)
                    if index >= len(rich_values) or rich_values[index] is None:
                        raise ValueError("rich metadata references an absent or unreadable rich value")
                    future["rich_value"] = rich_values[index]
                    future["rich_value_source"] = rich_sources[index]
                    future["record_location"] = location
                elif kind == 53:
                    future = None
                elif kind == 340:
                    if store is None or len(stores[store]) != store_count:
                        raise ValueError("future metadata store count does not match source blocks")
                    store = None
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("cells", "names"), str(exc), location)
        current, expected, found = None, 0, 0
        for kind, data, location, _component in records:
            try:
                if kind == 337:
                    expected, cell = struct.unpack_from("<II", data)
                    if cell not in (0, 1) or expected < 1:
                        raise ValueError("invalid metadata block kind or count")
                    current, found = "cell" if cell else "value", 0
                elif kind == 51:
                    count = struct.unpack_from("<i", data)[0]
                    if current is None or count < 1 or len(data) != 4 + count * 8:
                        raise ValueError("invalid metadata block entry count or owner")
                    context.check("records", context.consumed["records"] + count, location)
                    entries = []
                    previous = 0
                    for offset in range(4, len(data), 8):
                        context.charge("records", 1, location)
                        type_index, record_index = struct.unpack_from("<iI", data, offset)
                        if not previous < type_index <= len(types):
                            raise ValueError("metadata block type indexes are missing or unordered")
                        previous = type_index
                        source = types[type_index - 1]
                        entry = {"type_index": type_index, **source, "record_index": record_index,
                                 "block_location": location}
                        if source["type_name"] == "XLMDX":
                            if record_index >= len(mdx):
                                raise ValueError("metadata block references an absent MDX record")
                            entry["mdx_members"] = mdx[record_index]["members"]
                            entry["record_location"] = mdx[record_index]["location"]
                        else:
                            entry["future_metadata_index"] = record_index
                            blocks = stores.get(source["type_name"], ())
                            if record_index >= len(blocks):
                                raise ValueError("metadata references an absent future source block")
                            block = blocks[record_index]
                            entry.update(block)
                            if "rich_value" not in block and "dynamic_array" not in block:
                                context.problem(document, ("cells",),
                                                "unknown future value metadata record remains opaque", location)
                        entries.append(entry)
                    result[current].append({"index": len(result[current]) + 1,
                                            "entries": entries, "location": location})
                    context.finding(document, "cells", "metadata-block", location,
                                    metadata_kind=current, entries=entries)
                    found += 1
                elif kind == 338:
                    if current is None or expected != found:
                        raise ValueError("metadata block collection count does not match its records")
                    current = None
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("cells",), str(exc), location)
    return result


def _table_definitions(parts, members, context, document):
    """Recover actual table and column labels before structured formula decoding."""
    definitions = {}
    for _part, _ordinal, records in parts:
        current = None
        for kind, data, location, _component in records:
            try:
                if kind == 343:
                    label, _bounds = _range(data)
                    identifier = _u32(data, 20)
                    name, end = _wide(data, 64, nullable=True)
                    display, end = _wide(data, end, nullable=True)
                    comment, _end = _wide(data, end, nullable=True)
                    current = {"name": display or name, "columns": [], "id": identifier,
                               "range": label, "header_rows": _u32(data, 24),
                               "total_rows": _u32(data, 28), "flags": _u32(data, 32),
                               "location": location, "comment": comment}
                    if not identifier or current["name"] is None:
                        raise ValueError("table source has no numeric or display identity")
                    definitions.setdefault(identifier, []).append(current)
                    context.finding(document, "names", "table", location, text=current["name"],
                                    table_id=identifier, range=label, header_rows=current["header_rows"],
                                    total_rows=current["total_rows"], flags=current["flags"], comment=comment)
                elif kind == 347:
                    if current is None:
                        raise ValueError("table column is outside a table source collection")
                    name, end = _wide(data, 24, nullable=True)
                    caption, _end = _wide(data, end, nullable=True)
                    if name is None and caption is None:
                        raise ValueError("table column has no recoverable source name")
                    current["columns"].append(caption or name)
                    context.finding(document, "names", "table-column", location, text=caption or name,
                                    table_id=current["id"], column_id=_u32(data), total_function=_u32(data, 4),
                                    source_name=name, source_caption=caption)
                elif kind == 344:
                    current = None
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("names", "formulas"), str(exc), location)
    for ordinal, part, payload in members:
        if payload is None or not part.endswith(".xml") or not any(
                segment in part for segment in ("/tables/", "/pivotCache/")):
            continue
        location = {"kind": "package", "part": part, "member_ordinal": ordinal}
        try:
            _xml_preflight(payload, part, context.zip_budget)
            root = ET.fromstring(payload)
            for index, node in enumerate(root.iter(), 1):
                local = _local_name(node.tag)
                here = {**location, "element_ordinal": index, "element": local}
                if local in ("calculatedColumnFormula", "totalsRowFormula"):
                    context.finding(document, "formulas", "calculated-column-formula"
                                    if local == "calculatedColumnFormula" else "total-row-formula",
                                    here, text="".join(node.itertext()), attributes=dict(node.attrib))
                elif local in ("cacheField", "calculatedItem") and "formula" in node.attrib:
                    context.finding(document, "formulas", "pivot-formula", here,
                                    text=node.attrib["formula"], attributes=dict(node.attrib))
            if _local_name(root.tag) != "table":
                continue
            identifier = int(root.attrib["id"])
            columns = [node.attrib["name"] for node in root.iter() if _local_name(node.tag) == "tableColumn"]
            table = {"id": identifier, "name": root.get("displayName", root.get("name")),
                     "columns": columns, "range": root.get("ref"), "location": location}
            definitions.setdefault(identifier, []).append(table)
            context.finding(document, "names", "table", location, text=table["name"],
                            table_id=identifier, range=table["range"], columns=columns)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError, ET.ParseError) as exc:
            context.problem(document, ("names", "formulas"), str(exc), location)
    return definitions


def _validation_details(flags, ranges, strings, formulas):
    return {"ranges": ranges, "flags": flags, "value_type": flags & 15,
            "operator": (flags >> 20) & 15, "allow_blank": bool(flags & 256),
            "show_prompt": bool(flags & (1 << 18)), "show_error": bool(flags & (1 << 19)),
            "error_title": strings[0], "error": strings[1], "prompt_title": strings[2],
            "prompt": strings[3], "formulas": formulas}


def _validation(data, location, context, document, names, external, external_names, tables):
    """Recover one rule's source ranges, prompts, flags and formula containers."""
    flags, count = _u32(data), _u32(data, 4)
    if not 1 <= count < 8192 or 8 + count * 16 > len(data):
        raise ValueError("invalid validation source range count")
    ranges = [_range(data[offset:offset + 16])[0] for offset in range(8, 8 + count * 16, 16)]
    end, strings, formulas = 8 + count * 16, [], []
    for _index in range(4):
        value, end = _wide(data, end, nullable=True)
        strings.append(value)
    for index in range(2):
        here = {**location, "formula_ordinal": index + 1}
        formula = _record_formula(data, end, context, document, here, names, external,
                                  external_names=external_names, tables=tables, allow_empty=True)
        _tokens, _extra, end = _formula_container(data, end, allow_empty=True)
        formulas.append(formula)
    if end != len(data):
        raise ValueError("trailing bytes in data validation source")
    context.finding(document, "forms", "data-validation", location,
                    **_validation_details(flags, ranges, strings, formulas))


def _validation14(data, location, context, document, names, external, external_names, tables):
    """Read extended validation's FRT ranges and native formula framing."""
    frt_flags, groups, range_flags, count = struct.unpack_from("<IIII", data)
    if frt_flags not in (2, 6) or groups != 1 or not range_flags & 2 or count < 1:
        raise ValueError("invalid extended validation FRT header or range count")
    end = 16 + count * 16
    if end > len(data):
        raise ValueError("truncated extended validation range array")
    context.charge("records", count, location)
    ranges = [_range(data[offset:offset + 16])[0] for offset in range(16, end, 16)]
    parsed = []
    if frt_flags & 4:
        formula_count = _u32(data, end)
        end += 4
        if not 1 <= formula_count <= 2:
            raise ValueError("invalid extended validation formula count")
        for index in range(formula_count):
            formula = _record_formula(data, end, context, document,
                                      {**location, "formula_ordinal": index + 1}, names, external,
                                      external_names=external_names, tables=tables, frt=True)
            _tokens, _extra, end = _frt_formula_container(data, end)
            parsed.append(formula)
    flags = _u32(data, end)
    end += 4
    minimum, maximum = bool(flags & (1 << 24)), bool(flags & (1 << 25))
    if int(minimum) + int(maximum) != len(parsed):
        raise ValueError("extended validation formula flags do not match source formulas")
    formulas = [parsed[0] if minimum else None, parsed[-1] if maximum else None]
    strings = []
    for _index in range(4):
        value, end = _wide(data, end, nullable=True)
        strings.append(value)
    if end != len(data):
        raise ValueError("trailing bytes in extended validation")
    context.finding(document, "forms", "data-validation14", location,
                    **_validation_details(flags, ranges, strings, formulas),
                    frt_flags=frt_flags, range_flags=range_flags)


def _pivot_cache_value(kind, data, context, location):
    """Decode typed cache items, additional captions and native property indexes."""
    decorated = 27 <= kind <= 32
    base = kind - 7 if decorated else kind
    end = len(data)
    if base == 20:
        value, value_type, end = None, "blank", 0
    elif base == 21:
        value, value_type, end = struct.unpack_from("<d", data)[0], "number", 8
        if not math.isfinite(value):
            raise ValueError("non-finite native pivot cache number")
    elif base in (22, 23):
        if base == 22 and data[0] not in (0, 1):
            raise ValueError("invalid native pivot cache Boolean")
        value = bool(data[0]) if base == 22 else ERRORS[data[0]]
        value_type, end = ("boolean" if base == 22 else "error"), 1
    elif base == 24:
        value, end = _wide(data)
        value_type = "string"
    elif base == 25:
        year, month, day, hour, minute, second = struct.unpack_from("<HHBBBB", data)
        if not 1900 <= year <= 9999 or not 1 <= month <= 12 or day > 31 or hour > 23 or minute > 59 or second > 59:
            raise ValueError("native pivot cache date-time fields exceed their source bounds")
        if day == 0 and (year, month) != (1900, 1):
            raise ValueError("native zero-day Pivot cache date has an invalid year or month")
        value = {"year": year, "month": month, "day": day, "hour": hour, "minute": minute, "second": second}
        value_type, end = "datetime", 8
    elif base == 26:
        value, value_type, end = _u32(data), "index", 4
    else:
        raise ValueError("unsupported native pivot cache value record")
    additional = None
    if decorated:
        flags = struct.unpack_from("<H", data, end)[0]
        end += 2
        caption = None
        if flags & 4:
            caption, end = _wide(data, end, nullable=True)
        count = _u32(data, end)
        end += 4
        if count >= 0x1FFFFFFE or end + count * 4 != len(data):
            raise ValueError("invalid additional Pivot cache item property count")
        context.charge("records", count, location)
        indexes = [struct.unpack_from("<i", data, offset)[0] for offset in range(end, len(data), 4)]
        if any(index < -1 for index in indexes):
            raise ValueError("invalid additional Pivot cache item property index")
        additional = {"flags": flags, "ghost": bool(flags & 1), "calculated": bool(flags & 2),
                      "caption": caption, "member_property_indexes": indexes}
        end = len(data)
    if end != len(data):
        raise ValueError("trailing bytes in native pivot cache value")
    return {"value": value, "value_type": value_type,
            **({"additional_info": additional} if additional is not None else {})}


def _pivot_cache_run(data, context, location):
    kind, count = struct.unpack_from("<HI", data)
    source_kind = {1: 21, 2: 24, 16: 23, 32: 25}.get(kind)
    minimum = 4 if kind == 2 else 1 if kind == 16 else 8
    if source_kind is None or count > (len(data) - 6) // minimum:
        raise ValueError("invalid or truncated native Pivot cache run")
    context.charge("records", count, location)
    end = 6
    for index in range(count):
        following = _wide(data, end)[1] if kind == 2 else end + minimum
        yield index, _pivot_cache_value(source_kind, data[end:following], context, location)
        end = following
    if end != len(data):
        raise ValueError("trailing bytes in native Pivot cache run")


def _pivot_source(data, part, relationships, context, document, location):
    named, built_in, flags = struct.unpack_from("<BBB", data)
    if named not in (0, 1) or built_in not in (0, 1) or flags & ~3 or built_in and not named:
        raise ValueError("invalid native Pivot cache source flags")
    end, sheet, relation, source_name, source_range = 3, None, None, None, None
    if flags & 2:
        sheet, end = _wide(data, end)
    if flags & 1:
        relation, end = _wide(data, end)
    if named:
        source_name, end = _wide(data, end)
    else:
        source_range, _bounds = _range(data[end:end + 16])
        end += 16
    if end != len(data):
        raise ValueError("trailing bytes in native Pivot cache source")
    targets = _targets(relationships, part, relation) if relation else []
    if relation and len(targets) != 1:
        context.problem(document, ("external_references",), "missing or ambiguous Pivot cache source target", location)
    for target in targets or [{}]:
        context.finding(document, "external_references", "pivot-cache-source", location,
                        target=target.get("Target"), sheet=sheet, range=source_range,
                        name=source_name, built_in=bool(built_in), flags_hex=data[:3].hex(),
                        relationship_id=relation, relationship_location=target.get("inspection_location"))


def _pivot_item_source(records, position, fields, caches, context, document):
    parts = []
    closed = False
    for index in range(position + 1, len(records)):
        kind, data, location, _component = records[index]
        if kind == 256:
            closed = True
            break
        if kind != 259:
            continue
        flags, field_index, item_index = struct.unpack_from("<BIi", data)
        if len(data) != 9 or field_index >= len(fields) or flags & ~3:
            raise ValueError("invalid calculated pivot item source fields")
        item, value_type = None, None
        dynamic = bool(flags & 1)
        if not dynamic:
            if flags & 2:
                raise ValueError("cache-index pivot item cannot be runtime-relative")
            index, visited = item_index, set()
            while True:
                if not 0 <= index < len(caches[field_index]) or index in visited:
                    raise ValueError("pivot cache item reference is absent or cyclic")
                context.charge("records", 1, location)
                visited.add(index)
                source = caches[field_index][index]
                if source["value_type"] != "index":
                    item, value_type = source["value"], source["value_type"]
                    break
                index = source["value"]
        part = {"field": fields[field_index], "item": item, "value_type": value_type,
                "field_index": field_index, "item_index": item_index, "flags": flags,
                "context_required": dynamic}
        context.finding(document, "names", "pivot-item-reference", location,
                        text=item if isinstance(item, str) else None, **part)
        parts.append(part)
    if not closed or not parts:
        raise ValueError("calculated pivot item name has no native item references")
    return {"kind": "pivot-item", "parts": parts}


def _supplementary_records(parts, relationships, context, document, names, external,
                           external_names, tables):
    """Recover non-cell formulas, macro properties and alternative source locations."""
    for part, _ordinal, records in parts:
        fields = []
        field_records = []
        caches, active_field = [], None
        for index, (kind, data, location, component) in enumerate(records):
            try:
                if kind == 183:
                    flags = struct.unpack_from("<H", data)[0]
                    field_name, end = _wide(data, 20)
                    fields.append(field_name)
                    caches.append([])
                    active_field = len(fields) - 1
                    caption = None
                    if flags & 8:
                        caption, end = _wide(data, end)
                    field_records.append((index, flags, end, field_name, 184))
                    context.finding(document, "names", "pivot-cache-field", location,
                                    text=field_name, flags=flags, caption=caption, field_index=len(fields) - 1)
                elif kind == 184:
                    active_field = None
                elif 20 <= kind <= 32 or kind == 191:
                    entries = (_pivot_cache_run(data, context, location) if kind == 191 else
                               ((None, _pivot_cache_value(kind, data, context, location)),))
                    for run_index, source in entries:
                        item_index = len(caches[active_field]) if active_field is not None else None
                        source_location = {**location, **({"run_index": run_index} if run_index is not None else {})}
                        context.finding(document, "cells", "pivot-cache-item", source_location,
                                        text=source["value"] if source["value_type"] == "string" else None,
                                        field_index=active_field, item_index=item_index, **source)
                        if active_field is not None:
                            caches[active_field].append(source)
                elif kind == 245:
                    if data[:4] != b"\xff" * 4:
                        raise ValueError("invalid calculated pivot item native prefix")
                    field_records.append((index, 256, 4, None, 246))
                elif kind == 187:
                    _pivot_source(data, part, relationships, context, document, location)
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("external_references",) if kind == 187 else ("names", "formulas"),
                                str(exc), location)
        for index, flags, offset, field_name, boundary in field_records:
            if not flags & 256:
                continue
            kind, data, location, component = records[index]
            try:
                pivot_names = []
                closed = False
                for position in range(index + 1, len(records)):
                    next_kind, payload, where, _ref = records[position]
                    if next_kind == boundary:
                        closed = True
                        break
                    if next_kind == 255:
                        source_field = _u32(payload)
                        if len(payload) != 6:
                            raise ValueError("pivot PName references an absent source cache field")
                        if source_field == 0xFFFFFFFF:
                            source = _pivot_item_source(records, position, fields, caches, context, document)
                            pivot_names.append(source)
                            context.finding(document, "names", "pivot-name-reference", where,
                                            field_index=source_field, aggregation_function=payload[4],
                                            flags=payload[5], item_source=source)
                        else:
                            if source_field >= len(fields):
                                raise ValueError("pivot PName references an absent source cache field")
                            pivot_names.append(fields[source_field])
                            context.finding(document, "names", "pivot-name-reference", where,
                                            text=fields[source_field], field_index=source_field,
                                            aggregation_function=payload[4], flags=payload[5])
                if not closed:
                    raise ValueError("missing native calculated pivot formula boundary")
                formula = _record_formula(data, offset, context, document, location, names, external,
                                          external_names=external_names, tables=tables, pivot_names=pivot_names)
                context.finding(document, "formulas", "pivot-formula" if kind == 183 else "pivot-item-formula",
                                location, text=formula,
                                field=field_name, pivot_names=pivot_names)
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                context.problem(document, ("names", "formulas"), str(exc), location)
        current_table, current_column = None, None
        for kind, data, location, component in records:
            try:
                if kind == 343:
                    current_table = _u32(data, 20)
                elif kind == 347:
                    current_column = _u32(data)
                elif kind in (351, 352):
                    if current_table is None or current_column is None:
                        context.problem(document, ("names", "formulas"),
                                        "table formula has no source table or column owner", location)
                    formula = _record_formula(data, 1, context, document, location, names, external,
                                              external_names=external_names, tables=tables)
                    context.finding(document, "formulas", "calculated-column-formula" if kind == 351
                                    else "total-row-formula", location, text=formula,
                                    array=bool(data[0] & 2), table_id=current_table, column_id=current_column)
                elif kind == 1036:
                    count = _u32(data, 4)
                    if data[:4] != b"\0" * 4 or not 1 <= count <= 60:
                        raise ValueError("invalid macro-name extension header or argument count")
                    name, end = _wide(data, 8)
                    arguments = []
                    for _index in range(count):
                        argument = _u32(data, end)
                        description, end = _wide(data, end + 4)
                        if argument >= 60 or not 1 <= len(description) <= 255:
                            raise ValueError("invalid macro argument description")
                        arguments.append({"argument_index": argument, "description": description})
                    if end != len(data) or len({item["argument_index"] for item in arguments}) != count:
                        raise ValueError("duplicate or trailing macro argument descriptions")
                    context.finding(document, "macros", "macro-name-extension", location,
                                    text=name, arguments=arguments)
                elif kind == 5099:
                    if len(data) != 5 or data[:4] != b"\0" * 4 or data[4] not in (0, 1):
                        raise ValueError("invalid external refresh native flags")
                    context.finding(document, "actions", "external-refresh-action", location,
                                    automatic_refresh=bool(data[4]))
                elif kind == 5108:
                    drive, end = _wide(data)
                    item, end = _wide(data, end)
                    absolute, end = _wide(data, end)
                    relative, end = _wide(data, end)
                    if end != len(data):
                        raise ValueError("trailing bytes in alternate external URL source")
                    for form, relation in (("absolute", absolute), ("relative", relative)):
                        matches = _targets(relationships, part, relation)
                        if len(matches) != 1:
                            context.problem(document, ("links", "external_references"),
                                            "alternate external URL target is missing or ambiguous", location)
                        for match in matches:
                            context.finding(document, "external_references", "external-alternate-url", location,
                                            target=match.get("Target"), drive_id=drive, item_id=item,
                                            relationship_id=relation, url_form=form,
                                            relationship_location=match["inspection_location"])
            except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
                affected = ("names", "macros") if kind == 1036 else (
                    ("links", "external_references") if kind in (5099, 5108) else ("formulas",))
                context.problem(document, affected, str(exc), location)


def _data_table(data):
    """Decode the stored TABLE operation without evaluating its input substitutions."""
    if len(data) != 33:
        raise ValueError("invalid BIFF12 data table record size")
    label, bounds = _range(data)
    flags = data[32]
    if not bounds[0] or not bounds[2] or flags & 224 or flags & 2 and not flags & 1:
        raise ValueError("invalid BIFF12 data table range or flags")
    if not flags & 2 and flags & 8:
        raise ValueError("one-variable BIFF12 data table declares a second deleted input")
    inputs = []
    for index in range(2):
        row, column = struct.unpack_from("<II", data, 16 + index * 8)
        used = index == 0 or bool(flags & 2)
        deleted = bool(flags & (4 << index))
        cell = _address(row, column) if used and not deleted else None
        if cell is not None:
            inside = bounds[0] - 1 <= row <= bounds[1] and bounds[2] - 1 <= column <= bounds[3]
            corner = index == 0 and not flags & 2 and (row, column) == (bounds[0] - 1, bounds[2] - 1)
            if inside and not corner:
                raise ValueError("BIFF12 data table input overlaps its source range")
        inputs.append({"row_index": row, "column_index": column, "used": used,
                       "deleted": deleted, "cell": cell})
    first = "#REF!" if inputs[0]["deleted"] else inputs[0]["cell"]
    second = "#REF!" if inputs[1]["deleted"] else inputs[1]["cell"]
    operands = [first, second] if flags & 2 else ([first, ""] if flags & 1 else ["", first])
    return bounds, {"range": label, "flags": flags, "inputs": inputs,
                    "two_variable": bool(flags & 2), "row_input": bool(flags & 1),
                    "always_calculate": bool(flags & 16), "context_required": True,
                    "operation": "TABLE(" + ",".join(operands) + ")"}


def _sheet(records, sheet, strings, names, external, relationships, context, document,
           *, external_names=None, tables=None, metadata=None, caller_scopes=()):
    external = list(external)
    for scope in caller_scopes:
        external[scope] = _quote(sheet["name"])
    row = 0
    merges = []
    groups = []
    data_tables = []
    for kind, data, location, component in records:
        try:
            if kind == 176:
                label, bounds = _range(data)
                merges.append((label, bounds))
            elif kind in (426, 427):
                label, bounds = _range(data)
                tokens, extra, _end = _formula_container(data, 17 if kind == 426 else 16)
                groups.append((bounds, tokens, extra))
                context.finding(document, "formulas", "array-formula" if kind == 426 else "shared-formula",
                                location, component=component, range=label, tokens_hex=tokens.hex(),
                                auxiliary_hex=extra.hex())
            elif kind == 428:
                bounds, details = _data_table(data)
                source = {**location, "sheet": sheet["name"], "sheet_ordinal": sheet["ordinal"]}
                artifact = context.payload(document, "data-table-source", source, data,
                                           "application/octet-stream")
                data_tables.append((bounds, details, source))
                context.finding(document, "formulas", "data-table-formula", source,
                                text=details["operation"], artifact=artifact, component=component, **details)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            context.problem(document, ("cells",) if kind == 176 else ("formulas",), str(exc), location)
    occupied = []
    pending_metadata = {}
    for kind, data, original, component in records:
        location = {**original, "sheet": sheet["name"], "sheet_ordinal": sheet["ordinal"],
                    "workbook_scope": "unreferenced" if sheet["state"] == "unreferenced" else "workbook"}
        try:
            if kind == 0:
                if pending_metadata:
                    context.problem(document, ("cells",),
                                    "metadata reference is not followed by a source cell in its row", location)
                    pending_metadata.clear()
                row = _u32(data)
                _address(row, 0)
                context.finding(document, "hidden_structures", "row", location, row=row + 1,
                                hidden=bool(data[11] & 16), flags=data[11:13].hex())
            elif kind in (49, 50):
                index = struct.unpack_from("<i", data)[0]
                category = "cell" if kind == 49 else "value"
                if metadata is None or not 1 <= index <= len(metadata[category]):
                    raise ValueError("cell references an absent source metadata block")
                pending_metadata[category] = {**metadata[category][index - 1], "reference_location": location}
            elif 1 <= kind <= 11 or kind == 62:
                column = _u32(data)
                address = _address(row, column)
                location.update(kind="workbook-cell", cell=address,
                                qualified_cell="'" + sheet["name"].replace("'", "''") + "'!" + address)
                rich = None
                if kind == 62:
                    value, rich = _rich_string(data, 8, context, location)
                    value_type, end = "string", len(data)
                else:
                    value, value_type, end = _cell_value(kind, data, strings)
                is_formula = 8 <= kind <= 11
                formula = (_record_formula(data, end + 2, context, document, location, names,
                                           external, row=row, column=column, groups=groups,
                                           external_names=external_names, tables=tables)
                           if is_formula else None)
                table_matches = [item for item in data_tables if item[0][0] <= row <= item[0][1]
                                 and item[0][2] <= column <= item[0][3]]
                table_source = None
                if len(table_matches) > 1:
                    context.problem(document, ("formulas",), "overlapping BIFF12 data table definitions", location)
                elif table_matches and not is_formula:
                    _, table_source, source_location = table_matches[0]
                    formula = table_source["operation"]
                    is_formula = True
                    table_source = {**table_source, "source_location": source_location}
                match = [item for item in merges if item[1][0] <= row <= item[1][1]
                         and item[1][2] <= column <= item[1][3]]
                context.finding(document, "cells", "cell", location, component=component,
                                value=value, value_type=value_type, formula=formula,
                                cached_value_state=("blank" if value_type == "blank" else "present")
                                if is_formula else "not_formula",
                                error=value if value_type == "error" else None,
                                merge_origin=match[0][0].split(":")[0] if match else None,
                                merge_range=match[0][0] if match else None,
                                style_reference=_u32(data, 4) & 0xFFFFFF,
                                **({"metadata": dict(pending_metadata)} if pending_metadata else {}),
                                **({"data_table": table_source} if table_source else {}),
                                **({"shared_string_index": _u32(data, 8)} if kind == 7 else {}),
                                **({"rich_text": rich} if rich is not None else {}))
                pending_metadata.clear()
                occupied.append((row, column))
            elif kind == 60:
                first, last = _u32(data), _u32(data, 4)
                _address(0, first)
                _address(0, last)
                if first > last:
                    raise ValueError("reversed BIFF12 column range")
                context.finding(document, "hidden_structures", "columns", location,
                                first_column=first + 1, last_column=last + 1,
                                hidden=bool(data[16] & 1), flags=data[16:18].hex())
            elif kind == 148:
                label, _bounds = _range(data)
                context.finding(document, "cells", "occupied-range", location, range=label)
            elif kind == 64:
                _validation(data, location, context, document, names, external, external_names, tables)
            elif kind == 1053:
                _validation14(data, location, context, document, names, external, external_names, tables)
            elif kind == 176:
                label, _bounds = _range(data)
                context.finding(document, "cells", "merge", location, range=label,
                                merge_origin=label.split(":")[0])
            elif kind == 494:
                label, _bounds = _range(data)
                relation, end = _wide(data, 16)
                fragment, end = _wide(data, end)
                tooltip, end = _wide(data, end)
                display, _end = _wide(data, end)
                targets = _targets(relationships, original["part"], relation)
                if relation and len(targets) != 1:
                    context.problem(document, ("links",), "ambiguous BIFF12 hyperlink relationship", location)
                for target in targets or [{"Target": ""}]:
                    context.finding(document, "links", "hyperlink", location,
                                    target=target.get("Target", ""), range=label,
                                    fragment=fragment, tooltip=tooltip, display=display,
                                    relationship_id=relation)
            elif kind == 479:
                end = 2
                labels = ("header", "footer", "header-even", "footer-even", "header-first", "footer-first")
                for label in labels:
                    text, end = _wide(data, end, nullable=True)
                    context.finding(document, "text", label, location, text=text,
                                    active=bool(data[0] & (1 if "even" in label else 2))
                                    if "even" in label or "first" in label else True)
            elif kind in (550, 551, 552, 639, 644):
                metadata = {}
                capability = "forms" if kind == 644 else "attachments"
                label = "activex-control" if kind == 644 else "ole-object" if kind == 639 else "drawing"
                relation = None
                if kind == 644:
                    shape = _u32(data)
                    relation, end = _wide(data, 4)
                    control_name, end = _wide(data, end)
                    if not 1 <= shape <= 0x03FFD7FF or len(control_name) > 32 or end != len(data):
                        raise ValueError("invalid BIFF12 ActiveX control fields")
                    metadata = {"shape_id": shape, "name": control_name}
                elif kind == 639:
                    aspect, update, shape, flags = struct.unpack_from("<IIIH", data)
                    progid, end = _wide(data, 14)
                    linked, automatic = bool(flags & 1), bool(flags & 2)
                    if aspect not in (1, 4) or not 1 <= shape <= 0x03FFD7FF:
                        raise ValueError("invalid BIFF12 OLE object fields")
                    metadata = {"shape_id": shape, "progid": progid, "aspect": aspect,
                                "automatic_load": automatic, "linked": linked, "update_mode": update}
                    context.finding(document, "actions", "ole-load-action", location,
                                    automatic_load=automatic, linked=linked, update_mode=update,
                                    shape_id=shape, progid=progid)
                    if linked:
                        formula = _record_formula(data, end, context, document, location, names,
                                                  external, external_names=external_names)
                        context.finding(document, "external_references", "linked-ole-object", location,
                                        text=formula, component=component, **metadata)
                    else:
                        if update != 0:
                            raise ValueError("embedded OLE object has a linked-object update mode")
                        relation, end = _wide(data, end)
                        if end != len(data):
                            raise ValueError("trailing bytes in BIFF12 OLE object")
                else:
                    relation, end = _wide(data)
                    if end != len(data):
                        raise ValueError("trailing bytes in BIFF12 drawing relationship")
                    metadata = {"drawing_type": kind}
                if relation is not None:
                    for target, resolved, artifact, occurrence in _related_occurrences(
                            relationships, original["part"], relation, context, document,
                            location, (capability, "links")):
                        context.finding(document, capability, label, location, component=component,
                                        target=target, artifact=artifact, resolved_part=resolved,
                                        relationship_id=relation, relationship_location=occurrence, **metadata)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            affected = {49: ("cells",), 50: ("cells",), 6: ("cells", "text"), 7: ("cells", "text"),
                        8: ("cells", "text", "formulas"), 62: ("cells", "text"), 479: ("text",), 64: ("forms", "formulas"),
                        1053: ("forms", "formulas")}.get(
                kind, ("cells", "formulas") if 1 <= kind <= 11 else SEMANTICS)
            context.problem(document, affected, str(exc), location)
    if pending_metadata:
        for source in pending_metadata.values():
            context.problem(document, ("cells",), "metadata reference has no following source cell",
                            source["reference_location"])
    if occupied:
        first_row, last_row = min(item[0] for item in occupied), max(item[0] for item in occupied)
        first_col, last_col = min(item[1] for item in occupied), max(item[1] for item in occupied)
        occupied_range = _address(first_row, first_col) + ":" + _address(last_row, last_col)
    else:
        occupied_range = None
    context.finding(document, "hidden_structures", "sheet", sheet["location"],
                    sheet=sheet["name"], sheet_ordinal=sheet["ordinal"],
                    state=sheet["state"], occupied_range=occupied_range,
                    relationship_id=sheet["relationship"], sheet_id=sheet["sheet_id"])


def inspect_biff12(data: bytes, name: str, context: InspectionContext, document: str,
                   *, members=()) -> None:
    """Inspect every admitted XLSB binary part independently of readable sheet selection."""
    del data, name
    relationships = _relationships(members, context, document)
    native_types = _native_content_types(members, context, document)
    parts = []
    for ordinal, part, payload in members:
        if payload is None or part not in native_types:
            continue
        location = {"kind": "biff12", "part": part, "member_ordinal": ordinal}
        try:
            records = tuple(iter_biff12(payload, context, document, location))
            parts.append((part, ordinal, records))
            for start, end in ((131, 132), (129, 130)):
                if any(kind == start for kind, *_rest in records):
                    if sum(kind == start for kind, *_rest in records) != 1 or records[-1][0] != end:
                        context.problem(document, SEMANTICS,
                                        "BIFF12 source part has a missing or duplicate container boundary", location)
        except (ValueError, IndexError, struct.error) as exc:
            context.problem(document, SEMANTICS, str(exc), location)
    workbooks = [item for item in parts if any(record[0] == 131 for record in item[2])]
    if not workbooks:
        context.problem(document, SEMANTICS, "no readable BIFF12 workbook record", {"kind": "source"})
        return
    if len(workbooks) != 1:
        context.problem(document, SEMANTICS, "multiple BIFF12 workbook occurrences", {"kind": "source"})
    roots = [item for owner, item in relationships if owner == ""
             and item.get("Type") == OFFICE_RELATIONSHIPS + "officeDocument"]
    if len(roots) != 1 or roots[0].get("TargetMode") == "External":
        context.problem(document, SEMANTICS, "missing or ambiguous native workbook root relationship",
                        {"kind": "package"})
        return
    target = _resolve_ooxml_part("", roots[0].get("Target", ""))
    matching = [item for item in workbooks if item[0] == target]
    if len(matching) != 1:
        context.problem(document, SEMANTICS, "missing or duplicate native workbook root target",
                        {"kind": "package", "part": target})
        return
    workbook_part, _ordinal, workbook_records = matching[0]
    active_strings = _active_strings(parts, native_types, relationships, workbook_part, context, document)
    sheets = []
    names = []
    name_records = []
    supporting = []
    external = []
    external_names = {}
    external_tables = _external_tables(parts, relationships, context, document)
    rich_values, rich_sources = _rich_values(members, relationships, context, document)
    metadata = _metadata(parts, context, document, rich_values=rich_values, rich_sources=rich_sources)
    table_definitions = _table_definitions(parts, members, context, document)
    table_context = {}
    local_scopes = set()
    caller_scopes = set()
    strings = []
    for part, ordinal, records in parts:
        string_index = 0
        is_sst = native_types[part] == SHARED_STRINGS_TYPE
        headers = [record for record in records if record[0] == 159]
        if is_sst and (len(headers) != 1 or not records or records[-1][0] != 160):
            context.problem(document, ("cells", "text"), "missing or duplicate native shared-string boundary",
                            {"kind": "biff12", "part": part, "member_ordinal": ordinal})
        for kind, payload, location, _component in records:
            if kind == 19:
                try:
                    value, rich = _rich_string(payload, 0, context, location)
                    context.finding(document, "text", "shared-string", location, text=value,
                                    string_index=string_index, rich_text=rich,
                                    scope="workbook" if (part, ordinal) == active_strings else "unreferenced",
                                    workbook_part=workbook_part if (part, ordinal) == active_strings else None)
                    if (part, ordinal) == active_strings:
                        strings.append(value)
                except (ValueError, IndexError, UnicodeError, struct.error) as exc:
                    if (part, ordinal) == active_strings:
                        strings.append(None)
                    context.problem(document, ("cells", "text"), str(exc), location)
                string_index += 1
            elif kind == 59:
                try:
                    value, _end = _wide(payload)
                    context.finding(document, "text", "metadata-string", location, text=value)
                except (ValueError, UnicodeError, struct.error) as exc:
                    context.problem(document, ("text",), str(exc), location)
        if is_sst and len(headers) == 1:
            try:
                if len(headers[0][1]) != 8 or _u32(headers[0][1], 4) != string_index:
                    raise ValueError("native shared-string unique count does not match source entries")
            except (ValueError, struct.error) as exc:
                context.problem(document, ("cells", "text"), str(exc), headers[0][2])
    for kind, payload, location, component in workbook_records:
        try:
            if kind == 156:
                relation, end = _wide(payload, 8, nullable=True)
                sheet_name, _end = _wide(payload, end)
                state = _u32(payload)
                if state not in (0, 1, 2):
                    raise ValueError("invalid BIFF12 sheet visibility")
                sheet = {"name": sheet_name, "ordinal": len(sheets) + 1,
                         "sheet_id": _u32(payload, 4), "relationship": relation,
                         "state": ("visible", "hidden", "veryHidden")[state], "location": location}
                sheets.append(sheet)
                if relation is None:
                    if state != 2:
                        raise ValueError("BIFF12 module sheet is not very hidden")
                    context.finding(document, "macros", "module-sheet", location,
                                    sheet=sheet_name, sheet_ordinal=sheet["ordinal"], state=sheet["state"])
            elif kind == 39:
                value, end = _wide(payload, 9)
                names.append(value)
                name_records.append((payload, location, component, end, value))
            elif kind in (355, 357, 358, 667):
                relation, _end = _wide(payload) if kind == 355 else (None, 0)
                supporting.append((kind, relation))
                context.finding(document, "external_references", "supporting-link", location,
                                relationship_id=relation, supporting_type=kind)
            elif kind == 2071:
                value, _end = _wide(payload)
                context.finding(document, "external_references", "workbook-source-location",
                                location, target=value)
            elif kind == 362:
                count = _u32(payload)
                if 4 + count * 12 != len(payload):
                    raise ValueError("invalid BIFF12 external-sheet count")
                for offset in range(4, len(payload), 12):
                    support, first, last = struct.unpack_from("<Iii", payload, offset)
                    if support >= len(supporting):
                        raise ValueError("unresolved supporting link index")
                    link_kind, relation = supporting[support]
                    if link_kind == 357 and 0 <= first <= last < len(sheets):
                        scope = sheets[first]["name"] if first == last else (
                            sheets[first]["name"] + ":" + sheets[last]["name"])
                        external.append("'" + scope.replace("'", "''") + "'")
                        local_scopes.add(len(external) - 1)
                    elif link_kind == 358 and first == last == -2:
                        external.append({"kind": "caller-sheet", "supporting_record": link_kind,
                                         "supporting_index": support})
                        caller_scopes.add(len(external) - 1)
                        local_scopes.add(len(external) - 1)
                    elif link_kind == 667 and first == last == -2:
                        external.append({"kind": "add-in", "supporting_record": link_kind,
                                         "supporting_index": support})
                    elif link_kind == 355:
                        target = _resolve_relation(relationships, workbook_part, relation)
                        tables = external_tables.get(target, ())
                        if len(tables) != 1:
                            raise ValueError("missing or ambiguous BIFF12 external source table: " + target)
                        table = tables[0]
                        external.append(_table_scope(table, first, last))
                        for index, item in enumerate(table["names"], 1):
                            if "qualified_name" not in item:
                                raise ValueError("external name has no complete source declaration")
                            external_names[(len(external) - 1, index)] = item["qualified_name"]
                    else:
                        external.append("")
                        context.problem(document, ("external_references", "formulas"),
                                        "unresolved BIFF12 supporting link scope", location)
                    context.finding(document, "external_references", "external-sheet", location,
                                    supporting_index=support, first_sheet=first, last_sheet=last,
                                    resolved_scope=external[-1] if isinstance(external[-1], str) else None,
                                    source_scope=external[-1], context_required=isinstance(external[-1], dict))
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            context.problem(document, SEMANTICS, str(exc), location)
    for identifier, definitions in table_definitions.items():
        if len(definitions) != 1:
            context.problem(document, ("names", "formulas"), "ambiguous source table numeric identity",
                            definitions[0]["location"])
            continue
        for scope in local_scopes:
            table_context[(scope, identifier)] = definitions[0]
    _supplementary_records(parts, relationships, context, document, names, external,
                           external_names, table_context)
    for payload, location, component, offset, value in name_records:
        try:
            formula = _record_formula(payload, offset, context, document, location, names, external,
                                      external_names=external_names, tables=table_context)
            _tokens, _extra, end = _formula_container(payload, offset)
            flags = _u32(payload)
            comments = []
            while end < len(payload):
                text, end = _wide(payload, end, nullable=True)
                comments.append(text)
            context.finding(document, "names", "defined-name", location, component=component,
                            text=value, formula=formula, flags=flags, hidden=bool(flags & 1),
                            scope=_u32(payload, 5), trailing_strings=comments)
            if flags & 14:
                context.finding(document, "macros", "macro-name", location, text=value,
                                xlm=bool(flags & 2), vba=bool(flags & 4), macro=bool(flags & 8),
                                formula=formula, trailing_strings=comments)
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            context.problem(document, ("names", "macros", "formulas"), str(exc), location)
    scanned = set()
    for sheet in sheets:
        try:
            if sheet["relationship"] is None:
                context.finding(document, "hidden_structures", "sheet", sheet["location"],
                                sheet=sheet["name"], sheet_ordinal=sheet["ordinal"],
                                state=sheet["state"], occupied_range=None)
                continue
            target = _resolve_relation(relationships, workbook_part, sheet["relationship"])
            matching = [item for item in parts if item[0] == target]
            if len(matching) != 1:
                raise ValueError("missing or duplicate BIFF12 sheet part: " + target)
            _sheet(matching[0][2], sheet, strings, names, external, relationships, context, document,
                   external_names=external_names, tables=table_context, metadata=metadata,
                   caller_scopes=caller_scopes)
            scanned.add((matching[0][0], matching[0][1]))
        except (ValueError, IndexError, KeyError, struct.error, UnicodeError) as exc:
            context.problem(document, SEMANTICS, str(exc), sheet["location"])
    for part, ordinal, records in parts:
        if (part, ordinal) not in scanned and any(kind == 129 for kind, *_rest in records):
            sheet = {"name": part, "ordinal": len(sheets) + 1, "state": "unreferenced",
                     "relationship": None, "sheet_id": None,
                     "location": {"kind": "biff12", "part": part, "member_ordinal": ordinal}}
            sheets.append(sheet)
            _sheet(records, sheet, (), (), (), relationships, context, document)
    understood = FRAME_RECORDS | STYLE_RECORDS | set(range(12)) | set(range(20, 33)) | {19, 39, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 62, 64, 148, 156,
                  176, 355, 357, 358, 359, 360, 362, 363, 364, 366, 367, 368, 369, 370, 371,
                  426, 427, 428, 472, 479, 494, 550, 551, 552, 577, 578, 579, 580, 581, 582, 583,
                  584, 585, 586, 587, 588, 639, 644, 667, 2071, 332, 333, 334, 335, 336, 337,
                  338, 339, 340, 343, 344, 347, 348, 351, 352, 183, 184, 255, 256, 1036, 1053,
                  187, 188, 189, 190, 191, 192, 243, 244, 245, 246, 257, 258, 259, 260,
                  4096, 4097, 5002, 5003, 5099, 5108}
    for _part, _ordinal, records in parts:
        for kind, payload, location, component in records:
            if kind not in understood:
                context.finding(document, "inventory", "opaque-biff12-record", location,
                                component=component, byte_size=len(payload),
                                record_name=None, affected_capabilities=list(SEMANTICS))
                context.problem(document, SEMANTICS,
                                "unsupported BIFF12 record 0x%04x may contain required metadata" % kind,
                                location)
    for capability in SEMANTICS:
        context.cover(document, capability, "complete", "all admitted BIFF12 source records inspected")
