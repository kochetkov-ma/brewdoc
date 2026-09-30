"""Static HTML adapter with strict UTF-8 input and literal Unicode text."""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from urllib.parse import quote

from brewdoc.common import (MARKDOWN_ESCAPES, BrewdocError, Rendered, Route, _assemble,
                            new_tally, reading)

_SUPPORTED_RUNTIME = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
_PARSER_ERROR = ""
LexborHTMLParser = None
if _SUPPORTED_RUNTIME:
    try:
        from selectolax.lexbor import LexborHTMLParser
    except (ImportError, OSError, RuntimeError, ValueError, SystemError) as exc:
        _PARSER_ERROR = str(exc)

_EXCLUDED = frozenset({"head", "script", "style", "template", "object", "embed", "iframe",
                       "audio", "video", "applet"})
_BLOCKS = frozenset({"body", "address", "article", "aside", "div", "footer", "header", "main",
                    "nav", "p", "section", "figure", "figcaption", "dl", "dt", "dd", "hr",
                    "legend", "summary"})
_CONTROL_BOUNDARIES = frozenset({"form", "fieldset", "label", "select", "optgroup", "datalist",
                                "progress", "meter", "details"})
_CONTROL_TAGS = _CONTROL_BOUNDARIES | frozenset({"button", "input", "textarea", "output", "option"})
_EMPHASIS = {"em": "*", "i": "*", "strong": "**", "b": "**"}
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SPACE = re.compile(r"[ \t\r\n\f]+")
_TAG_NAME = re.compile(r"</?([A-Za-z][^\t\n\f\r />]*)")
_ATTRIBUTE = re.compile(r"([^\s/=<>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")
_INPUT_BYTE_LIMIT = 8 * 1048576
_DEPTH_LIMIT = 256
_ELEMENT_LIMIT = 200_000
_ATTRIBUTE_BYTE_LIMIT = 1048576
_VISIBLE_TEXT_BYTE_LIMIT = 32 * 1048576
_TABLE_CELL_LIMIT = 100_000
_MARKDOWN_BYTE_LIMIT = 64 * 1048576
_LITERAL_ESCAPES = MARKDOWN_ESCAPES | {ord("\n"): "&#10;", ord("\t"): "&#9;", ord("\r"): "&#13;"}


def _tag_end(prefix: str, start: int) -> int | None:
    """Find a complete tag boundary without treating quoted attribute text as markup."""
    quote_character = ""
    for index in range(start, len(prefix)):
        character = prefix[index]
        if quote_character:
            if character == quote_character:
                quote_character = ""
        elif character in "\"'":
            quote_character = character
        elif character == ">":
            return index
    return None


def _meta_attributes(prefix: str):
    """Yield real meta start tags within the prefix, excluding comments and raw text."""
    position = 0
    while (start := prefix.find("<", position)) >= 0:
        if prefix.startswith("<!--", start):
            empty = re.match(r"<!---?>", prefix[start:])
            closing = re.search(r"--!?>", prefix[start + 4:])
            if empty:
                position = start + empty.end()
            elif closing:
                position = start + 4 + closing.end()
            else:
                return
            continue
        match = _TAG_NAME.match(prefix, start)
        if match is None and not prefix.startswith(("<!", "<?"), start):
            position = start + 1
            continue
        end = _tag_end(prefix, start + 1)
        if end is None:
            return
        position = end + 1
        if match is None or prefix.startswith("</", start):
            continue
        name = match.group(1).lower()
        if name == "meta":
            yield prefix[match.end():end]
        elif name == "plaintext":
            return
        elif name in ("script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title"):
            closing = re.search(r"</%s(?=[\t\n\f\r />])" % name, prefix[position:], re.I)
            if closing is None:
                return
            end = _tag_end(prefix, position + closing.end())
            if end is None:
                return
            position = end + 1


def _decode_html(raw: bytes) -> str:
    """Reject declared non-UTF-8 encodings before strict decoding, preserving raw hashes."""
    payload = raw.removeprefix(b"\xef\xbb\xbf")
    for source_attributes in _meta_attributes(payload[:1024].decode("latin1")):
        attributes = [(item.group(1).lower(), next((value for value in item.groups()[1:]
                                                   if value is not None), None))
                      for item in _ATTRIBUTE.finditer(source_attributes)]
        declarations = [value for key, value in attributes if key == "charset"]
        if any(key == "http-equiv" and (value or "").strip().lower() == "content-type"
               for key, value in attributes):
            for key, value in attributes:
                if key != "content":
                    continue
                for charset in re.finditer(r"(?:^|;)\s*charset\b([^;]*)", value or "", re.I):
                    declared = re.fullmatch(r"\s*=\s*([^\s]+)\s*", charset.group(1))
                    declarations.append(declared.group(1).strip("\"'") if declared else None)
        if any(value is None or value.strip().lower() not in ("utf-8", "utf8")
               for value in declarations):
            raise BrewdocError("HTML charset must declare UTF-8 or UTF8")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrewdocError("HTML UTF-8 decoding failed: %s" % exc) from exc


def _escape(text: str) -> str:
    """Escape source syntax without folding or replacing meaningful Unicode."""
    return text.translate(MARKDOWN_ESCAPES)


def _close_emphasis(parts: list[str], marker: str) -> None:
    """Keep trailing word separation outside a generated emphasis delimiter."""
    whitespace = []
    while parts:
        trimmed = parts[-1].rstrip()
        if len(trimmed) == len(parts[-1]):
            break
        whitespace.append(parts[-1][len(trimmed):])
        if trimmed:
            parts[-1] = trimmed
            break
        parts.pop()
    parts.append(marker)
    parts.extend(reversed(whitespace))


def _hidden(node) -> bool:
    """Apply only source-declared hiding and supported inline CSS precedence."""
    attributes = node.attributes
    if (node.tag in _EXCLUDED or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").strip().lower() == "true"):
        return True
    effective = {}
    for declaration in (attributes.get("style") or "").split(";"):
        name, separator, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip().lower()
        if not separator or name not in ("display", "visibility"):
            continue
        important = re.search(r"!\s*important\s*$", value) is not None
        value = re.sub(r"!\s*important\s*$", "", value).strip()
        supported = ({"none", "block", "inline", "inline-block", "flex", "grid", "table",
                      "table-row", "table-cell", "list-item"} if name == "display"
                     else {"hidden", "visible", "collapse"})
        if value not in supported:
            continue
        if name not in effective or important or not effective[name][1]:
            effective[name] = (value, important)
    return (effective.get("display", ("", False))[0] == "none"
            or effective.get("visibility", ("", False))[0] == "hidden")


def _children(node) -> list:
    """Read direct children in recovered source order, including text nodes."""
    children, child = [], node.child
    while child is not None:
        children.append(child)
        child = child.next
    return children


def _validate_html(parser) -> None:
    """Check recovered structure and decoded text iteratively, including excluded elements."""
    elements = visible_bytes = 0
    pending = [(parser.root, 0, False)]
    while pending:
        node, depth, excluded = pending.pop()
        tag = node.tag
        if tag == "-text":
            if depth and not excluded:
                visible_bytes += len(node.text().encode("utf-8"))
                if visible_bytes > _VISIBLE_TEXT_BYTE_LIMIT:
                    raise BrewdocError("HTML visible text byte limit exceeded: %d" % _VISIBLE_TEXT_BYTE_LIMIT)
            continue
        if tag.startswith("-"):
            continue
        elements += 1
        if elements > _ELEMENT_LIMIT:
            raise BrewdocError("HTML element limit exceeded: %d" % _ELEMENT_LIMIT)
        attributes = node.attributes
        if any(len((value or "").encode("utf-8")) > _ATTRIBUTE_BYTE_LIMIT for value in attributes.values()):
            raise BrewdocError("HTML attribute byte limit exceeded: %d" % _ATTRIBUTE_BYTE_LIMIT)
        if tag == "body":
            depth = 1
        elif depth:
            depth += 1
        if depth > _DEPTH_LIMIT:
            raise BrewdocError("HTML depth limit exceeded: %d" % _DEPTH_LIMIT)
        excluded = excluded or _hidden(node) or (tag == "input" and (
            attributes.get("type") or "").strip().lower() == "hidden")
        pending.extend((child, depth, excluded) for child in reversed(_children(node)))


def _literal_text(node) -> str:
    """Collect included descendant text iteratively without whitespace reduction."""
    parts, pending = [], [node]
    while pending:
        current = pending.pop()
        if current.tag == "-text":
            parts.append(current.text())
        elif current.tag == "br":
            parts.append("\n")
        elif current.tag != "-comment" and not _hidden(current):
            pending.extend(reversed(_children(current)))
    return "".join(parts)


def _target(source: str | None) -> tuple[str, str | None]:
    """Classify a decoded destination without fetching or activating it."""
    if not source:
        return "missing", None
    cleaned = re.sub(r"[\x00-\x20\x7f]", "", source)
    scheme = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", cleaned)
    if scheme is None:
        return "relative", source
    name = scheme.group(1).lower()
    if name == "data":
        return "data", None
    if name not in ("http", "https", "mailto"):
        return "unsupported", None
    return "external", source


def _literal_links(node) -> list[str]:
    """Retain each included safe anchor inside consumed literal content in DOM order."""
    records, pending = [], [node]
    while pending:
        current = pending.pop()
        if current.tag in ("-text", "-comment") or _hidden(current):
            continue
        if current.tag == "a":
            target = _target(current.attributes.get("href"))[1]
            if target is not None and (label := _literal_text(current)):
                records.append('[Link target](%s); text="%s"' % (
                    quote(target, safe="/:#?&=@%+;,"), _escape_literal(label)))
        pending.extend(reversed(_children(current)))
    return records


def _image(node) -> str:
    """Emit source-declared image metadata with an inert destination."""
    attributes = node.attributes
    kind, target = _target(attributes.get("src"))
    return 'Image: alt="%s"; title="%s"; source=%s; target="%s"' % (
        _escape(attributes.get("alt") or "none"), _escape(attributes.get("title") or "none"),
        kind, _escape(target) if target is not None else "omitted")


def _escape_literal(text: str) -> str:
    """Preserve literal whitespace in quoted metadata without activating source entities."""
    return text.translate(_LITERAL_ESCAPES)


def _quoted(attributes: dict, name: str) -> str:
    """Distinguish absent strings from supplied empty strings in source records."""
    return '"%s"' % _escape_literal(attributes[name] or "") if name in attributes else "unspecified"


def _declared(attributes: dict, name: str) -> str:
    """Record boolean attribute presence without interpreting its supplied value."""
    return "true" if name in attributes else "false"


def _ancestors(node):
    """Read declared ancestors without inferring browser state."""
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _control(node, leaving: bool = False) -> tuple[list[str], bool]:
    """Return fixed source records and whether those records consume descendant text."""
    tag, attributes = node.tag, node.attributes
    if tag == "label":
        if leaving and ("id" in attributes or "for" in attributes):
            return ["Label: id=%s; for=%s" % (_quoted(attributes, "id"), _quoted(attributes, "for"))], False
        return [], False
    if leaving:
        return [], False
    role_button = (attributes.get("role") or "").strip().lower() == "button"
    if tag not in _CONTROL_TAGS and not role_button:
        return [], False
    kind = (attributes.get("type") or "").strip().lower()
    if tag == "input" and kind == "hidden":
        return [], True
    sensitive = tag == "input" and kind in ("password", "file")
    declared_value = '"redacted"' if sensitive and "value" in attributes else _quoted(attributes, "value")
    disabled = "true" if any(parent.tag == "fieldset" and "disabled" in parent.attributes
                              for parent in _ancestors(node)) else "false"
    fields, consumes = [], False
    if tag == "button" or role_button or (
            tag == "input" and kind in ("button", "submit", "reset")):
        if tag == "input":
            visible = "" if sensitive else attributes.get("value") or ""
        else:
            visible = _SPACE.sub(" ", _literal_text(node)).strip()
        label = visible or attributes.get("aria-label") or attributes.get("title") or "Unlabelled"
        fields = [("label", '"%s"' % _escape_literal(label)),
                  ("aria_label", _quoted(attributes, "aria-label"))]
        fields.extend((name, _quoted(attributes, name)) for name in ("title", "role", "type", "id", "name"))
        fields.append(("value", declared_value))
        fields.extend((("declared_disabled", _declared(attributes, "disabled")),
                       ("aria_disabled", _quoted(attributes, "aria-disabled")), ("fieldset_disabled", disabled)))
        fields.extend((name, _quoted(attributes, name))
                      for name in ("formaction", "formmethod", "formenctype", "formtarget"))
        fields.append(("formnovalidate", _declared(attributes, "formnovalidate")))
        label, consumes = "Button", True
    elif tag == "input":
        fields = [(name, _quoted(attributes, name)) for name in ("type", "id", "name")]
        fields.append(("value", declared_value))
        if kind in ("checkbox", "radio"):
            fields.append(("declared_checked", _declared(attributes, "checked")))
            fields.append(("required", _declared(attributes, "required")))
            fields.extend((("declared_disabled", _declared(attributes, "disabled")), ("fieldset_disabled", disabled)))
        else:
            fields.append(("placeholder", _quoted(attributes, "placeholder")))
            fields.extend((name, _declared(attributes, name)) for name in ("readonly", "required"))
            fields.extend((("declared_disabled", _declared(attributes, "disabled")), ("fieldset_disabled", disabled)))
            fields.extend((name, _quoted(attributes, name)) for name in (
                "min", "max", "step", "pattern", "minlength", "maxlength", "size"))
            fields.append(("multiple", _declared(attributes, "multiple")))
            fields.extend((name, _quoted(attributes, name)) for name in ("accept", "autocomplete", "list"))
            if kind == "image":
                fields.extend((name, _quoted(attributes, name))
                              for name in ("formaction", "formmethod", "formenctype", "formtarget"))
                fields.append(("formnovalidate", _declared(attributes, "formnovalidate")))
        label, consumes = "Input", True
    elif tag == "textarea":
        fields = [(name, _quoted(attributes, name)) for name in ("id", "name", "placeholder")]
        fields.extend((name, _declared(attributes, name)) for name in ("readonly", "required"))
        fields.extend((("declared_disabled", _declared(attributes, "disabled")), ("fieldset_disabled", disabled),
                       ("text", '"%s"' % _escape_literal(_literal_text(node)))))
        label, consumes = "Textarea", True
    elif tag == "output":
        fields = [(name, _quoted(attributes, name)) for name in ("id", "name", "for")]
        fields.append(("text", '"%s"' % _escape_literal(_literal_text(node))))
        label, consumes = "Output", True
    elif tag == "option":
        group = next((parent for parent in _ancestors(node) if parent.tag == "optgroup"), None)
        fields = [("text", '"%s"' % _escape_literal(_SPACE.sub(" ", _literal_text(node)).strip())),
                  ("value", _quoted(attributes, "value")),
                  ("declared_selected", _declared(attributes, "selected")),
                  ("declared_disabled", _declared(attributes, "disabled")),
                  ("group", _quoted(group.attributes, "label") if group is not None else "unspecified")]
        label, consumes = "Option", True
    elif tag == "select":
        fields = [(name, _quoted(attributes, name)) for name in ("id", "name")]
        fields.extend((name, _declared(attributes, name)) for name in ("multiple", "required"))
        fields.extend((("declared_disabled", _declared(attributes, "disabled")), ("fieldset_disabled", disabled)))
        label = "Select"
    elif tag == "fieldset":
        fields = [(name, _quoted(attributes, name)) for name in ("id", "name")]
        fields.append(("declared_disabled", _declared(attributes, "disabled")))
        label = "Fieldset"
    elif tag == "optgroup":
        fields = [("label", _quoted(attributes, "label")), ("declared_disabled", _declared(attributes, "disabled"))]
        label = "Optgroup"
    elif tag == "details":
        fields = [("id", _quoted(attributes, "id")), ("declared_open", _declared(attributes, "open"))]
        label = "Details"
    elif tag in ("form", "datalist", "progress", "meter"):
        names = {"form": ("action", "method", "id", "name"), "datalist": ("id",),
                 "progress": ("id", "value", "max"),
                 "meter": ("id", "value", "min", "max", "low", "high", "optimum")}[tag]
        fields = [(name, _quoted(attributes, name)) for name in names]
        label = tag.title()
    else:
        return [], False
    records = [label + ": " + "; ".join("%s=%s" % pair for pair in fields)]
    if tag == "input" and kind == "image":
        records.append(_image(node))
    return records, consumes


def _span(node, name: str, remaining_rows: int) -> int:
    """Read declared positive spans; zero rowspan covers the rest of its row group."""
    source = (node.attributes.get(name) or "").strip()
    if re.fullmatch(r"\+?[0-9]+", source) is None:
        return 1
    digits = source.lstrip("+0") or "0"
    if len(digits) > len(str(_TABLE_CELL_LIMIT)) or int(digits) > _TABLE_CELL_LIMIT:
        raise BrewdocError("HTML table cells limit exceeded: %d" % _TABLE_CELL_LIMIT)
    value = int(digits)
    return value or (remaining_rows if name == "rowspan" else 1)


def _table_layout(table) -> tuple[int, int, list[tuple], list]:
    """Plan origin coordinates and spans without allocating an expanded cell grid."""
    groups, captions = [], []
    for child in _children(table):
        if child.tag == "-text" or _hidden(child):
            continue
        if child.tag == "caption":
            captions.append(child)
        elif child.tag == "tr":
            groups.append([child])
        elif child.tag in ("thead", "tbody", "tfoot"):
            groups.append([row for row in _children(child) if row.tag == "tr" and not _hidden(row)])
    origins, active = [], []
    row_index, height, width = 0, sum(map(len, groups)), 0
    for group in groups:
        for group_row, row in enumerate(group):
            active = [span for span in active if span[0] > row_index]
            occupied = sorted((start, end) for _end_row, start, end in active)
            column = occupied_index = 0
            for cell in _children(row):
                if cell.tag not in ("td", "th") or _hidden(cell):
                    continue
                rowspan = _span(cell, "rowspan", len(group) - group_row)
                colspan = _span(cell, "colspan", len(group) - group_row)
                while occupied_index < len(occupied):
                    start, end = occupied[occupied_index]
                    if column + colspan <= start:
                        break
                    if column < end:
                        column = end
                    occupied_index += 1
                height, width = max(height, row_index + rowspan), max(width, column + colspan)
                if height * width > _TABLE_CELL_LIMIT:
                    raise BrewdocError("HTML table cells limit exceeded: %d" % _TABLE_CELL_LIMIT)
                origins.append((cell, row_index, column, rowspan, colspan))
                if rowspan > 1:
                    active.append((row_index + rowspan, column, column + colspan))
                column += colspan
            row_index += 1
    return height, width, origins, captions


def _cell_text(cell, tables: dict) -> str:
    """Render included cell data iteratively, keeping nested tables at their source position."""
    parts, links, emphasis, pending = [], [], [], [(cell, False)]
    while pending:
        node, leaving = pending.pop()
        tag = node.tag
        if tag == "-text":
            text = _escape(_SPACE.sub(" ", node.text()))
            first = next((frame for frame in emphasis if frame["first"]), None)
            if first is not None:
                trimmed = text.lstrip()
                leading = text[:len(text) - len(trimmed)]
                if leading:
                    index = first["start"]
                    parts.insert(index, leading)
                    for frame in emphasis:
                        if frame["start"] >= index:
                            frame["start"] += 1
                    text = trimmed
                if text:
                    for frame in emphasis:
                        frame["first"] = False
            parts.append(text)
            continue
        if tag == "-comment" or (not leaving and _hidden(node)):
            continue
        records, consumes = _control(node, leaving)
        if records:
            parts.extend(" " + record + " " for record in records)
        if consumes:
            continue
        if tag == "table":
            parts.append(" " + tables[node]["nested"] + " ")
            continue
        if tag in ("code", "pre"):
            parts.append('Code: text="%s"' % _escape_literal(_literal_text(node)))
            parts.extend(" " + record for record in _literal_links(node))
            continue
        if tag == "img":
            parts.append(" " + _image(node) + " ")
            continue
        if tag == "br" or tag in _BLOCKS or tag in _CONTROL_BOUNDARIES:
            parts.append(" ")
        if tag in _EMPHASIS and _literal_text(node).strip():
            if leaving:
                emphasis.pop()
                _close_emphasis(parts, _EMPHASIS[tag])
            else:
                emphasis.append({"start": len(parts), "first": True})
                parts.append(_EMPHASIS[tag])
        if tag == "a":
            if leaving:
                target = links.pop()
                if target is not None:
                    parts.append("](%s)" % quote(target, safe="/:#?&=@%+;,"))
            else:
                target = _target(node.attributes.get("href"))[1]
                links.append(target)
                if target is not None:
                    parts.append("[")
        if not leaving:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(_children(node)))
    return "".join(parts).strip()


def _prepare_tables(body) -> dict:
    """Validate total expansion first, then build nested records from the deepest table outward."""
    layouts, pending, expanded = {}, [body], 0
    while pending:
        node = pending.pop()
        if node.tag in ("-text", "-comment") or _hidden(node):
            continue
        if node.tag == "table":
            layout = _table_layout(node)
            expanded += layout[0] * layout[1]
            if expanded > _TABLE_CELL_LIMIT:
                raise BrewdocError("HTML table cells limit exceeded: %d" % _TABLE_CELL_LIMIT)
            layouts[node] = layout
        pending.extend(reversed(_children(node)))
    tables = {}
    for table, (height, width, origins, caption_nodes) in reversed(layouts.items()):
        captions = [caption for node in caption_nodes if (caption := _cell_text(node, tables))]
        cells = [(row, column, rowspan, colspan, _cell_text(cell, tables))
                 for cell, row, column, rowspan, colspan in origins]
        records = ["Caption: " + caption for caption in captions]
        records.extend('row=%d; column=%d; rowspan=%d; colspan=%d; text="%s"'
                       % (row + 1, column + 1, rowspan, colspan, text.replace('"', "&quot;"))
                       for row, column, rowspan, colspan, text in cells)
        tables[table] = {"rows": height, "columns": width, "cells": cells, "captions": captions,
                         "nested": "Nested table: " + "; ".join(records) if records else ""}
    return tables


def _table_rows(table: dict) -> list[list[str]]:
    """Allocate the validated rectangular grid, with empty span continuations."""
    rows = [[""] * table["columns"] for _ in range(table["rows"])]
    for row, column, _rowspan, _colspan, text in table["cells"]:
        rows[row][column] = text
    return rows


def _scoped_items(body, tables: dict) -> set:
    """Find items and ancestors whose standalone child blocks need explicit boundaries."""
    scoped, ancestors, pending = set(), [], [(body, False)]
    while pending:
        node, leaving = pending.pop()
        tag = node.tag
        if tag in ("-text", "-comment") or (not leaving and _hidden(node)):
            continue
        if leaving:
            if _control(node, True)[0]:
                scoped.update(ancestors)
            if tag == "li":
                ancestors.pop()
            continue
        if tag == "li":
            ancestors.append(node)
        records, consumes = _control(node)
        standalone = bool(records) or tag == "img"
        if tag in ("pre", "code"):
            standalone = bool(_literal_text(node))
        elif tag == "blockquote":
            standalone = bool(_literal_text(node).strip())
        elif tag == "table":
            standalone = bool(tables[node]["columns"] or tables[node]["captions"])
        if standalone:
            scoped.update(ancestors)
        if consumes or tag in ("pre", "code", "table", "img"):
            continue
        pending.append((node, True))
        pending.extend((child, False) for child in reversed(_children(node)))
    return scoped


def _html_blocks(body, tally: dict) -> list[tuple]:
    """Walk the recovered body in DOM order without recursive Python calls."""
    blocks, fragments, lists, items, contexts, quotes = [], [], [], [], [], []
    heading, last_list = "", False
    tables = _prepare_tables(body)
    scoped = _scoped_items(body, tables)

    def append_text(text: str) -> None:
        """Open generated contexts only when a segment contains meaningful source text."""
        if text.strip():
            if any(context["emphasis"] and not context["active"] for context in contexts):
                trimmed = text.lstrip()
                fragments.append(text[:len(text) - len(trimmed)])
                text = trimmed
            for context in contexts:
                if not context["active"]:
                    fragments.append(context["opening"])
                    context["active"] = True
                context["text"] = True
        fragments.append(text)

    def flush() -> None:
        """Emit one semantic region, preserving list grouping and source-derived context."""
        nonlocal last_list
        for context in reversed(contexts):
            if context["active"]:
                if context["emphasis"]:
                    _close_emphasis(fragments, context["closing"])
                else:
                    fragments.append(context["closing"])
                context["active"] = False
        text = "".join(fragments).strip()
        fragments.clear()
        if not text:
            return
        lines = text.split("\n")
        if heading:
            lines[0] = ("#" * (int(heading[1]) + 2) + " " + lines[0] if heading < "h5"
                        else "**Heading %s: %s**" % (heading[1], lines[0]))
        plain = bool(items) and not items[-1]["scoped"]
        first = plain and not items[-1]["emitted"]
        if plain:
            prefix = items[-1]["prefix"]
            continuation = " " * len(prefix)
            lines = [(prefix if first else continuation) + lines[0],
                     *(continuation + line for line in lines[1:])]
            items[-1]["emitted"] = True
        tally["text_regions"] += 1
        if first and last_list:
            blocks[-1][1].extend(lines)
        else:
            blocks.append(("text", lines))
        last_list = plain

    pending = [(body, False)]
    while pending:
        node, leaving = pending.pop()
        tag = node.tag
        if tag == "-text":
            append_text(_escape(_SPACE.sub(" ", node.text())))
            continue
        if tag == "-comment" or (not leaving and _hidden(node)):
            continue
        if leaving:
            if tag in _CONTROL_BOUNDARIES:
                flush()
                records, _consumes = _control(node, True)
                for record in records:
                    blocks.append(("text", [record]))
                    tally["text_regions"] += 1
                last_list = False
            elif tag in _EMPHASIS or tag == "a":
                context = contexts.pop()
                if context["active"]:
                    if context["emphasis"]:
                        _close_emphasis(fragments, context["closing"])
                    else:
                        fragments.append(context["closing"])
                if tag == "a" and context["fallback"] and not context["text"] and context["target"] is not None:
                    flush()
                    blocks.append(("text", ["[Link target](%s)" % context["target"]]))
                    tally["text_regions"] += 1
                    last_list = False
            elif tag == "li":
                flush()
                item = items.pop()
                if item["scoped"]:
                    if len(blocks) == item["start"] + 1:
                        del blocks[item["start"]:]
                    else:
                        blocks.append(("text", ["End list item (level %d)." % item["level"]]))
                    last_list = False
            elif tag in ("ul", "ol"):
                flush()
                lists.pop()
            elif tag == "blockquote":
                flush()
                start = quotes.pop()
                if len(blocks) == start + 1:
                    del blocks[start:]
                else:
                    blocks.append(("text", ["End quote (level %d)." % (len(quotes) + 1)]))
                last_list = False
            elif tag in _HEADINGS:
                flush()
                heading = ""
            elif tag in _BLOCKS:
                flush()
            continue
        records, consumes = _control(node)
        if consumes and not records:
            continue
        if records or tag in _CONTROL_BOUNDARIES:
            flush()
            for record in records:
                blocks.append(("text", [record]))
                tally["text_regions"] += 1
            last_list = False
        if consumes:
            continue
        if tag == "table":
            flush()
            table = tables[node]
            for caption in table["captions"]:
                blocks.append(("text", ["Caption: " + caption]))
                tally["text_regions"] += 1
            if table["columns"]:
                blocks.append(("table", _table_rows(table)))
                tally["tables"] += 1
            last_list = False
            continue
        if tag in ("pre", "code"):
            flush()
            literal = _literal_text(node)
            if literal:
                blocks.append(("block", ["```", *literal.split("\n"), "```"]))
                tally["text_regions"] += 1
                last_list = False
                for context in contexts:
                    context["fallback"] = True
                for record in _literal_links(node):
                    blocks.append(("text", [record]))
                    tally["text_regions"] += 1
            continue
        if tag == "img":
            flush()
            blocks.append(("text", [_image(node)]))
            tally["text_regions"] += 1
            last_list = False
            for context in contexts:
                context["fallback"] = True
            continue
        if tag == "br":
            append_text("\n")
            continue
        if tag in _BLOCKS or tag in ("ul", "ol", "li", "blockquote") or tag in _HEADINGS:
            flush()
        if tag in ("ul", "ol"):
            try:
                start = int(node.attributes.get("start") or "1")
            except ValueError:
                start = 1
            lists.append([tag, start])
        elif tag == "li":
            marker = "%d." % lists[-1][1] if lists and lists[-1][0] == "ol" else "-"
            if lists:
                lists[-1][1] += 1
            level = max(1, len(lists))
            base = next((item["level"] for item in reversed(items) if item["scoped"]), 0)
            item = {"level": level, "scoped": node in scoped, "start": len(blocks), "emitted": False,
                    "prefix": "    " * max(0, level - base - 1) + marker + " "}
            items.append(item)
            if item["scoped"]:
                blocks.append(("text", ['List item (level %d, marker "%s"):' % (level, marker)]))
                last_list = False
        elif tag == "blockquote":
            quotes.append(len(blocks))
            blocks.append(("text", ["Quote (level %d):" % len(quotes)]))
            last_list = False
        elif tag in _HEADINGS:
            heading = tag
        elif tag in _EMPHASIS or tag == "a":
            target = _target(node.attributes.get("href"))[1] if tag == "a" else None
            target = quote(target, safe="/:#?&=@%+;,") if target is not None else None
            opening = "[" if target is not None else _EMPHASIS.get(tag, "")
            closing = "](%s)" % target if target is not None else opening
            contexts.append({"opening": opening, "closing": closing, "active": False,
                             "text": False, "fallback": False, "target": target, "emphasis": tag in _EMPHASIS})
        pending.append((node, True))
        pending.extend((child, False) for child in reversed(_children(node)))
    flush()
    return blocks


def _render_html(path: Path, _sheets=None) -> Rendered:
    """Render one static chapter, or explicitly refuse an unavailable HTML prerequisite."""
    if not _SUPPORTED_RUNTIME:
        raise BrewdocError("HTML requires CPython 3.12-3.14")
    if LexborHTMLParser is None:
        raise BrewdocError("HTML parser unavailable: selectolax 0.4.13: %s" % _PARSER_ERROR)
    tally = new_tally()
    with reading(path, "HTML"):
        with path.open("rb") as source:
            raw = source.read(_INPUT_BYTE_LIMIT + 1)
        if len(raw) > _INPUT_BYTE_LIMIT:
            raise BrewdocError("HTML input byte limit exceeded: %d" % _INPUT_BYTE_LIMIT)
        parser = LexborHTMLParser(_decode_html(raw))
        _validate_html(parser)
        title = next((label for node in parser.head.css("title")
                      if (label := _SPACE.sub(" ", node.text()).strip())), "Untitled")
        blocks = _html_blocks(parser.body, tally)
    rendered = _assemble(path, ROUTE.name, ROUTE.unit_kind, 1, [(1, title, blocks)], tally,
                         not_carried=ROUTE.not_carried)
    if len(rendered[0].encode("utf-8")) > _MARKDOWN_BYTE_LIMIT:
        raise BrewdocError("HTML Markdown byte limit exceeded: %d" % _MARKDOWN_BYTE_LIMIT)
    return rendered


ROUTE = Route("html", "chapter", (".html",), _render_html,
              ("dynamic browser content, scripts and stylesheets",
               "graphical SVG content, canvas pixels and MathML semantics",
               "active media and embeds",
               "inline code layout outside tables: rendered as fenced blocks",
               "inline code monospace inside table cells",
               "hidden inputs and supplied password or file values"))


def render_html(path) -> tuple[str, dict]:
    """Return Markdown and tally for one UTF-8 static HTML chapter."""
    return _render_html(Path(path))[:2]
