"""Shared layer: error type, tally, ASCII normalization and schema 2 Markdown assembly."""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat

MARKDOWN_SCHEMA = "brewdoc.markdown/2"
# Receipt versions on its own: its key set moves when a format lands, the Markdown schema does not.
RECEIPT_SCHEMA = "brewdoc.receipt/1"

ZIP_XML_LIMITS = {
    "members": 4_096,
    "member_bytes": 33_554_432,
    "consumed_bytes": 268_435_456,
    "document_elements": 2_097_152,
    "total_elements": 4_194_304,
    "depth": 16_384,
    "document_characters": 33_554_432,
    "total_characters": 67_108_864,
}

CID_RE = re.compile(r"\(cid:\d+\)")
FOLD = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "′": "'", "″": '"',
    "•": "-", "‣": "-", "▪": "-", "■": "-", "●": "-", "·": "-",
    "…": "...", "≤": "<=", "≥": ">=", "≠": "!=", "±": "+/-",
    "×": "x", "÷": "/", "°": " deg", "®": "(R)", "©": "(C)",
    "™": "(TM)", "€": "EUR", "£": "GBP", "²": "2", "³": "3",
    "½": "1/2", "¼": "1/4", "¾": "3/4", "→": "->", "←": "<-",
    # Shade blocks are the only content of a spreadsheet gantt bar; `?` would erase it.
    "░": "#", "▒": "#", "▓": "#", "█": "#",
    " ": " ", " ": " ", " ": " ", " ": " ", "﻿": "",
}
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "ﬅ": "st", "ﬆ": "st"}
# A broken ToUnicode CMap seen in the wild maps `ff` to U+0161 inside an otherwise ASCII word.
BROKEN_LIGATURE = "š"
BROKEN_WORD_RE = re.compile(r"[A-Za-z]+%s[A-Za-z]*|[A-Za-z]*%s[A-Za-z]+"
                            % (BROKEN_LIGATURE, BROKEN_LIGATURE))

DROP_KEYS = ("control_chars", "soft_hyphens", "nbsp", "pua_glyphs", "cid_survivors",
             "ligatures", "running_heads", "page_numbers", "non_ascii_replaced")

DIGITS_RE = re.compile(r"\d+")


class BrewdocError(Exception):
    """A refusal naming what was not found or not parsed, and where."""


@dataclass(slots=True)
class _ZipBudget:
    """Count selected ZIP/XML reads against limits fixed for one operation."""

    limits: dict[str, int] = field(default_factory=lambda: ZIP_XML_LIMITS.copy())
    consumed_bytes: int = 0
    total_elements: int = 0
    total_characters: int = 0
    format_name: str = "OOXML"

    def check(self, key: str, value: int, part: str) -> None:
        """Refuse an overrun without exposing counters in successful output."""
        if value > self.limits[key]:
            raise BrewdocError("%s %s limit exceeded: %s: %d"
                               % (self.format_name, key, part, self.limits[key]))

    def charge(self, key: str, amount: int, part: str) -> None:
        """Charge physical rereads and repeated XML parses to the operation."""
        value = getattr(self, key) + amount
        self.check(key, value, part)
        setattr(self, key, value)


@contextlib.contextmanager
def reading(path: Path, noun: str) -> Iterator[None]:
    """Every parser error becomes one `<noun> unreadable` refusal; a BrewdocError passes through."""
    try:
        yield
    except BrewdocError:
        raise
    except Exception as exc:                      # each reader raises its own error types
        raise BrewdocError("%s unreadable: %s: %s" % (noun, path, exc)) from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_zip_members(package: zipfile.ZipFile, budget: _ZipBudget) -> None:
    """Bound archive entries before Brewdoc performs member searches."""
    if len(package.infolist()) > budget.limits["members"]:
        raise BrewdocError("%s members limit exceeded: %d"
                           % (budget.format_name, budget.limits["members"]))


def _zip_member_chunks(package: zipfile.ZipFile, part: str | zipfile.ZipInfo,
                       budget: _ZipBudget, *, max_bytes: int | None = None,
                       limit_error: str | None = None) -> Iterator[bytes]:
    """Yield bounded accepted chunks, charging every read and exhausting CRC checks."""
    _check_zip_members(package, budget)
    try:
        info = package.getinfo(part) if isinstance(part, str) else part
    except KeyError as exc:
        raise BrewdocError("%s part is missing: %s" % (budget.format_name, part)) from exc
    name = info.filename
    if max_bytes is not None and info.file_size > max_bytes:
        raise BrewdocError(limit_error)
    budget.check("member_bytes", info.file_size, name)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise BrewdocError("%s ZIP compression is unsupported: %s: %d"
                           % (budget.format_name, name, info.compress_type))
    size = 0
    member_limit = budget.limits["member_bytes"]
    if max_bytes is not None:
        member_limit = min(member_limit, max_bytes)
    with package.open(info) as stream:
        while True:
            chunk = stream.read(min(65_536, member_limit - size + 1,
                                    budget.limits["consumed_bytes"] - budget.consumed_bytes + 1))
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise BrewdocError(limit_error)
            budget.check("member_bytes", size, name)
            budget.charge("consumed_bytes", len(chunk), name)
            yield chunk


def _zip_member_bytes(package: zipfile.ZipFile, part: str | zipfile.ZipInfo,
                      budget: _ZipBudget, *, max_bytes: int | None = None,
                      limit_error: str | None = None) -> bytes:
    """Retain only a member admitted by its actual-byte and operation limits."""
    return b"".join(_zip_member_chunks(package, part, budget,
                                     max_bytes=max_bytes, limit_error=limit_error))


def _zip_member_size(package: zipfile.ZipFile, part: str | zipfile.ZipInfo,
                     budget: _ZipBudget) -> int:
    """Count a member's returned bytes without retaining its payload."""
    return sum(len(chunk) for chunk in _zip_member_chunks(package, part, budget))


def _xml_preflight(data: bytes, part: str, budget: _ZipBudget) -> None:
    """Bound expanded XML content before allocating its ElementTree."""
    parser = expat.ParserCreate(namespace_separator="}")
    depth, elements, characters = 0, 0, 0

    def text(value):
        nonlocal characters
        characters += len(value)
        budget.check("document_characters", characters, part)
        budget.charge("total_characters", len(value), part)

    def start(_tag, attributes):
        nonlocal depth, elements
        depth += 1
        elements += 1
        budget.check("depth", depth, part)
        budget.check("document_elements", elements, part)
        budget.charge("total_elements", 1, part)
        for value in attributes.values():
            text(value)

    def end(_tag):
        nonlocal depth
        depth -= 1

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = text
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        error = ET.ParseError(exc)
        error.code, error.position = exc.code, (exc.lineno, exc.offset)
        raise error from exc


def _xml_part(package: zipfile.ZipFile, part: str,
              budget: _ZipBudget | None = None) -> ET.Element:
    """Read one bounded XML part and preserve missing or malformed diagnostics."""
    if budget is None:
        budget = _ZipBudget()
    data = _zip_member_bytes(package, part, budget)
    try:
        _xml_preflight(data, part, budget)
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise BrewdocError("cannot parse %s part %s: %s"
                           % (budget.format_name, part, exc)) from exc


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


def _relationship_elements(package: zipfile.ZipFile, part: str,
                           budget: _ZipBudget | None = None) -> tuple[ET.Element, ...]:
    """Return every relationship element in document order, duplicate ids and types included."""
    root = _xml_part(package, part, budget)
    return tuple(element for element in root.iter()
                 if _local_name(element.tag) == "Relationship")


def _relationships(package: zipfile.ZipFile, part: str,
                   budget: _ZipBudget | None = None) -> dict[str, ET.Element]:
    """Index relationships by id, retaining the existing last-id-wins lookup."""
    return {element.attrib["Id"]: element for element in _relationship_elements(package, part, budget)
            if "Id" in element.attrib}


def _relationship_part(part: str) -> str:
    """Return the OPC relationship part paired with one package part."""
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def _opc_main_part(package: zipfile.ZipFile, budget: _ZipBudget | None = None) -> str:
    """Find a package's main part through its package-level office relationship."""
    for relationship in _relationships(package, "_rels/.rels", budget).values():
        if (relationship.attrib.get("Type", "").endswith("/officeDocument")
                and relationship.attrib.get("TargetMode") != "External"):
            return _resolve_ooxml_part("", relationship.attrib.get("Target", ""))
    raise BrewdocError("OOXML office document relationship is missing")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A retrievable workbook artifact described without retaining its payload."""

    key: str
    kind: str
    availability: str
    count: int | None
    location: str
    media_type: str
    byte_size: int | None = None
    sha256: str | None = None

    def to_dict(self, out=None) -> dict:
        """Return the stable receipt fields for this artifact."""
        return {"availability": self.availability, "byte_size": self.byte_size,
                "count": self.count, "key": self.key, "kind": self.kind,
                "location": self.location, "media_type": self.media_type,
                "out": str(out) if out is not None else None, "sha256": self.sha256}


# The only mapping from a route's unit kind to its tally counter; `_assemble` derives the count.
UNIT_COUNTERS = {"page": "pages", "sheet": "sheets", "chapter": "chapters",
                 "slide": "slides"}


def new_tally() -> dict:
    """An empty render tally: counts of units, tables, regions and every dropped class."""
    return {**dict.fromkeys(UNIT_COUNTERS.values(), 0), "tables": 0, "text_regions": 0,
            "columns_split": 0, "broken_ligature_words": 0,
            "dropped": dict.fromkeys(DROP_KEYS, 0)}


def sha256(text: str) -> str:
    """Hex sha256 of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitise(text: str, tally: dict, keep_layout: bool = False, *,
             preserve_unicode: bool = False) -> str:
    """Count named cleanup losses; fold to ASCII unless Unicode preservation is requested."""
    drop = tally["dropped"]
    tally["broken_ligature_words"] += len(BROKEN_WORD_RE.findall(text))
    drop["cid_survivors"] += len(CID_RE.findall(text))
    text = CID_RE.sub("", text)
    if not text:
        return text
    out = []
    for ch in text:
        if ch in LIGATURES:
            drop["ligatures"] += 1
            out.append(LIGATURES[ch])
            continue
        if ch == "­":
            drop["soft_hyphens"] += 1
            continue
        if ch == " ":
            drop["nbsp"] += 1
            out.append(" ")
            continue
        if not preserve_unicode and ch in FOLD:
            out.append(FOLD[ch])
            continue
        if ch in "\n\t" or " " <= ch <= "~":
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category == "Co":                       # Symbol/Wingdings bullets live in the PUA
            drop["pua_glyphs"] += 1
            out.append("-")
            continue
        if category in ("Cc", "Cf", "Zl", "Zp"):
            drop["control_chars"] += 1
            continue
        if preserve_unicode:
            out.append(ch)
            continue
        folded = "".join(c for c in unicodedata.normalize("NFKD", ch)
                         if not unicodedata.combining(c))
        if folded.isascii() and folded.strip():
            out.append(folded)
            continue
        drop["non_ascii_replaced"] += 1
        out.append("?")
    text = "".join(out)
    if not keep_layout:
        text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.rstrip() for line in text.split("\n"))


def cell_text(value, *, preserve_unicode: bool = False) -> str:
    """Keep float representations, lowercase booleans and ISO date days; None is empty.
    Keep time and duration strings; stringify other values, replace LF with spaces and trim outer whitespace."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()[:10]
    if isinstance(value, (datetime.time, datetime.timedelta)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value).replace("\n", " ").strip(" \t\r\n" if preserve_unicode else None)


def markdown_table(rows, *, preserve_unicode: bool = False,
                   max_markdown_bytes: int | None = None) -> list[str]:
    """Rows -> a Markdown table, first row as the header; ragged rows are padded, never cut."""
    # The only place a cell's pipe is escaped, so no caller can escape it twice.
    width = max((len(row) for row in rows), default=0)
    if not width:
        return []
    out = []
    size = 0
    _check_markdown_size(3 * width + 1, max_markdown_bytes)
    for index, row in enumerate(rows):
        cells = []
        row_size = 3 * width + 1
        for value in row:
            text = cell_text(value, preserve_unicode=preserve_unicode)
            if max_markdown_bytes is not None:
                row_size += _markdown_bytes(text) + text.count("|")
            _check_markdown_size(size + row_size + bool(out), max_markdown_bytes)
            cells.append(text.replace("|", "\\|"))
        _check_markdown_size(size + row_size + bool(out), max_markdown_bytes)
        cells += [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
        size += row_size + (len(out) > 1)
        if index == 0:
            _check_markdown_size(size + 6 * width + 2, max_markdown_bytes)
            out.append("| " + " | ".join(["---"] * width) + " |")
            size += 6 * width + 2
    return out


def head_key(text: str) -> str:
    """A running head's identity without its folio (`Page 31 of 232` never repeats verbatim)."""
    return DIGITS_RE.sub("#", text)


def _drop_furniture(lines: list[str], heads: set[str], numbers: set[str],
                    tally: dict) -> list[str]:
    """Drop page numbers before running heads; increment each removed line's loss counter."""
    kept = []
    for line in lines:
        text = line.strip()
        if text in numbers:
            tally["dropped"]["page_numbers"] += 1
        elif head_key(text) in heads:
            tally["dropped"]["running_heads"] += 1
        else:
            kept.append(line)
    return kept


# Punctuation gets a backslash, HTML specials and controls an entity; non-ASCII follows in encode.
# No pipe here: outside a table it is literal, and inside one `markdown_table` escapes it.
MARKDOWN_ESCAPES = str.maketrans({
    **{character: "\\" + character for character in "\\`*_{}[]()#+-.!"},
    **{chr(code): "&#%d;" % code for code in (*range(32), 127)},
    "\r": " ", "\n": " ", "\t": " ", "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def _markdown_encoding(route: str) -> str:
    """Unicode routes use UTF-8; other adapters retain strict ASCII."""
    return "utf-8" if route in ("html", "epub", "sheet", "text", "eml", "odt", "odp") else "ascii"


def _markdown_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _check_markdown_size(size: int, maximum: int | None) -> None:
    """Refuse prospective generated output before allocating its expanded form."""
    if maximum is not None and size > maximum:
        raise BrewdocError("Markdown byte limit exceeded: maximum %d" % maximum)


def _remaining_markdown_bytes(maximum: int | None, used: int) -> int | None:
    """Reserve existing output and fixed framing before expanding the next fragment."""
    if maximum is None:
        return None
    _check_markdown_size(used, maximum)
    return maximum - used


def _join_markdown_lines(lines: list[str], maximum: int | None) -> str:
    """Check the normalized trailing newline before joining complete output."""
    if maximum is None:
        return "\n".join(lines).rstrip("\n") + "\n"
    lines = list(lines)
    while lines and not lines[-1].rstrip("\n"):
        lines.pop()
    if lines:
        lines[-1] = lines[-1].rstrip("\n")
    size = sum(_markdown_bytes(line) for line in lines) + max(0, len(lines) - 1) + 1
    _check_markdown_size(size, maximum)
    return "\n".join(lines) + "\n"


def _escape_markdown_text(text: str, *, preserve_unicode: bool = False,
                          max_markdown_bytes: int | None = None) -> str:
    """Escape syntax; preserve Unicode only when the caller opts in."""
    text = str(text)
    if max_markdown_bytes is not None:
        size = 0
        for character in text:
            escaped = MARKDOWN_ESCAPES.get(ord(character), character)
            size += (_markdown_bytes(escaped) if preserve_unicode else
                     len(escaped.encode("ascii", "xmlcharrefreplace")))
            _check_markdown_size(size, max_markdown_bytes)
    escaped = text.translate(MARKDOWN_ESCAPES)
    return escaped if preserve_unicode else escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _unit_key(kind: str, ordinal: int) -> str:
    return "%s/%06d" % (kind, ordinal)


def _unit_anchor(key: str) -> str:
    return "brewdoc-" + key.replace("/", "-")


def _unit_heading(kind: str, ordinal: int, label: str, *, preserve_unicode: bool = False,
                  max_markdown_bytes: int | None = None) -> str:
    if kind == "page":
        _remaining_markdown_bytes(max_markdown_bytes, len(str(ordinal)) + 5)
        return "Page %d" % ordinal
    title = _escape_markdown_text(label, preserve_unicode=preserve_unicode,
                                  max_markdown_bytes=_remaining_markdown_bytes(
                                      max_markdown_bytes, len(kind) + len(str(ordinal)) + 5))
    return "%s %d: \"%s\"" % (kind.title(), ordinal, title)


def _escape_source_html(line: str, maximum: int | None = None) -> str:
    """Keep every source HTML-like fragment visible instead of active."""
    if maximum is not None:
        _check_markdown_size(_markdown_bytes(line) + 3 * (line.count("<") + line.count(">")), maximum)
    return line.replace("<", "&lt;").replace(">", "&gt;")


def _literal_fence(code: list[str], *, max_markdown_bytes: int | None = None) -> list[str]:
    """Fence source code verbatim; the fence outgrows every backtick run so none can close it."""
    longest = max((match.end() - match.start() for line in code
                   for match in re.finditer("`+", line)), default=0)
    width = max(3, longest + 1)
    if max_markdown_bytes is not None:
        _check_markdown_size(2 * width + sum(_markdown_bytes(line) for line in code)
                             + len(code) + 1, max_markdown_bytes)
    fence = "`" * width
    return [fence, *code, fence]


def _render_units(kind: str, units: list[tuple[int, str, list[tuple]]],
                  heads: set[str], numbers: set[str], tally: dict, *,
                  preserve_unicode: bool = False,
                  max_markdown_bytes: int | None = None) -> tuple[str, tuple[str, ...]]:
    """Render anchored content units before metadata so all loss tallies are final."""
    out = []
    keys = []
    size = 0
    bounded = max_markdown_bytes is not None
    for ordinal, label, blocks in units:
        key = _unit_key(kind, ordinal)
        keys.append(key)
        anchor = '<a id="%s"></a>' % _unit_anchor(key)
        heading = [anchor,
                   "## " + _unit_heading(kind, ordinal, label, preserve_unicode=preserve_unicode,
                                         max_markdown_bytes=_remaining_markdown_bytes(
                                             max_markdown_bytes, size + len(anchor) + 6)), ""]
        size += sum(_markdown_bytes(line) + 1 for line in heading) if bounded else 0
        out += heading
        anchors = set()
        for block_kind, payload in blocks:
            if block_kind == "anchor":
                if (not isinstance(payload, str)
                        or re.fullmatch(r"brewdoc-chapter-(?!000000)[0-9]{6}-fragment-"
                                        r"(?!000000)[0-9]{6}", payload) is None
                        or not payload.startswith(_unit_anchor(key) + "-fragment-")
                        or payload in anchors):
                    raise BrewdocError("invalid or duplicate generated fragment anchor: %r" % payload)
                anchors.add(payload)
                _remaining_markdown_bytes(max_markdown_bytes, size + len(payload) + 14)
                rendered = ['<a id="%s"></a>' % payload]
            elif block_kind == "table":
                rendered = markdown_table(payload, preserve_unicode=preserve_unicode,
                                           max_markdown_bytes=_remaining_markdown_bytes(
                                               max_markdown_bytes, size + 2))
            elif block_kind == "math":
                rendered = payload
            else:
                rendered = _drop_furniture(payload, heads, numbers, tally)
                if block_kind == "block" and len(rendered) <= 2:
                    rendered = [line for line in rendered if line != "```"]
            if rendered and block_kind in ("block", "math"):
                rendered = _literal_fence(rendered[1:-1], max_markdown_bytes=_remaining_markdown_bytes(
                    max_markdown_bytes, size + 2)) + [""]
                size += sum(_markdown_bytes(line) + 1 for line in rendered) if bounded else 0
                out += rendered
            elif rendered and block_kind == "anchor":
                size += sum(_markdown_bytes(line) + 1 for line in rendered) if bounded else 0
                out += rendered
            elif rendered:
                for line in rendered:
                    escaped = _escape_source_html(line, _remaining_markdown_bytes(
                        max_markdown_bytes, size + 2))
                    size += _markdown_bytes(escaped) + 1 if bounded else 0
                    out.append(escaped)
                size += 1 if bounded else 0
                out.append("")
    body = _join_markdown_lines(out, max_markdown_bytes)
    return body, tuple(keys)


# Every private renderer returns (Markdown, tally, unit keys, artifact references).
Rendered = tuple[str, dict, tuple[str, ...], tuple[ArtifactRef, ...]]


@dataclass(frozen=True, slots=True)
class Route:
    """One format route's contract, folded into the service's static suffix table."""

    name: str
    unit_kind: str
    suffixes: tuple[str, ...]
    render: Callable[..., Rendered] | None
    not_carried: tuple[str, ...]
    markdown_schema: str = MARKDOWN_SCHEMA


CAPABILITY_FIELDS = ("Formula capability", "Selected formula count", "VBA project capability",
                     "VBA project count", "VBA source module capability", "VBA source module count")
NOT_APPLICABLE = tuple((field, "not applicable") for field in CAPABILITY_FIELDS)


def _metadata_rows(path: Path, route: str, unit_kind: str, source_units: int,
                   unit_keys: tuple[str, ...], body: str, tally: dict, *,
                   preserve_unicode: bool = False,
                   max_markdown_bytes: int | None = None) -> list[tuple[str, str]]:
    """Hash raw source and route-encoded body separately with final unit and loss counts."""
    source, suffix = path.read_bytes(), path.suffix.lower()
    body_bytes = body.encode(_markdown_encoding(route))
    rows = [
        ("Markdown schema", MARKDOWN_SCHEMA),
        ("Source name", '"%s"' % _escape_markdown_text(
            path.name, preserve_unicode=preserve_unicode,
            max_markdown_bytes=_remaining_markdown_bytes(max_markdown_bytes, 2))),
        ("Source suffix", suffix[:1] + _escape_markdown_text(
            suffix[1:], preserve_unicode=preserve_unicode,
            max_markdown_bytes=_remaining_markdown_bytes(max_markdown_bytes, 1))),
        ("Source bytes", str(len(source))),
        ("Source SHA-256", hashlib.sha256(source).hexdigest()),
        ("Route", route),
        ("Unit kind", unit_kind),
        ("Source unit count", str(source_units)),
        ("Rendered unit count", str(len(unit_keys))),
        ("Ordered selection keys", ", ".join(unit_keys) or "none"),
        ("Rendered body bytes", str(len(body_bytes))),
        ("Rendered body SHA-256", hashlib.sha256(body_bytes).hexdigest()),
        ("Pages", str(tally["pages"])),
        ("Sheets", str(tally["sheets"])),
        ("Chapters", str(tally["chapters"])),
        ("Tables", str(tally["tables"])),
        ("Text regions", str(tally["text_regions"])),
        ("Column splits", str(tally["columns_split"])),
        ("Broken ligature words", str(tally["broken_ligature_words"])),
    ]
    return rows + [("Dropped " + key.replace("_", " "), str(tally["dropped"][key]))
                   for key in DROP_KEYS]


def _artifacts_markdown(artifacts: tuple[ArtifactRef, ...], *,
                        max_markdown_bytes: int | None = None) -> list[str]:
    """Describe artifacts in supplied order without reading payloads; mark an empty inventory explicitly.
    Keep unknown counts distinct from byte metadata that does not apply."""
    if not artifacts:
        return ["None."]
    rows = [["Key", "Kind", "Availability", "Count", "Location", "Media type", "Bytes",
             "SHA-256"]]
    for artifact in artifacts:
        label = "metadata" if artifact.kind == "vba-project" else artifact.key.split("/", 1)[1]
        location = "[%s](%s)" % (label, artifact.location)
        rows.append([artifact.key, artifact.kind, artifact.availability,
                     str(artifact.count) if artifact.count is not None else "unknown",
                     location, artifact.media_type,
                     str(artifact.byte_size) if artifact.byte_size is not None else "not applicable",
                     artifact.sha256 or "not applicable"])
    return markdown_table(rows, max_markdown_bytes=max_markdown_bytes)


def _assemble(path: Path, route: str, unit_kind: str, source_units: int,
              units: list[tuple[int, str, list[tuple]]], tally: dict, *,
              not_carried: tuple[str, ...], heads=frozenset(), numbers=frozenset(),
              capabilities: tuple = NOT_APPLICABLE,
              artifacts: tuple[ArtifactRef, ...] = (), preserve_unicode: bool = False,
              publication_rows: tuple = (), publication_navigation: tuple = (),
              max_markdown_bytes: int | None = None) -> Rendered:
    """Build schema 2 once content and loss counters are final; every route returns this shape."""
    if unit_kind not in UNIT_COUNTERS:
        raise BrewdocError("unknown unit kind %r; expected one of: %s"
                           % (unit_kind, ", ".join(UNIT_COUNTERS)))
    body, unit_keys = _render_units(unit_kind, units, heads, numbers, tally,
                                    preserve_unicode=preserve_unicode,
                                    max_markdown_bytes=max_markdown_bytes)
    tally[UNIT_COUNTERS[unit_kind]] = len(unit_keys)
    bounded = max_markdown_bytes is not None and bool(units)
    envelope_maximum = max_markdown_bytes if bounded else None
    size = _markdown_bytes(body) + 1 if bounded else 0
    prefix = ['# ""', "", '<a id="brewdoc-metadata"></a>', "## Metadata", ""]
    framing = sum(len(line) + 1 for line in prefix)
    name = _escape_markdown_text(path.name, preserve_unicode=preserve_unicode,
                                 max_markdown_bytes=_remaining_markdown_bytes(
                                     envelope_maximum, size + framing))
    prefix[0] = '# "%s"' % name
    size += framing + _markdown_bytes(name) if bounded else 0
    out = prefix
    metadata = _metadata_rows(path, route, unit_kind, source_units, unit_keys, body, tally,
                              preserve_unicode=preserve_unicode,
                              max_markdown_bytes=_remaining_markdown_bytes(envelope_maximum, size))
    table = markdown_table([["Field", "Value"], *metadata, *capabilities, *publication_rows],
                            preserve_unicode=preserve_unicode,
                            max_markdown_bytes=_remaining_markdown_bytes(envelope_maximum, size + 1))
    size += sum(_markdown_bytes(line) + 1 for line in table) if bounded else 0
    out += table
    section = ["", "## Artifacts", ""]
    framing = sum(len(line) + 1 for line in section)
    artifact_lines = _artifacts_markdown(artifacts, max_markdown_bytes=_remaining_markdown_bytes(
        envelope_maximum, size + framing + 1))
    size += framing + sum(_markdown_bytes(line) + 1 for line in artifact_lines) if bounded else 0
    out += section + artifact_lines
    section = ["", "## Known omissions", ""]
    size += sum(len(line) + 1 for line in section) if bounded else 0
    _check_markdown_size(size, envelope_maximum)
    out += section
    for item in not_carried:
        text = _escape_markdown_text(item, preserve_unicode=preserve_unicode,
                                     max_markdown_bytes=_remaining_markdown_bytes(
                                         envelope_maximum, size + 3))
        size += _markdown_bytes(text) + 3 if bounded else 0
        out.append("- " + text)
    section = ["", '<a id="brewdoc-contents"></a>', "## Contents", ""]
    size += sum(len(line) + 1 for line in section) if bounded else 0
    _check_markdown_size(size, envelope_maximum)
    out += section
    for ordinal, label, _blocks in units:
        anchor = _unit_anchor(_unit_key(unit_kind, ordinal))
        framing = len(anchor) + 8
        heading = _unit_heading(unit_kind, ordinal, label, preserve_unicode=preserve_unicode,
                                 max_markdown_bytes=_remaining_markdown_bytes(
                                     envelope_maximum, size + framing))
        size += _markdown_bytes(heading) + framing if bounded else 0
        out.append("- [%s](#%s)" % (heading, anchor))
    if publication_navigation:
        section = ["", '<a id="brewdoc-publication-navigation"></a>', "### Publication navigation", ""]
        size += sum(len(line) + 1 for line in section) if bounded else 0
        _check_markdown_size(size, envelope_maximum)
        out += section
        destinations = {_unit_anchor(key) for key in unit_keys}
        destinations.add("brewdoc-publication-navigation")
        destinations.update(payload for _ordinal, _label, blocks in units
                            for block_kind, payload in blocks if block_kind == "anchor")
        previous_depth = -1
        for depth, label, destination in publication_navigation:
            if (type(depth) is not int or depth < 0 or depth > previous_depth + 1
                    or destination is not None and destination not in destinations):
                raise BrewdocError("invalid publication navigation entry")
            previous_depth = depth
            framing = 2 * depth + 3 + (len(destination) + 5 if destination is not None else 0)
            text = _escape_markdown_text(label, preserve_unicode=preserve_unicode,
                                          max_markdown_bytes=_remaining_markdown_bytes(
                                              envelope_maximum, size + framing))
            size += framing + _markdown_bytes(text) if bounded else 0
            text = "[%s](#%s)" % (text, destination) if destination is not None else text
            out.append("  " * depth + "- " + text)
    _check_markdown_size(size, envelope_maximum)
    out += ["", body.rstrip("\n")]
    markdown = _join_markdown_lines(out, max_markdown_bytes)
    _check_markdown_size(_markdown_bytes(markdown), max_markdown_bytes)
    return markdown, tally, unit_keys, artifacts
