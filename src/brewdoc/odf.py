"""ODT and ODP text extraction from bounded, qualified ODF package parts."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from brewdoc.common import (BrewdocError, LIGATURES, Rendered, Route, _ZipBudget, _assemble,
                            _check_zip_members, _xml_part, _zip_member_bytes,
                            new_tally, reading, sanitise)

OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
PRESENTATION = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
MANIFEST = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
TABLE_MAX_COLUMNS = 256
TABLE_MAX_SLOTS = 65_536
TOTAL_TABLE_MAX_SLOTS = 262_144
EXPANDED_MAX_CHARACTERS = 33_554_432
OMISSIONS = ("images, charts, equations, embedded objects and macros",
             "notes, comments and tracked-change history",
             "page layout, styles, list numbering and formula evaluation")
TEXT_CONTAINERS = {TEXT + name for name in ("section", "list", "list-item", "list-header")}
ROW_CONTAINERS = {TABLE + name for name in ("table-header-rows", "table-rows", "table-row-group")}
INLINE_CONTAINERS = {TEXT + "span", TEXT + "a"}
FURNITURE = {"footer", "header", "date-time", "page-number"}


@dataclass
class _Expansion:
    """Admit retained character copies and expanded table slots before allocation."""

    characters: int = 0
    slots: int = 0

    def charge_characters(self, amount: int) -> None:
        """Refuse character growth before joining, repeating or normalizing retained text."""
        if self.characters + amount > EXPANDED_MAX_CHARACTERS:
            raise BrewdocError("ODF expanded characters limit exceeded: %d"
                               % EXPANDED_MAX_CHARACTERS)
        self.characters += amount

    def charge_slots(self, amount: int, table_slots: int) -> None:
        """Admit every explicit expanded slot, including empty rows and cells."""
        if table_slots + amount > TABLE_MAX_SLOTS:
            raise BrewdocError("ODF table slots limit exceeded: %d" % TABLE_MAX_SLOTS)
        if self.slots + amount > TOTAL_TABLE_MAX_SLOTS:
            raise BrewdocError("ODF total table slots limit exceeded: %d" % TOTAL_TABLE_MAX_SLOTS)
        self.slots += amount


def _count(node, attribute: str, maximum: int, error: str) -> int:
    """Parse a positive decimal after a bounded lexical comparison."""
    value = node.get(attribute, "1")
    if not value or not value.isascii() or not value.isdecimal() or not value.strip("0"):
        raise BrewdocError("ODF count must be a positive integer: %s" % attribute)
    digits = value.lstrip("0")
    bound = str(maximum)
    if len(digits) > len(bound) or len(digits) == len(bound) and digits > bound:
        raise BrewdocError(error)
    return int(digits)


def _spaces(count: int) -> str:
    """Materialize spaces only after the caller admits their character count."""
    return " " * count


def _lines(node, tally: dict, expansion: _Expansion) -> list[str]:
    """Keep qualified inline text and skipped-subtree tails without recursion."""
    fragments = []
    stack = [iter((("node", node),))]
    while stack:
        item = next(stack[-1], None)
        if item is None:
            stack.pop()
            continue
        kind, element = item
        if kind == "tail":
            text = element.tail or ""
        elif element is node or element.tag in INLINE_CONTAINERS:
            text = element.text or ""
            stack.append(iter((event, child) for child in element
                              for event in ("node", "tail")))
        elif element.tag == TEXT + "s":
            count = _count(element, TEXT + "c", EXPANDED_MAX_CHARACTERS,
                           "ODF expanded characters limit exceeded: %d" % EXPANDED_MAX_CHARACTERS)
            expansion.charge_characters(count)
            fragments.append(_spaces(count))
            continue
        elif element.tag == TEXT + "tab":
            text = " "
        elif element.tag == TEXT + "line-break":
            text = "\n"
        else:
            continue
        expansion.charge_characters(len(text) + sum(text.count(glyph) * (len(value) - 1)
                                                  for glyph, value in LIGATURES.items()))
        fragments.append(text)
    cleaned = sanitise("".join(fragments), tally, keep_layout=True, preserve_unicode=True)
    return [line.strip() for line in cleaned.split("\n") if line.strip()]


def _nodes(root, containers: set[str], *, skip=None):
    """Yield supported blocks in document order, descending only into their containers."""
    stack = [iter(root)]
    while stack:
        node = next(stack[-1], None)
        if node is None:
            stack.pop()
        elif node is skip:
            continue
        elif node.tag in (TEXT + "p", TEXT + "h", TABLE + "table"):
            yield node
        elif node.tag in containers:
            if node.tag == DRAW + "frame" and node.get(PRESENTATION + "class") in FURNITURE:
                continue
            stack.append(iter(node))


def _row_nodes(table):
    """Read direct rows through ODF row wrappers without entering nested tables."""
    stack = [iter(table)]
    while stack:
        node = next(stack[-1], None)
        if node is None:
            stack.pop()
        elif node.tag == TABLE + "table-row":
            yield node
        elif node.tag in ROW_CONTAINERS:
            stack.append(iter(node))


def _rows(table, tally: dict, expansion: _Expansion) -> list[list[str]]:
    """Expand admitted explicit slots and verify covered-cell ownership before retaining rows."""
    rows, active = [], []
    table_slots = 0
    for source_row in _row_nodes(table):
        descriptors, width = [], 0
        for cell in source_row:
            if cell.tag not in (TABLE + "table-cell", TABLE + "covered-table-cell"):
                continue
            repeat = _count(cell, TABLE + "number-columns-repeated", TABLE_MAX_COLUMNS,
                            "ODF table columns limit exceeded: %d" % TABLE_MAX_COLUMNS)
            width += repeat
            if width > TABLE_MAX_COLUMNS:
                raise BrewdocError("ODF table columns limit exceeded: %d" % TABLE_MAX_COLUMNS)
            span = _count(cell, TABLE + "number-columns-spanned", TABLE_MAX_COLUMNS,
                          "ODF table columns limit exceeded: %d" % TABLE_MAX_COLUMNS)
            height = _count(cell, TABLE + "number-rows-spanned", TABLE_MAX_SLOTS,
                            "ODF table slots limit exceeded: %d" % TABLE_MAX_SLOTS)
            covered = cell.tag == TABLE + "covered-table-cell"
            lines = [] if covered else [line for node in _nodes(cell, TEXT_CONTAINERS)
                                       if node.tag != TABLE + "table"
                                       for line in _lines(node, tally, expansion)]
            expansion.charge_characters(max(0, len(lines) - 1))
            value = " ".join(lines)
            expansion.charge_characters(len(value) * (repeat - 1))
            descriptors.append((covered, repeat, span, height, value))
        raw_repeat = source_row.get(TABLE + "number-rows-repeated", "1")
        if not width:
            if (not raw_repeat or not raw_repeat.isascii() or not raw_repeat.isdecimal()
                    or not raw_repeat.strip("0")):
                raise BrewdocError("ODF row repeat must be a positive integer")
            if any(active):
                raise BrewdocError("ODF merged grid lacks covered cells")
            continue
        repeat = _count(source_row, TABLE + "number-rows-repeated", TABLE_MAX_SLOTS // width,
                        "ODF table slots limit exceeded: %d" % TABLE_MAX_SLOTS)
        expansion.charge_slots(width * repeat, table_slots)
        table_slots += width * repeat
        row_characters = sum(len(value) * copies for _, copies, _, _, value in descriptors)
        expansion.charge_characters(row_characters * (repeat - 1))
        if len(active) > width and any(active[width:]):
            raise BrewdocError("ODF merged grid lacks covered cells")
        active = active[:width] + [0] * max(0, width - len(active))
        for _ in range(repeat):
            occupied = [remaining > 0 for remaining in active]
            future = [max(0, remaining - 1) for remaining in active]
            values, column = [], 0
            for covered, copies, span, height, value in descriptors:
                for _ in range(copies):
                    if covered:
                        if not occupied[column] or span != 1 or height != 1:
                            raise BrewdocError("ODF covered cell has no merged owner")
                    else:
                        if column + span > width or any(occupied[column:column + span]):
                            raise BrewdocError("ODF merged grid overlaps a cell")
                        for index in range(column + 1, column + span):
                            occupied[index] = True
                        for index in range(column, column + span):
                            future[index] = height - 1
                    values.append("" if covered else value)
                    column += 1
            active = future
            rows.append(values)
    if any(active):
        raise BrewdocError("ODF merged grid lacks covered rows")
    return rows


def _body(path: Path, kind: str):
    """Validate the three unique consumed parts and close the archive before rendering."""
    budget = _ZipBudget(format_name="ODF")
    mime = "application/vnd.oasis.opendocument." + ("text" if kind == "odt" else "presentation")
    with reading(path, "ODF"), zipfile.ZipFile(path) as package:
        _check_zip_members(package, budget)
        for part in ("mimetype", "META-INF/manifest.xml", "content.xml"):
            matches = [item for item in package.infolist() if item.filename == part]
            if not matches:
                raise BrewdocError("ODF part is missing: %s" % part)
            if len(matches) != 1:
                raise BrewdocError("ODF part occurs more than once: %s" % part)
        if _zip_member_bytes(package, "mimetype", budget) != mime.encode("ascii"):
            raise BrewdocError("ODF mimetype does not match %s" % kind)
        manifest = _xml_part(package, "META-INF/manifest.xml", budget)
        if manifest.tag != MANIFEST + "manifest":
            raise BrewdocError("ODF manifest root is unexpected")
        roots = [entry for entry in manifest if entry.tag == MANIFEST + "file-entry"
                 and entry.get(MANIFEST + "full-path") == "/"]
        if len(roots) != 1 or roots[0].get(MANIFEST + "media-type") != mime:
            raise BrewdocError("ODF manifest root media type does not match %s" % kind)
        for entry in manifest:
            if (entry.tag == MANIFEST + "file-entry"
                    and entry.get(MANIFEST + "full-path") == "content.xml"
                    and any(child.tag == MANIFEST + "encryption-data" for child in entry)):
                raise BrewdocError("ODF encrypted part is unsupported: content.xml")
        content = _xml_part(package, "content.xml", budget)
    if content.tag != OFFICE + "document-content":
        raise BrewdocError("ODF document-content root is unexpected")
    bodies = [node for node in content if node.tag == OFFICE + "body"]
    name = "text" if kind == "odt" else "presentation"
    candidates = [node for body in bodies for node in body if node.tag == OFFICE + name]
    if len(bodies) != 1 or len(candidates) != 1:
        raise BrewdocError("ODF body must contain one office:%s" % name)
    return candidates[0]


def _block(node, tally: dict, expansion: _Expansion):
    """Count one retained paragraph or table without adding table paragraphs to regions."""
    if node.tag == TABLE + "table":
        rows = _rows(node, tally, expansion)
        if rows:
            tally["tables"] += 1
            return "table", rows
    else:
        lines = _lines(node, tally, expansion)
        if lines:
            tally["text_regions"] += 1
            return "text", lines
    return None


def _render_odt(path: Path, _sheets=None) -> Rendered:
    """Render ordered text blocks as heading chapters with bounded repeat expansion."""
    body, tally, expansion = _body(path, "odt"), new_tally(), _Expansion()
    chapters, heading, blocks = [], "Body", []
    explicit_heading = False
    for node in _nodes(body, TEXT_CONTAINERS):
        if node.tag == TEXT + "h":
            lines = _lines(node, tally, expansion)
            if lines:
                if blocks or explicit_heading:
                    chapters.append((heading, blocks))
                expansion.charge_characters(max(0, len(lines) - 1))
                heading, blocks = re.sub(r"\s+", " ", " ".join(lines)), []
                explicit_heading = True
        else:
            block = _block(node, tally, expansion)
            if block is not None:
                blocks.append(block)
    if blocks or explicit_heading or not chapters:
        chapters.append((heading, blocks))
    units = [(ordinal, label, blocks) for ordinal, (label, blocks) in enumerate(chapters, 1)]
    return _assemble(path, ODT_ROUTE.name, ODT_ROUTE.unit_kind, len(units), units, tally,
                     not_carried=ODT_ROUTE.not_carried, preserve_unicode=True)


def _render_odp(path: Path, _sheets=None) -> Rendered:
    """Keep direct slides and grouped blocks, using one nonempty title frame as the label."""
    body, tally, expansion = _body(path, "odp"), new_tally(), _Expansion()
    units = []
    containers = TEXT_CONTAINERS | {DRAW + "g", DRAW + "frame", DRAW + "text-box"}
    for page in body:
        if page.tag != DRAW + "page":
            continue
        label, title_frame = page.get(DRAW + "name") or "Untitled", None
        frames = [iter(page)]
        while frames and title_frame is None:
            frame = next(frames[-1], None)
            if frame is None:
                frames.pop()
            elif frame.tag == DRAW + "g":
                frames.append(iter(frame))
            elif frame.tag == DRAW + "frame" and frame.get(PRESENTATION + "class") == "title":
                lines = [line for node in _nodes(frame, containers)
                         if node.tag != TABLE + "table" for line in _lines(node, tally, expansion)]
                if lines:
                    expansion.charge_characters(max(0, len(lines) - 1))
                    label, title_frame = " ".join(lines), frame
        if title_frame is None and page.get(DRAW + "name"):
            expansion.charge_characters(len(label))
        blocks = []
        for node in _nodes(page, containers, skip=title_frame):
            block = _block(node, tally, expansion)
            if block is not None:
                blocks.append(block)
        units.append((len(units) + 1, label, blocks))
    return _assemble(path, ODP_ROUTE.name, ODP_ROUTE.unit_kind, len(units), units, tally,
                     not_carried=ODP_ROUTE.not_carried, preserve_unicode=True)


ODT_ROUTE = Route("odt", "chapter", (".odt",), _render_odt, OMISSIONS)
ODP_ROUTE = Route("odp", "slide", (".odp",), _render_odp,
                  OMISSIONS + ("masters and automatic headers and footers",))
