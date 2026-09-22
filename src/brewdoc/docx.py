"""DOCX adapter: OPC main part paragraphs and tables in reading order, stdlib only."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from brewdoc.common import (BrewdocError, Rendered, Route, _assemble, _opc_main_part, _xml_part,
                            new_tally, reading, sanitise)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W_STRICT = "{http://purl.oclc.org/ooxml/wordprocessingml/main}"
W_NAMESPACES = (W, W_STRICT)
DOC_BODY = "Body"                     # the chapter of a document that declares no headings
HEADING_STYLE_RE = re.compile(r"(?:Heading[1-9]|Title)")


def _word_tag(element, local_name: str) -> bool:
    return any(element.tag == namespace + local_name for namespace in W_NAMESPACES)


def _word_child(element, local_name: str):
    return next((child for child in element if _word_tag(child, local_name)), None)


def _word_value(element) -> str | None:
    return next((element.get(namespace + "val") for namespace in W_NAMESPACES
                 if element.get(namespace + "val") is not None), None)


def _doc_lines(node, tally: dict) -> list[str]:
    # Breaks and tabs are siblings of text; tracked text and references use other tags.
    lines, current = [], []
    for element in node.iter():
        if _word_tag(element, "t"):
            current.append(element.text or "")
        elif _word_tag(element, "tab"):
            current.append(" ")
        elif _word_tag(element, "br") or _word_tag(element, "cr"):
            lines.append("".join(current))
            current = []
    lines.append("".join(current))
    return [text for text in (sanitise(line, tally).strip() for line in lines) if text]


def _doc_label(node) -> str:
    """Read a heading label without folding semantic Unicode before Markdown escaping."""
    parts = []
    for element in node.iter():
        if _word_tag(element, "t"):
            parts.append(element.text or "")
        elif (_word_tag(element, "tab") or _word_tag(element, "br")
              or _word_tag(element, "cr")):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip() or "Untitled"


def _doc_style(node) -> str:
    properties = _word_child(node, "pPr")
    style = _word_child(properties, "pStyle") if properties is not None else None
    return (_word_value(style) or "") if style is not None else ""


def _doc_span(cell) -> int:
    properties = _word_child(cell, "tcPr")
    span = _word_child(properties, "gridSpan") if properties is not None else None
    try:
        return max(1, int(_word_value(span))) if span is not None else 1
    except (TypeError, ValueError):
        return 1


def _doc_rows(table, tally: dict) -> list[list[str]]:
    rows = []
    for row in table:
        if not _word_tag(row, "tr"):
            continue
        cells = []
        for cell in row:
            if not _word_tag(cell, "tc"):
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
        body = _word_child(_xml_part(package, part), "body")
    if body is None:
        raise BrewdocError("no document body: %s carries no <w:body> in %s" % (path, part))
    chapters, heading, blocks = [], DOC_BODY, []
    for node in body:
        if _word_tag(node, "tbl"):
            rows = _doc_rows(node, tally)
            if rows:
                tally["tables"] += 1
                blocks.append(("table", rows))
        elif _word_tag(node, "p"):
            lines = _doc_lines(node, tally)
            if not lines:
                continue
            if HEADING_STYLE_RE.fullmatch(_doc_style(node)):
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
