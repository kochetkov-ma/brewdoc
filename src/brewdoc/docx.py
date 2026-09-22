"""DOCX adapter: OPC main part paragraphs and tables in reading order, stdlib only."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from brewdoc.common import (BrewdocError, Rendered, Route, _assemble, _opc_main_part, _xml_part,
                            new_tally, reading, sanitise)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOC_BODY = "Body"                     # the chapter of a document that declares no headings
HEADING_STYLE_RE = re.compile(r"^(?:Heading\d|Title)$")


def _doc_lines(node, tally: dict) -> list[str]:
    # `w:br` and `w:tab` are siblings of the `w:t` runs; `w:delText`, `w:instrText`, footnotes
    # and comments carry other tags and are never picked up.
    lines, current = [], []
    for element in node.iter():
        if element.tag == W + "t":
            current.append(element.text or "")
        elif element.tag == W + "tab":
            current.append(" ")
        elif element.tag == W + "br":
            lines.append("".join(current))
            current = []
    lines.append("".join(current))
    return [text for text in (sanitise(line, tally).strip() for line in lines) if text]


def _doc_label(node) -> str:
    """Read a heading label without folding semantic Unicode before Markdown escaping."""
    parts = []
    for element in node.iter():
        if element.tag == W + "t":
            parts.append(element.text or "")
        elif element.tag in (W + "tab", W + "br"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip() or "Untitled"


def _doc_style(node) -> str:
    style = node.find("%spPr/%spStyle" % (W, W))
    return style.get(W + "val", "") if style is not None else ""


def _doc_span(cell) -> int:
    span = cell.find("%stcPr/%sgridSpan" % (W, W))
    try:
        return max(1, int(span.get(W + "val"))) if span is not None else 1
    except ValueError:
        return 1


def _doc_rows(table, tally: dict) -> list[list[str]]:
    rows = []
    for row in table:
        if row.tag != W + "tr":
            continue
        cells = []
        for cell in row:
            if cell.tag != W + "tc":
                continue
            cells.append(" ".join(_doc_lines(cell, tally)))
            cells += [""] * (_doc_span(cell) - 1)
        if any(cell for cell in cells):
            rows.append(cells)
    return rows


def _render_doc(path: Path, _sheets=None) -> Rendered:
    tally = new_tally()
    with reading(path, "document"), zipfile.ZipFile(path) as package:
        part = _opc_main_part(package)
        body = _xml_part(package, part).find(W + "body")
    if body is None:
        raise BrewdocError("no document body: %s carries no <w:body> in %s" % (path, part))
    chapters, heading, blocks = [], DOC_BODY, []
    for node in body:
        if node.tag == W + "tbl":
            rows = _doc_rows(node, tally)
            if rows:
                tally["tables"] += 1
                blocks.append(("table", rows))
        elif node.tag == W + "p":
            lines = _doc_lines(node, tally)
            if not lines:
                continue
            if HEADING_STYLE_RE.match(_doc_style(node)):
                if blocks or heading != DOC_BODY:
                    chapters.append((heading, blocks))
                heading, blocks = _doc_label(node), []
            else:
                tally["text_regions"] += 1
                blocks.append(("text", lines))
    if blocks or heading != DOC_BODY or not chapters:
        chapters.append((heading, blocks))
    units = [(ordinal, heading, chapter_blocks)
             for ordinal, (heading, chapter_blocks) in enumerate(chapters, 1)]
    return _assemble(path, ROUTE.name, ROUTE.unit_kind, len(units), units, tally,
                     not_carried=ROUTE.not_carried)


ROUTE = Route("doc", "chapter", (".docx",), _render_doc,
              ("images, charts and the text drawn inside them",
               "tracked changes, comments, footnotes, headers and footers",
               "a table's own formatting - only its cells, row by row"))


def render_doc(path) -> tuple[str, dict]:
    """(Markdown, tally) for a .docx: one anchored chapter per Word heading, else `Body`."""
    return _render_doc(Path(path))[:2]
