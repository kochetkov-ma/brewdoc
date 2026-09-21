"""PDF adapter: text-layer pages, each region routed by its own ruling, via pdfplumber."""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import resolve1
from pdfplumber.utils import extract_text

from brewdoc.common import (CID_RE, BrewdocError, Rendered, _assemble, head_key, new_tally,
                            sanitise)

PDF_SUFFIX = ".pdf"

PDF_NOT_CARRIED = ("images, figures and the text drawn inside them",
                   "a table that spans a page break",
                   "text rotated out of the horizontal reading order")

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
REPEATS_AS_HEAD = 3      # a first/last line this many pages share is furniture, not content

TEXT_TABLE = {"vertical_strategy": "text", "horizontal_strategy": "text"}

PDF_GLYPHS = {"−": "-", "α": "[alpha]", "β": "[beta]", "θ": "[theta]", "π": "[pi]",
              "ϕ": "[phi-symbol]", "σ": "[sigma]", "τ": "[tau]", "∇": "[nabla]",
              "∈": "[in]", "≻": "[succeeds]", "∑": "[sum]", "∏": "[product]",
              "∼": "~", "∗": "*", "ˆ": "^", "′": "[prime]", "·": "[middle-dot]",
              "×": "[times]", "†": "[dagger]", "‡": "[double-dagger]", "̸": "[overlay:0338]"}
PDF_FONT_GLYPHS = {"summationtext": "[sum]", "summationdisplay": "[sum]",
                   "productdisplay": "[product]"}
for _glyph_stem, _glyph_token in (("parenleft", "("), ("parenright", ")"),
                                 ("bracketleft", "["), ("bracketright", "]"),
                                 ("braceleft", "{"), ("braceright", "}")):
    for _glyph_size in ("big", "Big", "bigg", "Bigg"):
        PDF_FONT_GLYPHS[_glyph_stem + _glyph_size] = _glyph_token


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


def _prose_gutters(band, width: float) -> list[float]:
    """Find repeated prose-column starts without treating numeric columns as page flow."""
    chars = [char for line in band for char in _line_chars(line)]
    if len(chars) < GUTTER_MIN_CHARS or sum(char.get("_source_text", char["text"]).isdigit() for char in chars) > len(chars) * 0.12:
        return []
    gaps = []
    for index, line in enumerate(band):
        for left, right in zip(_line_chars(line), _line_chars(line)[1:]):
            if right["x0"] - left["x1"] >= max(8, width * 0.015):
                gaps.append((right["x0"], left["x1"], index))
    clusters = []
    for gap in sorted(gaps):
        if not clusters or gap[0] - clusters[-1][0][0] > COLUMN_TOLERANCE:
            clusters.append([])
        clusters[-1].append(gap)
    gutters = []
    for group in clusters:
        rows = {gap[2] for gap in group}
        if len(rows) < max(3, len(band) * 0.35):
            continue
        gutter = (min(gap[0] for gap in group) + max(gap[1] for gap in group)) / 2
        # A left segment that is a bare integer on every line is a line-number rail, not a column.
        if not all("".join(char["text"] for char in _line_chars(band[row]) if char["x1"] <= gutter).isdigit()
                   for row in rows):
            gutters.append(gutter)
    return gutters


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
                 columns: list[float] | None, source=None) -> list[tuple]:
    source = source if source is not None else page
    blocks = []
    tally["text_regions"] += 1
    start, end = _tabular_run(flags)
    captioned = any(TABLE_CAPTION_RE.match(line["text"] or "") for line in band[:start])
    # The caption route needs the tabular lines in ONE run; a table with wrapped rows has them on
    # both sides of a prose line, and the rule grid reads that shape instead.
    grid = (_caption_table(source, band, start, end, tally)
            if captioned and end - start >= MIN_TABULAR_LINES and sum(flags) == end - start
            else None)
    if grid:
        head = _text_lines(page, band[:start], tally, margins)
        tail = _text_lines(page, band[end:], tally, margins)
        blocks += [("text", head), ("table", grid), ("text", tail)]
        tally["tables"] += 1
    elif end - start >= MIN_TABULAR_LINES:
        ruled = _rule_table(source, band, columns) if columns else None
        if ruled:
            first, last, cells = ruled
            blocks += [("text", _text_lines(page, band[:first], tally, margins)),
                       ("table", [[sanitise(cell, tally).strip() for cell in row]
                                  for row in cells]),
                       ("text", _text_lines(page, band[last:], tally, margins))]
            tally["tables"] += 1
        else:
            blocks.append(("block", _layout_block(source, band, tally)))
    else:
        blocks.append(("text", _text_lines(page, band, tally, margins)))
    return [block for block in blocks if block[1]]


def _text_region(page, crop, tally: dict, margins: list[str], wide_table: bool,
                 columns: list[float] | None = None, split: bool = True) -> list[tuple]:
    # Bands first, then a gutter search inside each: a two-column half usually sits under a
    # full-width abstract or table, and a whole-page histogram sees the two mixed.
    lines = sorted(crop.extract_text_lines(), key=lambda line: (line["top"], line["x0"]))
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
        gutters = _prose_gutters(band, page.width) if split and not wide_table else []
        if gutters:
            tally["columns_split"] += len(gutters)
            bounds = [crop.bbox[0], *gutters, crop.bbox[2]]
            for left, right in zip(bounds, bounds[1:]):
                column = _crop(crop, left, band[0]["top"] - 1, right, max(line["bottom"] for line in band) + 1)
                if column is not None and column.chars:
                    blocks += _text_region(page, column, tally, margins, False, columns, split=False)
            continue
        gutter = (detect_gutter(chars, page.width)
                  if split and not wide_table and not tabular else None)
        if gutter is None:
            blocks += _band_blocks(page, band, flags, tally, margins, columns, crop)
            continue
        tally["columns_split"] += 1
        top, bottom = band[0]["top"] - 1, band[-1]["bottom"] + 1
        for left, right in ((page.bbox[0], gutter), (gutter, page.bbox[2])):
            column = _crop(crop, left, top, right, bottom)
            if column is not None and column.chars:
                blocks += _text_region(page, column, tally, margins, wide_table, columns,
                                       split=False)
    return blocks


def _page_tables(page) -> list:
    """Keep non-overlapping data regions, excluding single-column boxes and plot grids."""
    kept = []
    drawn = page.find_tables()
    candidates = drawn + [table for table in _horizontal_tables(page)
                          if sum(_overlap(table._brewdoc_bounds, other.bbox) for other in drawn) < 2]
    candidates.sort(key=lambda table: (getattr(table, "_brewdoc_bounds", table.bbox)[1],
                                       -getattr(table, "_brewdoc_bounds", table.bbox)[3], table.bbox[0]))
    for table in candidates:
        xs, ys = _table_grid(page, table)
        if len(xs) < 3 or len(ys) < 2:
            continue
        grid = _logical_rows(page, (xs[0], ys[0], xs[-1], ys[-1]), xs, table)
        occupied = [sum(bool(cell.strip()) for cell in row) for row in grid]
        if sum(count >= 2 for count in occupied) < 2:
            continue
        if any(_overlap(table.bbox, previous.bbox) for previous in kept):
            continue
        kept.append(table)
    return kept


def _horizontal_tables(page) -> list:
    """Find repeated numeric records between matching horizontal rules without side borders."""
    groups = []
    for edge in page.edges:
        if edge["orientation"] != "h" or edge["width"] <= 0:
            continue
        group = next((group for group in groups if abs(group[0]["x0"] - edge["x0"]) <= RULE_SNAP
                      and abs(group[0]["x1"] - edge["x1"]) <= RULE_SNAP), None)
        if group is None:
            group = []
            groups.append(group)
        group.append(edge)
    found = []
    for group in groups:
        tops = _snap([edge["top"] for edge in group])
        if len(tops) < 2:
            continue
        bbox = (group[0]["x0"], tops[0], group[0]["x1"], tops[-1])
        crop = _crop(page, *bbox)
        for table in crop.find_tables(TEXT_TABLE) if crop is not None else []:
            if _numeric_records(table.extract()) < 2:
                continue
            table._brewdoc_bounds = bbox
            table._brewdoc_columns = [column.bbox[0] for column in table.columns] + [table.bbox[2]]
            found.append(table)
    return found


def _numeric_records(grid) -> int:
    """Require multiple independently populated numeric fields for alignment-only grids."""
    return sum(sum(bool(re.fullmatch(r"[+\-$]?\d[\d,.%]*", (cell or "").strip()))
                   for cell in row) >= 2 for row in grid)


def _table_grid(page, table):
    """Refine sparse structural columns only with repeated numeric text alignment."""
    if hasattr(table, "_brewdoc_grid"):
        return table._brewdoc_grid
    if hasattr(table, "_brewdoc_bounds"):
        bbox = table._brewdoc_bounds
        xs = [min(bbox[0], table._brewdoc_columns[0])] + table._brewdoc_columns[1:-1] + [max(bbox[2], table._brewdoc_columns[-1])]
        ys = [bbox[1], bbox[3]]
    else:
        xs, ys = _implied_grid(_ruled_region(page, table.bbox))
    if len(xs) < 2 or len(ys) < 2:
        return xs, ys
    crop = _crop(page, xs[0], ys[0], xs[-1], ys[-1])
    if crop is not None and len(xs) <= 5:
        candidates = crop.find_tables(TEXT_TABLE)
        for candidate in candidates:
            if len(candidate.columns) >= len(xs) and _numeric_records(candidate.extract()) >= 2:
                xs = [xs[0]] + [column.bbox[0] for column in candidate.columns[1:]] + [xs[-1]]
    if len(xs) <= 6 or hasattr(table, "_brewdoc_bounds"):
        derived = _body_columns(page, (xs[0], ys[0], xs[-1], ys[-1]), xs)
        if derived and len(derived) >= len(xs):
            xs = derived
            table._brewdoc_body_grid = True
    for word in page.extract_words(x_tolerance_ratio=0.15):
        if ys[0] <= (word["top"] + word["bottom"]) / 2 <= ys[-1]:
            if word["x0"] < xs[0] < word["x1"]:
                xs[0] = word["x0"]
            if word["x0"] < xs[-1] < word["x1"]:
                xs[-1] = word["x1"]
    table._brewdoc_grid = xs, ys
    return table._brewdoc_grid


def _body_columns(page, bbox, structural) -> list[float]:
    """Use repeated record anchors to retain close numeric fields and adjacent text stubs."""
    words = [word for word in page.extract_words(x_tolerance_ratio=0.15)
             if bbox[1] <= (word["top"] + word["bottom"]) / 2 <= bbox[3]]
    if not words:
        return []
    height = statistics.median(word["bottom"] - word["top"] for word in words)
    bands = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not bands or word["top"] - bands[-1][0]["top"] > max(1, height * 0.4):
            bands.append([])
        bands[-1].append(word)
    numeric = re.compile(r"[+\-$]?\d[\d,./%]*(?:[A-Za-z*]+)?$")
    records = [band for band in bands if sum(bool(numeric.fullmatch(word["text"]))
               and bbox[0] <= (word["x0"] + word["x1"]) / 2 <= bbox[2] for word in band) >= 2]
    if len(records) < 2:
        return []
    anchors = []
    for row, band in enumerate(records):
        groups = []
        for word in sorted(band, key=lambda item: item["x0"]):
            if (groups and word["x0"] - groups[-1]["x1"] <= height * 0.6
                    and not numeric.fullmatch(word["text"]) and not numeric.fullmatch(groups[-1]["text"])):
                groups[-1]["text"] += " " + word["text"]
                groups[-1]["x1"] = word["x1"]
            else:
                groups.append(dict(word))
        for group in groups:
            anchor = group["x1"] if numeric.fullmatch(group["text"]) else group["x0"]
            anchors.append((anchor, row, group["x0"], group["x1"]))
    clusters = []
    for anchor in sorted(anchors):
        if not clusters or anchor[0] - clusters[-1][0][0] > COLUMN_TOLERANCE:
            clusters.append([])
        clusters[-1].append(anchor)
    stable = [group for group in clusters if len({anchor[1] for anchor in group}) >= max(2, len(records) * 0.5)]
    stable = [group for group in stable if bbox[0] <= statistics.median(anchor[0] for anchor in group) <= bbox[2]
              or len({anchor[1] for anchor in group}) >= len(records) * 0.75]
    stable.sort(key=lambda group: statistics.median(anchor[2] for anchor in group))
    if len(stable) < 2:
        return []
    intervals = [(statistics.median(anchor[2] for anchor in group), statistics.median(anchor[3] for anchor in group))
                 for group in stable]
    gaps = [right[0] - left[1] for left, right in zip(intervals, intervals[1:]) if right[0] > left[1]]
    reach = max(height * 4, statistics.median(gaps) * 3) if gaps else height * 4
    intervals = [span for span in intervals if bbox[0] - reach <= span[0] and span[1] <= bbox[2] + reach]
    if len(intervals) < 2:
        return []
    boundaries = [min(bbox[0], intervals[0][0] - 1)]
    for left, right in zip(intervals, intervals[1:]):
        if left[1] >= right[0]:
            return []
        candidates = [x for x in structural[1:-1] if left[1] < x < right[0]]
        boundaries.append(min(candidates, key=lambda x: abs(x - (left[1] + right[0]) / 2))
                          if candidates else (left[1] + right[0]) / 2)
    return boundaries + [max(bbox[2], intervals[-1][1] + 1)]


def _overlap(first, second) -> bool:
    """Return whether two page regions overlap in both dimensions."""
    return (first[0] < second[2] and second[0] < first[2]
            and first[1] < second[3] and second[1] < first[3])


def _structural_page(page):
    """Remove shading rectangles while retaining stroked borders and thin filled rules."""
    return page.filter(lambda obj: obj["object_type"] != "rect" or obj.get("stroke")
                       or min(obj["width"], obj["height"]) <= RULE_SNAP)


def _pdf_font_encodings(page) -> dict:
    """Read only explicit names in embedded Type1 encoding headers, never glyph programs."""
    fonts = resolve1(page.page_obj.resources.get("Font", {}))
    encodings, seen = {}, set()
    for reference in fonts.values():
        font = resolve1(reference)
        name = getattr(font.get("BaseFont"), "name", "")
        if name in seen:
            encodings[name] = {}
            continue
        seen.add(name)
        if "Encoding" in font:
            continue
        descriptor = resolve1(font.get("FontDescriptor", {}))
        stream = resolve1(descriptor.get("FontFile"))
        if not stream or not hasattr(stream, "get_data"):
            continue
        length = resolve1(stream.get("Length1", 0))
        if not isinstance(length, int) or length <= 0:
            continue
        header = stream.get_data()[:length]
        definitions = list(re.finditer(rb"/Encoding\s+256\s+array(.*?)\bdef\b", header, re.S))
        if len(definitions) != 1:
            continue
        names = {}
        for code, glyph in re.findall(rb"dup\s+(\d+)\s+/([A-Za-z0-9]+)\s+put", definitions[0][1]):
            code = int(code)
            names[code] = glyph.decode("ascii") if code not in names else None
        encodings[name] = names
    return encodings


def _pdf_page(page):
    """Prepare private character copies before any text cache, preserving source identities."""
    encodings = _pdf_font_encodings(page)
    prepared = page.filter(lambda obj: True)
    chars = []
    for index, source in enumerate(page.chars):
        char = dict(source, _source_index=index, _source_text=source["text"], _unresolved=None)
        text, font = char["text"], char["fontname"]
        cid = CID_RE.fullmatch(text)
        if cid:
            code = int(text[5:-1])
            glyph = encodings.get(font, {}).get(code)
            char["text"] = PDF_FONT_GLYPHS.get(glyph, "[font-glyph:%s:%s]" % (font, code))
            char["_unresolved"] = None if glyph in PDF_FONT_GLYPHS else "cid_survivors"
        elif len(text) == 1 and unicodedata.category(text) == "Co":
            char["text"] = "[font-glyph:%s:U+%04X]" % (font, ord(text))
            char["_unresolved"] = "pua_glyphs"
        else:
            char["text"] = "".join(PDF_GLYPHS.get(glyph, glyph) for glyph in text)
        chars.append(char)
    prepared.objects["char"] = chars
    return prepared


def _math_regions(page) -> list:
    """Find displayed math from source glyphs, neighboring tiers and fraction rules."""
    prose = []
    for line in page.extract_text_lines():
        ink = [char for char in line["chars"] if char["text"].strip()]
        ordinary = sum(char["_source_text"].isalpha()
                       and not any(name in char["fontname"] for name in ("CMMI", "CMSY", "CMEX", "Symbol"))
                       for char in ink)
        if ordinary >= 35 and ordinary >= len(ink) * 0.6:
            prose.append((line["x0"], line["top"], line["x1"], line["bottom"] + 2))
    chars = [char for char in page.chars if char.get("upright", True) and char["text"].strip()
             and not any(box[0] <= (char["x0"] + char["x1"]) / 2 <= box[2]
                         and box[1] <= (char["top"] + char["bottom"]) / 2 <= box[3] for box in prose)]
    if not chars:
        return []
    height = statistics.median(char["height"] for char in chars)
    tiers = []
    for char in sorted(chars, key=lambda item: (item["top"], item["x0"])):
        if not tiers or char["top"] - tiers[-1][0]["top"] > max(0.8, height * 0.2):
            tiers.append([])
        tiers[-1].append(char)
    groups, current = [], []
    for tier in tiers:
        math = sum(not char["_unresolved"]
                   and (any(name in char["fontname"] for name in ("CMMI", "CMSY", "CMEX", "Symbol"))
                        or char.get("_source_text") in PDF_GLYPHS) for char in tier)
        text = "".join(char["_source_text"] for char in sorted(tier, key=lambda item: item["x0"]))
        dominant = math and (math * 3 >= len(tier) or len(tier) <= 12)
        label = re.fullmatch(r"\(\d+\)", text) is not None
        close = current and tier[0]["top"] - max(char["bottom"] for char in current[-1]) <= height * 2.5
        if close:
            previous_bottom = max(char["bottom"] for char in current[-1])
            close = not any(previous_bottom < box[1] < tier[0]["top"] for box in prose)
        ordinary = sum(char["_source_text"].isalpha() for char in tier)
        folio = text.isdigit() and tier[0]["top"] > page.bbox[3] - page.height * MARGIN_BAND
        short_tier = bool(current) and len(tier) <= 18 and not (not math and ordinary >= 8) and not folio
        if (dominant or label or short_tier) and (not current or close):
            current.append(tier)
        else:
            if current:
                groups.append(current)
            current = [tier] if dominant else []
    if current:
        groups.append(current)
    regions = []
    for group in groups:
        ink = [char for tier in group for char in tier]
        if len(group) < 2:
            continue
        bbox = (min(char["x0"] for char in ink), min(char["top"] for char in ink),
                max(char["x1"] for char in ink), max(char["bottom"] for char in ink))
        if bbox[2] - bbox[0] <= height * 4:
            continue
        bars = []
        for edge in page.edges:
            if edge["orientation"] != "h" or edge["width"] < 4 or edge["width"] >= bbox[2] - bbox[0]:
                continue
            y = edge["top"]
            overlap = [char for char in ink if char["x0"] < edge["x1"] and char["x1"] > edge["x0"]]
            if (any(char["bottom"] <= y for char in overlap) and any(char["top"] >= y for char in overlap)
                    and bbox[1] < y < bbox[3]
                    and not any(abs(y - bar["top"]) <= RULE_SNAP
                                and abs(edge["x0"] - bar["x0"]) <= RULE_SNAP for bar in bars)):
                bars.append(edge)
        if bars:
            bbox = (min(bbox[0], min(bar["x0"] for bar in bars)), bbox[1],
                    max(bbox[2], max(bar["x1"] for bar in bars)), bbox[3])
        regions.append((bbox, ink, bars))
    return regions


def _math_block(chars, bars, tally: dict) -> list[str]:
    """Use one expanded x grid for every measured math tier and source fraction bar."""
    unit = statistics.median(char["width"] for char in chars if char["width"] > 0) or 1
    left = min([char["x0"] for char in chars] + [bar["x0"] for bar in bars])
    rows = []
    for char in sorted(chars, key=lambda item: (item["top"], item["x0"])):
        if not rows or char["top"] - rows[-1][0] > 0.8:
            rows.append((char["top"], []))
        rows[-1][1].append((round((char["x0"] - left) / unit), char["text"]))
    reservations = {}
    for _, tokens in rows:
        widths = Counter()
        for anchor, text in tokens:
            widths[anchor] += len(text)
        for anchor, width in widths.items():
            reservations[anchor] = max(reservations.get(anchor, 0), width)
    for bar in bars:
        for x in (bar["x0"], bar["x1"]):
            reservations.setdefault(round((x - left) / unit), 0)
    positions, previous, end = {}, 0, 0
    for anchor in sorted(reservations):
        positions[anchor] = max(anchor, end + max(0, anchor - previous - 1))
        end = positions[anchor] + reservations[anchor]
        previous = anchor
    out = []
    for y, tokens in rows:
        text = ""
        for anchor, token in sorted(tokens, key=lambda item: item[0]):
            text += " " * max(0, positions[anchor] - len(text)) + token
        out.append((y, sanitise(text, tally, keep_layout=True)))
    for bar in bars:
        start, stop = (positions[round((x - left) / unit)] for x in (bar["x0"], bar["x1"]))
        out.append((bar["top"], " " * start + "-" * max(3, stop - start)))
    return ["```", *(text for _, text in sorted(out, key=lambda item: item[0])), "```"]


# --- PDF: ruling the producer never finished --------------------------------------------------
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
    """Follow connected rule segments without flooding through unrelated footer boxes."""
    edges = page.edges
    boxes = [(edge["x0"], edge["top"], edge["x1"], edge["bottom"]) for edge in edges]
    cell_size = max(RULE_SNAP * 4, page.width / 32)
    buckets = {}

    def cells(box, padding=0):
        """List spatial buckets intersecting a segment's optional tolerance envelope."""
        return ((x, y) for x in range(int((box[0] - padding) // cell_size), int((box[2] + padding) // cell_size) + 1)
                for y in range(int((box[1] - padding) // cell_size), int((box[3] + padding) // cell_size) + 1))

    for index, box in enumerate(boxes):
        for cell in cells(box):
            buckets.setdefault(cell, []).append(index)

    def touches(first, second):
        return (first[0] <= second[2] + RULE_SNAP and second[0] <= first[2] + RULE_SNAP
                and first[1] <= second[3] + RULE_SNAP and second[1] <= first[3] + RULE_SNAP)

    taken = {index for index, box in enumerate(boxes) if touches(box, bbox)}
    pending = list(sorted(taken))
    while pending:
        current = pending.pop()
        nearby = {index for cell in cells(boxes[current], RULE_SNAP) for index in buckets.get(cell, ())}
        for index in sorted(nearby - taken):
            if touches(boxes[current], boxes[index]):
                taken.add(index)
                pending.append(index)
    return [edges[index] for index in sorted(taken)]


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


def _fold_subscripts(source, box, text: str, chars=None) -> str:
    # Centre-based ownership keeps a glyph crossing a rule in one cell.
    # Subscript tolerance must not reach the next body line.
    x0, top, x1, bottom = box
    chars = source.chars if chars is None else chars
    chars = [char for char in chars
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


def _trim(grid: list[list[str]]) -> list[list[str]]:
    width = max((len(row) for row in grid), default=0)
    rows = [row + [""] * (width - len(row)) for row in grid]
    keep = [index for index in range(width) if any(row[index].strip() for row in rows)]
    return [[row[index] for index in keep] for row in rows if any(cell.strip() for cell in row)]


def _repair_table(page, table):
    """Read source baselines on the structural grid, assigning each word exactly once."""
    xs, ys = _table_grid(page, table)
    if len(xs) < 3 or len(ys) < 2:
        return None
    bbox = (xs[0], ys[0], xs[-1], ys[-1])
    grid = _logical_rows(page, bbox, xs, table)
    return (grid, bbox) if grid else None


def _logical_rows(page, bbox, xs, table=None) -> list[list[str]]:
    """Separate data baselines and attach only compatible wrapped cell continuations."""
    crop = page.filter(lambda obj: obj["object_type"] != "char"
                       or (bbox[0] <= (obj["x0"] + obj["x1"]) / 2 < bbox[2]
                           and bbox[1] <= (obj["top"] + obj["bottom"]) / 2 < bbox[3]))
    words = crop.extract_words(x_tolerance_ratio=0.2, return_chars=True)
    words = [word for word in words if bbox[0] <= (word["x0"] + word["x1"]) / 2 <= bbox[2]
             and bbox[1] <= (word["top"] + word["bottom"]) / 2 <= bbox[3]]
    if not words:
        return []
    height = statistics.median(word["bottom"] - word["top"] for word in words)
    tolerance = max(1.0, height * 0.4)
    bands = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not bands or abs(word["top"] - bands[-1][0]["top"]) > tolerance:
            bands.append([])
        bands[-1].append(word)
    def column_at(x):
        return min(len(xs) - 2, sum(x >= boundary for boundary in xs[1:-1]))

    def body_band(band):
        """Separate populated numeric records from bold or oversized header baselines."""
        chars = [char for word in band for char in word["chars"]]
        numeric = any(re.fullmatch(r"[+\-$]?\d[\d,./%]*", word["text"]) for word in band)
        occupied = {column_at((word["x0"] + word["x1"]) / 2) for word in band}
        return (numeric and len(occupied) >= 2
                and not all("bold" in char["fontname"].lower() for char in chars)
                and statistics.median(char["size"] for char in chars) <= height * 1.25)

    body_top = next((band[0]["top"] for band in bands if body_band(band)), bands[0][0]["top"])
    verticals = [edge for edge in page.edges if edge["orientation"] == "v"]
    inferred = table is not None and getattr(table, "_brewdoc_body_grid", False)
    horizontal = table is not None and hasattr(table, "_brewdoc_bounds")
    centres, body_gaps = {}, {}
    if horizontal:  # body cell spans per column: they anchor the header heads above them
        spans = []
        for band in bands:
            if band[0]["top"] < body_top:
                continue
            cells = {}
            for word in band:
                column = column_at(word["x0"] + 1 if inferred else (word["x0"] + word["x1"]) / 2)
                low, high = cells.get(column, (word["x0"], word["x1"]))
                cells[column] = (min(low, word["x0"]), max(high, word["x1"]))
            spans.append(cells)
        for column in {index for cells in spans for index in cells}:
            centres[column] = statistics.median((cells[column][0] + cells[column][1]) / 2
                                                for cells in spans if column in cells)
            body_gaps[column] = min((cells[column + 1][0] - cells[column][1] for cells in spans
                                     if column in cells and column + 1 in cells), default=0.0)

    def headed(word):
        """Body column a header span is centred over, or None when it is centred over none."""
        centre = (word["x0"] + word["x1"]) / 2
        column = min(centres, key=lambda index: abs(centres[index] - centre), default=None)
        return column if column is not None and abs(centres[column] - centre) <= COLUMN_TOLERANCE else None

    rows = []
    for band in bands:
        ordered_words = sorted(band, key=lambda item: item["x0"])
        header = horizontal and band[0]["top"] < body_top
        if header:
            grouped = []
            for word in ordered_words:
                aim = headed(grouped[-1]) if grouped else None
                # a head aimed further right keeps its own cell, unless the body columns touch
                split = (aim is not None and body_gaps[aim] > height * 0.4
                         and column_at((word["x0"] + word["x1"]) / 2) > aim)
                if grouped and word["x0"] - grouped[-1]["x1"] <= height * 0.4 and not split:
                    grouped[-1]["text"] += " " + word["text"]
                    grouped[-1]["x1"] = word["x1"]
                    grouped[-1]["chars"] += word["chars"]
                else:
                    grouped.append(dict(word, chars=list(word["chars"])))
            ordered_words = grouped
        middle = statistics.median((word["top"] + word["bottom"]) / 2 for word in band)
        unused = {index for index, boundary in enumerate(xs[1:-1], 1)
                  if not any(abs(edge["x0"] - boundary) <= RULE_SNAP
                             and edge["top"] <= middle <= edge["bottom"] for edge in verticals)
                  and (any(word["x0"] + 1 < boundary < word["x1"] - 1
                           and re.search(r"[A-Za-z]", word["text"]) for word in band)
                       or any(left["x1"] <= boundary <= right["x0"]
                              and right["x0"] - left["x1"] <= height * 0.6
                              and re.search(r"[A-Za-z]", left["text"]) and re.search(r"[A-Za-z]", right["text"])
                              for left, right in zip(ordered_words, ordered_words[1:])))}
        for word in ordered_words:
            x = (word["x0"] + word["x1"]) / 2
            y = (word["top"] + word["bottom"]) / 2
            column = column_at(word["x0"] + 1) if inferred else column_at(x)
            if header and (aim := headed(word)) is not None:
                column = aim
            anchor = band[0]["top"]
            if table is not None and not horizontal and word["top"] < body_top:
                separators = _snap([bbox[0], bbox[2]] + [edge["x0"] for edge in verticals
                                   if bbox[0] <= edge["x0"] <= bbox[2]
                                   and edge["top"] <= y <= edge["bottom"]])
                left = max(boundary for boundary in separators if boundary <= x)
                right = min(boundary for boundary in separators if boundary > x)
                column = min(range(len(xs) - 1), key=lambda index: abs(xs[index] - left))
                boxes = [box for box in table.cells if box[0] <= x < box[2]
                         and box[1] <= y < box[3] and abs(box[0] - left) <= RULE_SNAP
                         and abs(box[2] - right) <= RULE_SNAP]
                box = min(boxes, key=lambda box: (box[2] - box[0]) * (box[3] - box[1])) if boxes else None
                if box is not None:
                    anchor = box[1]
            elif not inferred:
                while column in unused:
                    column -= 1
            row = next((row for row in rows if abs(row["anchor"] - anchor) <= tolerance), None)
            if row is None:
                row = {"cells": [""] * (len(xs) - 1), "anchor": anchor, "top": word["top"],
                       "bottom": word["bottom"], "standalone": False, "left": {}, "chars": {}}
                rows.append(row)
            row["cells"][column] = (row["cells"][column] + " " + word["text"]).strip()
            row["chars"].setdefault(column, []).extend(word["chars"])
            row["left"][column] = min(row["left"].get(column, word["x0"]), word["x0"])
            row["top"] = min(row["top"], word["top"])
            row["bottom"] = max(row["bottom"], word["bottom"])
            row["standalone"] |= any("bold" in char["fontname"].lower() or char["size"] > height * 1.25
                                     for char in word["chars"])
    rows.sort(key=lambda row: row["anchor"])
    out, owned = [], []
    for index, row in enumerate(rows):
        filled = [column for column, cell in enumerate(row["cells"]) if cell]
        previous = rows[index - 1] if index else None
        following = rows[index + 1] if index + 1 < len(rows) else None
        if len(filled) == 1 and not row["standalone"]:
            column = filled[0]
            if (out and previous and row["top"] - previous["bottom"] <= height
                    and out[-1][column] and sum(bool(cell) for cell in out[-1]) >= 2
                    and abs(row["left"][column] - previous["left"].get(column, row["left"][column])) <= height * 3
                    and not any(edge["orientation"] == "h" and previous["bottom"] <= edge["top"] <= row["top"]
                                and edge["x0"] <= xs[column] + RULE_SNAP
                                and edge["x1"] >= xs[column + 1] - RULE_SNAP for edge in page.edges)):
                out[-1][column] += " " + row["cells"][column]
                owned[-1].setdefault(column, []).extend(row["chars"][column])
                continue
            if (not column and following and following["top"] - row["bottom"] <= height
                    and following["cells"][column] and sum(bool(cell) for cell in following["cells"]) >= 2):
                following["cells"][column] = row["cells"][column] + " " + following["cells"][column]
                following["chars"].setdefault(column, []).extend(row["chars"][column])
                continue
        out.append(row["cells"])
        owned.append(row["chars"])
    for cells, columns in zip(out, owned):
        for column, chars in columns.items():
            cells[column] = _fold_subscripts(page, bbox, cells[column], chars)
    return out


def _one_rule(edges: list) -> bool:
    """True when the edges are one segmented rule, not separate figures sharing a baseline."""
    ordered = sorted(edges, key=lambda edge: edge["x0"])
    reach = ordered[0]["x1"]
    for edge in ordered[1:]:
        if edge["x0"] > reach + RULE_SNAP:
            return False
        reach = max(reach, edge["x1"])
    return True


def _rule_columns(page) -> tuple[list[float], float, float] | None:
    # A rule emitted as one segment per column carries the grid in its segment ends. The opening
    # rule is the topmost of the same width: caption and running head sit above it.
    groups: dict[int, list] = {}
    for edge in page.edges:
        if edge["orientation"] == "h":
            groups.setdefault(int(edge["top"] / RULE_SNAP), []).append(edge)
    groups = {key: group for key, group in groups.items() if _one_rule(group)}
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
    """Render qualified table boxes and preserve the text beside them in column order."""
    page = _structural_page(_pdf_page(page))
    mathematics = _math_regions(page)
    math_chars = {char["_source_index"] for _, chars, _ in mathematics for char in chars}
    math_bars = [bar for _, _, bars in mathematics for bar in bars]

    def outside_math(obj):
        """Reserve only the characters and source bars owned by displayed math."""
        if obj["object_type"] == "char":
            return obj["_source_index"] not in math_chars
        return not (obj["object_type"] in ("line", "rect") and obj["height"] <= RULE_SNAP
                    and any(abs(obj["top"] - bar["top"]) <= RULE_SNAP
                            and abs(obj["x0"] - bar["x0"]) <= RULE_SNAP
                            and abs(obj["x1"] - bar["x1"]) <= RULE_SNAP for bar in math_bars))

    page = page.filter(outside_math)
    tables = _page_tables(page)
    columns, margins = _rule_columns(page), []
    regions = [(bbox, (chars, bars), "math") for bbox, chars, bars in mathematics]
    for table in tables:
        repaired = _repair_table(page, table)
        cells, bbox = repaired if repaired else (_table_cells(page, table), table.bbox)
        if not any(_overlap(bbox, previous[0]) for previous in regions):
            regions.append((bbox, cells, "table"))

    counted = set()

    def count_chars(chars):
        """Count unresolved glyphs only at final source-character ownership."""
        for char in chars:
            index = char["_source_index"]
            if index not in counted and char["_unresolved"]:
                tally["dropped"][char["_unresolved"]] += 1
            counted.add(index)

    def crop_region(box):
        """Assign boundary-crossing characters by their centers before clipping geometry."""
        filtered = page.filter(lambda obj: obj["object_type"] != "char" or (
            box[0] <= (obj["x0"] + obj["x1"]) / 2 < box[2]
            and box[1] <= (obj["top"] + obj["bottom"]) / 2 < box[3]))
        return _crop(filtered, *box)

    def text_region(box):
        """Emit residual text from the prepared view without restoring excluded objects."""
        crop = crop_region(box)
        if crop is not None:
            count_chars(crop.chars)
        return (_text_region(page, crop, tally, margins, False, columns)
                if crop is not None and crop.chars else [])

    def object_block(box, cells, kind):
        """Emit one table or math object and account for its source characters once."""
        if kind == "math":
            count_chars(cells[0])
            tally["text_regions"] += 1
            return text_region(box) + [("math", _math_block(*cells, tally))]
        crop = crop_region(box)
        if crop is not None:
            count_chars(crop.chars)
        tally["tables"] += 1
        return [("table", [[sanitise(cell, tally).strip() for cell in row] for row in cells])]

    def render_region(box, items):
        """Partition disjoint columns before carving stacked objects within each column."""
        if not items:
            return text_region(box)
        crop = crop_region(box)
        gutter = detect_gutter(crop.chars, box[2] - box[0]) if crop is not None else None
        if (gutter is not None and all(not (bbox[0] < gutter < bbox[2]) for bbox, _, _ in items)
                and all(bbox[2] - bbox[0] < WIDE_TABLE * (box[2] - box[0]) for bbox, _, _ in items)):
            tally["columns_split"] += 1
            return (render_region((box[0], box[1], gutter, box[3]),
                                  [item for item in items if item[0][2] <= gutter])
                    + render_region((gutter, box[1], box[2], box[3]),
                                    [item for item in items if item[0][0] >= gutter]))
        ordered = sorted(items, key=lambda item: (item[0][1], item[0][0]))
        top, bottom = ordered[0][0][1], ordered[0][0][3]
        group = []
        for item in ordered:
            if item[0][1] >= bottom and group:
                break
            group.append(item)
            bottom = max(bottom, item[0][3])
        blocks = text_region((box[0], box[1], box[2], top))
        groups = []
        for item in sorted(group, key=lambda item: item[0][0]):
            if not groups or item[0][0] >= max(other[0][2] for other in groups[-1]):
                groups.append([])
            groups[-1].append(item)
        if len(groups) > 1 and any(len(column) > 1 for column in groups):
            left = box[0]
            for column in groups:
                start = min(item[0][0] for item in column)
                end = max(item[0][2] for item in column)
                blocks += text_region((left, top, start, bottom))
                blocks += render_region((start, top, end, bottom), column)
                left = end
            blocks += text_region((left, top, box[2], bottom))
            return blocks + render_region((box[0], bottom, box[2], box[3]), ordered[len(group):])
        left = box[0]
        for bbox, cells, kind in sorted(group, key=lambda item: item[0][0]):
            blocks += text_region((left, top, bbox[0], bottom))
            blocks += text_region((bbox[0], top, bbox[2], bbox[1]))
            blocks += object_block(bbox, cells, kind)
            blocks += text_region((bbox[0], bbox[3], bbox[2], bottom))
            left = bbox[2]
        blocks += text_region((left, top, box[2], bottom))
        blocks += render_region((box[0], bottom, box[2], box[3]), ordered[len(group):])
        return blocks

    blocks = render_region(page.bbox, regions)
    return [block for block in blocks if block[1]], margins


def furniture(margins: list[list[str]]) -> tuple[set[str], set[str]]:
    """(running heads as head_key forms, page numbers) from every page's margin candidates."""
    counts = Counter(head_key(text) for page in margins for text in set(page))
    numbers = {text for page in margins for text in page if PAGE_NUMBER_RE.match(text)}
    heads = {key for key, count in counts.items() if count >= REPEATS_AS_HEAD}
    return heads, numbers


def _render_pdf(path: Path, _sheets=None) -> Rendered:
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
    units = [(number, "Page %d" % number, blocks)
             for number, blocks in enumerate(pages, 1)]
    return _assemble(path, "pdf", "page", len(pages), units, tally, not_carried=PDF_NOT_CARRIED,
                     heads=heads, numbers=numbers)


def render_pdf(path) -> tuple[str, dict]:
    """(Markdown, tally) for a PDF; refuses a document with no text layer, by name."""
    return _render_pdf(Path(path))[:2]
