"""Synthetic regressions for PDF record boundaries, prose and technical notation."""

import re

import pdfplumber
import pytest

from brewdoc import common, selfcheck, service

FONT_NAME = "BDTEST+CMEX10"


def text_op(x, y, text):
    """Return one 10 pt text-showing operator at (x, y)."""
    return "BT /F1 10 Tf 1 0 0 1 %s %s Tm (%s) Tj ET\n" % (x, y, text)


def rule(x, y, width, height=0.5):
    """Return one filled rectangle, drawn as a table rule."""
    return "%s %s %s %s re f\n" % (x, y, width, height)


def source_lines(path):
    """Read source text independently of brewdoc's region and table routing."""
    with pdfplumber.open(path) as document:
        return document.pages[0].extract_text().splitlines()


def symbol_pdf(commands):
    """Add an explicitly encoded Symbol font while retaining valid PDF offsets."""
    document = selfcheck.synthetic_pdf([commands])
    fonts = (rb"/Font << /F1 \1 0 R /F2 << /Type /Font /Subtype /Type1 /BaseFont /Symbol "
             rb"/Encoding << /Differences [45 /minus 98 /beta 112 /pi 229 /summation] >> >> >>")
    document = re.sub(rb"/Font << /F1 (\d+) 0 R >>", fonts, document)
    prefix = document[:document.index(b"xref\n")]
    objects = list(re.finditer(rb"(?m)^\d+ 0 obj$", prefix))
    size = len(objects) + 1
    offsets = b"".join(f"{match.start():010d} 00000 n \n".encode() for match in objects)
    xref = f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n" + offsets
    trailer = f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{len(prefix)}\n%%EOF\n"
    return prefix + xref + trailer.encode()


def type1_program(codes, trailing=""):
    """A Type1 font header: `codes` is its builtin encoding, `trailing` holds later definitions."""
    return (f"%!PS-AdobeFont-1.0: {FONT_NAME} 001.000\n"
            f"/FontName /{FONT_NAME} def\n/FontType 1 def\n"
            "/FontMatrix [0.001 0 0 0.001 0 0] readonly def\n"
            "/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
            + "".join(f"dup {code} /{glyph} put\n" for code, glyph in codes)
            + f"readonly def\n{trailing}currentdict end\ncurrentfile eexec\n")


SUMMATION_PROGRAM = type1_program(((80, "summationtext"),))
PRODUCT_PROGRAM = type1_program(((80, "productdisplay"),))
UNRELATED_ARRAY_PROGRAM = type1_program(((80, "summationtext"),),
                                        "/Other 256 array dup 80 /productdisplay put readonly def\n")
OVERRIDDEN_PROGRAM = type1_program(((80, "summationtext"), (255, "summationtext")))
PDF_DIFFERENCES = "/Encoding << /Differences [255 /productdisplay] >> "


def font_identity_pdf(fonts, encoding, code):
    """Show character `code` once per `(resource name, Type1 program)` entry, each font embedded."""
    commands = "".join(f"BT /{name} 10 Tf 1 0 0 1 {x} 690 Tm (\\{code:03o}) Tj ET\n"
                       for x, (name, _) in zip((80, 110), fonts))
    original = selfcheck.synthetic_pdf([commands]).split(b"xref\n", 1)[0]
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", original)}
    programs = dict(fonts)
    numbers = {name: 5 + 2 * index for index, name in enumerate(programs)}
    references = " ".join(f"/{name} {number} 0 R" for name, number in numbers.items())
    objects[3] = objects[3].replace(b"/F1 5 0 R", references.encode())
    widths = " ".join(["500"] * 256)
    for name, program in programs.items():
        data, stream_number = program.encode("ascii"), numbers[name] + 1
        descriptor = (f"/Type /FontDescriptor /FontName /{FONT_NAME} /Flags 4 "
                      f"/FontBBox [-1000 -1000 2000 2000] /ItalicAngle 0 /Ascent 1000 "
                      f"/Descent -200 /CapHeight 700 /StemV 80 /FontFile {stream_number} 0 R")
        objects[numbers[name]] = (f"<< /Type /Font /Subtype /Type1 /BaseFont /{FONT_NAME} "
                                  f"/FirstChar 0 /LastChar 255 /Widths [{widths}] "
                                  f"{encoding}/FontDescriptor << {descriptor} >> >>").encode()
        objects[stream_number] = (f"<< /Length {len(data)} /Length1 {len(data)} "
                                  "/Length2 0 /Length3 0 >>\nstream\n").encode() + data + b"\nendstream"
    output, offsets = b"%PDF-1.4\n", []
    for number, body in sorted(objects.items()):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start, size = len(output), len(objects) + 1
    output += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    output += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return output


@pytest.mark.parametrize("horizontal_rules", [(700, 680, 590), (700, 680, 635, 590)],
                         ids=["unruled-body", "two-records-per-ruled-band"])
def test_data_rows_remain_separate_without_individual_horizontal_rules(tmp_path, horizontal_rules):
    # GIVEN four independent records inside partially ruled columns
    path = tmp_path / "records.pdf"
    rows = [(686, ("Region", "Q1", "Q2")), (665, ("Alpha", "11", "12")),
            (645, ("Beta", "21", "22")), (625, ("Gamma", "31", "32")),
            (605, ("Delta", "41", "42"))]
    drawing = "".join(rule(80, y, 330) for y in horizontal_rules)
    drawing += "".join(rule(x, 590, 0.5, 110) for x in (80, 190, 300, 410))
    drawing += "".join(text_op(x, y, value) for y, row in rows
                       for x, value in zip((85, 195, 305), row))
    path.write_bytes(selfcheck.synthetic_pdf([drawing]))
    assert source_lines(path) == ["Region Q1 Q2", "Alpha 11 12", "Beta 21 22",
                                  "Gamma 31 32", "Delta 41 42"], "the source must contain four distinct records"
    # WHEN the PDF is rendered through the public entry point
    code, receipt, markdown = service.run(path)
    # THEN every record retains its label and its two values in one row
    assert (code, receipt["file_ok"], receipt["route"]) == (0, True, "pdf"), "conversion must succeed on the valid source"
    assert selfcheck.chapter_lines(markdown, "Page 1") == [
        "| Region | Q1 | Q2 |", "| --- | --- | --- |", "| Alpha | 11 | 12 |",
        "| Beta | 21 | 22 |", "| Gamma | 31 | 32 |", "| Delta | 41 | 42 |",
    ], "ruling bands must not merge independent table records"


def test_shaded_header_padding_does_not_add_columns_or_repeat_headers(tmp_path):
    # GIVEN a four-column table with centered labels and inset header shading
    path = tmp_path / "shaded-header.pdf"
    drawing = "".join(rule(80, y, 400) for y in (700, 675, 650))
    drawing += "".join(rule(x, 650, 0.5, 50) for x in (80, 180, 280, 380, 480))
    drawing += "".join("q .9 g " + rule(x, 679, 80, 17) + "Q\n"
                       for x in (90, 190, 290, 390))
    drawing += "".join(text_op(x, 686, value)
                       for x, value in zip((115, 213, 309, 404), ("DATE", "TYPE", "CHANGE", "PAGE")))
    drawing += "".join(text_op(x, 660, value)
                       for x, value in zip((85, 185, 285, 385), ("04/27", "Edit", "New label", "1")))
    path.write_bytes(selfcheck.synthetic_pdf([drawing]))
    assert source_lines(path) == ["DATE TYPE CHANGE PAGE", "04/27 Edit New label 1"], "the source must have one header and one data row"
    # WHEN decoration overlaps the table's ruled region
    code, receipt, markdown = service.run(path)
    # THEN header and body values occupy the same four columns
    assert (code, receipt["file_ok"]) == (0, True), "the shaded table must render successfully"
    assert selfcheck.chapter_lines(markdown, "Page 1") == [
        "| DATE | TYPE | CHANGE | PAGE |", "| --- | --- | --- | --- |",
        "| 04/27 | Edit | New label | 1 |",
    ], "header padding must not become data columns or duplicate header text"


def test_boxed_prose_remains_three_sentences_without_a_false_table(tmp_path):
    # GIVEN three prose sentences in a box with a decorative horizontal divider
    path = tmp_path / "boxed-prose.pdf"
    prose = ["Alpha statement is the first line.", "Beta statement is the second line.",
             "Gamma statement is the final line."]
    drawing = "".join(rule(80, y, 330) for y in (700, 675, 640))
    drawing += "".join(rule(x, 640, 0.5, 60) for x in (80, 410))
    drawing += "".join(text_op(88, y, text) for y, text in zip((686, 670, 654), prose))
    path.write_bytes(selfcheck.synthetic_pdf([drawing]))
    assert source_lines(path) == prose, "the boxed source must contain each sentence exactly once"
    # WHEN the boxed paragraph is classified
    code, receipt, markdown = service.run(path)
    # THEN prose stays in reading order without being turned into table cells
    assert (code, receipt["file_ok"]) == (0, True), "the boxed paragraph must render successfully"
    assert selfcheck.chapter_lines(markdown, "Page 1") == prose, "a decorative box must not change or repeat paragraph structure"
    assert receipt["tables"] == 0, "a single prose column is not a data table"


def test_right_hand_chart_grid_does_not_suppress_left_hand_prose(tmp_path):
    # GIVEN a sparse plot grid beside a paragraph and its following heading
    path = tmp_path / "chart-beside-prose.pdf"
    prose = ["A full sentence begins here.", "Its continuation must survive.",
             "Architecture", "A new section remains readable."]
    drawing = "".join(rule(320, y, 200) for y in (700, 675, 650, 625, 600))
    drawing += "".join(rule(x, 600, 0.5, 100) for x in (320, 360, 400, 440, 480, 520))
    drawing += "".join(text_op(60, y, text) for y, text in zip((690, 674, 650, 626), prose))
    drawing += text_op(420, 655, "Series A")
    path.write_bytes(selfcheck.synthetic_pdf([drawing]))
    assert source_lines(path) == [prose[0], prose[1], "Series A", prose[2], prose[3]], "the source must keep the plot and all left-column text"
    # WHEN a right-hand plot produces closed rectangular candidates
    code, receipt, markdown = service.run(path)
    # THEN each neighboring sentence and heading remains once in source order
    assert (code, receipt["file_ok"]) == (0, True), "the mixed-layout page must render successfully"
    assert re.findall("|".join(map(re.escape, prose)), markdown) == prose, "a plot candidate must not consume neighboring prose or its heading"
    assert receipt["tables"] == 0, "sparse plot axes and a legend must not be reported as a data table"


def test_scientific_notation_keeps_the_decoded_mathematical_minus(tmp_path):
    # GIVEN a real minus glyph between the exponent marker and its digit
    path = tmp_path / "scientific-minus.pdf"
    commands = text_op(80, 690, "3.0e")
    commands += "BT /F2 10 Tf 1 0 0 1 99.46 690 Tm (-) Tj ET\n"
    commands += text_op(104.95, 690, "4")
    path.write_bytes(symbol_pdf(commands))
    assert source_lines(path) == ["3.0e−4"], "the source font must decode a mathematical minus rather than an ASCII hyphen"
    # WHEN scientific notation is converted to the PDF route's ASCII representation
    code, receipt, markdown = service.run(path)
    # THEN the exponent sign and numeric magnitude remain unambiguous
    assert (code, receipt["file_ok"]) == (0, True), "the explicitly encoded PDF must render successfully"
    assert selfcheck.chapter_lines(markdown, "Page 1") == ["3.0e-4"], "a negative exponent must not become an unknown glyph"
    assert receipt["dropped"]["non_ascii_replaced"] == 0, "a recognized minus is preserved rather than replaced"


def test_greek_and_operator_glyphs_keep_distinct_pdf_tokens(tmp_path):
    # GIVEN independently decoded beta, pi and summation glyphs on one baseline
    path = tmp_path / "mathematical-symbols.pdf"
    path.write_bytes(symbol_pdf("BT /F2 10 Tf 1 0 0 1 80 660 Tm (b=p+\\345) Tj ET\n"))
    assert source_lines(path) == ["β=π+∑"], "the source must expose three distinct Unicode identities"
    # WHEN decoded mathematical glyphs pass through PDF-specific normalization
    code, receipt, markdown = service.run(path)
    # THEN their identities remain distinct without inventing equation layout
    assert (code, receipt["file_ok"]) == (0, True), "the explicitly encoded symbols must render successfully"
    assert selfcheck.chapter_lines(markdown, "Page 1") == ["[beta]=[pi]+[sum]"], "decoded Greek letters and operators need stable distinct ASCII tokens"
    assert receipt["dropped"]["non_ascii_replaced"] == 0, "recognized symbols must not be counted as replacement loss"


def test_pdf_notation_contract_does_not_change_shared_text_sanitising():
    # GIVEN the same glyphs outside the PDF-specific extraction route
    raw = "3.0e−4 β=π+∑"
    tally = common.new_tally()
    # WHEN the shared text sanitizer is used directly
    actual = common.sanitise(raw, tally)
    # THEN the existing non-PDF normalization contract stays intact
    assert (actual, tally["dropped"]["non_ascii_replaced"]) == (
        "3.0e?4 ?=?+?", 4), "PDF notation support must not silently change shared text normalization"


def test_displayed_fraction_keeps_its_bar_between_numerator_and_denominator(tmp_path):
    # GIVEN a displayed fraction with a vector bar and a separate equation label
    path = tmp_path / "displayed-fraction.pdf"
    commands = "BT /F2 10 Tf 1 0 0 1 100 690 Tm (b+\\345) Tj ET\n"
    commands += rule(90, 678, 65, 0.5)
    commands += "BT /F2 10 Tf 1 0 0 1 100 660 Tm (p) Tj ET\n"
    commands += text_op(200, 676, "(1)")
    path.write_bytes(symbol_pdf(commands))
    assert source_lines(path) == ["β+∑", "(1)", "π"], "the source must place distinct numerator and denominator glyphs around the bar"
    # WHEN the PDF contains two-dimensional mathematical notation
    code, receipt, markdown = service.run(path)
    # THEN one fixed-layout block preserves glyph identities and fraction geometry
    assert (code, receipt["file_ok"]) == (0, True), "the displayed equation must render successfully"
    blocks = re.findall(r"```\n(.*?)\n```", markdown, re.DOTALL)
    assert len(blocks) == 1, "a displayed fraction needs one fixed-layout block rather than detached prose"
    block = blocks[0]
    assert [block.count(token) for token in ("[beta]", "[sum]", "[pi]", "+", "(1)")] == [1, 1, 1, 1, 1], "each source symbol and the equation label must occur exactly once"
    lines = block.splitlines()
    numerator = next(index for index, line in enumerate(lines) if "[beta]" in line)
    denominator = next(index for index, line in enumerate(lines) if "[pi]" in line)
    bars = [(index, match) for index, line in enumerate(lines) for match in re.finditer(r"-{3,}", line)]
    assert len(bars) == 1, "the source fraction rule must appear once as a horizontal bar"
    bar_row, bar = bars[0]
    assert numerator < bar_row < denominator, "the fraction bar must separate numerator and denominator rows"
    assert "[sum]" in lines[numerator], "the summation symbol belongs beside beta in the numerator"
    columns = (lines[numerator].index("[beta]"), lines[denominator].index("[pi]"))
    assert bar.start() <= min(columns) <= max(columns) < bar.end(), "the bar must overlap both source-aligned numerator and denominator columns"


def test_unknown_font_glyph_keeps_its_font_and_code_instead_of_disappearing(tmp_path):
    # GIVEN an undecoded Symbol glyph between two ordinary words
    path = tmp_path / "unknown-glyph.pdf"
    commands = text_op(80, 690, "left")
    commands += "BT /F2 10 Tf 1 0 0 1 100 690 Tm (\\346) Tj ET\n"
    commands += text_op(110, 690, "right")
    path.write_bytes(symbol_pdf(commands))
    assert source_lines(path) == ["left (cid:230) right"], "the undecoded source glyph must have an observable code"
    # WHEN the PDF route cannot establish the glyph's semantic identity
    code, receipt, markdown = service.run(path)
    # THEN the unknown identity stays visible and is counted without guessing
    assert (code, receipt["file_ok"]) == (0, True), "an undecoded glyph must not refuse otherwise readable text"
    assert selfcheck.chapter_lines(markdown, "Page 1") == ["left [font-glyph:Symbol:230] right"], "unknown font glyphs need a visible font-scoped identity"
    assert receipt["dropped"]["cid_survivors"] == 1, "the unresolved glyph must remain counted exactly once"


def test_stacked_left_tables_beside_a_tall_table_do_not_repeat_cell_content(tmp_path):
    # GIVEN two short tables beside one tall table, with twelve distinct cell values
    path = tmp_path / "three-tables.pdf"
    tables = [(40, (692, 642, 592), (670, 620), (("A", "B"), ("1", "2"))),
              (40, (542, 492, 442), (520, 470), (("C", "D"), ("3", "4"))),
              (320, (692, 542, 392), (670, 420), (("E", "F"), ("5", "6")))]
    commands = ""
    for left, rules, baselines, rows in tables:
        commands += "".join(rule(left, y, 180) for y in rules)
        commands += "".join(rule(x, rules[-1], 0.5, rules[0] - rules[-1])
                            for x in (left, left + 90, left + 180))
        commands += "".join(text_op(x, y, value) for y, row in zip(baselines, rows)
                            for x, value in zip((left + 5, left + 95), row))
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        source_values = sorted(word["text"] for word in document.pages[0].extract_words())
    expected_values = ["1", "2", "3", "4", "5", "6", "A", "B", "C", "D", "E", "F"]
    assert source_values == expected_values, "each source cell must contain one distinct value"
    # WHEN adjacent table boxes have overlapping vertical ranges
    code, receipt, markdown = service.run(path)
    # THEN every cell remains once and belongs to its own two-column table row
    assert (code, receipt["file_ok"]) == (0, True), "all three valid tables must render successfully"
    content = "\n".join(selfcheck.chapter_lines(markdown, "Page 1"))
    assert sorted(re.findall(r"\b[A-F1-6]\b", content)) == expected_values, "table cells must not also appear in neighboring prose regions"
    assert sorted(re.findall(r"^\| [A-F1-6] \| [A-F1-6] \|$", content, re.MULTILINE)) == [
        "| 1 | 2 |", "| 3 | 4 |", "| 5 | 6 |", "| A | B |", "| C | D |", "| E | F |",
    ], "each header and data pair must retain its source table alignment"
    assert receipt["tables"] == 3, "exactly the three source tables must be recorded"


@pytest.mark.parametrize("fonts, encoding, code, expected, unresolved", [
    ((("F1", SUMMATION_PROGRAM), ("F2", PRODUCT_PROGRAM)), "", 80,
     f"[font-glyph:{FONT_NAME}:80] [font-glyph:{FONT_NAME}:80]", 2),
    ((("F1", UNRELATED_ARRAY_PROGRAM), ("F1", UNRELATED_ARRAY_PROGRAM)), "", 80, "[sum] [sum]", 0),
    ((("F1", OVERRIDDEN_PROGRAM), ("F1", OVERRIDDEN_PROGRAM)), PDF_DIFFERENCES, 255,
     f"[font-glyph:{FONT_NAME}:255] [font-glyph:{FONT_NAME}:255]", 2),
], ids=["duplicate-basefont", "unrelated-array", "pdf-encoding-override"])
def test_font_recovery_uses_only_unambiguous_encoding_identity(tmp_path, fonts, encoding, code, expected, unresolved):
    # GIVEN embedded Type1 encodings and one character code without a Unicode mapping
    path = tmp_path / "font-identity.pdf"
    path.write_bytes(font_identity_pdf(fonts, encoding, code))
    assert source_lines(path) == [f"(cid:{code}) (cid:{code})"], "both source characters must remain undecoded before recovery"
    # WHEN explicit font encodings are considered for the public PDF conversion
    code, receipt, markdown = service.run(path)
    # THEN conflicting declarations and unrelated arrays cannot determine glyph identity
    assert (code, receipt["file_ok"]) == (0, True), "the valid synthetic font resources must remain readable"
    assert selfcheck.chapter_lines(markdown, "Page 1") == [expected], "unproved or overridden font metadata must not determine a glyph identity"
    assert receipt["dropped"]["cid_survivors"] == unresolved, "only genuinely unresolved font identities must be counted"


def test_sparse_numeric_ruling_keeps_six_columns_and_grouped_headers(tmp_path):
    # GIVEN two text stubs beside a ruled four-column numeric core
    path = tmp_path / "six-column-table.pdf"
    commands = "".join(rule(180, y, 320) for y in (714, 695, 678, 632))
    commands += "".join(rule(x, 678, 0.5, 36) for x in (180, 340, 500))
    commands += "".join(rule(x, 678, 0.5, 17) for x in (260, 420))
    commands += "".join(text_op(x, 702, value)
                         for x, value in ((60, "Model"), (120, "Unit"), (202, "Score"), (365, "Rate")))
    commands += "".join(text_op(x, 686, value)
                         for x, value in zip((195, 275, 355, 435), ("A", "B", "C", "D")))
    for y, values in ((666, ("Alpha", "kg", "11", "12", "13", "14")),
                      (646, ("Beta", "kg", "21", "22", "23", "24"))):
        commands += "".join(text_op(x, y, value)
                             for x, value in zip((60, 120, 195, 275, 355, 435), values))
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    assert source_lines(path) == ["Model Unit Score Rate", "A B C D",
                                  "Alpha kg 11 12 13 14", "Beta kg 21 22 23 24"], "the source must expose both stubs, grouped labels and four numeric fields"
    # WHEN the numeric ruling covers only part of the complete table
    code, receipt, markdown = service.run(path)
    # THEN stubs and units stay associated with all four values under the grouped header
    assert (code, receipt["file_ok"]) == (0, True), "the sparse six-column source must render successfully"
    page = "\n".join(selfcheck.chapter_lines(markdown, "Page 1"))
    assert re.findall(r"^\|.*\|$", page, re.MULTILINE) == [
        "| Model | Unit | Score |  | Rate |  |", "| --- | --- | --- | --- | --- | --- |",
        "|  |  | A | B | C | D |", "| Alpha | kg | 11 | 12 | 13 | 14 |",
        "| Beta | kg | 21 | 22 | 23 | 24 |",
    ], "partial numeric ruling must not detach text stubs or shift grouped subheaders"


def test_narrow_table_keeps_its_caption_separate_from_neighboring_prose(tmp_path):
    # GIVEN a narrow three-column table and caption beside a complete prose column
    path = tmp_path / "narrow-table.pdf"
    prose = ["Left paragraph begins here.", "Its second line stays together.",
             "Its third line adds context.", "Its fourth line concludes."]
    commands = "".join(text_op(60, y, value)
                         for y, value in zip((700, 686, 672, 658), prose))
    commands += "".join(rule(360, y, 140) for y in (704, 685, 650))
    for y, row in ((692, ("Item", "Qty", "Cost")), (677, ("A", "2", "5")), (662, ("B", "3", "7"))):
        commands += "".join(text_op(x, y, value) for x, value in zip((365, 410, 465), row))
    commands += text_op(360, 635, "Table 9: Small inventory.")
    commands += text_op(360, 623, "Values are synthetic.")
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    with pdfplumber.open(path) as document:
        right = document.pages[0].crop((355, 85, 510, 180)).extract_text().splitlines()
    assert right == ["Item Qty Cost", "A 2 5", "B 3 7", "Table 9: Small inventory.",
                     "Values are synthetic."], "the source must contain a real narrow table followed by two caption lines"
    # WHEN the table and prose share the same vertical band
    code, receipt, markdown = service.run(path)
    # THEN the prose lane is contiguous and the caption immediately follows its table
    assert (code, receipt["file_ok"]) == (0, True), "the narrow mixed-layout source must render successfully"
    assert selfcheck.chapter_lines(markdown, "Page 1") == prose + [
        "| Item | Qty | Cost |", "| --- | --- | --- |", "| A | 2 | 5 |", "| B | 3 | 7 |",
        "Table 9: Small inventory.", "Values are synthetic.",
    ], "a narrow table, its caption and neighboring prose must remain separate ordered regions"
    assert receipt["tables"] == 1, "the narrow source table must be recorded once"


def test_spanning_table_header_outside_rule_end_keeps_every_character_once(tmp_path):
    # GIVEN a grouped heading that extends beyond the numeric table's horizontal rule
    path = tmp_path / "spanning-heading.pdf"
    heading = "Win rate vs. ground truth"
    commands = "".join(rule(360, y, 140) for y in (712, 680, 635))
    commands += text_op(400, 696, heading)
    for y, row in ((685, ("Method", "Warm", "Cold")),
                   (666, ("DPO", "0.36", "0.31")), (646, ("PPO", "0.26", "0.23"))):
        commands += "".join(text_op(x, y, value) for x, value in zip((365, 425, 475), row))
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    assert source_lines(path) == [heading, "Method Warm Cold", "DPO 0.36 0.31", "PPO 0.26 0.23"], "the complete source heading must be independently readable"
    with pdfplumber.open(path) as document:
        truth = next(word for word in document.pages[0].extract_words() if word["text"] == "truth")
    assert truth["x0"] < 500 < truth["x1"], "the rule boundary must cross the source word truth"
    # WHEN a qualified table owns a heading wider than its numeric rules
    code, receipt, markdown = service.run(path)
    # THEN the full heading survives once with its two source records
    assert (code, receipt["file_ok"]) == (0, True), "the valid source table must render successfully"
    assert markdown.count(heading) == 1, "the spanning heading must remain intact and occur once"
    assert len(re.findall(r"\btruth\b", markdown)) == 1, "the boundary word must not be duplicated in residual prose"
    content = "\n".join(selfcheck.chapter_lines(markdown, "Page 1"))
    assert sorted(re.findall(r"[A-Za-z]", content)) == sorted(re.findall(r"[A-Za-z]", heading + "Method Warm Cold DPO PPO")), "each source letter must belong to exactly one output region"
    assert re.findall(r"^\| (?:DPO|PPO).*\|$", markdown, re.MULTILINE) == [
        "| DPO | 0.36 | 0.31 |", "| PPO | 0.26 | 0.23 |",
    ], "both source records must retain their three-column association"


def test_long_model_name_does_not_absorb_adjacent_hardware_cell(tmp_path):
    # GIVEN three four-column records with one long model name close to its GPU cell
    path = tmp_path / "close-text-columns.pdf"
    commands = "".join(rule(60, y, 340) for y in (712, 689, 625))
    rows = [(698, ("Model", "GPU", "Power", "Carbon")),
            (678, ("Alpha", "V100", "10", "20")),
            (658, ("Beta", "V100", "30", "40")),
            (638, ("BLOOM-176B", "A100-80GB", "50", "60"))]
    for y, row in rows:
        commands += "".join(text_op(x, y, value) for x, value in zip((65, 134, 245, 330), row))
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    assert source_lines(path) == ["Model GPU Power Carbon", "Alpha V100 10 20",
                                  "Beta V100 30 40", "BLOOM-176B A100-80GB 50 60"], "the source must expose four distinct fields on every record baseline"
    # WHEN repeated body columns establish separate model and hardware fields
    code, receipt, markdown = service.run(path)
    # THEN the long model and adjacent hardware retain their own cells
    assert (code, receipt["file_ok"]) == (0, True), "the valid four-column source must render successfully"
    page = "\n".join(selfcheck.chapter_lines(markdown, "Page 1"))
    assert re.findall(r"^\|.*\|$", page, re.MULTILINE) == [
        "| Model | GPU | Power | Carbon |", "| --- | --- | --- | --- |",
        "| Alpha | V100 | 10 | 20 |", "| Beta | V100 | 30 | 40 |",
        "| BLOOM-176B | A100-80GB | 50 | 60 |",
    ], "a narrow gap must not merge text fields from distinct repeated columns"


def test_line_number_rail_keeps_each_code_line_whole_without_a_page_gutter(tmp_path):
    # GIVEN a numbered code listing whose rail leaves a wide gap on every line
    path = tmp_path / "numbered-listing.pdf"
    body = ["def solve(a, b, c):", "delta = b * b - 4 * a * c", "if delta == 0:", "return None",
            "root = delta ** 0.5", "left = (-b - root) / (2 * a)",
            "right = (-b + root) / (2 * a)", "return left, right"]
    indents = (100, 125, 125, 150, 125, 125, 125, 125)
    commands = "".join(text_op(80, 700 - 12 * index, str(index + 1)) + text_op(x, 700 - 12 * index, text)
                       for index, (x, text) in enumerate(zip(indents, body)))
    path.write_bytes(selfcheck.synthetic_pdf([commands]))
    listing = ["%d %s" % (index + 1, text) for index, text in enumerate(body)]
    assert source_lines(path) == listing, "the source must expose every line number beside its own code line"
    with pdfplumber.open(path) as document:
        glyph = document.pages[0].chars[1]
    assert (glyph["text"], round(glyph["x0"], 2), round(glyph["x1"], 2)) == ("d", 100.0, 105.56), "the first code glyph must straddle the midpoint the indented gaps imply"
    # WHEN the listing band is checked for repeated prose-column starts
    code, receipt, markdown = service.run(path)
    # THEN no gutter is taken and every glyph stays once on its own numbered line
    assert (code, receipt["file_ok"]) == (0, True), "the valid listing source must render successfully"
    assert (selfcheck.chapter_lines(markdown, "Page 1"), receipt["columns_split"]) == (listing, 0), "a line-number rail is not a page gutter, so no glyph may be cut or duplicated"
