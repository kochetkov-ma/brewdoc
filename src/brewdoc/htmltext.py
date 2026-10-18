"""Static HTML adapter with WHATWG charset decoding and literal Unicode text."""

from __future__ import annotations

import codecs
import functools
import platform
import re
import sys
from pathlib import Path

from brewdoc.common import (BrewdocError, Rendered, Route, _ascii_integer, _assemble, _close_list_item,
                            _emphasis, _escape_markdown_text, _merge_list, _ordered_marker,
                            _source_lines, _url_target, new_tally, reading)

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
                    "legend", "summary", "form", "fieldset", "label", "details", "td", "th"})
_CONTROLS = frozenset({"button", "input", "select", "option", "optgroup", "datalist", "textarea",
                       "output", "progress", "meter"})
ARTICLE_OMISSION = (
    "body content outside the selected article or main subtree; complete page coverage is not claimed"
)
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
_TAG_NAME = re.compile(r"</?([A-Za-z][^\t\n\f\r />]*)")
_ATTRIBUTE = re.compile(r"([^\s/=<>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")
_INPUT_BYTE_LIMIT = 8 * 1048576
_DEPTH_LIMIT = 256
_ELEMENT_LIMIT = 200_000
_ATTRIBUTE_BYTE_LIMIT = 1048576
_VISIBLE_TEXT_BYTE_LIMIT = 32 * 1048576
_TABLE_CELL_LIMIT = 100_000
_MARKDOWN_BYTE_LIMIT = 64 * 1048576


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


_BOMS = {b"\xef\xbb\xbf": "utf-8", b"\xfe\xff": "utf-16be", b"\xff\xfe": "utf-16le"}
# WHATWG Encoding Standard labels; replacement and x-user-defined labels are absent, so they refuse.
_ENCODINGS = {label: (name, codec) for name, codec, labels in (
    ("UTF-8", "utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
    ("IBM866", "cp866", "866 cp866 csibm866 ibm866"),
    ("ISO-8859-2", "iso8859_2", "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 "
     "iso_8859-2:1987 l2 latin2"),
    ("ISO-8859-3", "iso8859_3", "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 "
     "iso_8859-3:1988 l3 latin3"),
    ("ISO-8859-4", "iso8859_4", "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 "
     "iso_8859-4:1988 l4 latin4"),
    ("ISO-8859-5", "iso8859_5", "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 "
     "iso_8859-5 iso_8859-5:1988"),
    ("ISO-8859-6", "iso8859_6", "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 "
     "iso-8859-6 iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987"),
    ("ISO-8859-7", "iso8859_7", "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 "
     "iso8859-7 iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek"),
    ("ISO-8859-8", "iso8859_8", "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 "
     "iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 visual"),
    ("ISO-8859-8-I", "iso8859_8", "csiso88598i iso-8859-8-i logical"),
    ("ISO-8859-10", "iso8859_10", "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6"),
    ("ISO-8859-13", "iso8859_13", "iso-8859-13 iso8859-13 iso885913"),
    ("ISO-8859-14", "iso8859_14", "iso-8859-14 iso8859-14 iso885914"),
    ("ISO-8859-15", "iso8859_15", "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9"),
    ("ISO-8859-16", "iso8859_16", "iso-8859-16"),
    ("KOI8-R", "koi8_r", "cskoi8r koi koi8 koi8-r koi8_r"),
    ("KOI8-U", "koi8_u", "koi8-ru koi8-u"),
    ("macintosh", "mac_roman", "csmacintosh mac macintosh x-mac-roman"),
    ("windows-874", "cp874", "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874"),
    ("windows-1250", "cp1250", "cp1250 windows-1250 x-cp1250"),
    ("windows-1251", "cp1251", "cp1251 windows-1251 x-cp1251"),
    ("windows-1252", "cp1252", "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 "
     "iso-ir-100 iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252"),
    ("windows-1253", "cp1253", "cp1253 windows-1253 x-cp1253"),
    ("windows-1254", "cp1254", "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
     "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254"),
    ("windows-1255", "cp1255", "cp1255 windows-1255 x-cp1255"),
    ("windows-1256", "cp1256", "cp1256 windows-1256 x-cp1256"),
    ("windows-1257", "cp1257", "cp1257 windows-1257 x-cp1257"),
    ("windows-1258", "cp1258", "cp1258 windows-1258 x-cp1258"),
    ("x-mac-cyrillic", "mac_cyrillic", "x-mac-cyrillic x-mac-ukrainian"),
    ("GBK", "gb18030", "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk"),
    ("gb18030", "gb18030", "gb18030"),
    ("Big5", "big5hkscs", "big5 big5-hkscs cn-big5 csbig5 x-x-big5"),
    ("EUC-JP", "euc_jp", "cseucpkdfmtjapanese euc-jp x-euc-jp"),
    ("ISO-2022-JP", "iso2022_jp", "csiso2022jp iso-2022-jp"),
    ("Shift_JIS", "cp932", "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis"),
    ("EUC-KR", "cp949", "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
     "ksc5601 ksc_5601 windows-949"),
    ("UTF-16BE", "utf-16-be", "unicodefffe utf-16be"),
    ("UTF-16LE", "utf-16-le", "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le"),
) for label in labels.split()}
_STRICT_CODECS = frozenset({"utf-8", "utf-16-be", "utf-16-le", "gb18030", "big5hkscs", "euc_jp",
                            "iso2022_jp", "cp932", "cp949"})


def _gb18030_euro(error: UnicodeDecodeError) -> tuple[str, int]:
    """Decode a lead byte 0x80 as U+20AC and re-read what follows, as WHATWG does; other errors refuse."""
    if error.object[error.start] != 0x80:
        raise error
    return "\u20ac", error.start + 1


codecs.register_error("brewdoc.gb18030-euro", _gb18030_euro)
# 20 two-byte codes Python's 2005 tables decode to Private Use Area, plus WHATWG's swap of
# A8 BC and 81 35 F4 37; Python decodes these 21 characters from no other bytes.
_WHATWG_GB18030 = dict(zip(
    "\ue5e5\ue78d\ue78e\ue78f\ue790\ue791\ue792\ue793\ue794\ue795\ue796\ue7c7"
    "\ue81e\ue826\ue82b\ue82c\ue832\ue843\ue854\ue864\u1e3f",
    "\u3000\ufe10\ufe12\ufe11\ufe13\ufe14\ufe15\ufe16\ufe17\ufe18\ufe19\u1e3f"
    "\u9fb4\u9fb5\u9fb6\u9fb7\u9fb8\u9fb9\u9fba\u9fbb\ue7c7"))
_GB18030_PRIVATE_USE = re.compile("[%s]" % "".join(_WHATWG_GB18030))


def _codec(label: str) -> tuple[str, str]:
    """Resolve a WHATWG label to (encoding name, Python codec); refusals never echo the label."""
    label = label.strip("\t\n\f\r ")
    if len(label) > 1 and label[0] == label[-1] and label[0] in "\"'":
        label = label[1:-1]
    if (found := _ENCODINGS.get(label.lower())) is None:
        raise BrewdocError("HTML charset declaration refused")
    return found


@functools.cache
def _single_byte_table(codec: str) -> str:
    """Map undefined bytes 0x80-0x9F to U+0080+n and higher ones to U+FFFD, like WHATWG indexes."""
    decoded = bytes(range(256)).decode(codec, "replace")
    return "".join(chr(byte) if character == "\ufffd" and 0x80 <= byte < 0xA0 else character
                   for byte, character in enumerate(decoded))


def _decode_bytes(raw: bytes, label: str | None) -> str:
    """Decode by BOM, then `label`, then strict UTF-8; single-byte encodings never fail."""
    for bom, bom_label in _BOMS.items():
        if raw.startswith(bom):
            raw, label = raw[len(bom):], bom_label
            break
    name, codec = _codec("utf-8" if label is None else label)
    if codec not in _STRICT_CODECS:
        return codecs.charmap_decode(raw, "strict", _single_byte_table(codec))[0]
    try:
        if codec == "gb18030":
            return _GB18030_PRIVATE_USE.sub(lambda match: _WHATWG_GB18030[match.group()],
                                            raw.decode(codec, "brewdoc.gb18030-euro"))
        return raw.decode(codec)
    except UnicodeDecodeError as exc:
        if codec == "utf-8":
            raise BrewdocError("HTML UTF-8 decoding failed: %s" % exc) from exc
        raise BrewdocError("HTML %s encoding refused: invalid bytes" % name) from exc


def _meta_charset(source_attributes: str) -> str | None:
    """Return one meta's declared label, "" for a valueless declaration, or None if it declares none."""
    attributes = {}
    for item in _ATTRIBUTE.finditer(source_attributes):
        value = next((value for value in item.groups()[1:] if value is not None), "")
        attributes.setdefault(item.group(1).lower(), value)
    if "charset" in attributes:
        return attributes["charset"]
    if attributes.get("http-equiv", "").strip().lower() != "content-type":
        return None
    charset = re.search(r"(?:^|;)\s*charset\b([^;]*)", attributes.get("content", ""), re.I)
    if charset is None:
        return None
    declared = re.fullmatch(r"\s*=\s*([^\s]+)\s*", charset.group(1))
    return declared.group(1) if declared else ""


def _decode_html(raw: bytes, header_charset: str | None = None) -> str:
    """Decode by BOM, then header label, then first declaring meta in 1024 bytes, then UTF-8."""
    label = header_charset
    if label is None and not raw.startswith(tuple(_BOMS)):
        label = next((declared for attributes in _meta_attributes(raw[:1024].decode("latin1"))
                      if (declared := _meta_charset(attributes)) is not None), None)
        if label is not None and _codec(label)[0] in ("UTF-16BE", "UTF-16LE"):
            label = "utf-8"
    return _decode_bytes(raw, label)


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


def _literal_links(node) -> list[str]:
    """Retain each included safe anchor inside consumed literal content in DOM order."""
    records, pending = [], [node]
    while pending:
        current = pending.pop()
        if current.tag in ("-text", "-comment") or _dropped(current):
            continue
        if current.tag == "a":
            target = _target(current.attributes.get("href"))[1]
            if target is not None and (label := _literal_text(current)):
                label = _escape_markdown_text(label, preserve_unicode=True, literal=True)
                records.append('[Link target](%s); text="%s"' % (_url_target(target), label))
        pending.extend(reversed(_children(current)))
    return records


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
                if tag == "a" and context["fallback"] and not context["text"] and context["target"] is not None:
                    flush()
                    blocks.append(("text", ["[Link target](%s)" % context["target"]], prefix))
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
                for record in _literal_links(node):
                    blocks.append(("text", [record], prefix))
                    tally["text_regions"] += 1
            continue
        if tag == "img":
            if image := _image(node):
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


def _html_content(decoded_text: str, tally: dict, *, article: bool = False) -> tuple[str, list]:
    """Render bounded decoded HTML without applying source charset declarations."""
    if not _SUPPORTED_RUNTIME:
        raise BrewdocError("HTML requires CPython 3.12-3.14")
    if LexborHTMLParser is None:
        raise BrewdocError("HTML parser unavailable: selectolax 0.4.13: %s" % _PARSER_ERROR)
    if len(decoded_text.encode("utf-8")) > _INPUT_BYTE_LIMIT:
        raise BrewdocError("HTML input byte limit exceeded: %d" % _INPUT_BYTE_LIMIT)
    parser = LexborHTMLParser(decoded_text)
    _validate_html(parser)
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


def _render_html(path: Path, _sheets=None, *, article: bool = False) -> Rendered:
    """Render one static chapter, or explicitly refuse an unavailable HTML prerequisite."""
    tally = new_tally()
    with reading(path, "HTML"):
        with path.open("rb") as source:
            raw = source.read(_INPUT_BYTE_LIMIT + 1)
        if len(raw) > _INPUT_BYTE_LIMIT:
            raise BrewdocError("HTML input byte limit exceeded: %d" % _INPUT_BYTE_LIMIT)
        title, blocks = _html_content(_decode_html(raw), tally, article=article)
    selected = article and "article_fallback" not in tally
    rendered = _assemble(path, ROUTE.name, ROUTE.unit_kind, 1, [(1, title, blocks)], tally,
                         not_carried=ROUTE.not_carried + ((ARTICLE_OMISSION,) if selected else ()),
                         preserve_unicode=True)
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


def render_html(path, *, article: bool = False) -> tuple[str, dict]:
    """Return Markdown and tally for static HTML, optionally keeping only visible main or article content."""
    return _render_html(Path(path), article=article)[:2]
