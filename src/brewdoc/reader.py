"""Brew a PDF, a Word document or a spreadsheet into deterministic, ASCII, LLM-ready Markdown.

Reads .pdf, .docx and .ods/.xls/.xlsb/.xlsm/.xlsx. No OCR, no ML: a PDF without a text layer is
refused by name. Every render comes with one JSON receipt naming the route, what was rendered,
every sanitised item dropped and what the route structurally cannot carry.

PDF regions route by their own ruling, per region rather than per page:
  * ruling edges present -> `find_tables()` (lines strategy) -> a Markdown table
  * ruling unfinished    -> re-read on the grid the ruling implies, kept only when it carries
                            more characters than the drawn ruling did
  * ruled only across    -> a column grid off the segment ends of a horizontal rule
  * no ruling            -> a fixed-width block for a tabular band, plain lines for prose
  * unlined + captioned  -> the band under a `Table N` caption cropped, then read as text/text
  * two-column prose     -> cropped at the gutter, found by relative density on the x-histogram

Known shortfalls, counted and reported rather than repaired: a font whose ToUnicode CMap maps a
ligature to a stray code point (counted as `broken_ligature_words`, folded to ASCII, never
guessed back); a table spanning a page break stays two tables, its wrapped rows separate Markdown
rows; a sub/superscript is folded back into the line it belongs to.

Paths resolve against the current working directory, never this module's location; `--out`
creates its parent directories. Output is deterministic: no timestamps, no set iteration, floats
`repr`-rendered, dates as one ISO day - two renders of one file share a sha256.

Dependencies: `pdfplumber` and `python-calamine`; everything else is the standard library.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import re
import statistics
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_text
from python_calamine import CalamineWorkbook

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

RENDERED_BY = "brewdoc"
PDF_SUFFIX = ".pdf"
DOC_SUFFIX = ".docx"
SHEET_SUFFIXES = (".ods", ".xls", ".xlsb", ".xlsm", ".xlsx")
SUFFIXES = " ".join(sorted((PDF_SUFFIX, DOC_SUFFIX) + SHEET_SUFFIXES))

PDF_NOT_CARRIED = ("images, figures and the text drawn inside them",
                   "a table that spans a page break",
                   "text rotated out of the horizontal reading order")
SHEET_NOT_CARRIED = ("cell formulas - only the value the writer cached",
                     "formatting, colours, comments and data validation",
                     "charts and embedded images")
DOC_NOT_CARRIED = ("images, charts and the text drawn inside them",
                   "tracked changes, comments, footnotes, headers and footers",
                   "a table's own formatting - only its cells, row by row")

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

# Layout thresholds, measured on real documents.
HIST_BINS = 60           # char x-histogram resolution for the gutter search
GUTTER_RATIO = 0.25      # a gutter bin holds at most this share of the median column bin
GUTTER_MIN_BINS = 2      # a real gutter is several bins wide; one bin is noise
GUTTER_BAND = (0.35, 0.65)   # a gutter sits near the middle; a margin is not a gutter
GUTTER_MIN_CHARS = 40    # below this a region has no histogram worth reading
WIDE_TABLE = 0.60        # a lined table this wide means the page is a table, not two columns
BLANK_BAND = 1.6         # a vertical gap this many line-heights wide separates two regions
WIDE_GAP = 2.5           # an inter-word gap this many space-widths wide is a column boundary
MIN_WIDE_GAPS = 2        # a tabular line has at least this many of them
MIN_TABULAR_LINES = 2    # one aligned line is a heading, not a table
COLUMN_TOLERANCE = 3.0   # two column starts this close are the same column
MIN_SHARED_COLUMNS = 2   # two-column prose shares one start (the gutter); a table shares more
MIN_REGION = 4.0         # a band thinner than this holds no line
DEFAULT_SPACE = 2.5      # the space width assumed when a region has no gap to measure
MARGIN_BAND = 0.08       # the share of page height a running head or folio sits in
RULE_SNAP = 3.0          # two ruling edges this close are one line, never two columns
MIN_RULE_COLUMNS = 4     # a rule cut into fewer pieces is an underline, not a column grid
GRID_ROW_COLUMNS = 3     # a row filling this many columns is using the grid
GRID_ROW_SHARE = 3       # and one row in this many must, or the band is prose beside a rule
SUBSCRIPT = 0.75         # a char smaller than this share of its cell's body size sits off-baseline
TABLE_CAPTION_RE = re.compile(r"^\s*table\s+\d+", re.I)
PAGE_NUMBER_RE = re.compile(r"^(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?$", re.I)
DIGITS_RE = re.compile(r"\d+")
REPEATS_AS_HEAD = 3      # a first/last line this many pages share is furniture, not content

TEXT_TABLE = {"vertical_strategy": "text", "horizontal_strategy": "text"}


class BrewdocError(Exception):
    """A refusal naming what was not found or not parsed, and where."""


def new_tally() -> dict:
    """An empty render tally: counts of pages, tables, regions and every dropped class."""
    return {"pages": 0, "sheets": 0, "chapters": 0, "tables": 0, "text_regions": 0,
            "columns_split": 0,
            "broken_ligature_words": 0, "dropped": {key: 0 for key in DROP_KEYS}}


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
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def markdown_table(rows) -> list[str]:
    """Rows -> a Markdown table, first row as the header; ragged rows are padded, never cut."""
    width = max((len(row) for row in rows), default=0)
    if not width:
        return []
    out = []
    for index, row in enumerate(rows):
        cells = [cell_text(value) for value in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
        if index == 0:
            out.append("| " + " | ".join(["---"] * width) + " |")
    return out


# --- PDF: regions -----------------------------------------------------------------------------
def _line_chars(line) -> list:
    return sorted((c for c in line["chars"] if c["text"].strip()), key=lambda c: c["x0"])


def _gaps(chars) -> list[float]:
    return [b["x0"] - a["x1"] for a, b in zip(chars, chars[1:]) if b["x0"] - a["x1"] > 0]


def space_unit(lines) -> float:
    """The width of one word space in this region: the median inter-char gap, at any font size."""
    gaps = [gap for line in lines for gap in _gaps(_line_chars(line))]
    return statistics.median(gaps) if gaps else DEFAULT_SPACE


def is_tabular(line, unit: float) -> bool:
    """A line with several column-wide gaps. Not the two-column test: see `aligned_columns`."""
    chars = _line_chars(line)
    if len(chars) < 3:
        return False
    threshold = max(4.0, WIDE_GAP * unit)
    return sum(1 for gap in _gaps(chars) if gap >= threshold) >= MIN_WIDE_GAPS


def detect_gutter(chars, width: float) -> float | None:
    """The x of a two-column gutter, or None. Relative density: a running head or a figure
    caption crosses the gutter, so a zero-run test misses real ones."""
    if len(chars) < GUTTER_MIN_CHARS:
        return None
    x0 = min(c["x0"] for c in chars)
    span = max(c["x1"] for c in chars) - x0
    if span < 0.5 * width:
        return None
    histogram = [0] * HIST_BINS
    for char in chars:
        histogram[min(int((char["x0"] - x0) / span * HIST_BINS), HIST_BINS - 1)] += 1
    filled = [count for count in histogram if count]
    if not filled:
        return None
    threshold = statistics.median(filled) * GUTTER_RATIO
    index = 0
    while index < HIST_BINS:
        if histogram[index] > threshold:
            index += 1
            continue
        end = index
        while end < HIST_BINS and histogram[end] <= threshold:
            end += 1
        centre = (index + end) / 2 / HIST_BINS
        if end - index >= GUTTER_MIN_BINS and GUTTER_BAND[0] <= centre <= GUTTER_BAND[1]:
            return x0 + span * centre
        index = end
    return None


def aligned_columns(band, unit: float) -> int:
    """How many column starts the band's lines share: a table shares as many as it has columns,
    two-column prose shares exactly one (the gutter)."""
    starts, threshold = [], max(4.0, WIDE_GAP * unit)
    for line in band:
        chars = _line_chars(line)
        starts.append([b["x0"] for a, b in zip(chars, chars[1:]) if b["x0"] - a["x1"] >= threshold])
    flat = sorted(x for line in starts for x in line)
    shared, cluster = 0, []
    for x in flat + [None]:
        if cluster and (x is None or x - cluster[0] > COLUMN_TOLERANCE):
            if len(cluster) * 2 >= len(band) and len(cluster) >= MIN_TABULAR_LINES:
                shared += 1
            cluster = []
        if x is not None:
            cluster.append(x)
    return shared


def _crop(page, x0, top, x1, bottom):
    # pdfplumber refuses a box even a hair outside the page.
    px0, ptop, px1, pbottom = page.bbox
    box = (max(x0, px0), max(top, ptop), min(x1, px1), min(bottom, pbottom))
    if box[2] - box[0] < MIN_REGION or box[3] - box[1] < MIN_REGION:
        return None
    return page.crop(box)


def _bands(lines) -> list[list]:
    if not lines:
        return []
    pitch = statistics.median([line["bottom"] - line["top"] for line in lines]) or 1.0
    bands = [[lines[0]]]
    for previous, current in zip(lines, lines[1:]):
        if current["top"] - previous["bottom"] > BLANK_BAND * pitch:
            bands.append([current])
        else:
            bands[-1].append(current)
    return bands


def _tabular_run(flags: list[bool]) -> tuple[int, int]:
    best, start, index = (0, 0), 0, 0
    while index < len(flags):
        if not flags[index]:
            index += 1
            continue
        start = index
        while index < len(flags) and flags[index]:
            index += 1
        if index - start > best[1] - best[0]:
            best = (start, index)
    return best


def _text_lines(page, band, tally: dict, margins: list[str]) -> list[str]:
    # Registered on the rendered strings: a column split turns one running head into two.
    height = page.bbox[3] - page.bbox[1]
    top, bottom = page.bbox[1] + MARGIN_BAND * height, page.bbox[3] - MARGIN_BAND * height
    out = []
    for entry in band:
        text = sanitise(entry["text"], tally).strip()
        if not text:
            continue
        if entry["top"] <= top or entry["bottom"] >= bottom:
            margins.append(text)
        out.append(text)
    return out


def _layout_block(page, band, tally: dict) -> list[str]:
    crop = _crop(page, min(line["x0"] for line in band) - 1, band[0]["top"] - 1,
                 max(line["x1"] for line in band) + 1, band[-1]["bottom"] + 1)
    text = sanitise((crop or page).extract_text(layout=True) or "", tally, keep_layout=True)
    rows = [row for row in text.split("\n") if row.strip()]
    return ["```", *rows, "```"] if rows else []


def _caption_table(page, band, start: int, end: int, tally: dict) -> list[list] | None:
    rows = band[start:end]
    crop = _crop(page, min(line["x0"] for line in rows) - 1, rows[0]["top"] - 1,
                 max(line["x1"] for line in rows) + 1, rows[-1]["bottom"] + 1)
    if crop is None:
        return None
    tables = crop.extract_tables(TEXT_TABLE)
    if not tables:
        return None
    # The text strategy lays a row edge on every baseline, so widely leaded tables gain empty rows.
    grid = [[sanitise(cell or "", tally).strip() for cell in row] for row in tables[0]]
    grid = [row for row in grid if any(row)]
    if len(grid) < MIN_TABULAR_LINES or max((len(row) for row in grid), default=0) < 2:
        return None
    return grid


def _band_blocks(page, band, flags: list[bool], tally: dict, margins: list[str],
                 columns: list[float] | None) -> list[tuple]:
    blocks = []
    tally["text_regions"] += 1
    start, end = _tabular_run(flags)
    captioned = any(TABLE_CAPTION_RE.match(line["text"] or "") for line in band[:start])
    # The caption route needs the tabular lines in ONE run; a table with wrapped rows has them on
    # both sides of a prose line, and the rule grid reads that shape instead.
    grid = (_caption_table(page, band, start, end, tally)
            if captioned and end - start >= MIN_TABULAR_LINES and sum(flags) == end - start
            else None)
    if grid:
        head = _text_lines(page, band[:start], tally, margins)
        tail = _text_lines(page, band[end:], tally, margins)
        blocks += [("text", head), ("table", grid), ("text", tail)]
        tally["tables"] += 1
    elif end - start >= MIN_TABULAR_LINES:
        ruled = _rule_table(page, band, columns) if columns else None
        if ruled:
            first, last, cells = ruled
            blocks += [("text", _text_lines(page, band[:first], tally, margins)),
                       ("table", [[sanitise(cell, tally).strip() for cell in row]
                                  for row in cells]),
                       ("text", _text_lines(page, band[last:], tally, margins))]
            tally["tables"] += 1
        else:
            blocks.append(("block", _layout_block(page, band, tally)))
    else:
        blocks.append(("text", _text_lines(page, band, tally, margins)))
    return [block for block in blocks if block[1]]


def _text_region(page, crop, tally: dict, margins: list[str], wide_table: bool,
                 columns: list[float] | None = None, split: bool = True) -> list[tuple]:
    # Bands first, then a gutter search inside each: a two-column half usually sits under a
    # full-width abstract or table, and a whole-page histogram sees the two mixed.
    lines = crop.extract_text_lines()
    if not lines:
        return []
    unit, blocks = space_unit(lines), []
    for band in _bands(lines):
        flags = [is_tabular(line, unit) for line in band]
        start, end = _tabular_run(flags)
        # A wide unlined table's column whitespace looks like a gutter; alignment across the
        # band tells them apart.
        tabular = aligned_columns(band, unit) >= MIN_SHARED_COLUMNS
        chars = [char for line in band for char in line["chars"]]
        gutter = (detect_gutter(chars, page.width)
                  if split and not wide_table and not tabular else None)
        if gutter is None:
            blocks += _band_blocks(page, band, flags, tally, margins, columns)
            continue
        tally["columns_split"] += 1
        top, bottom = band[0]["top"] - 1, band[-1]["bottom"] + 1
        for left, right in ((page.bbox[0], gutter), (gutter, page.bbox[2])):
            column = _crop(page, left, top, right, bottom)
            if column is not None and column.chars:
                blocks += _text_region(page, column, tally, margins, wide_table, columns,
                                       split=False)
    return blocks


def _page_tables(page) -> list:
    kept = []
    for table in sorted(page.find_tables(), key=lambda t: (t.bbox[1], t.bbox[0])):
        if not any(table.bbox[1] < previous.bbox[3] for previous in kept):
            kept.append(table)
    return kept


# --- PDF: ruling the producer never finished --------------------------------------------------
# `find_tables` builds a cell only where four edges close it; an unclosed cell is reported as
# None and its text dropped in silence. Such a table is re-read on the grid its ruling implies.
def _snap(values) -> list[float]:
    # Single-linkage on consecutive values: a separator drawn as overlapping segments stays one.
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and value - clusters[-1][-1] <= RULE_SNAP:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [statistics.median(cluster) for cluster in clusters]


def _ruled_region(page, bbox) -> list:
    # Growing over touching edges is what reaches the rows below the last closed cell.
    window = [bbox[0] - RULE_SNAP, bbox[1] - RULE_SNAP, bbox[2] + RULE_SNAP, bbox[3] + RULE_SNAP]
    taken, growing = set(), True
    while growing:
        growing = False
        for index, edge in enumerate(page.edges):
            if index in taken or edge["x1"] < window[0] or edge["x0"] > window[2]:
                continue
            if edge["bottom"] < window[1] or edge["top"] > window[3]:
                continue
            taken.add(index)
            window = [min(window[0], edge["x0"] - RULE_SNAP), min(window[1], edge["top"] - RULE_SNAP),
                      max(window[2], edge["x1"] + RULE_SNAP), max(window[3], edge["bottom"] + RULE_SNAP)]
            growing = True
    return [page.edges[index] for index in sorted(taken)]


def _implied_grid(edges) -> tuple[list[float], list[float]]:
    # A horizontal rule's ends are column boundaries, a vertical rule's ends row boundaries.
    verticals = [edge["x0"] for edge in edges if edge["orientation"] == "v"]
    horizontals = [edge["top"] for edge in edges if edge["orientation"] == "h"]
    for edge in edges:
        if edge["orientation"] == "h":
            verticals += [edge["x0"], edge["x1"]]
        else:
            horizontals += [edge["top"], edge["bottom"]]
    return _snap(verticals), _snap(horizontals)


def _fold_subscripts(source, box, text: str) -> str:
    # The y tolerance is derived from the cell's own small chars and refused when it would reach
    # the next line. Chars are picked by centre, pdfplumber's own cell test, so a glyph drawn
    # across the closing rule lands in one cell only.
    x0, top, x1, bottom = box
    chars = [char for char in source.chars
             if x0 <= (char["x0"] + char["x1"]) / 2 < x1
             and top <= (char["top"] + char["bottom"]) / 2 < bottom]
    ink = [char for char in chars if char["text"].strip()]
    if not ink:
        return text
    body = max(char["size"] for char in ink)
    small = [char for char in ink if char["size"] < SUBSCRIPT * body]
    lines = sorted({round(char["top"], 1) for char in ink if char["size"] >= SUBSCRIPT * body})
    if not small or not lines:
        return text
    reach = max(min(abs(char["top"] - line) for line in lines) for char in small) + 0.5
    pitch = min((later - earlier for earlier, later in zip(lines, lines[1:])), default=reach + 1)
    if reach >= pitch:
        return text
    return extract_text(chars, y_tolerance=reach) or text


def _unused(lines, xs: list[float]) -> set[int]:
    # Grid boundaries a row's own glyphs sit astride: running text, not a column. Only a glyph
    # counts - a closed cell or an inter-word gap proves nothing.
    chars = [char for line in lines for char in _line_chars(line)]
    return {index for index in range(1, len(xs) - 1)
            if any(char["x0"] < xs[index] < char["x1"] for char in chars)}


def _unmerge(words, xs: list[float], cells: list[str], unused: set[int]) -> list[str]:
    # Rebuilt from the words, not the cut cells, since a cut can fall inside a word.
    if not unused:
        return list(cells)
    out, index = [], 0
    while index < len(cells):
        end = index + 1
        while end < len(cells) and end in unused:
            end += 1
        if end == index + 1:
            out.append(cells[index])
            index = end
            continue
        left, right = xs[index] - 0.5, xs[min(end, len(xs) - 1)] + 0.5
        out.append(" ".join(word["text"] for word in words
                            if left <= word["x0"] and word["x1"] <= right))
        out += [""] * (end - index - 1)
        index = end
    return out


def _row_cells(source, row, raw: list) -> list[str]:
    return [_fold_subscripts(source, box, cell) if box and cell else (cell or "")
            for box, cell in zip(row.cells, raw)]


def _table_cells(source, table, xs: list[float] | None = None) -> list[list[str]]:
    raw = table.extract()
    lines = source.extract_text_lines() if xs else []
    words = source.extract_words() if xs else []
    grid = []
    for index, row in enumerate(table.rows):
        cells = _row_cells(source, row, raw[index])
        if xs:
            top, bottom = row.bbox[1] - 1, row.bbox[3] + 1

            def within(items):
                return [item for item in items
                        if item["top"] >= top and item["bottom"] <= bottom]
            cells = _unmerge(within(words), xs, cells, _unused(within(lines), xs))
        grid.append(cells)
    return grid


def _place(source, row, raw: list, xs: list[float], width: int) -> list[str]:
    cells = [""] * width
    for box, text in zip(row.cells, _row_cells(source, row, raw)):
        if not box or not text.strip():
            continue
        index = min(range(width), key=lambda column: abs(xs[column] - box[0]))
        cells[index] = (cells[index] + " " + text).strip()
    return cells


def _prefer_drawn(source, table, repaired: list[list[str]], bands, xs) -> list[list[str]]:
    # Row by row the drawn ruling wins a tie on character count: the explicit grid shreds a
    # caption or a spanning header the drawn ruling had right. A drawn row is placed once, so a
    # header merged down two rows is not printed twice over its sub-header.
    raw = table.extract()
    out, used = [], set()
    for cells, (top, bottom) in zip(repaired, bands):
        middle = (top + bottom) / 2
        match = [index for index, row in enumerate(table.rows)
                 if index not in used and row.bbox[1] <= middle <= row.bbox[3]]
        drawn = _place(source, table.rows[match[0]], raw[match[0]], xs, len(cells)) if match else []
        if drawn and _weight([drawn]) >= _weight([cells]):
            used.add(match[0])
            out.append(drawn)
        else:
            out.append(cells)
    return out


def _trim(grid: list[list[str]]) -> list[list[str]]:
    width = max((len(row) for row in grid), default=0)
    rows = [row + [""] * (width - len(row)) for row in grid]
    keep = [index for index in range(width) if any(row[index].strip() for row in rows)]
    return [[row[index] for index in keep] for row in rows if any(cell.strip() for cell in row)]


def _weight(grid: list[list[str]]) -> int:
    return sum(len(re.sub(r"\s", "", cell)) for row in grid for cell in row)


def _repair_table(page, table):
    # Accepted only when it carries more characters than the drawn ruling did.
    xs, ys = _implied_grid(_ruled_region(page, table.bbox))
    if len(xs) < 2 or len(ys) < 2:
        return None
    crop = _crop(page, xs[0] - 1, ys[0] - 1, xs[-1] + 1, ys[-1] + 1)
    if crop is None:
        return None
    found = crop.find_tables({"vertical_strategy": "explicit", "horizontal_strategy": "explicit",
                              "explicit_vertical_lines": xs, "explicit_horizontal_lines": ys})
    if not found:
        return None
    bands = [(row.bbox[1], row.bbox[3]) for row in found[0].rows]
    grid = _trim(_prefer_drawn(page, table, _table_cells(crop, found[0], xs), bands, xs))
    if _weight(grid) <= _weight(_table_cells(page, table)):
        return None
    return grid, found[0].bbox


def _rule_columns(page) -> tuple[list[float], float, float] | None:
    # A rule emitted as one segment per column carries the grid in its segment ends. The opening
    # rule is the topmost of the same width: caption and running head sit above it.
    groups: dict[int, list] = {}
    for edge in page.edges:
        if edge["orientation"] == "h":
            groups.setdefault(int(edge["top"] / RULE_SNAP), []).append(edge)
    spans = {key: _snap([edge["x0"] for edge in group] + [edge["x1"] for edge in group])
             for key, group in groups.items()}
    best = None
    for key in sorted(spans):
        if len(spans[key]) >= MIN_RULE_COLUMNS and (best is None or len(spans[key]) > len(best[0])):
            best = (spans[key], key)
    if best is None:
        return None
    xs, chosen = best
    same = [key for key, span in spans.items()
            if abs(span[0] - xs[0]) <= RULE_SNAP and abs(span[-1] - xs[-1]) <= RULE_SNAP]
    opening = min(min(edge["top"] for edge in groups[key]) for key in same if key <= chosen)
    closing = max(max(edge["bottom"] for edge in groups[key]) for key in same)
    return xs, opening, closing


def _rule_rows(band, xs: list[float], opening: float) -> tuple[int, int]:
    # Past a page break the continuation carries only the closing rule, so try below first.
    def fits(line):
        return line["x0"] >= xs[0] - RULE_SNAP and line["x1"] <= xs[-1] + RULE_SNAP
    below = _tabular_run([fits(line) and line["top"] >= opening - RULE_SNAP for line in band])
    if below[1] - below[0] >= MIN_TABULAR_LINES:
        return below
    return _tabular_run([fits(line) and line["bottom"] <= opening + RULE_SNAP for line in band])


def _rule_table(page, band, columns: tuple[list[float], float, float]):
    # Must adjoin the ruling: short prose lines fit any grid. Outer boundaries come from the
    # words, since pdfplumber drops a column whose boundary lies beyond the last word.
    grid, opening, closing = columns
    reach = BLANK_BAND * (statistics.median([line["bottom"] - line["top"] for line in band]) or 1.0)
    if band[0]["top"] - closing > reach or opening - band[-1]["bottom"] > reach:
        return None
    start, end = _rule_rows(band, grid, opening)
    rows = band[start:end]
    if len(rows) < MIN_TABULAR_LINES:
        return None
    left, right = min(line["x0"] for line in rows), max(line["x1"] for line in rows)
    inner = [x for x in grid if left + RULE_SNAP < x < right - RULE_SNAP]
    if len(inner) < MIN_RULE_COLUMNS - 2:
        return None
    crop = _crop(page, left - 1, rows[0]["top"] - 1, right + 1, rows[-1]["bottom"] + 1)
    words = crop.extract_words() if crop is not None else []
    if not words:
        return None
    xs = [min(word["x0"] for word in words)] + inner + [max(word["x1"] for word in words)]
    found = crop.find_tables({"vertical_strategy": "explicit", "horizontal_strategy": "text",
                              "explicit_vertical_lines": xs})
    if not found:
        return None
    cells = _trim(_table_cells(crop, found[0], xs))
    # A real table fills three or more columns on roughly half its rows; prose beside a rule
    # reaches a few percent and belongs in a fixed-width block.
    gridded = sum(1 for row in cells if sum(1 for cell in row if cell.strip()) >= GRID_ROW_COLUMNS)
    if not cells or gridded * GRID_ROW_SHARE < len(cells):
        return None
    return start, end, cells


def render_page(page, tally: dict) -> tuple[list[tuple], list[str]]:
    """One page -> (its blocks top-down, its margin lines); lined tables carve the text regions."""
    tables = _page_tables(page)
    wide = any((table.bbox[2] - table.bbox[0]) / page.width >= WIDE_TABLE for table in tables)
    columns, margins = _rule_columns(page), []

    def between(top, bottom):
        crop = _crop(page, page.bbox[0], top, page.bbox[2], bottom)
        return (_text_region(page, crop, tally, margins, wide, columns)
                if crop is not None and crop.chars else [])

    blocks, top = [], page.bbox[1]
    for table in tables:
        if table.bbox[1] < top:                  # swallowed by an earlier repaired region
            continue
        repaired = _repair_table(page, table)
        cells, bbox = repaired if repaired else (_table_cells(page, table), table.bbox)
        blocks += between(top, min(table.bbox[1], bbox[1]))
        blocks.append(("table", [[sanitise(cell, tally).strip() for cell in row]
                                 for row in cells]))
        tally["tables"] += 1
        # A glyph drawn across the closing rule is already in the table; move past it.
        edge = max(bbox[3], table.bbox[3])
        top = max([edge] + [char["bottom"] + 0.5 for char in page.chars
                            if char["top"] < edge < char["bottom"]])
    blocks += between(top, page.bbox[3])
    return [block for block in blocks if block[1]], margins


def head_key(text: str) -> str:
    """A running head's identity without its folio (`Page 31 of 232` never repeats verbatim)."""
    return DIGITS_RE.sub("#", text)


def furniture(margins: list[list[str]]) -> tuple[set[str], set[str]]:
    """(running heads as head_key forms, page numbers) from every page's margin candidates."""
    counts = Counter(head_key(text) for page in margins for text in set(page))
    numbers = {text for page in margins for text in page if PAGE_NUMBER_RE.match(text)}
    heads = {key for key, count in counts.items() if count >= REPEATS_AS_HEAD}
    return heads, numbers


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


def _assemble(name: str, digest: str, chapters: list[tuple[str, list[tuple]]],
              heads: set[str], numbers: set[str], tally: dict) -> str:
    name = sanitise(name, tally).strip()
    out = ["# " + name, "",
           "<!-- rendered by %s from %s sha256 %s -->" % (RENDERED_BY, name, digest), ""]
    for heading, blocks in chapters:
        out += ["## " + heading, ""]
        for kind, payload in blocks:
            if kind == "table":
                rendered = markdown_table(payload)
            else:
                rendered = _drop_furniture(payload, heads, numbers, tally)
                if kind == "block" and len(rendered) <= 2:
                    rendered = [line for line in rendered if line != "```"]
            if rendered:
                out += rendered + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def render_pdf(path) -> tuple[str, dict]:
    """(Markdown, tally) for a PDF; refuses a document with no text layer, by name."""
    path = Path(path)
    tally = new_tally()
    pages, margins, empty = [], [], 0
    with pdfplumber.open(path) as pdf:
        tally["pages"] = len(pdf.pages)
        for page in pdf.pages:
            if not page.chars:
                empty += 1
            blocks, page_margins = render_page(page, tally)
            pages.append(blocks)
            margins.append(page_margins)
            page.flush_cache()          # a long book otherwise holds every char object
    if tally["pages"] and empty == tally["pages"]:
        raise BrewdocError(
            "no text layer: %d of %d pages carry zero characters in %s - this reader does no OCR"
            % (empty, tally["pages"], path))
    heads, numbers = furniture(margins)
    chapters = [("Page %d" % number, blocks) for number, blocks in enumerate(pages, 1)]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return _assemble(path.name, digest, chapters, heads, numbers, tally), tally


def _sheet_grid(rows, tally: dict) -> list[list[str]]:
    # calamine trims trailing empty rows but not trailing empty columns; interior empty rows stay.
    grid = [[sanitise(cell_text(cell), tally) for cell in row] for row in rows]
    width = max((index + 1 for row in grid for index, cell in enumerate(row) if cell), default=0)
    return [row[:width] for row in grid] if width else []


def render_book(path) -> tuple[str, dict]:
    """(Markdown, tally) for a spreadsheet: one `## <sheet name>` chapter per sheet, in order."""
    path = Path(path)
    tally = new_tally()
    try:
        book = CalamineWorkbook.from_path(str(path))
    except Exception as exc:                      # calamine raises its own error types
        raise BrewdocError("spreadsheet unreadable: %s: %s" % (path, exc)) from exc
    chapters = []
    for name in book.sheet_names:
        raw = book.get_sheet_by_name(name).to_python(skip_empty_area=False)
        rows = _sheet_grid(raw, tally)
        tally["sheets"] += 1
        if rows:
            tally["tables"] += 1
        chapters.append((sanitise(name, tally).strip() or name,
                         [("table", rows)] if rows else []))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return _assemble(path.name, digest, chapters, set(), set(), tally), tally


# --- .docx: `word/document.xml`, `w:p` paragraphs and `w:tbl` tables in reading order ---------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOC_PART = "word/document.xml"
DOC_BODY = "Body"                       # the chapter of a document that declares no headings
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
            cells.append(cell_text(" ".join(_doc_lines(cell, tally))))
            cells += [""] * (_doc_span(cell) - 1)
        if any(cell for cell in cells):
            rows.append(cells)
    return rows


def render_doc(path) -> tuple[str, dict]:
    """(Markdown, tally) for a .docx: one `## <heading>` chapter per Word heading, else `Body`."""
    path = Path(path)
    tally = new_tally()
    try:
        with zipfile.ZipFile(path) as package:
            body = ET.fromstring(package.read(DOC_PART)).find(W + "body")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise BrewdocError("document unreadable: %s: %s" % (path, exc)) from exc
    if body is None:
        raise BrewdocError("no document body: %s carries no <w:body> in %s" % (path, DOC_PART))
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
                chapters.append((heading, blocks))
                heading, blocks = " ".join(lines), []
            else:
                tally["text_regions"] += 1
                blocks.append(("text", lines))
    chapters.append((heading, blocks))
    chapters = [pair for pair in chapters if pair[1]]
    tally["chapters"] = len(chapters)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return _assemble(path.name, digest, chapters, set(), set(), tally), tally


# --- CLI --------------------------------------------------------------------------------------
def _line(route: str, file_ok: bool, reason: str, path, tally: dict | None = None,
          out=None) -> dict:
    tally = tally or new_tally()
    return {"file_ok": file_ok, "route": route, "reason": reason,
            "source": Path(path).name, "out": str(out) if out else None,
            "pages": tally["pages"], "sheets": tally["sheets"], "tables": tally["tables"],
            "text_regions": tally["text_regions"], "columns_split": tally["columns_split"],
            "dropped": dict(tally["dropped"]),
            "broken_ligature_words": tally["broken_ligature_words"],
            "not_carried": list(PDF_NOT_CARRIED if route == "pdf" else
                                DOC_NOT_CARRIED if route == "doc" else
                                SHEET_NOT_CARRIED if route == "sheet" else ())}


def run(path, out=None) -> tuple[int, dict, str]:
    """(exit code, receipt, Markdown); every path returns a receipt, a refusal names what and where."""
    path = Path(path)
    suffix = path.suffix.lower()
    route = ("pdf" if suffix == PDF_SUFFIX else "doc" if suffix == DOC_SUFFIX
             else "sheet" if suffix in SHEET_SUFFIXES else "none")
    if route == "none":
        return EXIT_FAIL, _line(route, False, "unsupported suffix '%s' in %s: brewdoc reads %s"
                                % (suffix, path, SUFFIXES), path), ""
    if not path.is_file():
        return EXIT_FAIL, _line(route, False, "no such file: %s" % path, path), ""
    try:
        markdown, tally = (render_pdf(path) if route == "pdf" else
                           render_doc(path) if route == "doc" else render_book(path))
    except BrewdocError as exc:
        return EXIT_FAIL, _line(route, False, str(exc), path), ""
    except Exception as exc:
        return EXIT_FAIL, _line(route, False, "%s unreadable: %s: %s: %s"
                                % (route, path, type(exc).__name__, exc), path), ""
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(markdown, encoding="ascii")
    reason = "%s rendered: %d chapters, %d tables, %d text regions, %d column splits" % (
        route, tally["pages"] or tally["sheets"] or tally["chapters"], tally["tables"],
        tally["text_regions"],
        tally["columns_split"])
    return EXIT_OK, _line(route, True, reason, path, tally, out), markdown


# --- self-check: synthetic documents, no fixture file needed ----------------------------------
def synthetic_pdf(pages, font: bool = True) -> bytes:
    """A minimal valid PDF printing each page's content stream verbatim; `font=False` for a scan."""
    objects, count = {}, len(pages)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (" ".join("%d 0 R" % (3 + i) for i in range(count)), count)).encode("ascii")
    first_stream = 3 + count
    resources = " /Font << /F1 %d 0 R >>" % (first_stream + count) if font else ""
    for index, body in enumerate(pages):
        objects[3 + index] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources <<%s >> "
            "/Contents %d 0 R >>" % (resources, first_stream + index)).encode("ascii")
        data = body.encode("latin-1")
        objects[first_stream + index] = (("<< /Length %d >>\nstream\n" % len(data)).encode("ascii")
                                         + data + b"\nendstream")
    if font:
        objects[first_stream + count] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    buffer, offsets = io.BytesIO(), {}
    buffer.write(b"%PDF-1.4\n")
    for number in sorted(objects):
        offsets[number] = buffer.tell()
        buffer.write(("%d 0 obj\n" % number).encode("ascii"))
        buffer.write(objects[number])
        buffer.write(b"\nendobj\n")
    start, size = buffer.tell(), max(objects) + 1
    buffer.write(("xref\n0 %d\n" % size).encode("ascii") + b"0000000000 65535 f \n")
    for number in range(1, size):
        buffer.write(("%010d 00000 n \n" % offsets.get(number, 0)).encode("ascii"))
    buffer.write(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (size, start)).encode("ascii"))
    return buffer.getvalue()


def _text_op(x: float, y: float, text: str, size: int = 10) -> str:
    return "BT /F1 %d Tf 1 0 0 1 %s %s Tm (%s) Tj ET\n" % (size, x, y, text)


def _rule(x: float, y: float, width: float, height: float = 0.5) -> str:
    return "%s %s %s %s re f\n" % (x, y, width, height)


FIXTURE_TABLE = [_text_op(80, 700, "Table 1. Yield by zone."),
                 _text_op(80, 685, "Zone") + _text_op(200, 685, "Area") + _text_op(320, 685, "Yield"),
                 _text_op(80, 670, "North") + _text_op(200, 670, "12.5") + _text_op(320, 670, "4.10"),
                 _text_op(80, 655, "South") + _text_op(200, 655, "9.0") + _text_op(320, 655, "3.75")]
FIXTURE_COLUMNS = [_text_op(60, 600 - 12 * i, "left column line %d of the body text here" % i)
                   + _text_op(330, 600 - 12 * i, "right column line %d of the body text" % i)
                   for i in range(12)]


def _fixture_pages() -> list[str]:
    body = "".join(FIXTURE_TABLE) + "".join(FIXTURE_COLUMNS)
    return [_text_op(72, 760, "Seasonal Operations Manual") + body + _text_op(300, 40, "%d" % n)
            for n in (1, 2, 3)]


# p1: a grid closed on the header row alone, plus a subscripted cell. p2: rules only across.
FIXTURE_UNCLOSED = (
    "".join(_rule(100, y, 300) for y in (700, 685, 670, 655, 640))
    + "".join(_rule(x, 640, 0.5, 60) for x in (100, 200, 300))
    + _rule(400, 685, 0.5, 15)
    + "".join(_text_op(x, 689, text) for x, text in ((105, "Zone"), (205, "Area"), (305, "Yield")))
    + "".join(_text_op(x, 674, text) for x, text in ((105, "North"), (205, "12.5"), (305, "4.10")))
    + "".join(_text_op(x, 659, text) for x, text in ((105, "South"), (205, "9.0"), (305, "3.75")))
    + _text_op(105, 644, "Salt") + _text_op(305, 644, "1.0")
    + _text_op(205, 644, "P") + _text_op(211, 641, "2", 6)
    + _text_op(215, 644, "O") + _text_op(221, 641, "5", 6))
FIXTURE_SEGMENTED = (
    "".join(_rule(x, 600, 99.5) for x in (100, 200, 300, 400))
    + "".join(_text_op(x, 585, text)
              for x, text in ((105, "Crop"), (205, "Zone"), (305, "Area"), (405, "Yield")))
    + "".join(_text_op(x, 570, text)
              for x, text in ((105, "Maize"), (205, "North"), (305, "12.5"), (405, "4.10")))
    + "".join(_text_op(x, 555, text)
              for x, text in ((105, "Wheat"), (205, "South"), (305, "9.0"), (405, "3.75")))
    # Prose, so the region's word-space yardstick is a word space and not a column gap.
    + "".join(_text_op(100, 520 - 12 * i, "a prose line with ordinary word spaces")
              for i in range(6)))


SHEET_ROWS = [["Zone", "Area", "Sown"], ["North", 12.5, "2026-07-18"], ["South", 9.0, ""]]


def _synthetic_xlsx(path: Path) -> None:
    def cell(column: str, row: int, value):
        reference = "%s%d" % (column, row)
        if isinstance(value, float):
            return '<c r="%s"><v>%r</v></c>' % (reference, value)
        return ('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (reference, value)
                if value != "" else '<c r="%s"/>' % reference)
    rows = "".join(
        '<row r="%d">%s</row>' % (index, "".join(
            cell(chr(ord("A") + position), index, value) for position, value in enumerate(row)))
        for index, row in enumerate(SHEET_ROWS, 1))
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("[Content_Types].xml",
                      '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/content-types"><Default Extension="rels" ContentType='
                      '"application/vnd.openxmlformats-package.relationships+xml"/><Override '
                      'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
                      '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr("_rels/.rels",
                      '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                      '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
                      'schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
                      ' Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/_rels/workbook.xml.rels",
                      '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                      '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
                      'schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                      'Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr("xl/workbook.xml",
                      '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                      'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                      'officeDocument/2006/relationships"><sheets><sheet name="Zones" sheetId="1" '
                      'r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/worksheets/sheet1.xml",
                      '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
                      'spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % rows)


DOC_TABLE = [[("Dose table", 3)], [("Product", 1), ("Dose", 1), ("BBCH", 1)],
             [("Moddus", 1), ("0.4", 1), ("31", 1)]]


def synthetic_docx() -> bytes:
    """A minimal .docx: one Heading1, one paragraph with a `w:br`, one table with a spanning row."""
    def cell(text: str, span: int) -> str:
        grid = '<w:tcPr><w:gridSpan w:val="%d"/></w:tcPr>' % span if span > 1 else ""
        return "<w:tc>%s<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % (grid, text)
    rows = "".join("<w:tr>%s</w:tr>" % "".join(cell(text, span) for text, span in row)
                   for row in DOC_TABLE)
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Growth regulators</w:t>'
            '</w:r></w:p>'
            '<w:p><w:r><w:t>first line</w:t><w:br/><w:t>second line</w:t></w:r>'
            '<w:del><w:r><w:delText>struck out</w:delText></w:r></w:del></w:p>'
            '<w:tbl>%s</w:tbl>' % rows)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("[Content_Types].xml",
                         '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/content-types"><Default Extension="rels" ContentType='
                         '"application/vnd.openxmlformats-package.relationships+xml"/><Override '
                         'PartName="/word/document.xml" ContentType="application/vnd.'
                         'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                         '</Types>')
        package.writestr("_rels/.rels",
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                         'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
                         'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         'relationships/officeDocument" Target="word/document.xml"/>'
                         '</Relationships>')
        package.writestr(DOC_PART,
                         '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                         'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body>'
                         '</w:document>' % body)
    return stream.getvalue()


def self_check() -> int:
    """Render synthetic fixtures and compare against pinned expectations; 0 when green."""
    failures = []

    def want(label, got, expected):
        if got != expected:
            failures.append("%s: got %r want %r" % (label, got, expected))

    tally = new_tally()
    want("sanitise folds and counts",
         sanitise(" a b (cid:9) oﬃce dišerent­ \x01x – ≥", tally),
         "- a b office diserent x - >=")
    want("every class counted", (tally["dropped"]["pua_glyphs"], tally["dropped"]["nbsp"],
                                 tally["dropped"]["cid_survivors"], tally["dropped"]["ligatures"],
                                 tally["dropped"]["soft_hyphens"],
                                 tally["dropped"]["control_chars"],
                                 tally["broken_ligature_words"]),
         (1, 1, 1, 1, 1, 1, 1))
    want("a float keeps its spreadsheet form", cell_text(85.0), "85.0")
    want("an empty cell is '' and renders empty", cell_text(""), "")
    want("a date is one ISO day", cell_text(datetime.date(2026, 7, 18)), "2026-07-18")
    want("a ragged table is padded, never cut", markdown_table([["a", "b"], ["c"]]),
         ["| a | b |", "| --- | --- |", "| c |  |"])

    with tempfile.TemporaryDirectory(prefix="brewdoc_docx_") as tmp:
        doc_path = Path(tmp, "fixture.docx")
        doc_path.write_bytes(synthetic_docx())
        markdown, tally = render_doc(doc_path)
        want("a Word heading opens its own chapter", markdown.count("## Growth regulators"), 1)
        want("a w:br is a line break, and a deleted run is not carried",
             chapter_lines(markdown, "Growth regulators")[:2], ["first line", "second line"])
        want("a spanning cell is padded, so every column stays under its own head",
             chapter_lines(markdown, "Growth regulators")[2:],
             ["| Dose table |  |  |", "| --- | --- | --- |", "| Product | Dose | BBCH |",
              "| Moddus | 0.4 | 31 |"])
        want("the docx tally counts its chapter and its table",
             (tally["chapters"], tally["tables"], tally["text_regions"]), (1, 1, 1))

    with tempfile.TemporaryDirectory(prefix="brewdoc_") as tmp:
        pdf_path = Path(tmp, "fixture.pdf")
        pdf_path.write_bytes(synthetic_pdf(_fixture_pages()))
        markdown, tally = render_pdf(pdf_path)
        lines = markdown.splitlines()
        want("the captioned unlined table recovers its cells",
             lines[lines.index("| Zone | Area | Yield |") + 2], "| North | 12.5 | 4.10 |")
        want("the blank rows the text strategy invents are gone",
             markdown.count("|  |  |  |"), 0)
        want("the caption stays prose", markdown.count("Table 1. Yield by zone."), 3)
        want("two-column prose is read column by column",
             lines[lines.index("left column line 0 of the body text here") + 1],
             "left column line 1 of the body text here")
        want("the region was split at the gutter", tally["columns_split"], 3)
        want("the running head is dropped once per page",
             (markdown.count("Seasonal Operations Manual"), tally["dropped"]["running_heads"]),
             (0, 3))
        want("the page number is dropped", tally["dropped"]["page_numbers"], 3)
        want("determinism sha256", sha256(render_pdf(pdf_path)[0]), sha256(markdown))

        ruled_path = Path(tmp, "ruled.pdf")
        ruled_path.write_bytes(synthetic_pdf([FIXTURE_UNCLOSED, FIXTURE_SEGMENTED]))
        ruled, _ = render_pdf(ruled_path)
        want("a column closed on the header row alone is still read",
             chapter_lines(ruled, "Page 1")[:5],
             ["| Zone | Area | Yield |", "| --- | --- | --- |", "| North | 12.5 | 4.10 |",
              "| South | 9.0 | 3.75 |", "| Salt | P2O5 | 1.0 |"])
        want("a rule cut into one segment per column carries the grid",
             chapter_lines(ruled, "Page 2")[:4],
             ["| Crop | Zone | Area | Yield |", "| --- | --- | --- | --- |",
              "| Maize | North | 12.5 | 4.10 |", "| Wheat | South | 9.0 | 3.75 |"])
        want("a repaired ruling stays deterministic",
             sha256(render_pdf(ruled_path)[0]), sha256(ruled))

        scan = Path(tmp, "scan.pdf")
        scan.write_bytes(synthetic_pdf(["0 0 0 rg 100 100 200 200 re f\n"] * 2, font=False))
        rc, line, _ = run(scan)
        want("a document with no text layer is refused by name",
             (rc, line["file_ok"], line["reason"].split(" in ")[0]),
             (EXIT_FAIL, False, "no text layer: 2 of 2 pages carry zero characters"))

        book_path = Path(tmp, "fixture.xlsx")
        _synthetic_xlsx(book_path)
        book_md, book_tally = render_book(book_path)
        want("one chapter per sheet",
             [row for row in book_md.splitlines() if row.startswith("## ")], ["## Zones"])
        want("the sheet's measured types survive",
             chapter_lines(book_md, "Zones"),
             ["| Zone | Area | Sown |", "| --- | --- | --- |",
              "| North | 12.5 | 2026-07-18 |", "| South | 9.0 |  |"])
        want("the sheet route counts its sheets", book_tally["sheets"], 1)
        out = Path(tmp, "out.md")
        rc, line, _ = run(book_path, out=out)
        want("a written render is ASCII and re-readable",
             (rc, line["route"], out.read_text(encoding="ascii") == book_md), (EXIT_OK, "sheet", True))
        rc, line, _ = run(Path(tmp, "absent.pdf"))
        want("a missing file names the path", (rc, line["reason"]),
             (EXIT_FAIL, "no such file: %s" % Path(tmp, "absent.pdf")))

    for failure in failures:
        print("FAIL %s" % failure)
    print("self-check: %s (%d checks)" % ("FAIL" if failures else "ok", 27))
    return EXIT_FAIL if failures else EXIT_OK


def chapter_lines(markdown: str, heading: str) -> list[str]:
    """The non-blank lines under one `## heading`."""
    lines = markdown.splitlines()
    start = lines.index("## " + heading) + 1
    rest = [index for index, line in enumerate(lines[start:], start) if line.startswith("## ")]
    body = lines[start:rest[0]] if rest else lines[start:]
    return [line for line in body if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    """The command-line parser: one document, optional `--out`, `--self-check`."""
    parser = argparse.ArgumentParser(
        prog=RENDERED_BY,
        description="Render a PDF, a Word document or a spreadsheet as deterministic, "
                    "sanitised, LLM-readable "
                    "Markdown. PDF regions route by their own ruling edges: a lined table becomes "
                    "a Markdown table, an unlined captioned table is cropped then read, other "
                    "regions become fixed-width or plain text, and two-column prose is cropped at "
                    "the gutter. A spreadsheet becomes one '## <sheet>' chapter per sheet, and a "
                    ".docx one '## <heading>' chapter per Word heading, its tables kept as "
                    "tables.",
        epilog="Both paths may be absolute or relative; a relative one is resolved against the "
               "current working directory, never against this script's location, and --out "
               "creates its parent directories. "
               "Reads %s. A document with no text layer is REFUSED by name - there is no OCR here. "
               "One JSON route line always goes to stdout: it names the route, what was rendered, "
               "every sanitised item dropped, and what this route structurally cannot carry. "
               "Without --out the Markdown follows that line on stdout."
               % SUFFIXES)
    parser.add_argument("document", nargs="?",
                        help="the .pdf, .docx or spreadsheet to render")
    parser.add_argument("--out", help="write the Markdown here (ASCII); default stdout")
    parser.add_argument("--self-check", action="store_true", dest="self_check",
                        help="render synthetic fixtures twice and compare; exits 0 when green")
    return parser


def main(argv=None) -> int:
    """Command-line entry: prints the JSON receipt, then the Markdown unless `--out` was given."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.document:
        parser.print_usage()
        return EXIT_USAGE
    rc, line, markdown = run(args.document, args.out)
    print(json.dumps(line, ensure_ascii=True, sort_keys=True))
    if rc == EXIT_OK and not args.out:
        print(markdown)
    return rc


if __name__ == "__main__":
    sys.exit(main())
