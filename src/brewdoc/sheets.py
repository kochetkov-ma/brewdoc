"""Workbook adapter: cached sheet values, sheet selection, OOXML formulas and VBA projects."""

from __future__ import annotations

import contextlib
import hashlib
import json
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from python_calamine import CalamineWorkbook

from brewdoc.common import (CAPABILITY_FIELDS, ArtifactRef, BrewdocError, Rendered, _assemble,
                            _unit_anchor, _unit_key, cell_text, new_tally, sanitise)

FORMULA_SCHEMA = "brewdoc.formulas/1"
SHEET_SUFFIXES = (".ods", ".xls", ".xlsb", ".xlsm", ".xlsx")
FORMULA_SUFFIXES = (".xlsm", ".xlsx")
VBA_SUFFIXES = (".xlsb", ".xlsm")

VBA_PROJECT_KEY = "vba/project/000001"
VBA_RELATIONSHIP_TYPE = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
# The two project fixtures top out at 17,920 bytes; 16 MiB is the passive-read boundary.
MAX_VBA_PROJECT_BYTES = 16 * 1024 * 1024

SHEET_NOT_CARRIED = ("cell formulas in Markdown content - only cached values are rendered",
                     "formatting, colours, comments and data validation",
                     "charts and embedded images",
                     "readable VBA source modules")


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


def _sheet_grid(rows, tally: dict) -> list[list[str]]:
    # calamine trims trailing empty rows but not trailing empty columns; interior empty rows stay.
    grid = [[sanitise(cell_text(cell), tally) for cell in row] for row in rows]
    width = max((index + 1 for row in grid for index, cell in enumerate(row) if cell), default=0)
    return [row[:width] for row in grid] if width else []


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
    try:
        with CalamineWorkbook.from_path(str(path)) as book:
            yield book
    except BrewdocError:
        raise
    except Exception as exc:                      # calamine raises its own error types
        raise BrewdocError("spreadsheet unreadable: %s: %s" % (path, exc)) from exc


def _render_book(path: Path, sheets) -> Rendered:
    """Render selected cached values; the same frozen selection scopes the artifact inventory."""
    tally = new_tally()
    with _workbook(path) as book:
        selected = _select_sheets(book.sheet_names, sheets)
        source_units = len(book.sheet_names)
        units = []
        for ordinal, name in selected:
            raw = book.get_sheet_by_name(name).to_python(skip_empty_area=False)
            rows = _sheet_grid(raw, tally)
            tally["sheets"] += 1
            if rows:
                tally["tables"] += 1
            units.append((ordinal, name, [("table", rows)] if rows else []))
    artifacts = _book_artifacts(path, sheets)
    return _assemble(path, "sheet", "sheet", source_units, units, tally,
                     not_carried=SHEET_NOT_CARRIED, artifacts=artifacts,
                     capabilities=_book_capabilities(path.suffix.lower(), artifacts))


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


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_part(package: zipfile.ZipFile, part: str) -> ET.Element:
    """Read one required OOXML part and name missing or malformed XML."""
    try:
        data = package.read(part)
    except KeyError as exc:
        raise BrewdocError("OOXML part is missing: %s" % part) from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise BrewdocError("cannot parse OOXML part %s: %s" % (part, exc)) from exc


def _resolve_ooxml_part(base: str, target: str) -> str:
    """Resolve one internal relationship target without leaving the package root."""
    if not target:
        raise BrewdocError("OOXML relationship target is missing")
    # join keeps an absolute target whole; OPC absolute part names start at the package root.
    joined = posixpath.join(posixpath.dirname(base), target).removeprefix("/")
    resolved = posixpath.normpath(joined)
    if resolved in (".", "..") or resolved.startswith("../"):
        raise BrewdocError("OOXML relationship target leaves the package: %s" % target)
    return resolved


def _relationship_elements(package: zipfile.ZipFile, part: str) -> tuple[ET.Element, ...]:
    """Read relationship elements without hiding duplicate identifiers or types."""
    root = _xml_part(package, part)
    return tuple(element for element in root.iter()
                 if _local_name(element.tag) == "Relationship")


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, ET.Element]:
    return {element.attrib["Id"]: element for element in _relationship_elements(package, part)
            if "Id" in element.attrib}


def _workbook_part(package: zipfile.ZipFile) -> str:
    """Find the workbook through its package-level office relationship."""
    relationships = _relationships(package, "_rels/.rels")
    for relationship in relationships.values():
        if (relationship.attrib.get("Type", "").endswith("/officeDocument")
                and relationship.attrib.get("TargetMode") != "External"):
            return _resolve_ooxml_part("", relationship.attrib.get("Target", ""))
    raise BrewdocError("OOXML office document relationship is missing")


def _relationship_part(part: str) -> str:
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


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


def _read_formula_artifacts(path: Path, sheets, references=False) -> tuple:
    """Read full artifacts or count-only `ArtifactRef`s with stable source ordinals."""
    suffix = path.suffix.lower()
    if suffix not in FORMULA_SUFFIXES:
        raise BrewdocError("formula artifacts unavailable for '%s'; supported suffixes: %s"
                           % (suffix, " ".join(FORMULA_SUFFIXES)))
    try:
        digest = None if references else hashlib.sha256(path.read_bytes()).hexdigest()
        with zipfile.ZipFile(path) as package:
            workbook_part = _workbook_part(package)
            workbook = _xml_part(package, workbook_part)
            entries = []
            for ordinal, element in enumerate(
                    (node for node in workbook.iter() if _local_name(node.tag) == "sheet"), 1):
                name = element.attrib.get("name")
                if name is None:
                    raise BrewdocError("OOXML worksheet name is missing")
                entries.append((ordinal, name, _sheet_relation_id(element, name)))
            selected = _select_sheets((name for _ordinal, name, _relation in entries), sheets)
            by_name = {name: (ordinal, relation) for ordinal, name, relation in entries}
            relationships = _relationships(package, _relationship_part(workbook_part))
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
                root = _xml_part(package, target)
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


def _vba_relationship(package: zipfile.ZipFile, workbook_part: str) -> str | None:
    """Return the package part of the single internal VBA relationship, or None."""
    relationships = tuple(
        relationship for relationship in _relationship_elements(
            package, _relationship_part(workbook_part))
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


def _vba_member(package: zipfile.ZipFile, package_part: str) -> bytes:
    """Read one bounded project member; zipfile never yields more than its declared size."""
    matches = tuple(info for info in package.infolist() if info.filename == package_part)
    if not matches:
        raise BrewdocError("OOXML part is missing: %s" % package_part)
    if len(matches) > 1:
        raise BrewdocError("OOXML part occurs more than once: %s" % package_part)
    info = matches[0]
    if info.flag_bits & 1:
        raise BrewdocError("VBA project part is encrypted: %s" % package_part)
    if info.file_size > MAX_VBA_PROJECT_BYTES:
        raise BrewdocError("VBA project part exceeds %d bytes: %s"
                           % (MAX_VBA_PROJECT_BYTES, package_part))
    return package.read(info)


def _vba_project(path: Path) -> bytes | None:
    """Read the related opaque VBA project as exact bytes without parsing its streams."""
    try:
        with zipfile.ZipFile(path) as package:
            package_part = _vba_relationship(package, _workbook_part(package))
            return None if package_part is None else _vba_member(package, package_part)
    except BrewdocError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BrewdocError("VBA artifact unreadable: %s: %s" % (path, exc)) from exc


def _workbook_suffix(path: Path) -> str:
    """Return a workbook suffix or refuse the artifact capability before any read."""
    suffix = path.suffix.lower()
    if suffix not in SHEET_SUFFIXES:
        raise BrewdocError("workbook artifacts unavailable for '%s'" % suffix)
    return suffix


def _book_artifacts(path: Path, sheets) -> tuple[ArtifactRef, ...]:
    """Inventory selected formula sheets and the workbook-level VBA project; None selects all."""
    suffix = _workbook_suffix(path)
    artifacts = (_read_formula_artifacts(path, sheets, references=True)
                 if suffix in FORMULA_SUFFIXES else ())
    project = _vba_project(path) if suffix in VBA_SUFFIXES else None
    if project is None:
        return artifacts
    return artifacts + (ArtifactRef(
        VBA_PROJECT_KEY, "vba-project", "available", 1, "#brewdoc-metadata",
        "application/vnd.ms-office.vbaProject", len(project), hashlib.sha256(project).hexdigest()),)


def list_book_artifacts(path, *, sheets=None) -> tuple[ArtifactRef, ...]:
    """List selected formula sheets and the workbook-level opaque VBA project."""
    path = Path(path)
    _workbook_suffix(path)
    sheets = _sheet_selection(sheets)
    with _workbook(path) as book:
        _select_sheets(book.sheet_names, sheets)      # the value reader validates every format
    return _book_artifacts(path, sheets)


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
                       keys: tuple[str, ...]) -> tuple[bytes, ...]:
    """Serialize listed artifacts by key: formula JSON or exact VBA project bytes."""
    kinds = [next((item.kind for item in references if item.key == key), None) for key in keys]
    formulas = ({item.key: item for item in _read_formula_artifacts(path, sheets)}
                if "formula" in kinds else {})
    payloads = []
    for key, kind in zip(keys, kinds):
        payload = (_formula_json(path, formulas[key]) if kind == "formula" and key in formulas
                   else _vba_project(path) if kind == "vba-project" else None)
        if payload is None:
            raise BrewdocError("workbook artifact not found: %s" % key)
        payloads.append(payload)
    return tuple(payloads)


def read_book_artifact(path, key: str, *, sheets=None) -> bytes:
    """Return deterministic formula JSON or exact opaque VBA project bytes by key."""
    path = Path(path)
    sheets = _sheet_selection(sheets)
    return _artifact_payloads(path, sheets, list_book_artifacts(path, sheets=sheets), (key,))[0]
