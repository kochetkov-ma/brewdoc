"""Mixed-page facts and local captions preserve source-backed PDF ordering."""

from collections import Counter
import hashlib
from pathlib import Path
import re

import pdfplumber
import pytest

from brewdoc import common, pdf, selfcheck, service
from test_contract import NOT_APPLICABLE, PDF_NOT_CARRIED, schema_two, zero_tally
from test_pdf_regressions import rule, text_op


IMAGE = "q 20 0 0 20 100 100 cm BI /W 1 /H 1 /BPC 8 /CS /RGB /F /AHx ID ff0000> EI Q\n"
RECTANGLE = "0 0 0 rg 100 100 20 20 re f\n"
GRID_ROWS = [("Item", "Qty", "Cost"), ("A", "2", "5"), ("B", "3", "7")]
GRID_MARKDOWN = "| Item | Qty | Cost |\n| --- | --- | --- |\n| A | 2 | 5 |\n| B | 3 | 7 |\n"
LEFT = ["Left paragraph begins here.", "Its second line stays together.",
        "Its third line adds context.", "Its fourth line continues.",
        "Its fifth line stays left.", "Its sixth line concludes."]
CAPTION = ["Table 9: Small inventory spans", "past its numeric rules."]
FULL_WIDTH = ["This full-width paragraph crosses the local separator and remains whole after the table caption.",
              "Continuation stays in the full-width flow."]
DPO_HASH = "92cb3a2b71362acda98a789b03d88688fd33cf5fcf13f81d2b1de30ee7d3b67a"
DPO_LEFT_9 = [
    "TofurthercomparetheperformanceofPPOandDPOun-",
    "derdistributionshifts,weevaluatethePPOandDPOpoli-",
    "ciesfromourRedditTL;DRsummarizationexperimenton",
    "adifferentdistribution,newsarticlesinthetestsplitofthe",
    "CNN/DailyMaildataset[26],usingthebestsamplingtemper-",
    "aturesfromTL;DR(0and0.25). Theresultsarepresented",
    "in Table 1. We computed the GPT-4 win rate against the",
    "ground-truthsummariesinthedatasets,usingthesameGPT-",
]
DPO_CAPTION_9 = ["Table 1: GPT-4 win rates vs. ground",
                 "truth summaries for out-of-distribution", "CNN/DailyMailinputarticles."]
DPO_AFTER_9 = [
    '4(C)promptweusedforRedditTL;DR,butreplacingthewords"forumpost"with"newsarticle".',
    "Forthisnewdistribution,DPOcontinuestooutperformthePPOpolicybyasignificantmargin. This",
    "experimentprovidesinitialevidencethatDPOpoliciescangeneralizesimilarlywelltoPPOpolicies,",
    "eventhoughDPOdoesnotusetheadditionalunlabeledRedditTL;DRpromptsthatPPOuses.",
]
DPO_LEFT_10 = [
    "middle-performing(SFT,temp. 0.25)methodwith",
    "theaimofcoveringadiversityofsamplequalities;",
    "all three methods are compared against greedily-",
    "sampled PPO (its best-performing temperature).",
    "We find that with both prompts, GPT-4 tends to",
    "agreewithhumansaboutasoftenashumansagree",
    "witheachother,suggestingthatGPT-4isareason-",
    "ableproxyforhumanevaluations(duetolimited",
    "humanraters,weonlycollectmultiplehumanjudg-",
    "mentsfortheDPOandPPO-1comparisons). Over-",
    "all,theGPT-4(C)promptgenerallyprovideswin",
    "ratesmorerepresentativeofhumans;wetherefore",
    "usethispromptforthemainresultsinSection6.2.",
    "For additional details about the human study, in-",
    "cludingthewebinterfacepresentedtoratersand",
    "thelistofhumanvolunteers,seeAppendixD.3.",
]
DPO_CAPTION_10 = [
    "Table 2: Comparing human and GPT-4 win rates",
    "andper-judgmentagreementonTL;DRsummariza-",
    "tionsamples.HumansagreewithGPT-4aboutas",
    "muchastheyagreewitheachother.Eachexperi-",
    "mentcomparesasummaryfromthestatedmethod",
    "withasummaryfromPPOwithtemperature0.",
]
DPO_TABLE_9 = [["", "Win rate vs. ground truth", ""], ["Alg.", "Temp 0", "Temp 0.25"],
               ["DPO", "0.36", "0.31"], ["PPO", "0.26", "0.23"]]
DPO_TABLE_10 = [["", "DPO", "SFT", "PPO-1"], ["N respondents", "272", "122", "199"],
                ["GPT-4 (S) win %", "47", "27", "13"], ["GPT-4 (C) win %", "54", "32", "12"],
                ["Human win %", "58", "43", "17"], ["GPT-4 (S)-H agree", "70", "77", "86"],
                ["GPT-4 (C)-H agree", "67", "79", "85"], ["H-H agree", "65", "-", "87"]]


def page_heading(ordinal):
    """Return the unchanged ordered page anchor and heading contract."""
    return '<a id="brewdoc-page-%06d"></a>\n## Page %d\n' % (ordinal, ordinal)


def expected_pdf(path, body, *, pages=1, tables=0, regions=1, columns=0, notes=()):
    """Pin complete public outputs while keeping source observations Markdown-only."""
    keys = ["page/%06d" % ordinal for ordinal in range(1, pages + 1)]
    receipt = {
        "file_ok": True, "route": "pdf", "reason":
        "pdf rendered: %d pages, %d tables, %d text regions, %d column splits" % (pages, tables, regions, columns),
        "source": path.name, "out": None, "receipt_schema": "brewdoc.receipt/1",
        "unit_kind": "page", "units": pages, "tables": tables, "text_regions": regions,
        "columns_split": columns, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": PDF_NOT_CARRIED, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": keys,
    }
    markdown = schema_two(
        path, body, **NOT_APPLICABLE, name="sample\\.pdf", suffix=".pdf", route="pdf", unit="page",
        count=pages, keys=", ".join(keys) or "none", pages=pages, sheets=0, chapters=0, tables=tables,
        text_regions=regions, omissions="\n".join("- " + item.replace(".", "\\.")
                                                for item in (*PDF_NOT_CARRIED, *notes)),
        contents="\n".join("- [Page %d](#brewdoc-page-%06d)" % (ordinal, ordinal)
                           for ordinal in range(1, pages + 1))).rstrip() + "\n"
    markdown = markdown.replace("| Column splits | 0 |", "| Column splits | %d |" % columns)
    return 0, receipt, markdown


def table_commands(origin=360, width=140, positions=(365, 410, 465)):
    """Draw one fixed three-column table without deriving expected cells from the reader."""
    return "".join(rule(origin, y, width) for y in (704, 685, 650)) + "".join(
        text_op(x, y, text) for y, row in zip((692, 677, 662), GRID_ROWS)
        for x, text in zip(positions, row))


@pytest.mark.parametrize("drawing,counts", [
    (RECTANGLE, (0, 1, 0)), ("100 100 m 120 120 l S\n", (1, 0, 0)),
    ("100 100 m 105 115 115 105 120 120 c S\n", (0, 0, 1)),
], ids=["rectangle", "line", "curve"])
def test_mixed_pages_disclose_original_source_facts_without_changing_receipts(tmp_path, drawing, counts):
    # GIVEN text, empty, vector-only, image-only and combined image/vector source pages
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([text_op(72, 700, "kept body"), "", drawing, IMAGE, drawing + IMAGE]))
    with pdfplumber.open(path) as document:
        facts = [(len(page.chars), len(page.images), len(page.lines), len(page.rects), len(page.curves))
                 for page in document.pages]
    assert facts == [(9, 0, 0, 0, 0), (0, 0, 0, 0, 0), (0, 0, *counts),
                     (0, 1, 0, 0, 0), (0, 1, *counts)], (
        "page facts must come from the original source, including filled rectangles filtered from layout")
    # WHEN the public service renders all five source ordinals twice
    actual = service.run(path)
    repeated = service.run(path)
    # THEN only Known omissions gains exact observations; anchors, body and full receipt stay coherent
    body = page_heading(1) + "\nkept body\n\n" + page_heading(2) + "\n" + page_heading(3) + "\n" + page_heading(4) + "\n" + page_heading(5)
    notes = (
        "Page 2: no extractable text; no image or vector drawing objects detected.",
        "Page 3: no extractable text; vector drawing content present.",
        "Page 4: no extractable text; image content present.",
        "Page 5: no extractable text; image and vector drawing content present.",
    )
    expected = expected_pdf(path, body, pages=5, notes=notes)
    assert (actual, repeated) == (expected, expected), (
        "mixed-page observations must be deterministic Markdown facts without adding receipt fields or claiming scans")


def test_wholly_textless_pdf_keeps_exact_refusal_and_existing_destination(tmp_path):
    # GIVEN a valid two-page nonempty PDF with no source characters
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf(["", RECTANGLE + IMAGE]))
    out = tmp_path / "preserved.md"
    out.write_bytes(b"previous output\n")
    with pdfplumber.open(path) as document:
        counts = [len(page.chars) for page in document.pages]
    assert counts == [0, 0], "the old no-OCR refusal requires every page to be textless"
    # WHEN the public service is asked to write the wholly textless source
    actual = service.run(path, out)
    # THEN the unchanged full refusal leaves the prior destination intact
    reason = "no text layer: 2 of 2 pages carry zero characters in %s - this reader does no OCR" % path
    receipt = {
        "file_ok": False, "route": "pdf", "reason": reason, "source": path.name, "out": None,
        "receipt_schema": "brewdoc.receipt/1", "unit_kind": "page", "units": 0, "tables": 0,
        "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": PDF_NOT_CARRIED,
    }
    assert (actual, out.read_bytes()) == ((1, receipt, ""), b"previous output\n"), (
        "page observations must not turn a wholly textless nonempty PDF into successful output")


@pytest.mark.parametrize("streams,body,pages,regions", [
    ([], "\n", 0, 0),
    ([text_op(72, 700, "hello"), text_op(72, 700, "world")],
     page_heading(1) + "\nhello\n\n" + page_heading(2) + "\nworld\n", 2, 2),
], ids=["zero-pages", "all-text-pages"])
def test_empty_and_all_text_pdf_controls_preserve_complete_public_bytes(tmp_path, streams, body, pages, regions):
    # GIVEN a zero-page document or two ordinary pages with extractable text
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf(streams))
    with pdfplumber.open(path) as document:
        observed_pages = len(document.pages)
    assert observed_pages == pages, "the control document must have the declared physical page count"
    # WHEN the existing public PDF route converts the source
    actual = service.run(path)
    # THEN no source observation changes these complete successful outputs
    assert actual == expected_pdf(path, body, pages=pages, regions=regions), (
        "zero-page success and all-text Markdown/receipt bytes must remain unchanged")


def test_existing_two_page_rectangle_shape_discloses_vector_presence(tmp_path):
    # GIVEN the existing pdf-empty-page contract shape whose second page contains a rectangle
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([text_op(72, 700, "kept body"), RECTANGLE]))
    with pdfplumber.open(path) as document:
        page = document.pages[1]
        facts = (len(page.chars), len(page.images), len(page.rects))
    assert facts == (0, 0, 1), "the old empty-page contract input is vector content rather than object-free"
    # WHEN the public reader retains the source page ordinal
    actual = service.run(path)
    # THEN the exact new note describes the detected object without changing body or receipt
    body = page_heading(1) + "\nkept body\n\n" + page_heading(2)
    assert actual == expected_pdf(path, body, pages=2, notes=(
        "Page 2: no extractable text; vector drawing content present.",
    )), "rectangle filtering during layout must not erase original source-page facts"


def test_local_caption_preserves_neighbor_prose_and_stops_at_full_width_flow(tmp_path):
    # GIVEN a narrow table, outlying caption characters, a continued left lane and full-width prose
    commands = table_commands() + "".join(text_op(60, y, text) for y, text in zip((694, 680, 666, 652, 638, 624), LEFT))
    commands += "".join(text_op(355, y, text) for y, text in zip((635, 623), CAPTION))
    commands += "".join(text_op(60, y, text) for y, text in zip((604, 590), FULL_WIDTH))
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        page = document.pages[0]
        caption_lines = page.crop((350, 150, 515, 175)).extract_text().splitlines()
        source_letters = Counter(re.findall(r"[A-Za-z0-9]", "".join(char["text"] for char in page.chars)))
    assert caption_lines == CAPTION, "the source caption must be independently readable beyond its numeric rules"
    # WHEN the public reader partitions only the local inset before resuming full-width text
    actual = service.run(path)
    # THEN table/caption stay adjacent, left prose stays continuous and every source letter survives once
    body = page_heading(1) + "\n" + "\n".join(LEFT) + "\n\n" + GRID_MARKDOWN + "\n" + "\n".join(CAPTION) + "\n\n" + "\n".join(FULL_WIDTH) + "\n"
    rendered_letters = Counter(re.findall(r"[A-Za-z0-9]", "".join(selfcheck.chapter_lines(actual[2], "Page 1"))))
    assert (actual, rendered_letters) == (
        expected_pdf(path, body, tables=1, regions=3, columns=1), source_letters,
    ), "a local caption must not absorb a cropped tail of the full-width paragraph or interleave the left lane"


def test_caption_keeps_second_line_when_later_neighbor_prose_widens(tmp_path):
    # GIVEN later left prose ends seven points before the second caption line
    left = [*LEFT[:-1], "The inset paragraph keeps all these source words beside the table."]
    prefix = "This full-width paragraph resumes after the table caption and"
    suffix = "continues across the separator as one source line."
    full_width = [prefix + " " + suffix, FULL_WIDTH[1]]
    commands = table_commands() + "".join(
        text_op(60, y, text) for y, text in zip((694, 680, 666, 652, 638), left[:-1]))
    commands += text_op(50.63, 624, left[-1]) + "".join(
        text_op(355, y, text) for y, text in zip((635, 623), CAPTION))
    commands += text_op(80.99, 604, prefix) + text_op(355, 604, suffix) + text_op(60, 590, full_width[1])
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        page = document.pages[0]
        left_source = page.crop((40, 88, 350, 172))
        caption_source = page.crop((350, 150, 515, 175))
        late_left = page.crop((40, 158, 350, 172))
        bounds = (round(min(char["x0"] for char in late_left.chars), 2),
                  round(max(char["x1"] for char in late_left.chars), 2),
                  round(min(char["x0"] for char in caption_source.chars), 2))
        local_source = page.crop((0, 87.75, page.width, 171.07))
        source_gutters = (pdf.detect_gutter(page.chars, page.width),
                          pdf.detect_gutter(local_source.chars, page.width))
        observations = (left_source.extract_text().splitlines(), caption_source.extract_text().splitlines(),
                        page.crop((40, 178, 600, 210)).extract_text().splitlines(), bounds, source_gutters)
        source_letters = Counter(re.findall(r"[A-Za-z0-9]", "".join(char["text"] for char in page.chars)))
    assert observations == (left, CAPTION, full_width, (50.63, 348.0, 355.0), (None, None)), (
        "source lanes must remain separate while the wider prose prevents a qualifying density gutter")
    # WHEN the public PDF route renders the widening neighboring prose beside the caption
    actual = service.run(path)
    # THEN all left lines precede the table, both caption lines follow it and full-width flow resumes whole
    body = page_heading(1) + "\n" + "\n".join(left) + "\n\n" + GRID_MARKDOWN + "\n" + "\n".join(CAPTION) + "\n\n" + "\n".join(full_width) + "\n"
    rendered_letters = Counter(re.findall(r"[A-Za-z0-9]", "".join(selfcheck.chapter_lines(actual[2], "Page 1"))))
    assert (actual, rendered_letters) == (
        expected_pdf(path, body, tables=1, regions=3, columns=0), source_letters,
    ), "later side prose must not truncate the caption or merge its second line into neighboring text"


@pytest.mark.parametrize("commands,body,regions,columns", [
    (table_commands(60, 480, (65, 250, 465)) + text_op(60, 635, "Table 2: Global summary.")
     + text_op(60, 620, "Full-width prose is independent."),
     page_heading(1) + "\n" + GRID_MARKDOWN + "\nTable 2: Global summary.\nFull-width prose is independent.\n", 1, 0),
    (table_commands() + text_op(60, 635, "Table 7: Unrelated left note.") + text_op(360, 620, "Right data is ordinary."),
     page_heading(1) + "\nTable 7: Unrelated left note.\n\n" + GRID_MARKDOWN + "\nRight data is ordinary.\n", 2, 1),
], ids=["full-width-table", "unrelated-caption-in-left-lane"])
def test_nonlocal_caption_guards_preserve_existing_full_output(tmp_path, commands, body, regions, columns):
    # GIVEN a full-width table or a caption outside the narrow table's horizontal lane
    path = tmp_path / "sample.pdf"
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        source_words = document.pages[0].extract_text().splitlines()
    assert source_words[:3] == ["Item Qty Cost", "A 2 5", "B 3 7"], (
        "the guard source must preserve the same complete numeric table independently of caption routing")
    # WHEN the existing PDF route handles geometry outside local caption admission
    actual = service.run(path)
    # THEN unrelated source flow and unchanged table cells retain their exact old contract
    assert actual == expected_pdf(path, body, tables=1, regions=regions, columns=columns), (
        "local caption association must not change full-width tables or unrelated neighboring text")


@pytest.mark.corpus
@pytest.mark.parametrize("ordinal,region,left_box,caption_box,grid_box,left,caption,bounds,expected,regions", [
    (9, (100, 510, 510, 670), (100, 535, 350, 625), (352, 585, 507, 616), (355, 523, 497, 581),
     DPO_LEFT_9, DPO_CAPTION_9, (355.222, 503.999, 355.5, 496.439),
     [("text", ["6.3 Generalizationtoanewinputdistribution"]), ("text", DPO_LEFT_9),
      ("table", DPO_TABLE_9), ("text", DPO_CAPTION_9), ("text", DPO_AFTER_9)], 4),
    (10, (100, 170, 510, 345), (100, 170, 312, 345), (316, 284, 507, 345), (320, 179, 499, 282),
     DPO_LEFT_10, DPO_CAPTION_10, (317.557, 505.488, 321.078, 498.561),
     [("text", DPO_LEFT_10[:1]), ("text", DPO_LEFT_10[1:]),
      ("table", DPO_TABLE_10), ("text", DPO_CAPTION_10)], 3),
], ids=["dpo-page-nine", "dpo-page-ten"])
def test_dpo_selected_regions_keep_caption_extents_and_neighbor_flow(
        ordinal, region, left_box, caption_box, grid_box, left, caption, bounds, expected, regions):
    # GIVEN licensed source regions whose caption extends beyond both numeric rule edges
    path = Path(__file__).parent / "fixtures/pdf/arxiv-2305.18290v3.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == DPO_HASH, "DPO bytes must retain their CC-BY-4.0 provenance"
    with pdfplumber.open(path) as document:
        page = document.pages[ordinal - 1]
        caption_source, grid_source = page.crop(caption_box), page.crop(grid_box)
        observed_bounds = tuple(round(value, 3) for value in (
            min(char["x0"] for char in caption_source.chars), max(char["x1"] for char in caption_source.chars),
            min(edge["x0"] for edge in grid_source.edges), max(edge["x1"] for edge in grid_source.edges)))
        assert (page.crop(left_box).extract_text().splitlines(), caption_source.extract_text().splitlines(),
                observed_bounds) == (left, caption, bounds), (
            "independent source crops must prove every caption line and its outlying footprint")
        tally = common.new_tally()
        # WHEN the existing page reader converts only the reviewed local source region
        blocks, _margins = pdf.render_page(page.crop(region), tally)
    # THEN complete selected blocks, exact cells and all counters preserve the reviewed lane order
    assert (blocks, tally) == (expected, {**zero_tally(), "tables": 1, "text_regions": regions}), (
        "DPO caption ownership must include outlying characters, keep side prose contiguous and resume ordinary flow")
