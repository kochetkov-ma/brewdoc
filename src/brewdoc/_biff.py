"""Bounded passive BIFF stream inspection, independent of cached-value rendering."""

from __future__ import annotations

from collections import Counter
import math
import hashlib
import re
import struct
import uuid
import zlib
from xml.etree import ElementTree

from brewdoc._inspection_common import InspectionLimit
from brewdoc.common import BrewdocError, _xml_preflight
from brewdoc._biff_strings import parse_sst, parse_xl_unicode_segments
from brewdoc._biff_legacy import (legacy_formula, legacy_defined_name, legacy_extern_sheet,
                                legacy_external_name, legacy_codepage, legacy_cached_values,
                                parse_legacy_cell, legacy_row, biff_array_definition,
                                biff_table_definition)
from brewdoc._excel_formula import decode_formula
from brewdoc._officeart import (_hyperlink as _office_hyperlink, inspect_biff_object,
                               inspect_officeart, _object_formula)

ERRORS = {0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!",
          0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A", 0x2B: "#GETTING_DATA"}
CAPABILITIES = ("text", "scripts", "cells", "formulas", "names", "links", "external_references",
                "hidden_structures", "macros", "actions", "forms", "images", "attachments")
_BUILTIN_NAMES = {0: "Consolidate_Area", 1: "Auto_Open", 2: "Auto_Close", 3: "Extract",
                  4: "Database", 5: "Criteria", 6: "Print_Area", 7: "Print_Titles",
                  8: "Recorder", 9: "Data_Form", 10: "Auto_Activate", 11: "Auto_Deactivate",
                  12: "Sheet_Title", 13: "_FilterDatabase"}
_SAFE_RECORDS = {
    0x0008, 0x0021, 0x0036, 0x0037, 0x0236,
    0x000A, 0x000C, 0x000D, 0x000E, 0x000F, 0x0010, 0x0011, 0x0012, 0x0013,
    0x0014, 0x0015, 0x0019, 0x001A, 0x001B, 0x001D, 0x0022, 0x0026, 0x0027,
    0x0028, 0x0029, 0x002A, 0x002B, 0x0031, 0x003D, 0x0040, 0x0041, 0x0042,
    0x0055, 0x005C, 0x005F, 0x0063, 0x007D, 0x0080, 0x0081, 0x0082, 0x0083,
    0x0084, 0x0085, 0x0086, 0x008C, 0x008D, 0x0092, 0x0099, 0x009A, 0x00A0,
    0x00A1, 0x00AB, 0x00BD, 0x00BE, 0x00C1, 0x00D6, 0x00DA, 0x00DD, 0x00E0,
    0x00E1, 0x00E2, 0x00E5, 0x00EB, 0x00FC, 0x00FD, 0x00FF, 0x013D, 0x0160,
    0x0161, 0x01AF, 0x01B7, 0x01BC, 0x01C0, 0x01C1, 0x0200, 0x0201,
    0x0203, 0x0204, 0x0205, 0x0207, 0x0208, 0x020B, 0x0225, 0x023E,
    0x027E, 0x0293, 0x041E, 0x04BC, 0x0809, 0x0862, 0x0863, 0x0867, 0x0868,
}
_OPAQUE_RECORDS = {
    0x0033: "PrintSize: chart print size setting",
    0x0850: "ChartFrtInfo: chart producer versions and future-record identifiers",
    0x0852: "StartBlock: chart future-record collection boundary",
    0x0853: "EndBlock: chart future-record collection boundary",
    0x0854: "StartObject: chart element collection boundary",
    0x0855: "EndObject: chart element collection boundary",
    0x0856: "CatLab: category axis label layout",
    0x089D: "CrtLayout12: attached label or legend geometry",
    0x08A7: "CrtLayout12A: plot area geometry",
    0x004D: "Pls: opaque printer driver settings",
    0x009C: "BuiltInFnGroupCount: built-in function category count",
    0x00D7: "DBCell: physical cell-block lookup offsets",
    0x00DE: "OleObjectSize: visible cell range when this workbook is embedded",
    0x00EF: "PhoneticInfo: phonetic string display formatting and ranges",
    0x087C: "XFCRC: formatting record checksum",
    0x087D: "XFExt: extended cell formatting",
    0x088B: "PLV: page layout view settings",
    0x088C: "Compat12: compatibility-check settings",
    0x088E: "TableStyles: default table and pivot style names",
    0x0892: "StyleExt: extended cell style formatting",
    0x0896: "Theme: opaque Office theme payload",
    0x089A: "MTRSettings: multithreaded calculation settings",
    0x089B: "CompressPictures: save-time image compression setting",
    0x08A3: "ForceFullCalculation: recalculation setting",
}
_CHART_METADATA = {
    0x1001: "Units", 0x1002: "Chart", 0x1003: "Series", 0x1006: "DataFormat",
    0x1007: "LineFormat", 0x1009: "MarkerFormat", 0x100A: "AreaFormat",
    0x100B: "PieFormat", 0x100C: "AttachedLabel", 0x1014: "ChartFormat",
    0x1015: "Legend", 0x1016: "SeriesList", 0x1017: "Bar", 0x1018: "Line",
    0x1019: "Pie", 0x101A: "Area", 0x101B: "Scatter", 0x101C: "CrtLine",
    0x101D: "Axis", 0x101E: "Tick", 0x101F: "ValueRange", 0x1020: "CatSerRange",
    0x1021: "AxisLine", 0x1022: "CrtLink", 0x1024: "DefaultText", 0x1025: "Text",
    0x1026: "FontX", 0x1027: "ObjectLink", 0x1032: "Frame", 0x1033: "Begin",
    0x1034: "End", 0x1035: "PlotArea", 0x103A: "Chart3d", 0x103D: "DropBar",
    0x103E: "Radar", 0x103F: "Surf", 0x1040: "RadarArea", 0x1041: "AxisParent",
    0x1043: "LegendException", 0x1044: "ShtProps", 0x1045: "SerToCrt",
    0x1046: "AxesUsed", 0x104A: "SerParent", 0x104B: "SerAuxTrend",
    0x104E: "IFmtRecord", 0x104F: "Pos", 0x1050: "AlRuns", 0x105F: "Chart3DBarShape",
    0x1062: "AxcExt", 0x1064: "PlotGrowth",
}
_OPAQUE_RECORDS.update({kind: name + ": chart geometry, structure or display formatting"
                        for kind, name in _CHART_METADATA.items()})


class _Cursor:
    """Read checked fields without accepting truncated or oversized structures."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data, self.offset = data, offset

    def take(self, size: int) -> bytes:
        """Consume an exact field or name its truncation."""
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("truncated BIFF field at byte %d" % self.offset)
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def number(self, kind: str):
        """Read one little-endian scalar."""
        return struct.unpack("<" + kind, self.take(struct.calcsize("<" + kind)))[0]


def _column(number: int) -> str:
    text = ""
    while number >= 0:
        number, remainder = divmod(number, 26)
        text = chr(65 + remainder) + text
        number -= 1
    return text


def _address(row: int, column: int) -> str:
    return _column(column) + str(row + 1)


def _unicode(cursor: _Cursor, count: int | None = None, *, short=False) -> str:
    """Read BIFF8 compressed Unicode including its optional rich/extension fields."""
    count = cursor.number("B" if short else "H") if count is None else count
    flags = cursor.number("B")
    runs = cursor.number("H") if flags & 8 else 0
    extension = cursor.number("I") if flags & 4 else 0
    data = cursor.take(count * (2 if flags & 1 else 1))
    text = data.decode("utf-16le" if flags & 1 else "latin1")
    cursor.take(runs * 4 + extension)
    return text


def _legacy_string(cursor: _Cursor, codepage: str, *, short=False) -> str:
    return cursor.take(cursor.number("B" if short else "H")).decode(codepage)


def _location(name, offset, kind, **details):
    return {"kind": "biff-record", "stream": name, "record_offset": offset,
            "record_type": "0x%04X" % kind, **details}


def _bof_version(kind, payload):
    """Select only an explicitly supported native BOF dialect."""
    if kind in (0x0009, 0x0209, 0x0409):
        return {0x0009: 2, 0x0209: 3, 0x0409: 4}[kind]
    version = _Cursor(payload).number("H")
    if version not in (0x0500, 0x0600):
        raise ValueError("unsupported BIFF BOF version 0x%04X" % version)
    return {0x0500: 5, 0x0600: 8}[version]


def _builtin_name(label, flags, context, document, location):
    """Resolve documented indexed aliases while retaining unknown source identities."""
    if not flags & 0x20 or len(label) != 1:
        return label
    index = ord(label)
    if index not in _BUILTIN_NAMES:
        context.problem(document, ("names", "formulas"), "unknown BIFF built-in name index 0x%02X" % index, location)
    return _BUILTIN_NAMES.get(index, "builtin-name-0x%02X" % index)


def _continuations(records, index, state):
    """Retain native continuation boundaries while recording their semantic owner."""
    source = [records[index]]
    index += 1
    while index < len(records) and records[index][1] == 0x003C:
        source.append(records[index])
        state["continued_offsets"].add(records[index][0])
        index += 1
    return source


def _records(data, name, context, document):
    """Inventory record occurrences before semantic passes; preserve framing failures."""
    result, offset = [], 0
    while offset < len(data):
        location = _location(name, offset, 0)
        if len(data) - offset < 4:
            context.problem(document, CAPABILITIES, "truncated BIFF record header", location)
            break
        kind, size = struct.unpack_from("<HH", data, offset)
        location["record_type"] = "0x%04X" % kind
        end = offset + 4 + size
        if kind == 0 and size == 0 and not any(data[offset:]):
            context.component(document, "biff-padding", location, data=data[offset:], disposition="opaque",
                              reason="zero padding after BIFF records")
            break
        if end > len(data):
            context.component(document, "biff-record", location, data=data[offset:],
                              disposition="unreadable", reason="truncated BIFF record payload")
            context.problem(document, CAPABILITIES, "truncated BIFF record payload", location)
            break
        payload = data[offset + 4:end]
        component = context.component(document, "biff-record", location, data=data[offset:end],
                                      disposition="opaque", reason=_OPAQUE_RECORDS.get(kind, "native record bytes retained in source stream"),
                                      record_type=kind, payload_bytes=size)
        if kind != 0x0851:
            result.append((offset, kind, payload, component))
        if kind == 0x0851:
            try:
                source = _Cursor(payload)
                if source.number("H") != kind:
                    raise ValueError("invalid BIFF FrtWrapper identity")
                source.take(2)
                inner_kind, inner_size = source.number("H"), source.number("H")
                inner = source.take(inner_size)
                padding = source.take(max(0, 4 - inner_size))
                if source.offset != len(payload) or any(padding) or inner_kind not in (
                        0x0031, 0x003C, 0x1007, 0x100A, 0x100D, 0x1024, 0x1025, 0x1026,
                        0x1027, 0x1032, 0x1033, 0x1034, 0x103C, 0x104F, 0x1050, 0x1051, 0x1060, 0x1066):
                    raise ValueError("malformed or unsupported BIFF wrapped record")
                where = {**_location(name, offset + 8, inner_kind), "wrapper_offset": offset}
                child = context.component(document, "biff-wrapped-record", where,
                                          data=payload[4:8 + inner_size], disposition="opaque",
                                          reason="native record inside FrtWrapper", wrapper_component=component)
                result.append((offset + 8, inner_kind, inner, child))
            except (ValueError, struct.error) as exc:
                context.problem(document, CAPABILITIES, str(exc), location)
        offset = end
    return result


def _formula(data, *, row=0, column=0, names=(), external_sheets=(), shared=None,
             extra=b"", external_names=None, version=8, codepage="cp1252", sheets=(),
             tables=None, sheet_contexts=None):
    """Resolve workbook anchors, then use the shared passive Excel token decoder."""
    if data[:1] == b"\x02":
        if extra:
            raise ValueError("unexpected BIFF data-table auxiliary bytes")
        anchor = _table_anchor(data, version)
        if tables is None or anchor not in tables:
            raise ValueError("unresolved BIFF data-table anchor %s" % _address(*anchor))
        definition = tables[anchor]
        first_row, last_row, first_col, last_col = definition["bounds"]
        if not first_row <= row <= last_row or not first_col <= column <= last_col:
            raise ValueError("BIFF data-table operand outside definition range")
        return definition["expression"]
    if data[:1] == b"\x01":
        _anchor, definition = _shared_definition(data, version, row, column, shared)
        data, extra = definition["tokens"], definition["extra"]
    if version < 8:
        return legacy_formula(data, extra, version=version, codepage=codepage, row=row, column=column,
                              names=names, external=external_sheets, external_names=external_names,
                              sheets=sheets, sheet_contexts=sheet_contexts)
    return decode_formula(data, extra, dialect="biff8", row=row, column=column,
                          names=names, external=external_sheets, external_names=external_names)


def _table_anchor(data, version):
    """Read the sole native tTbl operand without accepting trailing operations."""
    cursor = _Cursor(data, 1)
    anchor = cursor.number("H"), cursor.number("B" if version == 2 else "H")
    if cursor.offset != len(data):
        raise ValueError("invalid BIFF data-table operand length")
    return anchor


def _shared_definition(data, version, row, column, definitions):
    """Resolve a source anchor only within its unambiguous native definition bounds."""
    anchor = _table_anchor(data, version)
    if definitions is None or anchor not in definitions:
        raise ValueError("unresolved BIFF shared/array formula anchor %s" % _address(*anchor))
    definition = definitions[anchor]
    if definition.get("invalid"):
        raise ValueError("ambiguous BIFF shared/array formula definition")
    first_row, last_row, first_col, last_col = definition["bounds"]
    if not first_row <= row <= last_row or not first_col <= column <= last_col:
        raise ValueError("BIFF shared/array operand outside definition range")
    return anchor, definition


def _table_expression(definition):
    """Describe the source TABLE operation without calculating its result."""
    def operand(coordinate, deleted):
        """Keep deleted references distinct from literal source coordinates."""
        return "#REF!" if deleted else "$" + _column(coordinate[1]) + "$" + str(coordinate[0] + 1)

    first = operand(definition["first_input"], definition["first_input_deleted"])
    if definition["two_variable"]:
        return "TABLE(" + first + "," + operand(definition["second_input"], definition["second_input_deleted"]) + ")"
    return "TABLE(" + (first + "," if definition["row_input"] else "," + first) + ")"


def _rk(value):
    if value & 2:
        number = (value - 0x100000000 if value & 0x80000000 else value) >> 2
    else:
        number = struct.unpack("<d", struct.pack("<II", 0, value & 0xFFFFFFFC))[0]
    return number / 100 if value & 1 else number


def _cache(raw):
    """Distinguish BIFF cached errors, empty results, strings and numeric values."""
    if raw[6:8] != b"\xff\xff":
        value = struct.unpack("<d", raw)[0]
        if not math.isfinite(value):
            raise ValueError("non-finite BIFF numeric cache")
        return value, "number", "present", None
    kind, value = raw[0], raw[2]
    if kind == 0:
        return None, "string", "missing", None
    if kind == 1:
        return bool(value), "boolean", "present", None
    if kind == 2 and value in ERRORS:
        return ERRORS[value], "error", "present", ERRORS[value]
    if kind == 3:
        return "", "string", "blank", None
    raise ValueError("unsupported BIFF cached result type %d" % kind)


def _external_definition(data, supporting):
    """Translate the restricted external-name formula into the shared token dialect."""
    cursor = _Cursor(data)
    length = cursor.number("H")
    tokens = cursor.take(length)
    if cursor.offset != len(data):
        raise ValueError("unvisited BIFF external formula bytes")
    if not tokens:
        return None
    if tokens[0] == 0x1C:
        return decode_formula(tokens, dialect="biff8")
    if tokens[0] not in (0x3A, 0x3B, 0x3C, 0x3D):
        raise ValueError("unsupported BIFF external formula token 0x%02X" % tokens[0])
    source = _Cursor(tokens, 1)
    first, last = source.number("H"), source.number("H")
    sheets = supporting["sheets"]
    if first >= len(sheets) or last >= len(sheets):
        raise ValueError("unresolved BIFF external defined-name sheet scope")
    scope = sheets[first] + (":" + sheets[last] if first != last else "")
    target = supporting["target"]
    if target is None:
        raise ValueError("BIFF external definition has no source workbook target")
    scope = "'[" + target.replace("'", "''") + "]" + scope.replace("'", "''") + "'"
    body = source.take(len(tokens) - source.offset)
    if tokens[0] in (0x3A, 0x3B):
        coordinates = struct.unpack("<HH" if tokens[0] == 0x3A else "<HHHH", body)
        references = ((coordinates[0], coordinates[1]),) if tokens[0] == 0x3A else (
            (coordinates[0], coordinates[2]), (coordinates[1], coordinates[3]))
        if any(column & 0xC000 for _row, column in references):
            return scope + "!" + ":".join(_external_relative_ref(row, column) for row, column in references)
    translated = tokens[:1] + b"\x00\x00" + body
    return decode_formula(translated, dialect="biff8", external=(scope,))


def _external_relative_ref(row, column):
    """Keep relative name offsets in R1C1 notation when no consuming cell exists."""
    row_relative, col_relative = bool(column & 0x8000), bool(column & 0x4000)
    column &= 0x3FFF
    row_value = row - 65536 if row & 0x8000 else row
    column_value = column - 16384 if column & 0x2000 else column
    return ("R[%d]" % row_value if row_relative else "R%d" % (row + 1)) + (
        "C[%d]" % column_value if col_relative else "C%d" % (column + 1))


def _moper(data, context, location):
    """Recover typed OLE/DDE cache operands without opening their source service."""
    cursor = _Cursor(data)
    columns, rows = cursor.number("B") + 1, cursor.number("H") + 1
    context.check("records", context.consumed["records"] + rows * columns, location)
    context.check("report_bytes", context.consumed["report_bytes"] + rows * columns * 48, location)
    context.charge("records", rows * columns, location)
    values = []
    for _ in range(rows):
        row = []
        for _ in range(columns):
            kind = cursor.number("B")
            error = None
            if kind == 0:
                cursor.take(8)
                value, value_type = None, "blank"
            elif kind == 1:
                value, value_type = cursor.number("d"), "number"
                if not math.isfinite(value):
                    raise ValueError("non-finite BIFF external cached number")
            elif kind == 2:
                value, value_type = _unicode(cursor), "string"
            elif kind == 4:
                boolean = cursor.number("B")
                if boolean not in (0, 1):
                    raise ValueError("invalid BIFF external cached Boolean")
                value, value_type = bool(boolean), "boolean"
                cursor.take(7)
            elif kind == 16:
                code = cursor.number("B")
                if code not in ERRORS:
                    raise ValueError("unknown BIFF external cache error 0x%02X" % code)
                value, value_type, error = ERRORS[code], "error", ERRORS[code]
                cursor.take(7)
            else:
                raise ValueError("unsupported BIFF external cache operand 0x%02X" % kind)
            row.append({"value": value, "value_type": value_type, "error": error})
        values.append(row)
    if cursor.offset != len(data):
        raise ValueError("unvisited BIFF external cache bytes")
    return values


def _drawing(records, kind, name, context, document):
    """Join only drawing payloads and preserve a physical source map for every segment."""
    pieces, segments, total, continued = [], [], 0, set()
    continuing = False
    for offset, record_kind, payload, _component in records:
        if record_kind == kind or record_kind == 0x003C and continuing:
            context.check("payload_bytes", total + len(payload), _location(name, offset, record_kind))
            pieces.append(payload)
            segments.append({"record_offset": offset, "payload_offset": offset + 4,
                             "assembled_offset": total, "byte_size": len(payload)})
            total += len(payload)
            continuing = True
            if record_kind == 0x003C:
                continued.add(offset)
        else:
            continuing = False
    if pieces:
        location = {**_location(name, segments[0]["record_offset"], kind), "segments": segments}
        context.check("decoded_bytes", context.consumed["decoded_bytes"] + total, location)
        inspect_officeart(b"".join(pieces), context, document, location=location)
    return continued


def _gel_frame(records, index, state, location, context, document):
    """Pass one chart fill property group through the existing OfficeArt reader."""
    parts = _continuations(records, index, state)
    total = sum(len(item[2]) for item in parts)
    context.check("payload_bytes", total, location)
    segments, start = [], 0
    for offset, _kind, payload, _component in parts:
        segments.append({"record_offset": offset, "payload_offset": offset + 4,
                         "assembled_offset": start, "byte_size": len(payload)})
        start += len(payload)
    inspect_officeart(b"".join(item[2] for item in parts), context, document,
                      location={**location, "segments": segments})


def _object(payload, location, state, context, document):
    """Resolve parsed control actions and exact CFB data ranges using source identities."""
    start = len(context.findings)
    inspect_biff_object(payload, context, document, location=location)
    for finding in tuple(context.findings[start:]):
        if finding["kind"] == "object-macro":
            index = finding["details"]["name_index"]
            if not 1 <= index <= len(state["names"]):
                context.problem(document, "actions", "unresolved BIFF object macro name", finding["location"])
                continue
            context.finding(document, "actions", "object-macro-name", finding["location"],
                            target=state["names"][index - 1], name_index=index)
        elif finding["kind"] == "object-data-source":
            target = finding["target"]
            streams = [item for item in state.get("streams", ()) if item["kind"] == "stream"
                       and (item["name"].casefold() == target.casefold() or
                            item["path"].casefold().startswith(target.casefold() + "/"))]
            if not streams:
                context.problem(document, "attachments", "linked BIFF object stream is missing: " + target,
                                finding["location"])
            for item in streams:
                if item["data"] is None:
                    context.problem(document, "attachments", "linked BIFF object stream is unreadable", item["location"])
                    continue
                details = finding["details"]
                offset = details.get("stream_offset", 0)
                size = details.get("byte_size", len(item["data"]) - offset)
                source = _Cursor(item["data"], offset)
                raw = source.take(size)
                where = {**finding["location"], "compound_path": item["path"],
                         "stream_offset": offset, "byte_size": size}
                artifact = context.payload(document, "object-data", where, raw, "application/octet-stream")
                context.finding(document, "attachments", "object-data", where, target=target, artifact=artifact)


def _text_object(records, index, state, location, context, document):
    """Decode TxO character records separately from raw formatting continuations."""
    cursor = _Cursor(records[index][2])
    flags = cursor.number("H")
    cursor.take(8)
    count, run_bytes = cursor.number("H"), cursor.number("H")
    cursor.take(2)
    formula_data = cursor.take(len(cursor.data) - cursor.offset)
    if formula_data:
        tokens, used = _object_formula(formula_data)
        if used != len(formula_data):
            raise ValueError("unvisited BIFF text object formula bytes")
        if tokens:
            formula = _formula(tokens, names=state["names"], external_sheets=state["external_sheets"],
                               external_names=state["external_names"])
            artifact = context.payload(document, "object-text-formula", location, formula_data, "application/octet-stream")
            context.finding(document, "formulas", "object-text-formula", location, text=formula, artifact=artifact)
    if (not count and run_bytes) or (count and (run_bytes < 16 or run_bytes % 8)):
        raise ValueError("invalid BIFF text object formatting length")
    context.check("decoded_bytes", context.consumed["decoded_bytes"] + count * 2 + run_bytes, location)
    parts = _continuations(records, index, state)[1:]
    strings, remaining, used = [], count, 0
    while remaining:
        if used >= len(parts):
            raise ValueError("truncated BIFF text object characters")
        data = parts[used][2]
        used += 1
        if not data or data[0] & 0xFE:
            raise ValueError("invalid BIFF text continuation encoding")
        width = 2 if data[0] else 1
        if (len(data) - 1) % width or (len(data) - 1) // width > remaining:
            raise ValueError("invalid BIFF text continuation character boundary")
        strings.append(data[1:].decode("utf-16le" if width == 2 else "latin1"))
        remaining -= (len(data) - 1) // width
    raw_runs = b"".join(item[2] for item in parts[used:])
    if len(raw_runs) != run_bytes:
        raise ValueError("truncated or excess BIFF text formatting runs")
    runs = []
    for offset in range(0, len(raw_runs), 8):
        character, font = struct.unpack_from("<HH", raw_runs, offset)
        if character > count:
            raise ValueError("BIFF text formatting run exceeds characters")
        runs.append({"character": character, "font": font})
    context.charge("decoded_bytes", count * 2 + run_bytes, location)
    return "".join(strings), runs


def _legacy_image(records, index, state, location, context, document):
    """Retain the framed image payload without its BIFF wrapper or continuation headers."""
    parts = _continuations(records, index, state)
    cursor = _Cursor(parts[0][2])
    image_format, environment, size = cursor.number("H"), cursor.number("H"), cursor.number("I")
    context.check("payload_bytes", size, location)
    if image_format not in (2, 9, 14) or environment not in (1, 2) or size < 1:
        raise ValueError("invalid BIFF image format, environment or size")
    chunks, segments, total = [], [], 0
    for ordinal, (offset, _kind, payload, _component) in enumerate(parts):
        skip = 8 if ordinal == 0 else 0
        chunk = payload[skip:]
        chunks.append(chunk)
        segments.append({"payload_offset": offset + 4 + skip, "image_offset": total, "byte_size": len(chunk)})
        total += len(chunk)
    if total != size:
        raise ValueError("BIFF image payload length mismatch")
    media = {9: "image/x-dib", 14: "application/octet-stream"}.get(image_format,
             "image/wmf" if environment == 1 else "image/x-pict")
    where = {**location, "segments": segments}
    raw = b"".join(chunks)
    artifact = context.payload(document, "legacy-image", where, raw, media)
    context.finding(document, "images", "legacy-image", where, artifact=artifact, media_type=media,
                    image_format=image_format, environment=environment, byte_size=size,
                    sha256=hashlib.sha256(raw).hexdigest(), opaque=image_format == 14)
    if image_format == 14:
        context.component(document, "native-image", where, data=raw, disposition="opaque", artifact=artifact,
                          reason="foreign application native image format")


def _chart_xml(payload, kind, location, context, document):
    """Inspect original XML evidence independently of whether its display checksum is current."""
    cursor = _Cursor(payload, 20 if kind == 0x08A4 else 16)
    raw = cursor.take(cursor.number("I"))
    if cursor.offset != len(payload):
        raise ValueError("unvisited BIFF chart XML bytes")
    _chart_xml_bytes(raw, location, context, document)


def _chart_xml_bytes(raw, location, context, document):
    """Preserve and inspect one exact chart XML source using the shared XML budget."""
    artifact = context.payload(document, "chart-xml", location, raw, "application/xml")
    _xml_preflight(raw, "BIFF chart XML", context.zip_budget)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("BIFF chart XML declarations are not expanded")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("malformed BIFF chart XML") from exc
    for ordinal, node in enumerate(root.iter()):
        context.check("records", context.consumed["records"] + ordinal + 1, location)
        where = {**location, "xml_element": ordinal, "xml_tag": node.tag}
        if node.tag.rsplit("}", 1)[-1] == "t" and node.text:
            context.finding(document, "text", "chart-text", where, text=node.text, artifact=artifact)
        for attribute, target in node.attrib.items():
            if attribute.rsplit("}", 1)[-1] in ("id", "embed", "link") and "relationships" in attribute:
                context.finding(document, "links", "chart-resource-reference", where,
                                target=target, artifact=artifact, attribute=attribute)
                context.problem(document, "links", "BIFF chart XML relationship target requires package context", where)


def _chart_properties(payload, location, context, document):
    """Frame the typed property chain and retain opaque display settings separately."""
    cursor = _Cursor(payload, 12)
    chain = _Cursor(cursor.take(cursor.number("I")))
    cursor.take(4)
    version, _unused, parent = chain.number("B"), chain.number("B"), chain.number("H")
    if version or cursor.offset != len(payload) or parent not in (1, 2, 4, 5, 15, 19, 22, 25, 55):
        raise ValueError("invalid or unsupported BIFF chart property chain")
    while chain.offset < len(chain.data):
        start = chain.offset
        kind, _unused, tag = chain.number("B"), chain.number("B"), chain.number("H")
        where = {**location, "property_offset": start, "property_tag": tag, "property_parent": parent}
        if kind == 2:
            value = chain.number("B")
            chain.take(1)
            if value > 1:
                raise ValueError("invalid BIFF chart property Boolean")
        elif kind == 4:
            chain.take(4)
        elif kind == 5:
            count = chain.number("I")
            raw = chain.take(count * 2)
            context.finding(document, "text", "chart-property-string", where, text=raw.decode("utf-16le"))
        elif kind == 3:
            chain.take(4)
            value = chain.number("d")
            if not math.isfinite(value):
                raise ValueError("non-finite BIFF chart property double")
            context.finding(document, "inventory", "chart-property-number", where, value=value)
        elif kind == 7 and tag in (0x0033, 0x0034):
            raw = chain.take(chain.number("I"))
            allowance = min(context.limits["payload_bytes"], context.limits["decoded_bytes"] - context.consumed["decoded_bytes"])
            decoder = zlib.decompressobj(-15)
            xml = decoder.decompress(raw, allowance + 1)
            context.check("decoded_bytes", context.consumed["decoded_bytes"] + len(xml), where)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("invalid BIFF chart compressed property XML")
            context.charge("decoded_bytes", len(xml), where)
            _chart_xml_bytes(xml, where, context, document)
        else:
            raise ValueError("unsupported BIFF chart property type 0x%02X" % kind)
        context.component(document, "chart-property", where, data=chain.data[start:chain.offset],
                          disposition="opaque", reason="typed chart display property override")


def _future_content(records, index, state, location, context):
    """Join future-record payloads after removing their individual twelve-byte headers."""
    continuation = 0x089F if records[index][1] == 0x089E else 0x087F
    parts, segments, total = [records[index][2]], [], 0
    for ordinal, item in enumerate(records[index:]):
        offset, kind, payload, _component = item
        if ordinal and kind != continuation:
            break
        skip = 12 if ordinal else 0
        if ordinal:
            if len(payload) < 12 or struct.unpack_from("<H", payload)[0] != kind:
                raise ValueError("invalid BIFF future continuation header")
            state["continued_offsets"].add(offset)
            parts.append(payload[skip:])
        segments.append({"record_offset": offset, "payload_offset": offset + 4 + skip,
                         "assembled_offset": total, "byte_size": len(payload) - skip})
        total += len(payload) - skip
        context.check("payload_bytes", total, location)
    context.charge("decoded_bytes", total, location)
    return b"".join(parts), {**location, "segments": segments}


def _legacy_name(payload, state, location, component, context, document):
    """Resolve one legacy external name against its original ordered link entry."""
    if not state["legacy_links"]:
        raise ValueError("legacy external name has no supporting link")
    entry = legacy_external_name(payload, state["version"], state["codepage"])
    supporting = state["legacy_links"][-1]
    label, target = entry["name"], supporting["target"]
    index, source_index = len(supporting["names"]) + 1, len(state["legacy_links"]) - 1
    supporting["names"].append(label)
    if supporting["kind"] in ("add-in", "current-workbook"):
        qualified = label
    elif supporting["kind"] == "caller-sheet":
        qualified = "CALLER_SHEET!" + label
    else:
        qualified = "'" + (supporting["sheet"] or target or "").replace("'", "''") + "'!" + label
    state["external_names"][(source_index, index)] = qualified
    details = {key: value for key, value in entry.items() if key not in ("name", "definition")}
    details.update(source_scope=supporting["kind"], context_required=supporting["kind"] == "caller-sheet")
    tail = entry["definition"]
    if target and "\x03" in target:
        details["service"], details["topic"] = target.split("\x03", 1)
        details["cached_values"] = (legacy_cached_values(tail, state["version"], state["codepage"],
                                                        context=context, location=location) if tail else None)
        state["external_names"][(source_index, index)] = target.replace("\x03", "|", 1) + "!" + label
    elif tail:
        cursor = _Cursor(tail)
        size = cursor.number("B" if state["version"] == 2 else "H")
        tokens = cursor.take(size)
        details["formula"] = _formula(tokens, version=state["version"], codepage=state["codepage"],
                                      names=state["names"], external_sheets=state["external_sheets"],
                                      external_names=state["external_names"]) if tokens else None
        if cursor.offset != len(tail):
            raise ValueError("unvisited legacy external name formula bytes")
    artifact = context.payload(document, "external-name", location, payload, "application/octet-stream")
    supporting.setdefault("resolved_names", {})[index] = state["external_names"][(source_index, index)]
    context.finding(document, "external_references", "external-name", location, component=component,
                    target=target, text=label, artifact=artifact, name_index=index, **details)


def _globals(records, name, context, document):
    """Read workbook-wide identities before resolving source cell records."""
    state = {"version": 8, "codepage": "cp1252", "sheets": [], "names": [],
             "name_records": [], "supbooks": [], "external_sheets": [], "strings": [],
             "external_names": {}, "continued_offsets": set(), "legacy_links": [], "legacy_name_metadata": {},
             "wrapper_offsets": {item["location"]["record_offset"]: item["location"]["wrapper_offset"]
                                 for item in context.components if item["document"] == document
                                 and item["kind"] == "biff-wrapped-record"},
             "legacy_link_sets": {None: []}, "legacy_scopes": {}}
    state["legacy_links"] = state["legacy_link_sets"][None]
    for index, (offset, kind, payload, component) in enumerate(records):
        location = _location(name, offset, kind)
        cursor = _Cursor(payload)
        try:
            if kind in (0x0009, 0x0209, 0x0409, 0x0809) and len(payload) >= 4:
                substream_type = struct.unpack_from("<H", payload, 2)[0]
                if substream_type in (0x10, 0x40):
                    state["legacy_links"] = state["legacy_link_sets"].setdefault(offset, [])
                    state["external_names"] = {} if state["version"] < 8 else state["external_names"]
            if kind == 0x0809 and not state["sheets"]:
                state["version"] = _bof_version(kind, payload)
            elif kind in (0x0009, 0x0209, 0x0409):
                state["version"] = _bof_version(kind, payload)
            elif kind == 0x0042:
                codepage = cursor.number("H")
                state["codepage"] = legacy_codepage(codepage)
                b"".decode(state["codepage"])
            elif kind == 0x008F:
                size = cursor.number("I")
                label = _legacy_string(cursor, state["codepage"], short=True)
                state["sheets"].append({"offset": offset + 4 + len(payload), "name": label,
                                         "ordinal": len(state["sheets"]) + 1, "hidden": None, "type": 0})
                context.finding(document, "inventory", "legacy-sheet-header", location, text=label,
                                source_substream_size=size)
            elif kind == 0x0085:
                sheet_offset, hidden, sheet_type = cursor.number("I"), cursor.number("B"), cursor.number("B")
                sheet_name = (_unicode(cursor, short=True) if state["version"] == 8 else
                              _legacy_string(cursor, state["codepage"], short=True))
                if not 1 <= len(sheet_name) <= 31 or hidden & 3 == 3 or sheet_type not in (0, 1, 2, 6):
                    context.problem(document, CAPABILITIES, "invalid BIFF sheet name, visibility or type", location)
                state["sheets"].append({"offset": sheet_offset, "name": sheet_name,
                                         "ordinal": len(state["sheets"]) + 1,
                                         "hidden": hidden & 3, "type": sheet_type})
            elif kind == 0x00FC:
                parts = _continuations(records, index, state)
                state["strings"] = parse_sst(tuple(item[2] for item in parts), context, location)
            elif kind == 0x0018:
                if state["version"] < 8:
                    entry = legacy_defined_name(payload, state["version"], state["codepage"])
                    label, flags, scope, tokens = (entry[key] for key in ("name", "flags", "scope", "tokens"))
                    label = _builtin_name(label, flags, context, document, location)
                    state["names"].append(label)
                    state["name_records"].append((label, tokens, b"", flags, scope, location, component))
                    state["legacy_name_metadata"][offset] = {key: value for key, value in entry.items() if key not in ("name", "tokens", "flags", "scope")}
                    continue
                flags, _key, count, length = (cursor.number(code) for code in ("H", "B", "B", "H"))
                cursor.take(2)
                scope = cursor.number("H")
                cursor.take(4)
                label = (_unicode(cursor, count) if state["version"] == 8 else cursor.take(count).decode(state["codepage"]))
                label = _builtin_name(label, flags, context, document, location)
                tokens = cursor.take(length)
                extra = cursor.take(len(payload) - cursor.offset)
                state["names"].append(label)
                state["name_records"].append((label, tokens, extra, flags, scope, location, component))
            elif kind == 0x01AE:
                count, length = cursor.number("H"), cursor.number("H")
                target = None if length in (0x0401, 0x3A01) else _unicode(cursor, length)
                sheets = [_unicode(cursor) for _ in range(count)] if target is not None and cursor.offset < len(payload) else []
                ole = bool(target and re.fullmatch(r'[^\x00-\x1f]+\x03[^\x00-\x1f]+', target))
                state["supbooks"].append({"target": target, "sheets": sheets, "type": length,
                                         "ole": ole, "names": []})
                if target is not None:
                    context.finding(document, "external_references", "supporting-link", location,
                                    component=component, target=target, sheet_names=sheets,
                                    reference_type="dde_or_ole" if ole else "external_workbook")
            elif kind == 0x0023:
                if state["version"] < 8:
                    _legacy_name(payload, state, location, component, context, document)
                    continue
                if not state["supbooks"]:
                    raise ValueError("BIFF external name has no supporting link")
                supporting = state["supbooks"][-1]
                parts = _continuations(records, index, state)
                size = sum(len(item[2]) for item in parts)
                context.check("payload_bytes", size, location)
                context.charge("decoded_bytes", size, location)
                payload = b"".join(item[2] for item in parts)
                cursor = _Cursor(payload)
                flags = cursor.number("H")
                body_prefix = cursor.take(4)
                scope = struct.unpack_from("<H", body_prefix)[0]
                label = _unicode(cursor, short=True)
                tail = cursor.take(len(payload) - cursor.offset)
                supporting["names"].append((label, scope, flags))
                details = {"flags": flags, "scope": scope,
                           "name_index": len(supporting["names"]), "definition_hex": tail.hex()}
                if supporting["ole"]:
                    details["service"], details["topic"] = supporting["target"].split("\x03", 1)
                    details["storage_id"] = struct.unpack("<I", body_prefix)[0]
                    if flags & 8:
                        if label != "StdDocumentName" or tail:
                            raise ValueError("invalid BIFF ExternDdeLinkNoOper")
                    else:
                        details["cached_values"] = _moper(tail, context, location) if tail else None
                elif supporting["type"] != 0x3A01:
                    details["formula"] = _external_definition(tail, supporting)
                artifact = context.payload(document, "external-name", location, payload,
                                           "application/octet-stream")
                context.finding(document, "external_references", "external-name", location,
                                component=component, target=supporting["target"], text=label,
                                artifact=artifact, **details)
                if supporting["type"] == 0x3A01:
                    padding = _Cursor(tail)
                    padding.take(padding.number("H"))
                    if padding.offset != len(tail):
                        raise ValueError("unvisited BIFF add-in name padding")
            elif kind == 0x0017:
                if state["version"] < 8:
                    entry = legacy_extern_sheet(payload, state["version"], state["codepage"])
                    state["legacy_links"].append({**entry, "names": []})
                    context.finding(document, "external_references", "legacy-supporting-link", location,
                                    component=component, target=entry["target"], source_kind=entry["kind"],
                                    original=entry["original"], sheet=entry["sheet"])
                    continue
                count = cursor.number("H")
                context.check("records", count, location)
                entries = []
                for _ in range(count):
                    book, first, last = cursor.number("H"), cursor.number("h"), cursor.number("h")
                    entries.append((book, first, last))
                state["external_sheets"].extend(entries)
            elif kind == 0x01BA:
                text = _unicode(cursor)
                if len(text) > 31 or cursor.offset != len(payload):
                    raise ValueError("invalid BIFF VBA code name")
                context.finding(document, "macros", "vba-code-name", location, text=text, component=component)
            elif kind == 0x0897:
                cursor.take(12)
                identifier = str(uuid.UUID(bytes_le=cursor.take(16)))
                if cursor.offset != len(payload):
                    raise ValueError("unvisited BIFF type-library identifier bytes")
                context.finding(document, "external_references", "vba-type-library", location,
                                target=identifier, component=component)
            elif kind == 0x004D:
                _continuations(records, index, state)
        except (ValueError, LookupError, UnicodeError, struct.error) as exc:
            context.problem(document, CAPABILITIES, str(exc), location)
    resolved = []
    if state["version"] < 8:
        for source_offset, links in state["legacy_link_sets"].items():
            scopes, names = [], {}
            for link_index, entry in enumerate(links):
                scope = entry["sheet"] if entry["sheet"] is not None else entry["target"]
                scopes.append("'" + scope.replace("'", "''") + "'" if scope is not None else
                              {"kind": entry["kind"]} if entry["kind"] in ("caller-sheet", "add-in") else None)
                names.update({(link_index, index): label for index, label in entry.get("resolved_names", {}).items()})
            state["legacy_scopes"][source_offset] = scopes, names
        state["external_sheets"], state["external_names"] = state["legacy_scopes"][None]
        return state
    for xti, (book, first, last) in enumerate(state["external_sheets"]):
        if book >= len(state["supbooks"]):
            resolved.append("[unresolved supporting link %d]" % book)
            context.problem(document, ("formulas", "external_references"), "unresolved BIFF supporting link", {"kind": "biff-stream", "stream": name})
            continue
        supporting = state["supbooks"][book]
        sheets = supporting["sheets"] or [item["name"] for item in state["sheets"]]
        target = "[" + supporting["target"] + "]" if supporting["target"] else ""
        sheet_name = sheets[first] if 0 <= first < len(sheets) else "#REF!"
        if first != last:
            sheet_name += ":" + (sheets[last] if 0 <= last < len(sheets) else "#REF!")
        resolved.append("'" + (target + sheet_name).replace("'", "''") + "'")
        for index, (label, scope, flags) in enumerate(supporting["names"], 1):
            if supporting["type"] == 0x3A01:
                qualified = label
            elif supporting["ole"]:
                qualified = supporting["target"].replace("\x03", "|", 1) + "!" + label
            else:
                scope_name = sheets[scope - 1] if 1 <= scope <= len(sheets) else ""
                qualified = "'" + (target + scope_name).replace("'", "''") + "'!" + label
            state["external_names"][(xti, index)] = qualified
    state["external_sheets"] = resolved
    return state


def _hyperlink(payload):
    """Strip the source cell range, then reuse the bounded IHlink moniker reader."""
    return _office_hyperlink(payload[8:])[0]


def _cell_record(kind, payload, state):
    """Recover addressed native values before the binding loses errors and blanks."""
    if state["version"] < 8 and kind not in (0x027E,):
        return parse_legacy_cell(kind, payload, state["version"], state["codepage"])
    cursor = _Cursor(payload)
    row, column = cursor.number("H"), cursor.number("H")
    cursor.take(3 if state["version"] == 2 else 2)
    basic = {"row": row, "column": column, "value": None, "value_type": "blank", "formula": None,
             "cached_value_state": "not_formula", "error": None, "tokens": None, "extra": b""}
    if kind in (0x0201, 0x0001):
        basic["cached_value_state"] = "blank"
    elif kind in (0x0203, 0x0003):
        basic.update(value=cursor.number("d"), value_type="number")
    elif kind == 0x027E:
        basic.update(value=_rk(cursor.number("I")), value_type="number")
    elif kind in (0x0205, 0x0005):
        value, error = cursor.number("B"), cursor.number("B")
        if error and value not in ERRORS:
            raise ValueError("unknown BIFF cell error 0x%02X" % value)
        basic.update(value=ERRORS[value] if error else bool(value), value_type="error" if error else "boolean",
                     error=ERRORS[value] if error else None)
    elif kind == 0x00FD:
        index = cursor.number("I")
        if index >= len(state["strings"]):
            raise ValueError("unresolved BIFF shared string %d" % index)
        basic.update(value=state["strings"][index], value_type="string")
    elif kind in (0x0204, 0x0004):
        value = (_unicode(cursor) if state["version"] == 8 else
                 _legacy_string(cursor, state["codepage"], short=state["version"] == 2))
        basic.update(value=value, value_type="string")
    elif kind == 0x0006:
        value, value_type, cache, error = _cache(cursor.take(8))
        cursor.take(1 if state["version"] == 2 else 2)
        if state["version"] >= 5:
            cursor.take(4)
        length = cursor.number("B" if state["version"] == 2 else "H")
        basic.update(value=value, value_type=value_type, cached_value_state=cache, error=error,
                     tokens=cursor.take(length))
        basic["extra"] = cursor.take(len(payload) - cursor.offset)
    return basic


def _emit_cells(cells, merges, shared, tables, sheet, name, state, context, document):
    """Emit addressed source cells and check native data-table membership."""
    table_cells = {anchor: set() for anchor in tables}
    shared_cells = {anchor: set() for anchor in shared}
    positions = Counter((cell["row"], cell["column"]) for cell, _location, _component in cells)
    for cell, location, component in cells:
        row, column, tokens = cell.pop("row"), cell.pop("column"), cell.pop("tokens")
        extra = cell.pop("extra")
        address = _address(row, column)
        qualified = "'" + sheet["name"].replace("'", "''") + "'!" + address if sheet["name"] is not None else None
        location = {**location, "kind": "workbook-cell", "sheet": sheet["name"],
                    "sheet_ordinal": sheet["ordinal"], "cell": address, "qualified_cell": qualified}
        if sheet["name"] is None:
            location.update(sheet_name_state="not_stored", display_sheet=sheet["display_name"],
                            source_bof_offset=sheet["offset"])
        merge = next((item for item in merges if item[0] <= row <= item[1] and item[2] <= column <= item[3]), None)
        cell.update(merge_origin=_address(merge[0], merge[2]) if merge else None,
                    merge_range=_address(merge[0], merge[2]) + ":" + _address(merge[1], merge[3]) if merge else None)
        if tokens is not None:
            artifact = context.payload(document, "formula-source", location, tokens + extra, "application/octet-stream")
            sheet_contexts = []
            try:
                if tokens[:1] == b"\x01":
                    anchor, _definition = _shared_definition(tokens, state["version"], row, column, shared)
                    shared_cells[anchor].add((row, column))
                if tokens[:1] == b"\x01" and positions[(row, column)] > 1:
                    raise ValueError("duplicate BIFF shared/array consuming cell")
                cell["formula"] = _formula(tokens, row=row, column=column, names=state["names"],
                                            external_sheets=state["external_sheets"], shared=shared,
                                            extra=extra, external_names=state.get("external_names", {}),
                                            version=state["version"], codepage=state["codepage"],
                                            sheets=tuple(item["name"] for item in state["sheets"]),
                                            tables=tables, sheet_contexts=sheet_contexts)
                if tokens[:1] == b"\x02":
                    anchor = _table_anchor(tokens, state["version"])
                    definition = tables[anchor]
                    table_cells[anchor].add((row, column))
                    cell["data_table"] = {key: value for key, value in definition.items() if key != "bounds"}
            except (ValueError, UnicodeError, struct.error) as exc:
                capabilities = ("formulas",) if tokens[:1] in (b"\x01", b"\x02") else ("formulas", "external_references")
                if sheet["type"] == 1:
                    capabilities += ("macros",)
                context.problem(document, capabilities, str(exc), location)
            expression = cell["formula"]
            if sheet_contexts:
                cell.update(formula=None, recovered_inner_expression=expression, legacy_sheet_contexts=sheet_contexts)
                context.problem(document, ("formulas", "external_references"),
                                "legacy BIFF sheet-context binding is undocumented", location)
            representation = ("unresolved_legacy_sheet_context" if sheet_contexts else
                              "native_data_table" if "data_table" in cell else "reconstructed_from_biff_tokens")
            context.finding(document, "formulas", "formula", location, component=component,
                            text=expression, artifact=artifact, token_hex=tokens.hex(),
                            auxiliary_hex=extra.hex(),
                            representation=representation,
                            **({"legacy_sheet_contexts": sheet_contexts} if sheet_contexts else {}))
            if sheet["type"] == 1:
                context.finding(document, "macros", "xlm-formula", location,
                                text=cell["formula"], artifact=artifact, token_hex=tokens.hex())
            hyperlink = re.match(r'^HYPERLINK\("((?:""|[^"\n])*)"(?:,|\))', cell["formula"] or "")
            if hyperlink:
                context.finding(document, "links", "formula-hyperlink", location,
                                target=hyperlink.group(1).replace('""', '"'), artifact=artifact)
        context.finding(document, "cells", "cell", location, component=component, **cell)
    for anchor, definition in tables.items():
        first_row, last_row, first_col, last_col = definition["bounds"]
        if len(table_cells[anchor]) != (last_row - first_row + 1) * (last_col - first_col + 1):
            context.problem(document, ("formulas",), "BIFF data-table range lacks required formula cells",
                            definition["location"])
    for anchor, definition in shared.items():
        if definition.get("invalid"):
            continue
        first_row, last_row, first_col, last_col = definition["bounds"]
        expected = ((last_row - first_row + 1) * (last_col - first_col + 1)
                    if definition["kind"] == "array" else definition["uses"])
        if len(shared_cells[anchor]) != expected:
            capabilities = ("formulas", "macros") if sheet["type"] == 1 else ("formulas",)
            context.problem(document, capabilities, "BIFF shared/array definition lacks required formula cells",
                            definition["location"])


def _sheet(records, sheet, name, state, context, document):
    """Inspect one source sheet with local link scopes and deferred formula resolution."""
    if state["version"] < 8 and sheet["offset"] in state["legacy_scopes"]:
        state = dict(state)
        state["external_sheets"], state["external_names"] = state["legacy_scopes"][sheet["offset"]]
    cells, merges, shared, tables = [], [], {}, {}
    objects, object_id, chart, cache_index, chart_count = {}, None, None, None, 0
    if sheet["type"] == 2:
        chart = sheet["offset"]
    state["continued_offsets"].update(_drawing(records, 0x00EC, name, context, document))
    location = {"kind": "workbook-sheet", "stream": name, "sheet": sheet["name"],
                "sheet_ordinal": sheet["ordinal"], "record_offset": sheet["offset"]}
    if sheet["name"] is None:
        location.update(sheet_name_state="not_stored", display_sheet=sheet["display_name"],
                        source_bof_offset=sheet["offset"])
    context.finding(document, "hidden_structures", "sheet", location,
                    hidden=({0: "visible", 1: "hidden", 2: "very_hidden"}.get(sheet["hidden"], "invalid")
                            if sheet["hidden"] is not None else None),
                    hidden_state="not_stored" if sheet["hidden"] is None else "stored",
                    sheet_type=sheet["type"])
    if sheet["type"] in (1, 6):
        context.finding(document, "macros", "xlm-sheet" if sheet["type"] == 1 else "vba-sheet", location,
                        sheet=sheet["name"], sheet_ordinal=sheet["ordinal"], sheet_type=sheet["type"])
    for index, (offset, kind, payload, component) in enumerate(records):
        location = {**_location(name, offset, kind), "sheet": sheet["name"], "sheet_ordinal": sheet["ordinal"]}
        if offset in state["wrapper_offsets"]:
            location["wrapper_offset"] = state["wrapper_offsets"][offset]
        cursor = _Cursor(payload)
        try:
            if kind == 0x0809 and len(payload) >= 4 and struct.unpack_from("<H", payload, 2)[0] == 0x20:
                chart, cache_index = offset, None
                chart_count += 1
            if chart is not None:
                location.update(chart_offset=chart, chart_ordinal=max(chart_count, 1))
            if kind == 0x000A and chart is not None and offset != sheet["offset"]:
                chart, cache_index = None, None
            if kind == 0x1065:
                cache_index = cursor.number("H")
            elif kind == 0x100D:
                cursor.take(2)
                context.finding(document, "text", "chart-text", location, component=component,
                                text=_unicode(cursor, short=True))
            elif kind == 0x1051:
                part, source_type, flags, number_format = (cursor.number(code) for code in ("B", "B", "H", "H"))
                tokens = cursor.take(cursor.number("H"))
                if cursor.offset != len(payload):
                    raise ValueError("unvisited BIFF chart formula bytes")
                artifact = context.payload(document, "chart-formula", location, tokens, "application/octet-stream")
                formula = (_formula(tokens, names=state["names"], external_sheets=state["external_sheets"],
                                    external_names=state["external_names"]) if tokens else None)
                context.finding(document, "formulas", "chart-formula", location, component=component,
                                text=formula, artifact=artifact, part=part, source_type=source_type,
                                number_format=number_format, flags=flags)
            elif kind in (0x08A4, 0x08A5, 0x08A6):
                assembled, where = _future_content(records, index, state, location, context)
                _chart_xml(assembled, kind, where, context, document)
            elif kind == 0x1066:
                _gel_frame(records, index, state, location, context, document)
            elif kind == 0x089E:
                assembled, where = _future_content(records, index, state, location, context)
                _chart_properties(assembled, where, context, document)
            elif kind == 0x01B6:
                text, runs = _text_object(records, index, state, location, context, document)
                if object_id is not None:
                    objects[object_id] = text
                context.finding(document, "text", "object-text", location, component=component,
                                text=text, object_id=object_id, formatting_runs=runs)
            elif kind == 0x001C:
                row, column, flags, identifier = (cursor.number("H") for _ in range(4))
                author = _unicode(cursor)
                cursor.take(1)
                if cursor.offset != len(payload) or identifier not in objects:
                    raise ValueError("unresolved or malformed BIFF comment object")
                cell = _address(row, column)
                where = {**location, "cell": cell, "qualified_cell": (
                    "'" + sheet["name"].replace("'", "''") + "'!" + cell if sheet["name"] is not None else None)}
                context.finding(document, "text", "comment", where, component=component,
                                text=objects[identifier], author=author, object_id=identifier, flags=flags)
            elif kind in (0x007F, 0x00E9):
                _legacy_image(records, index, state, location, context, document)
            elif kind in (0x0201, 0x0203, 0x0204, 0x0205, 0x00FD, 0x027E, 0x0006,
                        0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0206, 0x0406):
                value = _cell_record(kind, payload, state)
                if chart is not None:
                    if kind not in (0x0201, 0x0203, 0x0204, 0x0205):
                        raise ValueError("unsupported BIFF chart cache cell record")
                    row, column = value.pop("row"), value.pop("column")
                    value.pop("tokens")
                    value.pop("extra")
                    context.finding(document, "cells", "chart-cache", location, component=component,
                                    cache_index=cache_index, point_index=row, series_index=column, **value)
                else:
                    cells.append((value, location, component))
            elif kind in (0x00BD, 0x00BE):
                row, first = cursor.number("H"), cursor.number("H")
                last = struct.unpack("<H", payload[-2:])[0]
                if last < first or last > 255:
                    raise ValueError("invalid BIFF multiple-cell range")
                for column in range(first, last + 1):
                    style = cursor.take(2)
                    raw = struct.pack("<HH", row, column) + style + (cursor.take(4) if kind == 0x00BD else b"")
                    cells.append((_cell_record(0x027E if kind == 0x00BD else 0x0201, raw, state), location, component))
                if cursor.offset != len(payload) - 2:
                    raise ValueError("invalid BIFF multiple-cell length")
            elif kind in (0x0007, 0x0207) and cells and cells[-1][0]["cached_value_state"] == "missing":
                parts = _continuations(records, index, state)
                value = (parse_xl_unicode_segments(tuple(item[2] for item in parts), context, location)
                         if state["version"] == 8 else _legacy_string(cursor, state["codepage"]))
                cells[-1][0].update(value=value, value_type="string", cached_value_state="present")
            elif kind in (0x0007, 0x0207):
                raise ValueError("BIFF cached STRING has no preceding string formula")
            elif kind == 0x00E5:
                count = cursor.number("H")
                context.check("records", count, location)
                merges.extend(tuple(cursor.number("H") for _ in range(4)) for _ in range(count))
                for first_row, last_row, first_col, last_col in merges[-count:] if count else ():
                    if first_row > last_row or first_col > last_col:
                        raise ValueError("invalid BIFF merge range")
                    context.finding(document, "cells", "merge", location,
                                    merge_origin=_address(first_row, first_col),
                                    merge_range=_address(first_row, first_col) + ":" + _address(last_row, last_col))
            elif kind in (0x0036, 0x0037, 0x0236):
                definition = biff_table_definition(payload, state["version"], kind)
                first_row, last_row, first_col, last_col = definition["bounds"]
                anchor = first_row, first_col
                if anchor in tables:
                    raise ValueError("duplicate BIFF data-table anchor")
                definition.update(expression=_table_expression(definition), location=location,
                                  range=_address(first_row, first_col) + ":" + _address(last_row, last_col),
                                  anchor=_address(*anchor),
                                  artifact=context.payload(document, "data-table-source", location, payload,
                                                           "application/octet-stream"))
                tables[anchor] = definition
                context.finding(document, "formulas", "data-table", location, component=component,
                                **{key: value for key, value in definition.items() if key not in ("bounds", "location")})
            elif kind in (0x04BC, 0x0021, 0x0221):
                if kind != 0x04BC:
                    if kind != (0x0021 if state["version"] == 2 else 0x0221):
                        raise ValueError("BIFF ARRAY record does not match source version")
                    definition = biff_array_definition(payload, state["version"])
                    first_row, last_row, first_col, last_col = definition["bounds"]
                    tokens, extra = definition["tokens"], definition["extra"]
                else:
                    first_row, last_row, first_col, last_col = (cursor.number(code) for code in ("H", "H", "B", "B"))
                    cursor.take(1)
                    uses = cursor.number("B")
                    tokens = cursor.take(cursor.number("H"))
                    extra = cursor.take(len(payload) - cursor.offset)
                    if first_row > last_row or first_col > last_col:
                        raise ValueError("invalid BIFF shared formula range")
                if (first_row, first_col) in shared:
                    shared[(first_row, first_col)]["invalid"] = True
                    context.problem(document, "formulas", "duplicate BIFF shared/array formula anchor", location)
                else:
                    shared[(first_row, first_col)] = {"tokens": tokens, "extra": extra,
                        "bounds": (first_row, last_row, first_col, last_col), "location": location,
                        "kind": "array" if kind != 0x04BC else "shared", "uses": uses if kind == 0x04BC else None}
                metadata = ({"flags": definition["flags"], "reserved_hex": definition["reserved_hex"]}
                            if kind != 0x04BC else {"declared_uses": uses})
                context.finding(document, "formulas", "shared-formula" if kind == 0x04BC else "array-formula",
                                location, token_hex=tokens.hex(), auxiliary_hex=extra.hex(),
                                artifact=context.payload(document, "array-formula-source" if kind != 0x04BC else "shared-formula-source",
                                                         location, payload, "application/octet-stream"),
                                **metadata,
                                range=_address(first_row, first_col) + ":" + _address(last_row, last_col))
            elif kind == 0x0008:
                if state["version"] != 2:
                    raise ValueError("BIFF2 ROW in a different source version")
                details = legacy_row(payload)
                row = details.pop("row")
                details["first_column"] = _column(details["first_column"])
                details["last_column_exclusive"] = _column(details["last_column_exclusive"])
                context.finding(document, "hidden_structures", "row", {**location, "row": row + 1}, **details)
            elif kind == 0x0208:
                row = cursor.number("H")
                cursor.take(10)
                flags = cursor.number("B")
                context.finding(document, "hidden_structures", "row", {**location, "row": row + 1},
                                hidden=bool(flags & 0x20), outline_level=flags & 7)
            elif kind == 0x007D:
                first, last = cursor.number("H"), cursor.number("H")
                cursor.take(4)
                flags = cursor.number("H")
                context.finding(document, "hidden_structures", "column", location,
                                first_column=_column(first), last_column=_column(last), hidden=bool(flags & 1))
            elif kind == 0x01B8:
                target = _hyperlink(payload)
                context.finding(document, "links", "hyperlink", location, component=component,
                                target=target, range_bytes=payload[:8].hex())
            elif kind == 0x005D:
                _object(payload, location, state, context, document)
                object_id = struct.unpack_from("<H", payload, 6)[0] if len(payload) >= 8 and payload[:2] == b"\x15\0" else None
            elif kind in (0x0014, 0x0015):
                text = _unicode(cursor) if payload and state["version"] == 8 else (
                    _legacy_string(cursor, state["codepage"], short=True) if payload else "")
                context.finding(document, "text", "header" if kind == 0x0014 else "footer", location,
                                component=component, text=text)
            elif kind == 0x089C:
                cursor.take(28)
                flags = cursor.number("H")
                counts = [cursor.number("H") for _ in range(4)]
                for label, count in zip(("header-even", "footer-even", "header-first", "footer-first"), counts):
                    if count:
                        text = _unicode(cursor)
                        if len(text) != count:
                            raise ValueError("BIFF header/footer character count mismatch")
                        context.finding(document, "text", label, location, component=component,
                                        text=text, flags=flags)
                if cursor.offset != len(payload):
                    raise ValueError("unvisited BIFF header/footer bytes")
        except (ValueError, UnicodeError, LookupError, struct.error, BrewdocError, zlib.error) as exc:
            context.problem(document, CAPABILITIES, str(exc), location)
    _emit_cells(cells, merges, shared, tables, sheet, name, state, context, document)


def inspect_biff_stream(data: bytes, name: str, context, document: str, *, streams=()) -> None:
    """Inspect Workbook/Book bytes with exact offsets and explicit unsupported evidence."""
    root = {"kind": "biff-stream", "stream": name}
    try:
        context.charge("decoded_bytes", len(data), root)
        records = _records(data, name, context, document)
        unsupported = False
        for offset, kind, payload, component in records:
            if kind not in (0x0009, 0x0209, 0x0409, 0x0809):
                continue
            try:
                _bof_version(kind, payload)
            except (ValueError, struct.error) as exc:
                location = _location(name, offset, kind)
                artifact = context.payload(document, "unsupported-biff-bof", location,
                                           data[offset:offset + 4 + len(payload)], "application/octet-stream")
                context.finding(document, "inventory", "unsupported-biff-version", location,
                                component=component, artifact=artifact,
                                version=int.from_bytes(payload[:2], "little") if len(payload) >= 2 else None)
                context.problem(document, CAPABILITIES, str(exc), location)
                unsupported = True
        if unsupported:
            return
        state = _globals(records, name, context, document)
        state["streams"] = streams
        state["continued_offsets"].update(_drawing(records, 0x00EB, name, context, document))
        if not records or records[0][1] not in (0x0009, 0x0209, 0x0409, 0x0809):
            context.problem(document, CAPABILITIES, "BIFF stream has no opening BOF", root)
        depth = 0
        for offset, kind, _payload, _component in records:
            if kind in (0x0009, 0x0209, 0x0409, 0x0809):
                depth += 1
            elif kind == 0x000A:
                depth -= 1
                if depth < 0:
                    context.problem(document, CAPABILITIES, "BIFF EOF has no matching BOF",
                                    _location(name, offset, kind))
                    depth = 0
        if depth:
            context.problem(document, CAPABILITIES, "BIFF substream has no closing EOF", root)
        for offset, kind, payload, component in records:
            location = _location(name, offset, kind)
            if kind == 0x002F:
                context.problem(document, CAPABILITIES, "encrypted BIFF content", location)
                return
            if kind not in _SAFE_RECORDS and kind not in _OPAQUE_RECORDS and kind not in (0x0006, 0x0018, 0x0017, 0x01AE, 0x0023,
                                                         0x01B8, 0x005D, 0x00EC, 0x007F, 0x0221,
                                                         0x0009, 0x0209, 0x0409, 0x0001, 0x0003,
                                                         0x0004, 0x0005, 0x089C, 0x003C, 0x01BA, 0x0897,
                                                         0x01B6, 0x001C, 0x00E9, 0x100D, 0x1051,
                                                         0x1065, 0x1066, 0x08A4, 0x08A5, 0x08A6, 0x089E,
                                                         0x0002, 0x0007, 0x0206, 0x0406, 0x0851, 0x008F,
                                                         0x087F, 0x089F):
                artifact = context.payload(document, "opaque-biff-record", location, payload,
                                           "application/octet-stream")
                context.finding(document, "inventory", "unsupported-record", location,
                                component=component, artifact=artifact)
                context.problem(document, CAPABILITIES, "unsupported BIFF record 0x%04X" % kind, location)
        for label, tokens, extra, flags, scope, location, component in state["name_records"]:
            artifact = context.payload(document, "defined-name-formula", location, tokens + extra, "application/octet-stream")
            formula = None
            try:
                formula = _formula(tokens, names=state["names"], external_sheets=state["external_sheets"],
                                   extra=extra, external_names=state["external_names"],
                                   version=state["version"], codepage=state["codepage"],
                                   sheets=tuple(item["name"] for item in state["sheets"]))
            except (ValueError, UnicodeError, struct.error) as exc:
                context.problem(document, ("names", "formulas"), str(exc), location)
            context.finding(document, "names", "defined-name", location, component=component,
                            text=label, artifact=artifact, formula=formula, flags=flags, scope=scope,
                            hidden=bool(flags & 1), macro=bool(flags & 0xE),
                            **state["legacy_name_metadata"].get(location["record_offset"], {}))
            if flags & 0xE or label.lower().startswith("auto_"):
                context.finding(document, "macros", "macro-name", location, text=label,
                                formula=formula, flags=flags, scope=scope)
        sheets = state["sheets"]
        if not sheets:
            first_sheet = next((offset for offset, kind, payload, _component in records
                                if kind in (0x0009, 0x0209, 0x0409) or
                                kind == 0x0809 and len(payload) >= 4 and struct.unpack_from("<H", payload, 2)[0] in (0x10, 0x40)), None)
            if first_sheet is not None:
                sheets = [{"offset": first_sheet, "name": None, "ordinal": 1, "hidden": None, "type": 0,
                           "display_name": "[unnamed BIFF sheet at byte %d]" % first_sheet}]
            else:
                context.problem(document, CAPABILITIES, "BIFF stream has no recoverable sheet identity", root)
        offsets = {offset for offset, _kind, _payload, _component in records}
        for sheet in sheets:
            if sheet["offset"] not in offsets:
                context.problem(document, CAPABILITIES, "BIFF sheet offset does not identify a record", root)
                continue
            following = min((other["offset"] for other in sheets if other["offset"] > sheet["offset"]), default=len(data))
            scoped = [item for item in records if sheet["offset"] <= item[0] < following]
            _sheet(scoped, sheet, name, state, context, document)
        for offset, kind, _payload, _component in records:
            if kind in (0x003C, 0x087F, 0x089F) and offset not in state["continued_offsets"]:
                context.problem(document, CAPABILITIES, "BIFF CONTINUE has no decoded semantic owner",
                                _location(name, offset, kind))
        for capability in CAPABILITIES:
            coverage = context.coverage.get((document, capability), {})
            recovered = any(item["document"] == document and item["capability"] == capability
                            for item in context.findings)
            if coverage.get("status") == "unavailable" and coverage.get("reason") != "inspection pending" and recovered:
                context.cover(document, capability, "partial", "some BIFF evidence recovered")
            context.cover(document, capability, "complete", "all BIFF stream records inspected")
    except InspectionLimit as exc:
        context.problem(document, CAPABILITIES, str(exc), root)
