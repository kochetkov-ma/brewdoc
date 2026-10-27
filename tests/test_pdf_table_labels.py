"""Source alignment and label ownership regressions for selected PDF regions."""

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pdfplumber
import pytest

from inspection_contract import rendering_result

from brewdoc import common, pdf, selfcheck, service


@pytest.fixture
def cell_ownership(monkeypatch):
    """Observe final cell glyph identities while retaining real extraction and folding."""
    observations = []
    logical_rows, fold_subscripts = pdf._logical_rows, pdf._fold_subscripts

    def observe_rows(page, bbox, xs, table=None):
        """Compare each table's assigned glyph objects with its source-owned spans."""
        assigned = []

        def observe_folding(page, bbox, cell, chars):
            """Keep original cell glyph objects while forwarding their normal folding."""
            assigned.extend((char["_source_index"], id(char)) for char in chars)
            return fold_subscripts(page, bbox, cell, chars)

        expected = sorted((char["_source_index"], id(char)) for char in page.chars
                          if char["text"].strip()
                          and bbox[0] <= (char["x0"] + char["x1"]) / 2 < bbox[2]
                          and bbox[1] <= (char["top"] + char["bottom"]) / 2 < bbox[3])
        with monkeypatch.context() as patch:
            patch.setattr(pdf, "_fold_subscripts", observe_folding)
            cells = logical_rows(page, bbox, xs, table)
        observations.append((sorted(assigned), expected))
        return cells

    monkeypatch.setattr(pdf, "_logical_rows", observe_rows)
    return observations


def body_words(text_spans):
    """Build six independent numeric records with explicit textual spans."""
    return [dict(text=text, x0=left, x1=right, top=row * 20, bottom=row * 20 + 10)
            for row, spans in enumerate(text_spans)
            for text, left, right in [*spans, (str(111 + row), 250, 260),
                                     (str(211 + row), 290, 300), (str(311 + row), 330, 340)]]


def test_centered_body_field_is_separate_from_left_aligned_names():
    # GIVEN independent text fields with stable left and centre anchors
    spans = [(180, 240), (200, 220), (190, 230), (195, 225), (197, 223), (185, 235)]
    words = body_words([[("Source", 100, 120), (label, left, right)] for label, (left, right)
                        in zip(("web pages", "code", "social media", "papers", "books", "encyclopedic"), spans)])
    page = SimpleNamespace(extract_words=lambda **kwargs: words)
    assert [(left + right) / 2 for left, right in spans] == [210] * 6, "Type spans must share a centre despite six different left edges"
    # WHEN body records establish their field boundaries
    actual = pdf._body_columns(page, (90, -1, 350, 120), [90, 350])
    # THEN source names and centered labels each own one independent field
    assert actual == [90, 156.25, 238.75, 275, 315, 350], "the repeated Type centre must establish the missing second textual field"


@pytest.mark.parametrize("spans,expected", [
    pytest.param([(180, 200), (180, 210), (180, 220), (180, 230), (180, 240), (180, 250)],
                 [90, 160, 237.5, 275, 315, 350], id="stable-left-with-varying-centres"),
    pytest.param([(180, 220)] * 6, [90, 160, 235, 275, 315, 350],
                 id="same-group-left-and-centre-must-not-count-twice"),
])
def test_left_supported_text_group_does_not_create_an_extra_field(spans, expected):
    # GIVEN two text fields whose existing left anchors already own the records
    words = body_words([[("Source name", 100, 140), ("Type name", left, right)] for left, right in spans])
    page = SimpleNamespace(extract_words=lambda **kwargs: words)
    assert len(words) == 30, "six complete records must supply two text and three numeric groups each"
    # WHEN alignment inference considers those source groups
    actual = pdf._body_columns(page, (90, -1, 350, 120), [90, 350])
    # THEN a centre cannot duplicate an independently supported left field
    assert actual == expected, "source group ownership must prevent duplicate text columns"


def stub_commands(label_y, extra_rules=(), record_y=(665, 645, 625, 605), bottom=580):
    """Place one first-field label among four ruled numeric records."""
    horizontal = "".join(f"80 {y} 410 .5 re f\n" for y in (700, 680, bottom, *extra_rules))
    vertical = "".join(f"{x} {bottom} .5 {700 - bottom} re f\n" for x in (80, 165, 240, 320, 400, 490))
    headings = "".join(selfcheck._text_op(x, 686, value) for x, value in
                       zip((85, 180, 250, 330, 410), ("Model", "Size", "A", "B", "C")))
    records = "".join(selfcheck._text_op(x, y, value)
                      for y, row in zip(record_y,
                                        (("7B", "11", "12", "13"), ("13B", "21", "22", "23"),
                                         ("33B", "31", "32", "33"), ("65B", "41", "42", "43")))
                      for x, value in zip((180, 250, 330, 410), row))
    return horizontal + vertical + headings + records + selfcheck._text_op(85, label_y, "Family")


def expected_receipt(name):
    """Specify the complete successful one-page table receipt."""
    return {"file_ok": True, "route": "pdf", "reason": "pdf rendered: 1 pages, 1 tables, 0 text regions, 0 column splits",
            "source": name, "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "page",
            "units": 1, "tables": 1, "text_regions": 0, "columns_split": 0,
            "dropped": dict.fromkeys(common.DROP_KEYS, 0), "broken_ligature_words": 0,
            "not_carried": ["images, figures and the text drawn inside them", "a table that spans a page break",
                            "text rotated out of the horizontal reading order"],
            "artifacts": [], "markdown_schema": "brewdoc.markdown/2", "unit_keys": ["page/000001"]}


def test_ruled_group_stub_belongs_once_to_first_covered_record(tmp_path, cell_ownership):
    # GIVEN one vertically centred stub covering four independent ruled records
    path = tmp_path / "stub.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([stub_commands(635)]))
    with pdfplumber.open(path) as document:
        source = document.pages[0].extract_text().splitlines()
        source_count = len(document.pages[0].chars)
    assert source == ["Model Size A B C", "7B 11 12 13", "13B 21 22 23", "Family",
                      "33B 31 32 33", "65B 41 42 43"], "the source must show a centred stub between four complete records"
    # WHEN the source is converted through its public route
    code, receipt, markdown = rendering_result(service.run(path))
    content = selfcheck.chapter_lines(markdown, "Page 1")
    # THEN the row span appears once on the first record without creating a fifth record
    assert (code, receipt, content) == (0, expected_receipt("stub.pdf"), [
        "| Model | Size | A | B | C |", "| --- | --- | --- | --- | --- |",
        "| Family | 7B | 11 | 12 | 13 |", "|  | 13B | 21 | 22 | 23 |",
        "|  | 33B | 31 | 32 | 33 |", "|  | 65B | 41 | 42 | 43 |",
    ]), "the enclosing ruled group must assign Family to 7B exactly once and preserve every value"
    assert len(re.sub(r"[|\s]", "", "".join(content[:1] + content[2:]))) == source_count, "every source glyph must survive once in the complete table"
    assert sorted(re.sub(r"[|\s]", "", "".join(content[:1] + content[2:]))) == sorted("".join(source).replace(" ", "")), "the emitted table must retain exactly the source glyph multiset"
    assert [assigned for assigned, _ in cell_ownership] == [expected for _, expected in cell_ownership], "each extraction must assign every original source glyph object to exactly one final cell"


@pytest.mark.parametrize("label_y,extra_rules,record_y,bottom,expected", [
    pytest.param(665, (650,), (645, 625, 605, 585), 560, ["| Family |  |  |  |  |"],
                 id="standalone-subsection-above-group"),
    pytest.param(555, (), (665, 645, 625, 605), 580, [], id="stub-outside-group-bounds"),
])
def test_independent_stub_outside_record_group_stays_separate(tmp_path, label_y, extra_rules, record_y, bottom, expected):
    # GIVEN an independently ruled heading or a label outside the table bounds
    path = tmp_path / "independent.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([stub_commands(label_y, extra_rules, record_y, bottom)]))
    with pdfplumber.open(path) as document:
        source_labels = [char["text"] for char in document.pages[0].chars][-6:]
    assert source_labels == list("Family"), "the source must contain exactly one complete independent label"
    # WHEN the page is converted
    code, receipt, markdown = rendering_result(service.run(path))
    content = selfcheck.chapter_lines(markdown, "Page 1")
    # THEN independent labels are not attached to a numeric record
    assert [line for line in content if line.startswith("| Family")] == expected, "a label outside the record group must retain its independent ownership"
    assert (code, receipt["tables"], markdown.count("Family")) == (0, 1, 1), "conversion must retain one table and emit the label once"


def test_wrapped_cell_continuation_remains_on_its_original_record(tmp_path):
    # GIVEN a first-field continuation directly beneath a populated record
    path = tmp_path / "wrapped.pdf"
    commands = stub_commands(653) + selfcheck._text_op(85, 665, "Long")
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        source = document.pages[0].extract_text().splitlines()
    assert source[:3] == ["Model Size A B C", "Long 7B 11 12 13", "Family"], "the source must place the continuation immediately below its first record"
    # WHEN the wrapped table is converted
    code, receipt, markdown = rendering_result(service.run(path))
    # THEN wrapping preserves the original record rather than becoming a row-span stub
    assert (code, receipt, selfcheck.chapter_lines(markdown, "Page 1")) == (
        0, expected_receipt("wrapped.pdf"), ["| Model | Size | A | B | C |", "| --- | --- | --- | --- | --- |",
        "| Long Family | 7B | 11 | 12 | 13 |", "|  | 13B | 21 | 22 | 23 |",
        "|  | 33B | 31 | 32 | 33 |", "|  | 65B | 41 | 42 | 43 |"]), "a genuine wrapped cell must retain its complete first-record text"


def math_chars(last_changes=None):
    """Keep original glyph identities beside one expanded alias on another tier."""
    letters = [dict(text=text, _source_text=text, x0=index, x1=index + 1, width=1,
                    top=20, bottom=21, fontname="Plain", size=1, _source_index=index)
               for index, text in enumerate("ref")]
    letters[-1].update(last_changes or {})
    return letters + [dict(text="[pi]", _source_text="π", x0=1, x1=2, width=1,
                           top=0, bottom=1, fontname="Symbol", size=1, _source_index=3)]


def test_expanded_math_alias_does_not_split_source_contiguous_label():
    # GIVEN an expanded numerator alias above an unchanged contiguous denominator label
    chars = math_chars()
    source = [(char["_source_index"], char["_source_text"]) for char in chars]
    assert [(char["x0"], char["x1"]) for char in chars[:3]] == [(0, 1), (1, 2), (2, 3)], "ref must have exactly contiguous same-tier source edges"
    # WHEN a single display grid reserves numerator and denominator widths
    tally = common.new_tally()
    actual = pdf._math_block(chars, [{"x0": 0, "x1": 4, "top": 10}], tally)
    # THEN source letters remain one label while the vector bar separates the tiers
    assert (actual[-2], actual) == ("ref", ["```", "   [pi]", "---------", "ref", "```"]), "an expanded alias on another tier must not insert spaces inside ref"
    assert ([(char["_source_index"], char["_source_text"]) for char in chars], tally) == (
        source, common.new_tally()), "layout must preserve every source identity and leave all drop counters unchanged"


@pytest.mark.parametrize("changes,expected", [
    pytest.param({"x0": 3, "x1": 4}, ["re", "f"], id="natural-word-gap"),
    pytest.param({"fontname": "Other"}, ["re", "f"], id="mixed-font"),
    pytest.param({"size": 2}, ["re", "f"], id="mixed-size"),
    pytest.param({"top": 20.2, "bottom": 21.2}, ["re", "f"], id="distinct-baseline-within-display-tier"),
    pytest.param({"top": 22, "bottom": 22.5, "size": 0.5}, ["re", "f"], id="true-script-on-another-tier"),
    pytest.param({"text": "[pi]", "_source_text": "π"}, ["re", "[pi]"], id="true-glyph-alias"),
])
def test_math_label_join_requires_physical_source_continuity(changes, expected):
    # GIVEN a final glyph whose geometry or identity prevents one plain-letter run
    chars = math_chars(changes)
    assert [char["_source_index"] for char in chars] == [0, 1, 2, 3], "all four original source glyph identities must remain independent"
    # WHEN the display grid renders the independent source pieces
    actual = pdf._math_block(chars, [{"x0": 0, "x1": 4, "top": 10}], common.new_tally())
    # THEN a display tier cannot override source gaps, fonts, sizes or baselines
    assert re.findall(r"\[pi\]|[A-Za-z]+", "\n".join(actual[3:-1])) == expected, "independent source pieces must not become a fabricated contiguous identifier"


@pytest.mark.corpus
def test_olmo_source_and_centered_type_keep_five_fields(cell_ownership):
    # GIVEN the licensed OLMo page with six centred Type phrases and a Total row
    path = Path(__file__).parent / "fixtures/pdf/arxiv-2402.00838v4.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "121e82248da601e8e4d4da5fa9b4486660ecf0f5a0fae5292b7af3c656c2385d", "the reviewed CC-BY-4.0 source bytes must match SOURCES.md"
    # WHEN only source page 3 is rendered
    with pdfplumber.open(path) as document:
        page = document.pages[2]
        tally = common.new_tally()
        blocks, margins = pdf.render_page(page, tally)
        page.close()
    tables = [payload for kind, payload in blocks if kind == "table"]
    rows = tables[1]
    headings = [" ".join(cell for cell in column if cell) for column in zip(*rows[:5])]
    # THEN all source columns and complete header tiers retain their ownership
    assert (headings, rows[5:]) == (["Source", "Type", "UTF-8 bytes (GB)", "Docs (millions)", "Tokens (billions)"], [
        ["Common Crawl", "web pages", "9,812", "3,734", "2,180"],
        ["GitHub", "code", "1,043", "210", "342"], ["Reddit", "social media", "339", "377", "80"],
        ["Semantic Scholar", "papers", "268", "38.8", "57"],
        ["Project Gutenberg", "books", "20.4", "0.056", "5.2"],
        ["Wikipedia", "encyclopedic", "16.2", "6.2", "3.7"], ["Total", "", "11,519", "4,367", "2,668"],
    ]), "the visually reviewed source has five fields, six complete records and one Total with empty Type"
    assert (len(tables), tally["text_regions"], tally["columns_split"], tally["dropped"], margins) == (
        2, 5, 1, dict.fromkeys(common.DROP_KEYS, 0), []), "field repair must preserve page regions, table count and zero glyph drops"
    assert [assigned for assigned, _ in cell_ownership] == [expected for _, expected in cell_ownership], "each extraction must assign every original source glyph object to exactly one final cell"
    table_index = blocks.index(("table", tables[1]))
    assert re.sub(r"\s", "", "".join(blocks[table_index + 1][1])) == (
        "Table2:CompositionofDolma.TokenscountsarebasedontheGPT-NeoXtokenizer."), "the source caption must follow its table exactly once before right-column prose resumes"


@pytest.mark.corpus
def test_llama_ruled_stub_keeps_every_table8_record_without_an_extra_row(cell_ownership):
    # GIVEN the licensed LLaMA Table 8 whose single stub spans four model sizes
    path = Path(__file__).parent / "fixtures/pdf/arxiv-2302.13971v1.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "2e663675ae36ad12adb2f5a05281bac2747ecf8d23d92bedd9f937a89fee7136", "the reviewed CC-BY-4.0 source bytes must match SOURCES.md"
    # WHEN only source page 6 is rendered
    with pdfplumber.open(path) as document:
        page = document.pages[5]
        tally = common.new_tally()
        blocks, margins = pdf.render_page(page, tally)
        page.close()
    tables = [payload for kind, payload in blocks if kind == "table"]
    # THEN every original record keeps its values and LLaMA appears once on 7B
    assert tables[1] == [
        ["", "Params", "HumanEval", "", "MBPP", ""], ["pass@", "", "@1", "@100", "@1", "@80"],
        ["LaMDA", "137B", "14.0", "47.3", "14.8", "62.4"],
        ["PaLM", "8B", "3.6*", "18.7*", "5.0*", "35.7*"],
        ["PaLM", "62B", "15.9", "46.3*", "21.4", "63.2*"],
        ["PaLM-cont", "62B", "23.7", "-", "31.2", "-"], ["PaLM", "540B", "26.2", "76.2", "36.8", "75.0"],
        ["LLaMA", "7B", "10.5", "36.5", "17.7", "56.2"], ["", "13B", "15.8", "52.5", "22.0", "64.0"],
        ["", "33B", "21.7", "70.7", "30.2", "73.4"], ["", "65B", "23.7", "79.3", "37.7", "76.8"],
    ], "the reviewed source has four LLaMA records and no independent LLaMA-only record"
    assert (len(tables), tally["text_regions"], tally["columns_split"], tally["dropped"], margins) == (
        2, 4, 1, dict.fromkeys(common.DROP_KEYS, 0), []), "stub repair must preserve other page regions and zero glyph drops"
    assert [assigned for assigned, _ in cell_ownership] == [expected for _, expected in cell_ownership], "each extraction must assign every original source glyph object to exactly one final cell"
    table_index = blocks.index(("table", tables[1]))
    assert [re.sub(r"\s", "", blocks[index][1][0]) for index in (table_index + 1, table_index + 2)] == [
        "Table8:Modelperformanceforcodegeneration.", "3.6MassiveMultitaskLanguage"], "Table 8 must retain its following source caption and subsection in their original order"


@pytest.mark.corpus
def test_dpo_contiguous_ref_labels_survive_the_fraction_grid():
    # GIVEN two exactly contiguous source ref labels in the licensed Equation 7
    path = Path(__file__).parent / "fixtures/pdf/arxiv-2305.18290v3.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "92cb3a2b71362acda98a789b03d88688fd33cf5fcf13f81d2b1de30ee7d3b67a", "the reviewed CC-BY-4.0 source bytes must match SOURCES.md"
    with pdfplumber.open(path) as document:
        page = document.pages[3]
        labels = [char for char in page.chars if char["top"] == 688.2805408 and char["text"] in "ref"]
        assert [(char["text"], char["fontname"], round(char["size"], 4)) for char in labels] == [
            (letter, "PVSOKV+NimbusRomNo9L-Regu", 6.9738) for letter in "refref"], "both denominator labels must use the same unchanged Latin font, size and baseline"
        assert [round(right["x0"] - left["x1"], 6) for start in (0, 3)
                for left, right in zip(labels[start:start + 2], labels[start + 1:start + 3])] == [0.0] * 4, "every r/e/f source edge must be physically contiguous"
        pi_tiers = [(char["text"], round(char["top"], 8)) for char in page.chars
                    if char["text"] == "π" and round(char["top"], 8) in (684.2231444, 688.2805408)]
        assert pi_tiers == [("π", 684.2231444)] * 2, "both denominator pi glyphs must belong to the earlier source tier, with none on the ref baseline"
        folio = [(char["text"], round(char["top"], 8), round(char["bottom"], 8))
                 for char in page.chars if char["top"] >= 730]
        assert folio == [("4", 742.0773216, 752.0399216)], "the source footer must contain the single printed page number retained by margin discovery"
        # WHEN only source page 4 is rendered
        tally = common.new_tally()
        blocks, margins = pdf.render_page(page, tally)
        page.close()
    math = [payload for kind, payload in blocks if kind == "math"]
    denominator = math[3][-2]
    # THEN source labels remain legible without losing fraction tiers or source bars
    assert (len(math), re.findall(r"\bref\b", denominator), re.findall(r"\blog\b", "\n".join(math[1])),
            (math[3][-3].count("[pi]"), denominator.count("[pi]")),
            [len(re.findall(r"-{3,}", "\n".join(block))) for block in math],
            tally["tables"], tally["text_regions"], tally["dropped"], margins) == (
        4, ["ref", "ref"], ["log", "log"], (2, 0), [2, 1, 2, 2], 0, 10, dict.fromkeys(common.DROP_KEYS, 0), ["4"]), "reviewed ref/log labels must survive with four math blocks, all seven fraction bars and the original page folio"
    assert [kind for kind, _ in blocks] == [
        "text", "math", "block", "math", "block", "math", "text", "math", "text", "text"], "all four equations must keep their original neighboring prose order"
