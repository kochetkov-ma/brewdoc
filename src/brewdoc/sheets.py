"""Workbook adapter: cached sheet values, sheet selection, OOXML formulas and VBA projects."""

from __future__ import annotations

import contextlib
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from python_calamine import CalamineWorkbook

from brewdoc._inspection_workbook import (_calamine_panic_types, _coordinate,
                                         _workbook_cell_findings, column_letters)
from brewdoc.common import (CAPABILITY_FIELDS, ArtifactRef, BrewdocError, Rendered, Route,
                            _ZipBudget, _assemble, _check_zip_members, _local_name, _opc_main_part,
                            _relationship_elements, _relationship_part, _relationships,
                            _resolve_ooxml_part,
                            _unit_anchor, _unit_key, _xml_part, _zip_member_bytes, cell_text,
                            new_tally, reading, sanitise)

FORMULA_SCHEMA = "brewdoc.formulas/1"
FORMULA_SUFFIXES = (".xlsm", ".xlsx")
VBA_SUFFIXES = (".xlsb", ".xlsm")

VBA_PROJECT_KEY = "vba/project/000001"
VBA_RELATIONSHIP_TYPE = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
# The two project fixtures top out at 17,920 bytes; 16 MiB is the passive-read boundary.
MAX_VBA_PROJECT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CellFormula:
    """A passive OOXML formula attached to one source cell."""

    sheet: str
    cell: str
    formula: str
    attributes: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict:
        """Return a JSON-ready formula without changing its source text."""
        return {"attributes": dict(self.attributes), "cell": self.cell,
                "formula": self.formula, "sheet": self.sheet}


@dataclass(frozen=True, slots=True)
class FormulaArtifact:
    """All recoverable formulas from one source worksheet."""

    key: str
    source_name: str
    source_sha256: str
    source_ordinal: int
    sheet: str
    formulas: tuple[CellFormula, ...]


def _sheet_cell_text(cell, tally: dict) -> str:
    """Keep exposed datetime precision and Unicode with the named text cleanup."""
    text = cell.isoformat() if isinstance(cell, datetime) else cell_text(cell, preserve_unicode=True)
    return sanitise(text, tally, preserve_unicode=True)


def _sheet_grid(rows, tally: dict) -> list[list[str]]:
    """Trim trailing empty columns of nonempty grids; preserve leading/interior blanks and ragged rows.
    Sanitise every input cell into the shared tally, including cells later trimmed."""
    grid = [[_sheet_cell_text(cell, tally) for cell in row] for row in rows]
    width = max((index + 1 for row in grid for index, cell in enumerate(row) if cell), default=0)
    return [row[:width] for row in grid] if width else []


def _addressed_grid(sheet, records: tuple[dict, ...], tally: dict) -> tuple[list[list[str]], str | None, bool]:
    """Add generated axes and literal source addresses without expanding distant empty areas."""
    values = {}
    projected = {}
    merge_bounds = []
    if sheet is not None:
        top, left = sheet.start or (0, 0)
        bottom, right = sheet.end or (top, left)
        rows = sheet.to_python() if (bottom - top + 1) * (right - left + 1) <= 100_000 else ()
        for row, cells in enumerate(rows, top + 1):
            for column, value in enumerate(cells, left + 1):
                text = _sheet_cell_text(value, tally)
                projected[(row, column)] = text
                if text:
                    values[(row, column)] = text
    for record in records:
        if record["kind"] != "cell":
            continue
        location, details = record["location"], record["details"]
        point = _coordinate(location["cell"])
        if details.get("merge_range"):
            merge_bounds.extend(_coordinate(address) for address in details["merge_range"].split(":"))
        if details.get("error") is not None:
            values[point] = details["error"]
        elif details.get("cached_value_state") == "missing":
            values[point] = "[missing cached result]"
        elif details.get("value_type") in ("blank", "empty"):
            values[point] = "[blank]"
        elif point not in values:
            values[point] = (projected[point] if point in projected else
                             _sheet_cell_text(details.get("value"), tally))
    if not values:
        return [], None, False
    row_numbers = sorted({row for row, _column in (*values, *merge_bounds)})
    column_numbers = sorted({column for _row, column in (*values, *merge_bounds)})
    first_row, last_row = row_numbers[0], row_numbers[-1]
    first_column, last_column = column_numbers[0], column_numbers[-1]
    occupied = "%s%d:%s%d" % (column_letters(first_column), first_row,
                               column_letters(last_column), last_row)
    sparse = (last_row - first_row + 1) * (last_column - first_column + 1) > 100_000
    if not sparse:
        row_numbers = range(first_row, last_row + 1)
        column_numbers = range(first_column, last_column + 1)
    if len(row_numbers) * len(column_numbers) > 100_000:
        return ([["Row", "Column", "Cell"], *[
            [str(row), column_letters(column), "%s%d: %s" % (column_letters(column), row, value)]
            for (row, column), value in sorted(values.items())]], occupied, True)
    grid = [["Row", *(column_letters(column) for column in column_numbers)]]
    for row in row_numbers:
        cells = [str(row)]
        for column in column_numbers:
            point = (row, column)
            cells.append("%s%d: %s" % (column_letters(column), row, values[point])
                         if point in values else "")
        grid.append(cells)
    return grid, occupied, sparse


def _sheet_selection(sheets) -> tuple | None:
    """Freeze a public sheet selection once; None keeps every sheet."""
    if sheets is None:
        return None
    if isinstance(sheets, (str, bytes)) or not isinstance(sheets, Iterable):
        raise BrewdocError("sheet selection must be a sequence of names")
    return tuple(sheets)


def _select_sheets(available, sheets) -> tuple[tuple[int, str], ...]:
    """Validate a selection and retain each sheet's one-based source ordinal."""
    available = tuple(available)
    if sheets is None:
        requested = available
    else:
        requested = sheets
        if not requested:
            raise BrewdocError("sheet selection is empty")
        if any(not isinstance(name, str) for name in requested):
            raise BrewdocError("sheet selection names must be strings")
        seen = set()
        for name in requested:
            if name in seen:
                raise BrewdocError("duplicate sheet selection: %s" % name)
            seen.add(name)
        unknown = [name for name in requested if name not in available]
        if unknown:
            raise BrewdocError("unknown sheet selection: %s; available sheets: %s"
                               % (", ".join(unknown), ", ".join(available)))
    ordinals = {name: index for index, name in enumerate(available, 1)}
    return tuple((ordinals[name], name) for name in requested)


@contextlib.contextmanager
def _workbook(path: Path):
    """Open a workbook with calamine; its own error types become one BrewdocError."""
    with reading(path, "spreadsheet"):
        try:
            with CalamineWorkbook.from_path(str(path)) as book:
                yield book
        except _calamine_panic_types() as exc:
            raise BrewdocError("spreadsheet unreadable: %s: %s: %s" % (path, type(exc).__name__, exc)) from exc


def _render_book(path: Path, sheets, *, _zip_budget: _ZipBudget | None = None,
                 _inspection_report: dict | None = None) -> Rendered:
    """Render addressed selected values while inspection retains the complete source workbook."""
    budget = _ZipBudget() if _zip_budget is None else _zip_budget
    tally = new_tally()
    with _workbook(path) as book:
        book_names = tuple(book.sheet_names)
        if sheets is not None:
            _select_sheets(sheets, sheets)
        findings = _workbook_cell_findings(path, _inspection_report)
        source_locations = {item["location"]["sheet_ordinal"]: item["location"]
                            for item in findings if item["kind"] in ("sheet", "sheet-declaration")
                            and item["location"].get("sheet_ordinal") is not None}
        stored_names = {location["sheet"] for location in source_locations.values() if location["sheet"] is not None}
        source_ordinals, original_names, aliases = {}, {}, {}
        for ordinal, location in sorted(source_locations.items()):
            original = location["sheet"]
            alias = original if original in book_names else None
            if original is None and ordinal <= len(book_names) and book_names[ordinal - 1] not in stored_names:
                alias = book_names[ordinal - 1]
            display = (original if original is not None else alias or location.get("display_sheet")
                       or "[unnamed source sheet %d]" % ordinal)
            source_ordinals[display], original_names[display], aliases[display] = ordinal, original, alias
        available = tuple(sorted(source_ordinals, key=source_ordinals.get))
        available += tuple(name for name in book_names
                           if name not in source_ordinals and name not in aliases.values())
        selected = _select_sheets(available, sheets)
        source_units = max(len(available), max(source_ordinals.values(), default=0))
        units = []
        for ordinal, name in selected:
            ordinal = source_ordinals.get(name, ordinal)
            original = original_names.get(name, name)
            records = tuple(item for item in findings if item["location"].get("sheet") == original
                            and item["location"].get("sheet_ordinal") == ordinal)
            points = [_coordinate(item["location"]["cell"]) for item in records if item["kind"] == "cell"]
            span = ((max(row for row, _column in points) - min(row for row, _column in points) + 1)
                    * (max(column for _row, column in points) - min(column for _row, column in points) + 1)
                    if points else 0)
            alias = aliases.get(name, name)
            sheet = book.get_sheet_by_name(alias) if alias in book_names and span <= 100_000 else None
            rows, occupied, sparse = _addressed_grid(sheet, records, tally)
            blocks = []
            if original is None:
                blocks.append(("text", ["Source sheet name: not stored. The heading is a generated display label."]))
            if rows:
                tally["tables"] += 1
                blocks.append(("text", ["Source range: " + occupied]))
                if sparse:
                    blocks.append(("text", ["Empty row and column gaps are omitted; source addresses are preserved."]))
                blocks.append(("table", rows))
            units.append((ordinal, name, blocks))
    artifacts = _book_artifacts(path, sheets, budget)
    return _assemble(path, ROUTE.name, ROUTE.unit_kind, source_units, units, tally,
                     not_carried=ROUTE.not_carried, artifacts=artifacts,
                     capabilities=_book_capabilities(path.suffix.lower(), artifacts),
                     preserve_unicode=True)


ROUTE = Route("sheet", "sheet", (".ods", ".xls", ".xlsb", ".xlsm", ".xlsx"), _render_book,
              ("cell formulas in Markdown content - only cached values are rendered",
               "formatting, colours, comments and data validation",
               "charts and embedded images",
               "readable VBA source modules"))


def render_book(path, *, sheets=None) -> tuple[str, dict]:
    """Render cached values for selected sheets with full-source ordinal navigation."""
    return _render_book(Path(path), _sheet_selection(sheets))[:2]


def _book_capabilities(suffix: str, artifacts: tuple[ArtifactRef, ...]) -> tuple:
    """Workbook formula and VBA rows; a reader the format lacks reports an unknown count."""
    unknown = ("unavailable", "unknown")
    formulas = sum(item.count for item in artifacts if item.kind == "formula")
    projects = sum(item.kind == "vba-project" for item in artifacts)
    values = ("available", str(formulas)) if suffix in FORMULA_SUFFIXES else unknown
    values += ("available", str(projects)) if suffix in VBA_SUFFIXES else unknown
    return tuple(zip(CAPABILITY_FIELDS, values + unknown))


def _sheet_relation_id(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == "id":
            return value
    raise BrewdocError("worksheet relationship is missing for sheet %s" % name)


def _formula_elements(root: ET.Element, sheet: str):
    """Yield addressed formula elements in worksheet XML order."""
    for cell in (element for element in root.iter() if _local_name(element.tag) == "c"):
        formula = next((child for child in cell if _local_name(child.tag) == "f"), None)
        if formula is None:
            continue
        address = cell.attrib.get("r")
        if not address:
            raise BrewdocError("formula cell address is missing in sheet %s" % sheet)
        yield address, formula


def _formula_cells(root: ET.Element, sheet: str) -> tuple[CellFormula, ...]:
    """Read formula elements, including empty shared followers."""
    return tuple(CellFormula(sheet, address, "".join(formula.itertext()),
                             tuple(sorted(formula.attrib.items())))
                 for address, formula in _formula_elements(root, sheet))


def _read_formula_artifacts(path: Path, sheets, references=False,
                            _zip_budget: _ZipBudget | None = None) -> tuple:
    """Read full artifacts or count-only `ArtifactRef`s with stable source ordinals."""
    suffix = path.suffix.lower()
    if suffix not in FORMULA_SUFFIXES:
        raise BrewdocError("formula artifacts unavailable for '%s'; supported suffixes: %s"
                           % (suffix, " ".join(FORMULA_SUFFIXES)))
    budget = _ZipBudget() if _zip_budget is None else _zip_budget
    try:
        digest = None if references else hashlib.sha256(path.read_bytes()).hexdigest()
        with zipfile.ZipFile(path) as package:
            _check_zip_members(package, budget)
            workbook_part = _opc_main_part(package, budget)
            workbook = _xml_part(package, workbook_part, budget)
            entries = []
            for ordinal, element in enumerate(
                    (node for node in workbook.iter() if _local_name(node.tag) == "sheet"), 1):
                name = element.attrib.get("name")
                if name is None:
                    raise BrewdocError("OOXML worksheet name is missing")
                entries.append((ordinal, name, _sheet_relation_id(element, name)))
            selected = _select_sheets((name for _ordinal, name, _relation in entries), sheets)
            by_name = {name: (ordinal, relation) for ordinal, name, relation in entries}
            relationships = _relationships(package, _relationship_part(workbook_part), budget)
            artifacts = []
            for _selected_ordinal, name in selected:
                ordinal, relation_id = by_name[name]
                relationship = relationships.get(relation_id)
                if relationship is None:
                    raise BrewdocError("worksheet relationship %s is missing for sheet %s"
                                       % (relation_id, name))
                if not relationship.attrib.get("Type", "").endswith("/worksheet"):
                    continue
                if relationship.attrib.get("TargetMode") == "External":
                    raise BrewdocError("worksheet relationship is external for sheet %s" % name)
                target = _resolve_ooxml_part(
                    workbook_part, relationship.attrib.get("Target", ""))
                root = _xml_part(package, target, budget)
                key = "formula/sheet/%06d" % ordinal
                if references:
                    count = sum(1 for _address, _formula in _formula_elements(root, name))
                    if count:
                        artifacts.append(ArtifactRef(
                            key, "formula", "available", count,
                            "#" + _unit_anchor(_unit_key("sheet", ordinal)), "application/json"))
                else:
                    formulas = _formula_cells(root, name)
                    if formulas:
                        artifacts.append(FormulaArtifact(
                            key, path.name, digest, ordinal, name, formulas))
            return tuple(artifacts)
    except BrewdocError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BrewdocError("formula artifact unreadable: %s: %s" % (path, exc)) from exc


def read_formulas(path, *, sheets=None) -> tuple[FormulaArtifact, ...]:
    """Read formula-bearing OOXML sheets as immutable keyed artifacts."""
    return _read_formula_artifacts(Path(path), _sheet_selection(sheets))


def _vba_relationship(package: zipfile.ZipFile, workbook_part: str,
                      _zip_budget: _ZipBudget) -> str | None:
    """Return the package part of the single internal VBA relationship, or None."""
    relationships = tuple(
        relationship for relationship in _relationship_elements(
            package, _relationship_part(workbook_part), _zip_budget)
        if relationship.attrib.get("Type") == VBA_RELATIONSHIP_TYPE
    )
    if len(relationships) > 1:
        raise BrewdocError("multiple VBA project relationships")
    if not relationships:
        return None
    relationship = relationships[0]
    if relationship.attrib.get("TargetMode") == "External":
        raise BrewdocError("VBA project relationship is external")
    return _resolve_ooxml_part(workbook_part, relationship.attrib.get("Target", ""))


def _vba_member(package: zipfile.ZipFile, package_part: str,
                 _zip_budget: _ZipBudget | None = None) -> bytes:
    """Read exact project bytes within the VBA and operation bounds."""
    budget = _ZipBudget() if _zip_budget is None else _zip_budget
    _check_zip_members(package, budget)
    matches = tuple(info for info in package.infolist() if info.filename == package_part)
    if not matches:
        raise BrewdocError("OOXML part is missing: %s" % package_part)
    if len(matches) > 1:
        raise BrewdocError("OOXML part occurs more than once: %s" % package_part)
    info = matches[0]
    if info.flag_bits & 1:
        raise BrewdocError("VBA project part is encrypted: %s" % package_part)
    return _zip_member_bytes(
        package, info, budget, max_bytes=MAX_VBA_PROJECT_BYTES,
        limit_error="VBA project part exceeds %d bytes: %s" % (MAX_VBA_PROJECT_BYTES, package_part))


def _vba_project(path: Path, _zip_budget: _ZipBudget | None = None) -> bytes | None:
    """Read the related opaque VBA project as exact bytes without parsing its streams."""
    budget = _ZipBudget() if _zip_budget is None else _zip_budget
    try:
        with zipfile.ZipFile(path) as package:
            _check_zip_members(package, budget)
            package_part = _vba_relationship(package, _opc_main_part(package, budget), budget)
            return None if package_part is None else _vba_member(package, package_part, budget)
    except BrewdocError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BrewdocError("VBA artifact unreadable: %s: %s" % (path, exc)) from exc


def _workbook_suffix(path: Path) -> str:
    """Return a workbook suffix or refuse the artifact capability before any read."""
    suffix = path.suffix.lower()
    if suffix not in ROUTE.suffixes:
        raise BrewdocError("workbook artifacts unavailable for '%s'" % suffix)
    return suffix


def _book_artifacts(path: Path, sheets, _zip_budget: _ZipBudget) -> tuple[ArtifactRef, ...]:
    """Inventory selected formula sheets and the workbook-level VBA project; None selects all."""
    suffix = _workbook_suffix(path)
    artifacts = (_read_formula_artifacts(path, sheets, references=True, _zip_budget=_zip_budget)
                 if suffix in FORMULA_SUFFIXES else ())
    project = _vba_project(path, _zip_budget) if suffix in VBA_SUFFIXES else None
    if project is None:
        return artifacts
    return artifacts + (ArtifactRef(
        VBA_PROJECT_KEY, "vba-project", "available", 1, "#brewdoc-metadata",
        "application/vnd.ms-office.vbaProject", len(project), hashlib.sha256(project).hexdigest()),)


def _list_book_artifacts(path: Path, sheets, _zip_budget: _ZipBudget) -> tuple[ArtifactRef, ...]:
    """Validate native sheet selection and inventory within the caller's budget."""
    _workbook_suffix(path)
    sheets = _sheet_selection(sheets)
    with _workbook(path) as book:
        _select_sheets(book.sheet_names, sheets)      # the value reader validates every format
    return _book_artifacts(path, sheets, _zip_budget)


def list_book_artifacts(path, *, sheets=None) -> tuple[ArtifactRef, ...]:
    """List selected formula sheets and the workbook-level opaque VBA project."""
    return _list_book_artifacts(Path(path), sheets, _ZipBudget())


def _formula_json(path: Path, artifact: FormulaArtifact) -> bytes:
    """Serialize one formula-bearing sheet as versioned deterministic ASCII JSON."""
    payload = {
        "formulas": [formula.to_dict() for formula in artifact.formulas],
        "schema": FORMULA_SCHEMA,
        "sheet": {"key": _unit_key("sheet", artifact.source_ordinal),
                  "name": artifact.sheet, "ordinal": artifact.source_ordinal},
        "source": {"bytes": path.stat().st_size, "name": path.name,
                   "sha256": artifact.source_sha256, "suffix": path.suffix.lower()},
    }
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def _artifact_payloads(path: Path, sheets, references: tuple[ArtifactRef, ...],
                       keys: tuple[str, ...], _zip_budget: _ZipBudget | None = None) -> tuple[bytes, ...]:
    """Serialize listed artifacts by key: formula JSON or exact VBA project bytes."""
    budget = _ZipBudget() if _zip_budget is None else _zip_budget
    kinds = [next((item.kind for item in references if item.key == key), None) for key in keys]
    formulas = ({item.key: item for item in _read_formula_artifacts(path, sheets, _zip_budget=budget)}
                if "formula" in kinds else {})
    payloads = []
    for key, kind in zip(keys, kinds):
        payload = (_formula_json(path, formulas[key]) if kind == "formula" and key in formulas
                   else _vba_project(path, budget) if kind == "vba-project" else None)
        if payload is None:
            raise BrewdocError("workbook artifact not found: %s" % key)
        payloads.append(payload)
    return tuple(payloads)


def read_book_artifact(path, key: str, *, sheets=None) -> bytes:
    """Return deterministic formula JSON or exact opaque VBA project bytes by key."""
    path = Path(path)
    sheets = _sheet_selection(sheets)
    budget = _ZipBudget()
    return _artifact_payloads(path, sheets, _list_book_artifacts(path, sheets, budget), (key,), budget)[0]
