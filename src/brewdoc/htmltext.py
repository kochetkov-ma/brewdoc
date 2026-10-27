"""Static HTML adapter with WHATWG charset decoding and literal Unicode text."""

from __future__ import annotations

import codecs
import functools
import re
from pathlib import Path

from brewdoc._html_encoding import (_ASCII, _ATTRIBUTE, _BOMS, _CYRILLIC_SHARE, _ENCODINGS,
                                    _GB18030_PRIVATE_USE, _HIGH_RUNS, _INVALID_UTF8,
                                    _LATIN_PAIRED_SHARE, _STRICT_CODECS, _TAG_NAME, _WHATWG_GB18030,
                                    _codec, _decode_bytes, _decode_html, _gb18030_euro,
                                    _legacy_charset, _meta_attributes, _meta_charset, _meta_labels,
                                    _single_byte_table, _tag_end)
from brewdoc._html_runtime import LexborHTMLParser, _PARSER_ERROR, _SUPPORTED_RUNTIME
from brewdoc.common import (BrewdocError, Rendered, Route, _ascii_integer, _assemble, _close_list_item,
                            _emphasis, _escape_markdown_text, _merge_list, _ordered_marker,
                            _source_lines, _url_target, new_tally, reading)

_EXCLUDED = frozenset({"head", "script", "style", "template", "object", "embed", "iframe",
                       "audio", "video", "applet"})
_BLOCKS = frozenset({"body", "address", "article", "aside", "div", "footer", "header", "main",
                    "nav", "p", "section", "figure", "figcaption", "dl", "dt", "dd", "hr",
                    "legend", "summary", "form", "fieldset", "label", "details", "td", "th"})
_CONTROLS = frozenset({"button", "input", "select", "option", "optgroup", "datalist", "textarea",
                       "output", "progress", "meter"})
ARTICLE_OMISSION = (
    "body content outside the selected article or main subtree; complete page coverage is not claimed"
)
LINKS_OMISSION = "link destinations: omitted by --no-links"
_EMPHASIS = {"em": "*", "i": "*", "strong": "**", "b": "**"}
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BREAKS = _BLOCKS | _HEADINGS | {"ul", "ol", "li", "blockquote"}
# A header-less, caption-less table with one of these in a cell is page layout, not data.
_LAYOUT_BLOCKS = _HEADINGS | {"p", "div", "ul", "ol", "table", "blockquote", "pre"}
_SPACE = re.compile(r"[ \t\r\n\f]+")
_LANDMARKS = frozenset({"nav", "header", "footer", "aside"})
_LANDMARK_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary"})
# Concrete WAI-ARIA 1.2 roles; abstract roles are invalid in `role` and are skipped like unknown tokens.
_ARIA_ROLES = frozenset("""
    alert alertdialog application article banner blockquote button caption cell checkbox code columnheader
    combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure form
    generic grid gridcell group heading img insertion link list listbox listitem log main marquee math menu
    menubar menuitem menuitemcheckbox menuitemradio meter navigation none note option paragraph presentation
    progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider
    spinbutton status strong subscript superscript switch tab table tablist tabpanel term textbox time timer
    toolbar tooltip tree treegrid treeitem""".split())
_INPUT_BYTE_LIMIT = 8 * 1048576
_DEPTH_LIMIT = 256
_ELEMENT_LIMIT = 200_000
_ATTRIBUTE_BYTE_LIMIT = 1048576
_VISIBLE_TEXT_BYTE_LIMIT = 32 * 1048576
_TABLE_CELL_LIMIT = 100_000
_MARKDOWN_BYTE_LIMIT = 64 * 1048576


def _node_text(node) -> str:
    """Escape one text node with collapsed whitespace, keeping meaningful Unicode."""
    return _escape_markdown_text(_SPACE.sub(" ", node.text()), preserve_unicode=True)


def _hidden(node) -> bool:
    """Apply only source-declared hiding and supported inline CSS precedence."""
    attributes = node.attributes
    if (node.tag in _EXCLUDED or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").strip().lower() == "true"):
        return True
    style = attributes.get("style")
    if not style:
        return False
    effective = {}
    for declaration in style.split(";"):
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


def _dropped(node) -> bool:
    """Leave hidden subtrees and form control subtrees, values included, out of the output."""
    return node.tag in _CONTROLS or _hidden(node)


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
        excluded = excluded or _hidden(node)
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
        elif current.tag != "-comment" and not _dropped(current):
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


def _link_line(anchor) -> str | None:
    """Write a safe anchor whose text a fence consumed as one link; blank text shows the target."""
    target, literal = _target(anchor.attributes.get("href"))[1], _literal_text(anchor)
    if target is None or not literal:
        return None
    label = _SPACE.sub(" ", literal).strip() or target
    return "[%s](%s)" % (_escape_markdown_text(label, preserve_unicode=True), _url_target(target))


def _literal_links(node) -> list[str]:
    """Write each included safe anchor inside consumed literal content as a link, in DOM order."""
    links, pending = [], [node]
    while pending:
        current = pending.pop()
        if current.tag in ("-text", "-comment") or _dropped(current):
            continue
        if current.tag == "a" and (line := _link_line(current)):
            links.append(line)
        pending.extend(reversed(_children(current)))
    return links


def _image(node) -> str:
    """Render an image with alt text and an inert destination; without alt text, nothing."""
    alt = _SPACE.sub(" ", node.attributes.get("alt") or "").strip()
    if not alt:
        return ""
    target = _target(node.attributes.get("src"))[1]
    return "![%s](%s)" % (_escape_markdown_text(alt, preserve_unicode=True),
                          "" if target is None else _url_target(target))


def _ancestors(node):
    """Read declared ancestors without inferring browser state."""
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _role(node) -> str:
    """Return the first ARIA role token, case-folded; a role attribute with no known token is "other"."""
    tokens = _SPACE.split((node.attributes.get("role") or "").lower())
    return next((token for token in tokens if token in _ARIA_ROLES), "other" if any(tokens) else "")


def _holds(node, match) -> bool:
    """Report whether an included descendant element satisfies `match`."""
    pending = _children(node)
    while pending:
        current = pending.pop()
        if current.tag in ("-text", "-comment") or _dropped(current):
            continue
        if match(current):
            return True
        pending.extend(_children(current))
    return False


def _landmark_contents(node, memo: dict) -> int:
    """Flag included descendants (1: a main, 2: an `h1`); `memo` keeps visited subtrees by node `mem_id`.
    Node keys would compare serialized HTML on every hit."""
    pending = [(node, None)]
    while pending:
        current, children = pending.pop()
        if children is not None:
            flags = 0
            for child in children:
                flags |= (memo[child.mem_id] | (child.tag == "main" or _role(child) == "main")
                          | (child.tag == "h1") << 1)
            memo[current.mem_id] = flags
        elif current.mem_id not in memo:
            children = [child for child in _children(current)
                        if child.tag not in ("-text", "-comment") and not _dropped(child)]
            pending.append((current, children))
            pending.extend((child, None) for child in children)
    return memo[node.mem_id]


def _page_landmark(node, memo: dict) -> bool:
    """Recognize page chrome: a landmark outside sectioning or main content that holds no main.
    `header`/`footer` are scoped by any sectioning ancestor; a banner holding an `h1` is kept."""
    role = _role(node)
    if role not in ("", *_LANDMARK_ROLES) or not role and node.tag not in _LANDMARKS:
        return False
    scopes = ("article", "aside", "main", "nav", "section") if node.tag in ("header", "footer") else (
        "article", "main")
    if any(parent.tag in scopes or _role(parent) == "main" for parent in _ancestors(node)):
        return False
    banner = role == "banner" or not role and node.tag == "header"
    return not _landmark_contents(node, memo) & (3 if banner else 1)


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


def _context(node) -> dict:
    """Describe a link or emphasis wrapper; a missing or unsafe link target writes no brackets."""
    if node.tag in _EMPHASIS:
        marker = _EMPHASIS[node.tag]
        return {"opening": (marker, True), "closing": (marker, False), "active": False, "text": False,
                "fallback": False, "target": None}
    target = _target(node.attributes.get("href"))[1]
    target = None if target is None else _url_target(target)
    return {"opening": "" if target is None else ("[", True),
            "closing": "" if target is None else ("](%s)" % target, False),
            "active": False, "text": False, "fallback": False, "target": target}


def _open(contexts: list[dict], parts: list) -> None:
    """Open pending wrappers at the first content they enclose, so empty ones write nothing."""
    for context in contexts:
        if not context["active"]:
            parts.append(context["opening"])
            context["active"] = True
        context["text"] = True


def _cell_text(cell, tables: dict, landmarks: dict) -> str:
    """Render included cell data iteratively, keeping nested tables at their source position."""
    parts, contexts, pending = [], [], [(cell, False)]
    while pending:
        node, leaving = pending.pop()
        tag = node.tag
        if tag == "-text":
            inline = [_node_text(node)]
        elif tag == "-comment" or (not leaving and (_dropped(node) or _page_landmark(node, landmarks))):
            continue
        elif tag == "table":
            inline = [" %s " % tables[node.mem_id].pop("nested")]
        elif tag in ("code", "pre"):
            inline = ['Code: text="%s"' % _escape_markdown_text(_literal_text(node), preserve_unicode=True,
                                                               literal=True),
                      *(" " + record for record in _literal_links(node))]
        elif tag == "img":
            inline = [" %s " % image] if (image := _image(node)) else []
        else:
            inline = None
        if inline is not None:
            if "".join(inline).strip():
                _open(contexts, parts)
            parts.extend(inline)
            continue
        if tag == "br" or tag in _BREAKS:
            parts.append(" ")
        if tag in _EMPHASIS or tag == "a":
            if not leaving:
                contexts.append(_context(node))
            elif (context := contexts.pop())["active"]:
                parts.append(context["closing"])
        if not leaving:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(_children(node)))
    return _emphasis(parts).strip()


def _table_layouts(body, starts: set) -> dict:
    """Validate the complete body's table expansion before materializing any cells; note each enclosing
    table and whether a walk from `body` or a `starts` node `mem_id` crosses only layout tables to reach it."""
    layouts, pending, expanded = {}, [(body, None, True)], 0
    while pending:
        node, outer, reached = pending.pop()
        if node.tag in ("-text", "-comment") or _hidden(node):
            continue
        reached = reached or node.mem_id in starts
        if node.tag == "table":
            height, width, origins, captions = _table_layout(node)
            expanded += height * width
            if expanded > _TABLE_CELL_LIMIT:
                raise BrewdocError("HTML table cells limit exceeded: %d" % _TABLE_CELL_LIMIT)
            layout = not captions and all(cell.tag == "td" for cell, *_ in origins) and any(
                _holds(cell, lambda child: child.tag in _LAYOUT_BLOCKS) for cell, *_ in origins)
            layouts[node] = (height, width, origins, captions, outer, layout, reached)
            outer, reached = node.mem_id, reached and layout
        pending.extend((child, outer, reached) for child in reversed(_children(node)))
    return layouts


def _prepare_tables(layouts: dict, landmarks: dict) -> dict:
    """Materialize only reachable grids and the nested records their cells read, deepest table first.
    Keyed by node `mem_id` (node keys compare serialized HTML); the one cell holding a record pops it."""
    used, tables = set(), {}
    for table, (*_, outer, layout, reached) in layouts.items():
        if reached and not layout or outer in used:
            used.add(table.mem_id)
    for table, (height, width, origins, caption_nodes, outer, layout, reached) in reversed(layouts.items()):
        tables[table.mem_id] = record = {"layout": layout}
        if table.mem_id not in used:
            continue
        captions = [caption for node in caption_nodes if (caption := _cell_text(node, tables, landmarks))]
        cells = [(row, column, rowspan, colspan, _cell_text(cell, tables, landmarks))
                 for cell, row, column, rowspan, colspan in origins]
        if reached and not layout:
            record.update(rows=height, columns=width, cells=cells, captions=captions)
        if outer in used:
            records = ["Caption: " + caption for caption in captions]
            records.extend('row=%d; column=%d; rowspan=%d; colspan=%d; text="%s"'
                           % (row + 1, column + 1, rowspan, colspan, text.replace('"', "&quot;"))
                           for row, column, rowspan, colspan, text in cells)
            record["nested"] = "Nested table: " + "; ".join(records) if records else ""
    return tables


def _table_rows(table: dict) -> list[list[str]]:
    """Allocate the validated rectangular grid, with empty span continuations."""
    rows = [[""] * table["columns"] for _ in range(table["rows"])]
    for row, column, _rowspan, _colspan, text in table["cells"]:
        rows[row][column] = text
    return rows


def _html_blocks(body, tally: dict, tables: dict, landmarks: dict) -> list[tuple]:
    """Walk the recovered body in DOM order without recursive Python calls."""
    blocks, fragments, lists, scopes, contexts = [], [], [], [], []
    heading, prefix = "", ""

    def flush() -> None:
        """Emit one semantic region under the current quote and list item prefix."""
        for context in reversed(contexts):
            if context["active"]:
                fragments.append(context["closing"])
                context["active"] = False
        text = _emphasis(fragments).strip()
        fragments.clear()
        if text:
            tally["text_regions"] += 1
            blocks.append(("text", _source_lines(text, int(heading[1]) if heading else None), prefix))

    pending = [(body, False)]
    while pending:
        node, leaving = pending.pop()
        tag = node.tag
        if tag == "-text":
            text = _node_text(node)
            if text.strip():
                _open(contexts, fragments)
            fragments.append(text)
            continue
        if tag == "-comment" or (not leaving and (_dropped(node) or _page_landmark(node, landmarks))):
            continue
        if leaving:
            if tag in _EMPHASIS or tag == "a":
                context = contexts.pop()
                if context["active"]:
                    fragments.append(context["closing"])
                if tag == "a" and context["fallback"] and not context["text"] and (line := _link_line(node)):
                    flush()
                    blocks.append(("text", [line], prefix))
                    tally["text_regions"] += 1
            elif tag in ("li", "blockquote"):
                flush()
                prefix, marker, first = scopes.pop()
                if tag == "li":
                    _close_list_item(blocks, marker, first, prefix)
                else:
                    blocks.append(("end", None, prefix))
            elif tag in ("ul", "ol"):
                flush()
                _merge_list(blocks, lists.pop()[3], scopes[-1][2] if scopes else 0)
            elif tag in _HEADINGS:
                flush()
                heading = ""
            elif tag in _BLOCKS:
                flush()
            continue
        if tag == "table" and not (table := tables[node.mem_id])["layout"]:
            flush()
            for caption in table["captions"]:
                blocks.append(("text", ["Caption: " + caption], prefix))
                tally["text_regions"] += 1
            if table["columns"]:
                blocks.append(("table", _table_rows(table), prefix))
                tally["tables"] += 1
            continue
        if tag in ("pre", "code"):
            flush()
            literal = _literal_text(node)
            if literal:
                blocks.append(("block", ["```", *literal.split("\n"), "```"], prefix))
                tally["text_regions"] += 1
                for context in contexts:
                    context["fallback"] = True
                for line in _literal_links(node):
                    blocks.append(("text", [line], prefix))
                    tally["text_regions"] += 1
            continue
        if tag == "img":
            # An image opening a safe link is its content; after link text it stays a block, as in EPUB.
            if (image := _image(node)) and any(context["target"] and not context["text"] for context in contexts):
                _open(contexts, fragments)
                fragments.append(" %s " % image)
            elif image:
                flush()
                blocks.append(("text", [image], prefix))
                tally["text_regions"] += 1
                for context in contexts:
                    context["fallback"] = True
            continue
        if tag == "br":
            fragments.append("\n")
            continue
        if tag in _BREAKS:
            flush()
        if tag in ("ul", "ol"):
            step = -1 if "reversed" in node.attributes else 1
            items = 1 if step == 1 else sum(
                child.tag == "li" and not (_dropped(child) or _page_landmark(child, landmarks))
                for child in _children(node))
            lists.append([tag, _ascii_integer(node.attributes.get("start"), items), step, len(blocks)])
        elif tag == "li":
            ordered = lists and lists[-1][0] == "ol"
            if ordered:
                lists[-1][1] = _ascii_integer(node.attributes.get("value"), lists[-1][1])
            marker = _ordered_marker(lists[-1][1]) if ordered else "- "
            if lists:
                lists[-1][1] += lists[-1][2]
            scopes.append((prefix, marker, len(blocks)))
            prefix += " " * len(marker)
        elif tag == "blockquote":
            scopes.append((prefix, "", len(blocks)))
            prefix += "> "
        elif tag in _HEADINGS:
            heading = tag
        elif tag in _EMPHASIS or tag == "a":
            contexts.append(_context(node))
        pending.append((node, True))
        pending.extend((child, False) for child in reversed(_children(node)))
    flush()
    return blocks


def _article_roots(body, landmarks: dict) -> list:
    """Select visible top-level mains outside articles, else visible top-level articles."""
    mains, articles, pending = [], [], [body]
    while pending:
        node = pending.pop()
        if node.tag in ("-text", "-comment") or _hidden(node) or _page_landmark(node, landmarks):
            continue
        if node.tag == "main" or _role(node) == "main":
            mains.append(node)
        elif node.tag == "article":
            articles.append(node)
        else:
            pending.extend(reversed(_children(node)))
    return mains or articles


def _html_content(decoded_text: str, tally: dict, *, article: bool = False,
                  links: bool = True) -> tuple[str, list]:
    """Render bounded decoded HTML without applying source charset declarations; no links drops each href."""
    if not _SUPPORTED_RUNTIME:
        raise BrewdocError("HTML requires CPython 3.12-3.14")
    if LexborHTMLParser is None:
        raise BrewdocError("HTML parser unavailable: selectolax 0.4.13: %s" % _PARSER_ERROR)
    if len(decoded_text.encode("utf-8")) > _INPUT_BYTE_LIMIT:
        raise BrewdocError("HTML input byte limit exceeded: %d" % _INPUT_BYTE_LIMIT)
    parser = LexborHTMLParser(decoded_text)
    _validate_html(parser)
    if not links:
        for anchor in parser.css("a[href]"):
            del anchor.attrs["href"]
    body = parser.body
    landmarks = {}
    visible = not any(_hidden(parent) for parent in _ancestors(body))
    roots = _article_roots(body, landmarks) if article and visible else []
    tables = _prepare_tables(_table_layouts(body, {root.mem_id for root in roots}), landmarks)
    title = next((label for node in parser.head.css("title")
                  if (label := _SPACE.sub(" ", node.text()).strip())), "Untitled")
    counts = tally["text_regions"], tally["tables"]
    blocks = [block for root in roots for block in _html_blocks(root, tally, tables, landmarks)]
    if article and counts == (tally["text_regions"], tally["tables"]):
        tally["article_fallback"] = True
        blocks = []
    if visible and not blocks:
        blocks = _html_blocks(body, tally, tables, landmarks)
    return title, blocks


def _render_html(path: Path, _sheets=None, *, article: bool = False, links: bool = True) -> Rendered:
    """Render one static chapter, or explicitly refuse an unavailable HTML prerequisite."""
    tally = new_tally()
    with reading(path, "HTML"):
        with path.open("rb") as source:
            raw = source.read(_INPUT_BYTE_LIMIT + 1)
        if len(raw) > _INPUT_BYTE_LIMIT:
            raise BrewdocError("HTML input byte limit exceeded: %d" % _INPUT_BYTE_LIMIT)
        text, charset, source = _decode_html(raw)
        if source == "fallback":
            tally["charset_fallback"] = charset
        title, blocks = _html_content(text, tally, article=article, links=links)
    selected = article and "article_fallback" not in tally
    omissions = ((ARTICLE_OMISSION,) if selected else ()) + (() if links else (LINKS_OMISSION,))
    rendered = _assemble(path, ROUTE.name, ROUTE.unit_kind, 1, [(1, title, blocks)], tally,
                         not_carried=ROUTE.not_carried + omissions, preserve_unicode=True)
    if len(rendered[0].encode("utf-8")) > _MARKDOWN_BYTE_LIMIT:
        raise BrewdocError("HTML Markdown byte limit exceeded: %d" % _MARKDOWN_BYTE_LIMIT)
    return rendered


ROUTE = Route("html", "chapter", (".html",), _render_html,
              ("dynamic browser content, scripts and stylesheets",
               "graphical SVG content, canvas pixels and MathML semantics",
               "active media and embeds",
               "inline code layout outside tables: rendered as fenced blocks",
               "inline code monospace inside table cells",
               "form controls and their values",
               "images without alt text",
               "navigation, header, footer and aside landmarks outside main or article content"))


def render_html(path, *, article: bool = False, links: bool = True) -> tuple[str, dict]:
    """Return Markdown and tally for static HTML; options keep only main or article content or drop links."""
    return _render_html(Path(path), article=article, links=links)[:2]
