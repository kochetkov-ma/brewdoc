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
from dataclasses import dataclass
from pathlib import Path

MARKDOWN_SCHEMA = "brewdoc.markdown/2"
# Receipt versions on its own: its key set moves when a format lands, the Markdown schema does not.
RECEIPT_SCHEMA = "brewdoc.receipt/1"

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
    """Return every relationship element in document order, duplicate ids and types included."""
    root = _xml_part(package, part)
    return tuple(element for element in root.iter()
                 if _local_name(element.tag) == "Relationship")


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, ET.Element]:
    return {element.attrib["Id"]: element for element in _relationship_elements(package, part)
            if "Id" in element.attrib}


def _relationship_part(part: str) -> str:
    """Return the OPC relationship part paired with one package part."""
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def _opc_main_part(package: zipfile.ZipFile) -> str:
    """Find a package's main part through its package-level office relationship."""
    for relationship in _relationships(package, "_rels/.rels").values():
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


def sanitise(text: str, tally: dict, keep_layout: bool = False) -> str:
    """ASCII out, every dropped class counted; `keep_layout` preserves fixed-width alignment."""
    drop = tally["dropped"]
    tally["broken_ligature_words"] += len(BROKEN_WORD_RE.findall(text))
    drop["cid_survivors"] += len(CID_RE.findall(text))
    text = CID_RE.sub("", text)
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
        if ch in FOLD:
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


def cell_text(value) -> str:
    """One cell as text; calamine returns '' for an empty cell and a float for every number."""
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
    return str(value).replace("\n", " ").strip()


def markdown_table(rows) -> list[str]:
    """Rows -> a Markdown table, first row as the header; ragged rows are padded, never cut."""
    # The only place a cell's pipe is escaped, so no caller can escape it twice.
    width = max((len(row) for row in rows), default=0)
    if not width:
        return []
    out = []
    for index, row in enumerate(rows):
        cells = [cell_text(value).replace("|", "\\|") for value in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
        if index == 0:
            out.append("| " + " | ".join(["---"] * width) + " |")
    return out


def head_key(text: str) -> str:
    """A running head's identity without its folio (`Page 31 of 232` never repeats verbatim)."""
    return DIGITS_RE.sub("#", text)


def _drop_furniture(lines: list[str], heads: set[str], numbers: set[str],
                    tally: dict) -> list[str]:
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


def _escape_markdown_text(text: str) -> str:
    """Preserve semantic text as ASCII without exposing Markdown or HTML syntax."""
    escaped = str(text).translate(MARKDOWN_ESCAPES)
    return escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _unit_key(kind: str, ordinal: int) -> str:
    return "%s/%06d" % (kind, ordinal)


def _unit_anchor(key: str) -> str:
    return "brewdoc-" + key.replace("/", "-")


def _unit_heading(kind: str, ordinal: int, label: str) -> str:
    if kind == "page":
        return "Page %d" % ordinal
    title = _escape_markdown_text(label)
    return "%s %d: \"%s\"" % (kind.title(), ordinal, title)


def _escape_source_html(line: str) -> str:
    """Keep every source HTML-like fragment visible instead of active."""
    return line.replace("<", "&lt;").replace(">", "&gt;")


def _literal_fence(code: list[str]) -> list[str]:
    """Fence source code verbatim; the fence outgrows every backtick run so none can close it."""
    longest = max((len(run) for line in code for run in re.findall("`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, *code, fence]


def _render_units(kind: str, units: list[tuple[int, str, list[tuple]]],
                  heads: set[str], numbers: set[str], tally: dict) -> tuple[str, tuple[str, ...]]:
    """Render anchored content units before metadata so all loss tallies are final."""
    out = []
    keys = []
    for ordinal, label, blocks in units:
        key = _unit_key(kind, ordinal)
        keys.append(key)
        out += ['<a id="%s"></a>' % _unit_anchor(key),
                "## " + _unit_heading(kind, ordinal, label), ""]
        for block_kind, payload in blocks:
            if block_kind == "table":
                rendered = markdown_table(payload)
            elif block_kind == "math":
                rendered = payload
            else:
                rendered = _drop_furniture(payload, heads, numbers, tally)
                if block_kind == "block" and len(rendered) <= 2:
                    rendered = [line for line in rendered if line != "```"]
            if rendered and block_kind in ("block", "math"):
                out += _literal_fence(rendered[1:-1]) + [""]
            elif rendered:
                out += [_escape_source_html(line) for line in rendered] + [""]
    body = "\n".join(out).rstrip("\n") + "\n"
    return body, tuple(keys)


# Every private renderer returns (Markdown, tally, unit keys, artifact references).
Rendered = tuple[str, dict, tuple[str, ...], tuple[ArtifactRef, ...]]


@dataclass(frozen=True, slots=True)
class Route:
    """One adapter's whole contract: each adapter declares exactly one, the service folds them."""

    name: str
    unit_kind: str
    suffixes: tuple[str, ...]
    render: Callable[..., Rendered] | None
    not_carried: tuple[str, ...]


CAPABILITY_FIELDS = ("Formula capability", "Selected formula count", "VBA project capability",
                     "VBA project count", "VBA source module capability", "VBA source module count")
NOT_APPLICABLE = tuple((field, "not applicable") for field in CAPABILITY_FIELDS)


def _metadata_rows(path: Path, route: str, unit_kind: str, source_units: int,
                   unit_keys: tuple[str, ...], body: str, tally: dict) -> list[tuple[str, str]]:
    source, suffix = path.read_bytes(), path.suffix.lower()
    body_bytes = body.encode("ascii")
    rows = [
        ("Markdown schema", MARKDOWN_SCHEMA),
        ("Source name", '"%s"' % _escape_markdown_text(path.name)),
        ("Source suffix", suffix[:1] + _escape_markdown_text(suffix[1:])),
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


def _artifacts_markdown(artifacts: tuple[ArtifactRef, ...]) -> list[str]:
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
    return markdown_table(rows)


def _assemble(path: Path, route: str, unit_kind: str, source_units: int,
              units: list[tuple[int, str, list[tuple]]], tally: dict, *,
              not_carried: tuple[str, ...], heads=frozenset(), numbers=frozenset(),
              capabilities: tuple = NOT_APPLICABLE,
              artifacts: tuple[ArtifactRef, ...] = ()) -> Rendered:
    """Build schema 2 once content and loss counters are final; every route returns this shape."""
    if unit_kind not in UNIT_COUNTERS:
        raise BrewdocError("unknown unit kind %r; expected one of: %s"
                           % (unit_kind, ", ".join(UNIT_COUNTERS)))
    body, unit_keys = _render_units(unit_kind, units, heads, numbers, tally)
    tally[UNIT_COUNTERS[unit_kind]] = len(unit_keys)
    metadata = _metadata_rows(path, route, unit_kind, source_units, unit_keys, body, tally)
    out = ['# "%s"' % _escape_markdown_text(path.name), "",
           '<a id="brewdoc-metadata"></a>', "## Metadata", ""]
    out += markdown_table([["Field", "Value"], *metadata, *capabilities])
    out += ["", "## Artifacts", ""] + _artifacts_markdown(artifacts) + ["", "## Known omissions", ""]
    out += ["- " + _escape_markdown_text(item) for item in not_carried]
    out += ["", '<a id="brewdoc-contents"></a>', "## Contents", ""]
    out += ["- [%s](#%s)" % (_unit_heading(unit_kind, ordinal, label), _unit_anchor(
        _unit_key(unit_kind, ordinal))) for ordinal, label, _blocks in units]
    out += ["", body.rstrip("\n")]
    return "\n".join(out).rstrip("\n") + "\n", tally, unit_keys, artifacts
