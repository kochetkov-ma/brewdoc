"""Source-addressed workbook records independent of the readable sheet selection."""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePosixPath

from python_calamine import CalamineWorkbook

from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc.common import (BrewdocError, _local_name, _relationship_part,
                            _resolve_ooxml_part, _xml_preflight, _zip_member_bytes)

WORKBOOK_CAPABILITIES = ("cells", "formulas", "names", "hidden_structures", "text")
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
CALCEXT_NS = "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"
STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
ODS_MEDIA = "application/vnd.oasis.opendocument.spreadsheet"
XML_SST_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
XML_SST_RELATIONS = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings",
                     "http://purl.oclc.org/ooxml/officeDocument/relationships/sharedStrings")
CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)\Z")
FORMULA_TOKEN = re.compile(r'"(?:""|[^"])*"|' + r"'(?:''|[^'])*'|[\w.$?\\\u0080-\U0010ffff]+|\s+|.", re.DOTALL)
FORMULA_COLUMN = re.compile(r"\$?[A-Za-z]{1,3}\Z")
FORMULA_ROW = re.compile(r"\$?[1-9][0-9]*\Z")


def column_letters(column: int) -> str:
    """Convert a one-based source column to Excel letters."""
    result = ""
    while column:
        column, digit = divmod(column - 1, 26)
        result = chr(65 + digit) + result
    return result


def _coordinate(address: str) -> tuple[int, int]:
    """Return the original Excel row and column after validating the source address."""
    match = CELL_RE.fullmatch(address)
    if match is None:
        raise ValueError("invalid workbook cell address: %s" % address)
    column = 0
    for character in match[1].upper():
        column = column * 26 + ord(character) - 64
    row = int(match[2])
    if column > 16384 or row > 1048576:
        raise ValueError("workbook cell address exceeds Excel bounds: %s" % address)
    return row, column


def _address(row: int, column: int) -> str:
    return column_letters(column) + str(row)


def _qualified(sheet: str, address: str) -> str:
    return "'%s'!%s" % (sheet.replace("'", "''"), address)


def _location(base: dict, sheet: str, ordinal: int | None, address: str | None = None) -> dict:
    location = {**base, "kind": "workbook-cell" if address else "workbook-sheet",
                "sheet": sheet, "sheet_ordinal": ordinal}
    if address is not None:
        location.update(cell=address, qualified_cell=_qualified(sheet, address))
    return location


def _attrs(element: ET.Element) -> dict[str, str]:
    return {_local_name(key): value for key, value in element.attrib.items()}


def _descendants(root: ET.Element, name: str):
    return (node for node in root.iter() if _local_name(node.tag) == name)


def _child(root: ET.Element, name: str) -> ET.Element | None:
    return next((node for node in root if _local_name(node.tag) == name), None)


def _xml(payload: bytes | None, part: str, context: InspectionContext,
         document: str, ordinal: int) -> ET.Element | None:
    location = {"kind": "package", "part": part, "member_ordinal": ordinal}
    if payload is None:
        context.problem(document, WORKBOOK_CAPABILITIES, "workbook member unreadable", location)
        return None
    try:
        _xml_preflight(payload, part, context.zip_budget)
        return ET.fromstring(payload)
    except InspectionLimit:
        raise
    except (BrewdocError, ET.ParseError) as exc:
        context.problem(document, WORKBOOK_CAPABILITIES, str(exc), location)
        return None


def _json_value(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value


def _calamine_panic_types() -> tuple[type[BaseException], ...]:
    """Return loaded PyO3 panic classes without including process-control exceptions."""
    # The binding does not export PyO3's BaseException-derived panic type.
    return tuple(
        error for error in BaseException.__subclasses__()
        if error.__module__ == "pyo3_runtime" and error.__name__ == "PanicException")


def _cached_values(data: bytes, context: InspectionContext, document: str) -> dict:
    """Overlay typed caches atomically; optional binding failures preserve native evidence."""
    cache_errors = (Exception,) + _calamine_panic_types()
    values = {}
    bounds = {}
    for item in context.findings:
        if item["document"] != document or item["kind"] != "cell":
            continue
        row, column = _coordinate(item["location"]["cell"])
        sheet_name = item["location"]["sheet"]
        top, left, bottom, right = bounds.get(sheet_name, (row, column, row, column))
        bounds[sheet_name] = min(top, row), min(left, column), max(bottom, row), max(right, column)
    try:
        with CalamineWorkbook.from_filelike(io.BytesIO(data)) as book:
            for sheet_name in book.sheet_names:
                if sheet_name not in bounds:
                    continue
                top, left, bottom, right = bounds[sheet_name]
                location = {"kind": "workbook-sheet", "sheet": sheet_name}
                if (bottom - top + 1) * (right - left + 1) > 100_000:
                    context.finding(document, "inventory", "typed-cache-unavailable", location,
                                    reason="dense native sheet range exceeds 100000 cells")
                    continue
                sheet = book.get_sheet_by_name(sheet_name)
                if sheet.start is None or sheet.end is None:
                    continue
                top, left = sheet.start
                bottom, right = sheet.end
                count = (bottom - top + 1) * (right - left + 1)
                if count > context.remaining("records"):
                    context.finding(document, "inventory", "typed-cache-unavailable", location,
                                    reason="dense cached grid exceeds remaining record allowance")
                    continue
                context.charge("records", count, location)
                for row_offset, row in enumerate(sheet.to_python()):
                    for column_offset, value in enumerate(row):
                        if value != "" and value is not None:
                            values[(sheet_name, top + row_offset + 1,
                                    left + column_offset + 1)] = _json_value(value)
    except InspectionLimit:
        raise
    except cache_errors as exc:
        context.finding(document, "inventory", "typed-cache-unavailable", {"kind": "source"},
                        reason="%s: %s" % (type(exc).__name__, exc),
                        reader="python-calamine", optional=True)
        return {}
    return values


def _formula_tokens(formula: str) -> list[str]:
    """Keep nested structured references and their escaped brackets in one literal token."""
    tokens, position = [], 0
    while position < len(formula):
        if formula[position] != "[":
            match = FORMULA_TOKEN.match(formula, position)
            tokens.append(match.group())
            position = match.end()
            continue
        start, depth = position, 1
        position += 1
        while position < len(formula) and depth:
            if formula[position] == "'" and position + 1 < len(formula) and formula[position + 1] in "[]#'":
                position += 2
                continue
            if formula[position] == "[":
                depth += 1
            elif formula[position] == "]":
                depth -= 1
            position += 1
        tokens.append(formula[start:position])
    return tokens


def _shift_formula(formula: str, origin: str, destination: str) -> str:
    """Translate A1 cell and whole-axis tokens without rewriting names, strings or sheet qualifiers."""
    source_row, source_column = _coordinate(origin)
    target_row, target_column = _coordinate(destination)
    row_delta, column_delta = target_row - source_row, target_column - source_column
    tokens = _formula_tokens(formula)

    def neighbor(index: int, step: int) -> int:
        """Return the next non-whitespace token index without wrapping source boundaries."""
        while 0 <= index + step < len(tokens):
            index += step
            if not tokens[index].isspace():
                return index
        return -1

    def token(index: int) -> str:
        """Keep a missing neighbor distinct from the last source token."""
        return tokens[index] if index >= 0 else ""

    def column(value: str) -> int:
        """Decode a column token without evaluating its enclosing formula."""
        number = 0
        for character in value.removeprefix("$").upper():
            number = number * 26 + ord(character) - 64
        return number

    def axis(value: str, *, rows: bool) -> str:
        """Shift a relative axis while preserving absolute markers and source bounds."""
        original = int(value.removeprefix("$")) if rows else column(value)
        maximum = 1048576 if rows else 16384
        if not 1 <= original <= maximum:
            return value
        shifted = original if value.startswith("$") else original + (row_delta if rows else column_delta)
        if not 1 <= shifted <= maximum:
            return "#REF!"
        return ("$" if value.startswith("$") else "") + (str(shifted) if rows else column_letters(shifted))

    output = []
    for index, value in enumerate(tokens):
        output.append(value)
        if not value or value[0] in "\"'[" or value.isspace():
            continue
        previous, following = neighbor(index, -1), neighbor(index, 1)
        if token(following) in ("!", "(") or token(following).startswith("["):
            continue
        end = neighbor(following, 1) if token(following) == ":" else -1
        if token(previous) != "!" and end >= 0 and token(neighbor(end, 1)) == "!":
            continue
        kind = FORMULA_COLUMN if FORMULA_COLUMN.fullmatch(value) else FORMULA_ROW
        before_axis = neighbor(previous, -1) if token(previous) == ":" else -1
        if kind.fullmatch(value) and ((end >= 0 and kind.fullmatch(token(end)))
                                     or (before_axis >= 0 and kind.fullmatch(token(before_axis)))):
            output[-1] = axis(value, rows=kind is FORMULA_ROW)
            continue
        match = CELL_RE.fullmatch(value)
        if match is None:
            continue
        try:
            _coordinate(value)
        except ValueError:
            continue
        marker = value.find(match[2])
        output[-1] = axis(value[:marker].rstrip("$"), rows=False) + axis(
            ("$" if marker > 0 and value[marker - 1] == "$" else "") + match[2], rows=True)
        if "#REF!" in output[-1]:
            output[-1] = "#REF!"
    return "".join(output)


def _merge(address: str, ranges: list[str]) -> tuple[str | None, str | None]:
    row, column = _coordinate(address)
    for span in ranges:
        first, _, last = span.partition(":")
        top, left = _coordinate(first)
        bottom, right = _coordinate(last or first)
        if top <= row <= bottom and left <= column <= right:
            return first, span
    return None, None


def _cell_record(context: InspectionContext, document: str, location: dict, *, value,
                 value_type: str, formula: str | None = None, cache: str = "not_formula",
                 error: str | None = None, merge_origin=None, merge_range=None, **details) -> None:
    context.finding(document, "cells", "cell", location, value=value, value_type=value_type,
                    formula=formula, cached_value_state=cache, error=error,
                    merge_origin=merge_origin, merge_range=merge_range, **details)
    if formula is not None:
        context.finding(document, "formulas", "formula", location, text=formula,
                        cached_value_state=cache, **details)


def _relations(roots: list[tuple[int, str, ET.Element]], source: str) -> list[dict]:
    return [{**node.attrib, "member_ordinal": ordinal}
            for ordinal, part, root in roots if part == _relationship_part(source)
            for node in _descendants(root, "Relationship")]


def _xlsx_value(cell: ET.Element, strings: list[str]) -> tuple[object, str, str | None, str | None]:
    kind = cell.attrib.get("t", "n")
    cached = _child(cell, "v")
    raw = "".join(cached.itertext()) if cached is not None else None
    if kind == "inlineStr":
        value = "".join(node.text or "" for node in _descendants(cell, "t"))
        return value, "string", None, raw
    if kind == "str" and raw is not None:
        return raw, "string", None, raw
    if raw is None or raw == "":
        return None, "blank", None, raw
    if kind == "e":
        return raw, "error", raw, raw
    if kind == "s":
        index = int(raw)
        if index < 0:
            raise ValueError("negative shared string index")
        return strings[index], "string", None, raw
    if kind in ("str", "d"):
        return raw, "string" if kind == "str" else "date", None, raw
    if kind == "b":
        if raw not in ("0", "1"):
            raise ValueError("invalid boolean cache: %s" % raw)
        return raw == "1", "boolean", None, raw
    if kind not in ("n", ""):
        raise ValueError("unsupported cell value type: %s" % kind)
    number = float(raw)
    return _json_value(number), "number", None, raw


def _xlsx_sheet(root: ET.Element, base: dict, sheet: str, ordinal: int | None,
                strings: list[str], context: InspectionContext,
                document: str, relations: list[dict], *, state="visible", sheet_type="worksheet") -> None:
    """Preserve source cells, shared formulas, errors, merges and visibility metadata."""
    ranges = [node.attrib["ref"] for node in _descendants(root, "mergeCell") if "ref" in node.attrib]
    count = sum(1 for _cell in _descendants(root, "c"))
    context.check("records", context.consumed["records"] + count, base)
    cells = list(_descendants(root, "c"))
    shared = {}
    for cell in cells:
        formula = _child(cell, "f")
        if formula is not None and formula.attrib.get("t") == "shared" and formula.text:
            key = formula.attrib.get("si")
            if key in shared:
                context.problem(document, ("formulas",), "duplicate shared formula definition", base)
            shared[key] = (cell.attrib.get("r", ""), "".join(formula.itertext()), dict(formula.attrib))
    occupied = []
    seen = set()
    last_row = 0
    for row in _descendants(root, "row"):
        source_row = int(row.attrib.get("r", last_row + 1))
        last_row = source_row
        row_location = _location(base, sheet, ordinal) | {"row": source_row}
        if row.attrib.get("hidden") in ("true", "1"):
            context.finding(document, "hidden_structures", "hidden-row", row_location, **row.attrib)
        column = 0
        for cell in (node for node in row if _local_name(node.tag) == "c"):
            address = cell.attrib.get("r", _address(source_row, column + 1)).replace("$", "").upper()
            location = _location(base, sheet, ordinal, address)
            try:
                cell_row, column = _coordinate(address)
                occupied.append((cell_row, column))
                if address in seen:
                    context.problem(document, ("cells", "formulas"), "duplicate cell address", location)
                seen.add(address)
                value, value_type, error, raw = _xlsx_value(cell, strings)
                formula_node = _child(cell, "f")
                formula = None
                details = {"source_attributes": dict(cell.attrib), "raw_value": raw}
                cache = "not_formula"
                if formula_node is not None:
                    formula = "".join(formula_node.itertext())
                    details["formula_attributes"] = dict(formula_node.attrib)
                    details["source_formula"] = formula
                    cache = ("missing" if _child(cell, "v") is None else
                             "blank" if raw == "" and value_type == "blank" else "present")
                    if formula_node.attrib.get("t") == "shared":
                        definition = shared.get(formula_node.attrib.get("si"))
                        if definition is None:
                            context.problem(document, ("formulas",), "shared formula master missing", location)
                        else:
                            origin, expression, attributes = definition
                            formula = _shift_formula(expression, origin, address)
                            details["shared_master"] = {"cell": origin, "formula": expression,
                                                        "attributes": attributes}
                    if "[" in formula or "|" in formula:
                        context.finding(document, "external_references", "formula-reference", location,
                                        text=formula)
                    hyperlink = re.match(r'(?i)^\s*(?:_xlfn\.)?HYPERLINK\s*\(\s*"((?:""|[^"])*)"', formula)
                    if hyperlink:
                        context.finding(document, "links", "formula-hyperlink", location,
                                        target=hyperlink[1].replace('""', '"'), text=formula)
                origin, span = _merge(address, ranges)
                _cell_record(context, document, location, value=value, value_type=value_type,
                             formula=formula, cache=cache, error=error,
                             merge_origin=origin, merge_range=span, **details)
                if formula is not None and "macro" in sheet_type.lower():
                    context.finding(document, "macros", "xlm-formula", location, text=formula)
            except (ValueError, IndexError) as exc:
                context.problem(document, ("cells", "formulas"), str(exc), location)
    for span in ranges:
        first = span.split(":", 1)[0]
        location = _location(base, sheet, ordinal, first)
        context.finding(document, "cells", "merge", location, merge_origin=first, merge_range=span)
        if first not in seen:
            _cell_record(context, document, location, value=None, value_type="blank",
                         merge_origin=first, merge_range=span, source="merge-metadata")
    for node in _descendants(root, "col"):
        if node.attrib.get("hidden") in ("true", "1"):
            context.finding(document, "hidden_structures", "hidden-column", _location(base, sheet, ordinal),
                            **node.attrib)
    for node in _descendants(root, "hyperlink"):
        attributes = _attrs(node)
        location = _location(base, sheet, ordinal) | {"range": attributes.get("ref")}
        links = [relation for relation in relations if relation.get("Id") == attributes.get("id")]
        for relation in links:
            context.finding(document, "links", "cell-hyperlink", location,
                            target=relation.get("Target"), source_attributes=attributes,
                            relationship=relation)
        if attributes.get("location"):
            context.finding(document, "links", "cell-hyperlink", location,
                            target=attributes["location"], source_attributes=attributes)
        if attributes.get("id") and not links:
            context.problem(document, ("links",), "cell hyperlink relationship missing", location)
    dimension = next(_descendants(root, "dimension"), None)
    occupied_range = (dimension.attrib.get("ref") if dimension is not None else
                      _address(min(row for row, _ in occupied), min(col for _, col in occupied)) + ":"
                      + _address(max(row for row, _ in occupied), max(col for _, col in occupied))
                      if occupied else None)
    context.finding(document, "hidden_structures", "sheet", _location(base, sheet, ordinal),
                    state=state, sheet_type=sheet_type, occupied_range=occupied_range)
    if "macro" in sheet_type.lower():
        context.finding(document, "macros", "xlm-sheet", _location(base, sheet, ordinal),
                        sheet_type=sheet_type, state=state)


def _xlsx_strings(roots: list, members: tuple, workbook_part: str, relations: list,
                  context: InspectionContext, document: str) -> list[str]:
    """Resolve shared-string indices only through their owning workbook's unique relationship."""
    declarations = [node for _ordinal, part, root in roots if part == "[Content_Types].xml" for node in root]
    overrides = {node.attrib.get("PartName", "").lstrip("/") for node in declarations
                 if _local_name(node.tag) == "Override"}
    tables = {node.attrib.get("PartName", "").lstrip("/") for node in declarations
              if _local_name(node.tag) == "Override" and node.attrib.get("ContentType") == XML_SST_MEDIA}
    extensions = {node.attrib.get("Extension", "").lower() for node in declarations
                  if _local_name(node.tag) == "Default" and node.attrib.get("ContentType") == XML_SST_MEDIA}
    tables.update(part for _ordinal, part, _payload in members
                  if part not in overrides and part.rsplit(".", 1)[-1].lower() in extensions)
    matches = [relation for relation in relations if relation.get("Type") in XML_SST_RELATIONS]
    location = {"kind": "package", "part": workbook_part}
    if not matches:
        if tables:
            context.problem(document, ("cells",), "shared-string relationship missing", location)
        return []
    owners = [ordinal for ordinal, part, _payload in members if part == _relationship_part(workbook_part)]
    if len(matches) != 1 or len(owners) != 1 or matches[0].get("TargetMode") == "External":
        context.problem(document, ("cells",), "shared-string relationship ambiguous or external", location)
        return []
    try:
        target = _resolve_ooxml_part(workbook_part, matches[0].get("Target", ""))
    except BrewdocError as exc:
        context.problem(document, ("cells",), str(exc), location)
        return []
    for extra in sorted(tables - {target}):
        context.problem(document, ("cells",), "extra declared shared-string part",
                        {"kind": "package", "part": extra})
    sources = [(ordinal, root) for ordinal, part, root in roots if part == target]
    physical = [(ordinal, payload) for ordinal, part, payload in members if part == target]
    if len(physical) != 1 or len(sources) != 1:
        context.problem(document, ("cells",), "shared-string target missing or ambiguous", location | {"target": target})
        return []
    ordinal, root = sources[0]
    if root.tag not in ("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sst",
                        "{http://purl.oclc.org/ooxml/spreadsheetml/main}sst"):
        context.problem(document, ("cells",), "unsupported shared-string target root",
                        {"kind": "package", "part": target, "member_ordinal": ordinal})
        return []
    return ["".join(node.text or "" for node in _descendants(item, "t"))
            for item in root if _local_name(item.tag) == "si"]


def _inspect_xlsx(members: tuple, data: bytes, context: InspectionContext, document: str) -> None:
    roots = []
    for member_ordinal, part, payload in members:
        if part.endswith((".xml", ".rels")):
            root = _xml(payload, part, context, document, member_ordinal)
            if root is not None:
                roots.append((member_ordinal, part, root))
    workbooks = [(ordinal, part, root) for ordinal, part, root in roots
                 if _local_name(root.tag) == "workbook"]
    if not workbooks:
        context.problem(document, WORKBOOK_CAPABILITIES, "workbook XML missing", {"kind": "source"})
        return
    visited = set()
    for workbook_ordinal, workbook_part, workbook in workbooks:
        relations = _relations(roots, workbook_part)
        strings = _xlsx_strings(roots, members, workbook_part, relations, context, document)
        for ordinal, sheet in enumerate(_descendants(workbook, "sheet"), 1):
            attributes = _attrs(sheet)
            name = attributes.get("name", "[unnamed sheet]")
            base = {"part": workbook_part, "member_ordinal": workbook_ordinal}
            if "name" not in attributes:
                context.problem(document, WORKBOOK_CAPABILITIES, "worksheet name missing", base)
            context.finding(document, "hidden_structures", "sheet-declaration",
                            _location(base, name, ordinal), state=attributes.get("state", "visible"),
                            source_attributes=attributes)
            matches = [relation for relation in relations if relation.get("Id") == attributes.get("id")]
            if len(matches) != 1:
                context.problem(document, WORKBOOK_CAPABILITIES,
                                "worksheet relationship missing or ambiguous", _location(base, name, ordinal))
            for relation in matches:
                if relation.get("TargetMode") == "External":
                    context.problem(document, WORKBOOK_CAPABILITIES, "external worksheet", base)
                    continue
                try:
                    target = _resolve_ooxml_part(workbook_part, relation.get("Target", ""))
                except BrewdocError as exc:
                    context.problem(document, WORKBOOK_CAPABILITIES, str(exc), base)
                    continue
                sheets = [(member, part, root) for member, part, root in roots if part == target]
                if len(sheets) != 1:
                    context.problem(document, WORKBOOK_CAPABILITIES, "worksheet member missing or ambiguous",
                                    _location(base, name, ordinal))
                for member, part, root in sheets:
                    visited.add(member)
                    _xlsx_sheet(root, {"part": part, "member_ordinal": member}, name, ordinal,
                                strings, context, document, _relations(roots, part),
                                state=attributes.get("state", "visible"),
                                sheet_type=relation.get("Type", "").rsplit("/", 1)[-1])
        for name_ordinal, name in enumerate(_descendants(workbook, "definedName"), 1):
            location = {"kind": "package", "part": workbook_part, "member_ordinal": workbook_ordinal,
                        "name_ordinal": name_ordinal}
            text = "".join(name.itertext())
            context.finding(document, "names", "defined-name", location, text=text,
                            source_attributes=dict(name.attrib))
            if "[" in text or "|" in text:
                context.finding(document, "external_references", "named-reference", location, text=text,
                                source_attributes=dict(name.attrib))
            if name.attrib.get("xlm") in ("true", "1") or name.attrib.get("vbProcedure") in ("true", "1"):
                context.finding(document, "macros", "macro-name", location, text=text,
                                source_attributes=dict(name.attrib))
    for member, part, root in roots:
        if _local_name(root.tag) in ("worksheet", "macrosheet", "dialogsheet") and member not in visited:
            _xlsx_sheet(root, {"part": part, "member_ordinal": member}, part, None,
                        [], context, document, _relations(roots, part), sheet_type="unreferenced")
        for node in root.iter():
            if _local_name(node.tag) in ("externalBook", "ddeLink", "oleLink", "ddeItem", "oleItem"):
                context.finding(document, "external_references", _local_name(node.tag),
                                {"kind": "package", "part": part, "member_ordinal": member},
                                source_attributes=dict(node.attrib), text="".join(node.itertext()))
    for capability in WORKBOOK_CAPABILITIES:
        context.cover(document, capability, "complete", "all source workbook cells and metadata scanned")


def _odf_attr(node: ET.Element, name: str, namespace: str = TABLE_NS, default=None):
    return node.attrib.get("{%s}%s" % (namespace, name), default)


def _odf_text(node: ET.Element, context: InspectionContext, location: dict) -> str:
    """Preserve ODF paragraph boundaries and explicit repeated spaces."""
    pieces = []
    stack = [(node, False)]
    while stack:
        child, closing = stack.pop()
        name = _local_name(child.tag)
        if closing:
            if name in ("p", "h"):
                pieces.append("\n")
            if child.tail:
                pieces.append(child.tail)
            continue
        if name == "s":
            count = int(_odf_attr(child, "c", "urn:oasis:names:tc:opendocument:xmlns:text:1.0", "1"))
            if count < 1:
                raise ValueError("invalid ODS repeated space count")
            context.charge("decoded_bytes", count, location)
            pieces.append(" " * count)
        elif name == "tab":
            pieces.append("\t")
        elif name == "line-break":
            pieces.append("\n")
        if child.text:
            pieces.append(child.text)
        stack.append((child, True))
        stack.extend((item, False) for item in reversed(child))
    return "".join(pieces).rstrip("\n")


def _odf_value(cell: ET.Element, context: InspectionContext,
               location: dict) -> tuple[object, str, str | None, str | None]:
    """Read stored ODF cache types without interpreting formula source."""
    kind = _odf_attr(cell, "value-type", OFFICE_NS)
    formula = _odf_attr(cell, "formula")
    text = _odf_text(cell, context, location)
    raw = _odf_attr(cell, "value", OFFICE_NS)
    string_value = _odf_attr(cell, "string-value", OFFICE_NS)
    if _odf_attr(cell, "value-type", CALCEXT_NS) == "error":
        error = string_value or text or None
        return error, "error", error, error
    if string_value is None:
        string_value = text if next(_descendants(cell, "p"), None) is not None else None
    if kind in ("float", "percentage", "currency"):
        return _json_value(float(raw)) if raw is not None else None, "number", None, raw
    if kind == "boolean":
        value = _odf_attr(cell, "boolean-value", OFFICE_NS)
        if value is None and formula is not None:
            return None, "boolean", None, None
        if value not in ("true", "false", "1", "0"):
            raise ValueError("invalid ODS boolean value")
        return value in ("true", "1"), "boolean", None, value
    if kind in ("date", "time"):
        value = _odf_attr(cell, kind + "-value", OFFICE_NS)
        return value, kind, None, value
    if kind == "string" or string_value is not None:
        return string_value, "string", None, string_value
    if kind is not None and kind != "void":
        raise ValueError("unsupported ODS cell type: %s" % kind)
    return None, "blank", None, None if formula else ""


def _odf_rows(table: ET.Element, hidden: bool = False):
    """Visit admitted row groups iteratively, preserving source order and inherited visibility."""
    stack = [(iter(table), hidden)]
    while stack:
        children, inherited_hidden = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            continue
        name = _local_name(child.tag)
        if name == "table-row":
            yield child, inherited_hidden or _odf_attr(child, "visibility", default="visible") != "visible"
        elif name in ("table-header-rows", "table-rows", "table-row-group"):
            stack.append((iter(child), inherited_hidden or _odf_attr(child, "display") == "false"))


def _odf_sheet(table: ET.Element, base: dict, ordinal: int,
               context: InspectionContext, document: str, styles: dict) -> None:
    sheet = _odf_attr(table, "name", default="[unnamed sheet]")
    row_number, last_row, last_column = 1, 0, 0
    ranges = []
    style = styles.get(_odf_attr(table, "style-name"), {})
    context.finding(document, "hidden_structures", "sheet", _location(base, sheet, ordinal),
                    state="hidden" if style.get("display") == "false" else "visible",
                    source_attributes=dict(table.attrib))
    for row, hidden in _odf_rows(table):
        repeat_rows = int(_odf_attr(row, "number-rows-repeated", default="1"))
        if repeat_rows < 1:
            raise ValueError("invalid ODS repeated row count")
        context.charge("records", 1, _location(base, sheet, ordinal) | {"row": row_number})
        if hidden:
            context.finding(document, "hidden_structures", "hidden-row", _location(base, sheet, ordinal),
                            row=row_number, repeated=repeat_rows, source_attributes=dict(row.attrib))
        row_cells = []
        column = 1
        for cell in row:
            if _local_name(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            repeat_columns = int(_odf_attr(cell, "number-columns-repeated", default="1"))
            if repeat_columns < 1:
                raise ValueError("invalid ODS repeated column count")
            location = _location(base, sheet, ordinal, _address(row_number, column))
            try:
                value, kind, error, raw = _odf_value(cell, context, location)
                formula = _odf_attr(cell, "formula")
                metadata = (bool(set(_attrs(cell)) - {"number-columns-repeated"}) or kind != "blank"
                            or _local_name(cell.tag) == "covered-table-cell")
                row_cells.append((column, repeat_columns, cell, value, kind, error, raw, formula, metadata))
            except ValueError as exc:
                context.problem(document, ("cells", "formulas"), str(exc), location)
            column += repeat_columns
        active = sum(count for _column, count, _cell, value, _kind, _error, _raw, formula, metadata in row_cells
                     if value is not None or formula is not None or metadata)
        context.check("records", context.consumed["records"] + active * repeat_rows,
                      _location(base, sheet, ordinal) | {"row": row_number})
        for first_column, count, cell, value, kind, error, raw, formula, metadata in row_cells:
            if value is None and formula is None and not metadata:
                context.finding(document, "cells", "blank-range",
                                _location(base, sheet, ordinal, _address(row_number, first_column)),
                                range=_address(row_number, first_column) + ":"
                                + _address(row_number + repeat_rows - 1, first_column + count - 1),
                                value=None, value_type="blank", source_attributes=dict(cell.attrib))
        row_cells = [item for item in row_cells if item[3] is not None or item[7] is not None or item[8]]
        for source_row in range(row_number, row_number + repeat_rows) if row_cells else ():
            for first_column, count, cell, value, kind, error, raw, formula, metadata in row_cells:
                for source_column in range(first_column, first_column + count):
                    address = _address(source_row, source_column)
                    location = _location(base, sheet, ordinal, address)
                    width = int(_odf_attr(cell, "number-columns-spanned", default="1"))
                    height = int(_odf_attr(cell, "number-rows-spanned", default="1"))
                    span = address + ":" + _address(source_row + height - 1, source_column + width - 1)
                    if width > 1 or height > 1:
                        ranges.append(span)
                    origin, merge_range = _merge(address, ranges)
                    cache = "not_formula" if formula is None else "missing" if raw is None else "present"
                    _cell_record(context, document, location, value=value, value_type=kind,
                                 formula=formula, cache=cache, error=error, merge_origin=origin,
                                 merge_range=merge_range, raw_value=raw, source_attributes=dict(cell.attrib),
                                 source_repetition={"row": row_number, "rows": repeat_rows,
                                                    "column": first_column, "columns": count})
                    last_row, last_column = max(last_row, source_row), max(last_column, source_column)
        row_number += repeat_rows
    column = 1
    for node in table.iter():
        name = _local_name(node.tag)
        if name == "table-column":
            count = int(_odf_attr(node, "number-columns-repeated", default="1"))
            if _odf_attr(node, "visibility", default="visible") != "visible":
                context.finding(document, "hidden_structures", "hidden-column", _location(base, sheet, ordinal),
                                first_column=column, last_column=column + count - 1,
                                source_attributes=dict(node.attrib))
            column += count
    context.finding(document, "cells", "occupied-range", _location(base, sheet, ordinal),
                    occupied_range="A1:" + _address(last_row, last_column) if last_row else None)


def _inspect_ods(members: tuple, context: InspectionContext, document: str, *, prefix: str = "") -> None:
    """Inspect native ODS members in one exact original directory scope."""
    roots = []
    styles = {}
    selected = {prefix + "content.xml", prefix + "styles.xml"}
    content = [(ordinal, part, payload) for ordinal, part, payload in members if part == prefix + "content.xml"]
    if len(content) != 1:
        context.problem(document, WORKBOOK_CAPABILITIES, "ODS content.xml missing or ambiguous",
                        {"kind": "package", "part": prefix + "content.xml"})
    for ordinal, part, payload in members:
        if part in selected:
            root = _xml(payload, part, context, document, ordinal)
            if root is not None:
                roots.append((ordinal, part, root))
                for node in _descendants(root, "style"):
                    properties = _child(node, "table-properties")
                    if properties is not None:
                        styles[_odf_attr(node, "name", STYLE_NS)] = _attrs(properties)
    found = False
    for ordinal, part, root in roots:
        for spreadsheet in _descendants(root, "spreadsheet"):
            found = True
            for sheet_ordinal, table in enumerate((node for node in spreadsheet
                                                   if _local_name(node.tag) == "table"), 1):
                try:
                    _odf_sheet(table, {"part": part, "member_ordinal": ordinal}, sheet_ordinal,
                               context, document, styles)
                except ValueError as exc:
                    context.problem(document, WORKBOOK_CAPABILITIES, str(exc),
                                    {"kind": "package", "part": part, "member_ordinal": ordinal})
        for node in root.iter():
            name = _local_name(node.tag)
            location = {"kind": "package", "part": part, "member_ordinal": ordinal}
            if name in ("named-expression", "named-range"):
                attributes = _attrs(node)
                context.finding(document, "names", name, location,
                                text=attributes.get("expression", attributes.get("cell-range-address")),
                                source_attributes=dict(node.attrib))
            if name in ("dde-link", "dde-source", "table-source", "cell-range-source"):
                context.finding(document, "external_references", name, location,
                                source_attributes=dict(node.attrib), text="".join(node.itertext()))
    if not found:
        context.problem(document, WORKBOOK_CAPABILITIES, "ODS spreadsheet body missing", {"kind": "source"})
    for capability in WORKBOOK_CAPABILITIES:
        context.cover(document, capability, "complete", "all source ODS cells and metadata scanned")


def inspect_workbook(data: bytes | None, name: str, context: InspectionContext, document: str,
                     *, members=(), prefix: str = "", contiguous_source: bool = True,
                     media_type: str | None = None, scan_embedded=None) -> None:
    """Inspect source workbook structures; ODF directory scopes use native caches without file bytes."""
    suffix = ".ods" if media_type == ODS_MEDIA else PurePosixPath(name).suffix.lower()
    if data is None and contiguous_source:
        raise ValueError("contiguous workbook source bytes are missing")
    if not contiguous_source and suffix != ".ods":
        raise ValueError("member-scope workbook inspection requires ODS media type")
    if prefix and (not prefix.endswith("/") or prefix.startswith("/")
                   or any(part in ("", ".", "..") for part in prefix[:-1].split("/"))):
        raise ValueError("invalid embedded workbook directory prefix")
    start = len(context.findings)
    if suffix == ".xls":
        from brewdoc._biff import inspect_biff_stream
        from brewdoc._compound import read_compound
        from brewdoc._ole import inspect_compound_payloads
        from brewdoc._vba import inspect_vba

        streams = read_compound(data, context, document)
        workbooks = [entry for entry in streams if entry["kind"] == "stream"
                     and entry["name"].casefold() in ("workbook", "book") and entry["data"] is not None]
        if not workbooks:
            context.problem(document, WORKBOOK_CAPABILITIES, "CFB workbook stream missing", {"kind": "source"})
        for entry in workbooks:
            inspect_biff_stream(entry["data"], entry["name"], context, document, streams=streams)
        if any(entry["name"].casefold() in ("dir", "vba", "_vba_project", "project")
               or "vba" in entry["path"].casefold().split("/") for entry in streams):
            inspect_vba(data, name, context, document, streams=streams)
        inspect_compound_payloads(streams, name, context, document, scan_embedded=scan_embedded)
    elif suffix == ".xlsb":
        from brewdoc._biff12 import inspect_biff12

        inspect_biff12(data, name, context, document, members=members)
    elif suffix in (".xlsx", ".xlsm"):
        _inspect_xlsx(tuple(members), data, context, document)
    elif suffix == ".ods":
        _inspect_ods(tuple(members), context, document, prefix=prefix)
    else:
        raise ValueError("unsupported workbook inspection suffix: %s" % suffix)
    typed = _cached_values(data, context, document) if contiguous_source else {}
    for item in context.findings[start:]:
        if item["kind"] != "cell" or item["details"].get("value_type") in ("error", "blank"):
            continue
        location = item["location"]
        row, column = _coordinate(location["cell"])
        key = (location["sheet"], row, column)
        if key in typed:
            item["details"]["source_cached_value"] = item["details"].get("value")
            item["details"]["value"] = typed[key]


def _workbook_cell_findings(path: Path, report: dict | None = None) -> tuple[dict, ...]:
    """Supply root-workbook source records to the readable projection without generic inspection."""
    if report is not None:
        if not report["documents"]:
            return ()
        root = report["documents"][0]["id"]
        return tuple(item for item in report["findings"] if item["document"] == root
                     and item["capability"] in ("cells", "hidden_structures"))
    context = InspectionContext()
    location = {"kind": "source"}
    context.check("source_bytes", path.stat().st_size, location)
    data = path.read_bytes()
    context.charge("decoded_bytes", len(data), location)
    document = context.document(data, path.name, parent=None, location=location)
    members = ()
    if path.suffix.lower() != ".xls":
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = tuple((ordinal, info.filename, _zip_member_bytes(package, info, context.zip_budget))
                            for ordinal, info in enumerate(package.infolist(), 1)
                            if info.filename.endswith((".xml", ".rels", ".bin")))
    inspect_workbook(data, path.name, context, document, members=members)
    return tuple(item for item in context.findings
                 if item["capability"] in ("cells", "hidden_structures"))
