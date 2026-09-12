import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import brewdoc
from brewdoc import reader
from brewdoc.cli import main

HEAD = "BT /F1 10 Tf 1 0 0 1 72 740 Tm (%s) Tj ET\n"
BODY = "BT /F1 10 Tf 1 0 0 1 72 %d Tm (%s) Tj ET\n"
FOOT = "BT /F1 10 Tf 1 0 0 1 300 40 Tm (%s) Tj ET\n"


def three_page_fixture(path: Path) -> Path:
    path.write_bytes(reader.synthetic_pdf(reader._fixture_pages()))
    return path


def test_self_check_is_green():
    # GIVEN the module's own synthetic fixtures
    # WHEN the self-check runs
    # THEN it exits 0
    assert reader.self_check() == 0, "self-check must be green"


def test_the_cli_self_check_prints_its_verdict():
    # GIVEN the package run as a fresh interpreter
    proc = subprocess.run([sys.executable, "-m", "brewdoc.cli", "--self-check"],
                          capture_output=True, text=True, timeout=300)
    # WHEN the self-check runs
    # THEN it exits 0 and the last line says so
    assert (proc.returncode, proc.stdout.splitlines()[-1]) == (0, "self-check: ok (27 checks)"), (
        "self-check stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr)
    )


def test_version_matches_package_metadata():
    assert brewdoc.__version__ == "0.1.0", "version drifted from pyproject"


def test_cli_renders_a_spreadsheet_to_out(capsys):
    # GIVEN a synthetic workbook and an --out path
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp, "zones.xlsx")
        reader._synthetic_xlsx(book)
        out = Path(tmp, "nested", "zones.md")
        # WHEN the CLI renders it
        rc = main([str(book), "--out", str(out)])
        # THEN the receipt is on stdout and the Markdown on disk
        assert rc == 0, "render must exit 0"
        receipt = capsys.readouterr().out.strip().splitlines()
        assert len(receipt) == 1, "exactly one receipt line with --out"
        assert '"route": "sheet"' in receipt[0], "receipt names the sheet route"
        assert reader.chapter_lines(out.read_text(encoding="ascii"), "Zones") == [
            "| Zone | Area | Sown |", "| --- | --- | --- |",
            "| North | 12.5 | 2026-07-18 |", "| South | 9.0 |  |"], "rendered table"


def test_cli_refuses_an_unknown_suffix(capsys):
    rc = main(["notes.txt"])
    assert rc == 1, "unsupported suffix exits 1"
    assert '"file_ok": false' in capsys.readouterr().out, "receipt reports the refusal"


def test_a_document_with_no_text_layer_is_refused_by_name(tmp_path):
    # GIVEN a synthetic image-only PDF
    path = tmp_path / "scan.pdf"
    path.write_bytes(reader.synthetic_pdf(["0 0 0 rg 100 100 200 200 re f\n"] * 2, font=False))
    # WHEN it is read
    rc, line, markdown = reader.run(path)
    # THEN the refusal names the file, the page count and that no OCR was attempted
    assert (rc, line["file_ok"], line["reason"], markdown) == (
        reader.EXIT_FAIL, False,
        "no text layer: 2 of 2 pages carry zero characters in %s - this reader does no OCR" % path,
        "",
    ), "an empty render must never stand in for a refusal"


def test_an_unsupported_suffix_names_what_it_reads(tmp_path):
    # GIVEN a file this reader has no route for - .doc is the binary Word format, not OOXML
    path = tmp_path / "brief.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    # WHEN it is read
    rc, line, markdown = reader.run(path)
    # THEN the reason names the suffix, the path and the suffixes that would have worked
    assert (rc, line["route"], line["reason"], markdown) == (
        reader.EXIT_FAIL, "none",
        "unsupported suffix '.doc' in %s: brewdoc reads .docx .ods .pdf .xls .xlsb .xlsm .xlsx"
        % path,
        "",
    ), "a refusal must say what it could not read and where"


def test_a_missing_file_names_the_path(tmp_path):
    # GIVEN a path that does not exist
    path = tmp_path / "absent.pdf"
    # WHEN it is read
    rc, line, markdown = reader.run(path)
    # THEN the reason is the path itself, not a traceback
    assert (rc, line["file_ok"], line["reason"], markdown) == (
        reader.EXIT_FAIL, False, "no such file: %s" % path, ""
    ), "a missing input is named, never rendered empty"


def test_the_sanitiser_counts_every_class_it_drops():
    # GIVEN one of each hygiene class: PUA bullet, nbsp, cid survivor, soft hyphen, ligature,
    # broken ligature, control char, dash, math symbol, CJK
    tally = reader.new_tally()
    raw = ("\uf0b7\xa0Wingdings bullet and (cid:190) survivor\n"
           "soft\xadhyphen o\ufb03ce di\u0161erent \x01control \u2013 \u2265 \u4e2d\n")
    # WHEN it is sanitised
    got = reader.sanitise(raw, tally)
    # THEN the text is ASCII and every dropped class is counted by name
    assert got == ("- Wingdings bullet and survivor\n"
                   "softhyphen office diserent control - >= ?\n"), (
        "ASCII out, one fold per class, nothing repaired"
    )
    assert tally["dropped"] == {
        "control_chars": 1, "soft_hyphens": 1, "nbsp": 1, "pua_glyphs": 1,
        "cid_survivors": 1, "ligatures": 1, "running_heads": 0, "page_numbers": 0,
        "non_ascii_replaced": 1,
    }, "a loss is counted and named, never silent"


def test_a_broken_ligature_word_is_counted_and_never_repaired():
    # GIVEN a CMap defect where the ff ligature lifts as U+0161
    tally = reader.new_tally()
    # WHEN the text is sanitised
    got = reader.sanitise("di\u0161erent runo\u0161 and Sto\u0161ic", tally)
    # THEN each corrupted word is counted and the text is folded, not guessed back to ff
    assert (got, tally["broken_ligature_words"]) == ("diserent runos and Stosic", 3), (
        "a word-level repair would silently rewrite the document"
    )


def test_running_heads_and_page_numbers_are_dropped_and_counted(tmp_path):
    # GIVEN three pages sharing one running head and carrying their own page numbers
    body = "".join(BODY % (700 - 14 * i, "body line %d of this page" % i) for i in range(4))
    path = tmp_path / "manual.pdf"
    path.write_bytes(reader.synthetic_pdf([
        (HEAD % "Running head of the manual") + body + FOOT % foot
        for foot in ("Page 1 of 3", "2", "3")]))
    # WHEN the document is rendered
    markdown, tally = reader.render_pdf(path)
    # THEN the furniture is gone from the Markdown and counted in the tally
    assert (markdown.count("Running head of the manual"), markdown.count("body line 3"),
            tally["dropped"]["running_heads"], tally["dropped"]["page_numbers"]) == (0, 3, 3, 3), (
        "furniture repeats on every page and buries the content a model must read"
    )


def test_two_renders_of_one_document_share_a_sha256(tmp_path):
    # GIVEN a three-page document with a captioned table, two-column prose and furniture
    path = three_page_fixture(tmp_path / "fixture.pdf")
    # WHEN it is rendered twice
    first, second = reader.render_pdf(path)[0], reader.render_pdf(path)[0]
    # THEN the bytes are identical - no timestamp, no set ordering, no dict iteration order
    assert reader.sha256(first) == reader.sha256(second), "the render must be deterministic"


def test_a_horizontally_merged_cell_keeps_the_columns_below_it_aligned(tmp_path):
    # GIVEN a table whose first row is one cell spanning all three columns (w:gridSpan)
    path = tmp_path / "merged.docx"
    path.write_bytes(reader.synthetic_docx())
    # WHEN it is rendered
    markdown, tally = reader.render_doc(path)
    lines = [line for line in markdown.splitlines() if line.startswith("|")]
    # THEN the span is padded out, so every body cell still sits under its own header
    assert (lines, tally["tables"]) == ([
        "| Dose table |  |  |",
        "| --- | --- | --- |",
        "| Product | Dose | BBCH |",
        "| Moddus | 0.4 | 31 |",
    ], 1), "an unpadded span shifts every column left and moves a dose under the wrong head"


def test_a_word_heading_opens_its_own_chapter_and_a_line_break_stays_one(tmp_path):
    # GIVEN a .docx with one Heading1, a paragraph split by w:br and a deleted run
    path = tmp_path / "fixture.docx"
    path.write_bytes(reader.synthetic_docx())
    # WHEN it is rendered
    markdown, tally = reader.render_doc(path)
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    # THEN the heading is the chapter, the break opens a new line and the deleted run is absent
    assert (headings, reader.chapter_lines(markdown, "Growth regulators")[:2],
            markdown.count("struck out"),
            (tally["chapters"], tally["tables"], tally["text_regions"])) == (
        ["## Growth regulators"], ["first line", "second line"], 0, (1, 1, 1)
    ), "w:br is Word's line break; joining across it welds two lines into one sentence"


def test_an_empty_cell_is_rendered_empty_and_a_date_as_an_iso_day():
    # GIVEN what calamine returns: '' for an empty cell and datetime.date for a date
    rows = [["head", "", 5.0], ["x", datetime.date(2026, 7, 18), ""]]
    # WHEN those cells are rendered
    got = reader.markdown_table(rows)
    # THEN the empty cell is an empty column and the date is one ISO day
    assert got == [
        "| head |  | 5.0 |",
        "| --- | --- | --- |",
        "| x | 2026-07-18 |  |",
    ], "a reader testing `is None` never fires - the empty cell is ''"


def test_a_workbook_renders_one_chapter_per_sheet_with_its_receipt(tmp_path):
    # GIVEN a one-sheet workbook of strings, floats, a date and an empty cell
    path = tmp_path / "book.xlsx"
    reader._synthetic_xlsx(path)
    # WHEN it is read
    rc, line, markdown = reader.run(path)
    # THEN there is one chapter per sheet and the receipt names the sheet route
    assert ([row for row in markdown.splitlines() if row.startswith("## ")],
            rc, line["route"], line["sheets"], line["tables"], line["reason"],
            line["not_carried"]) == (
        ["## Zones"], reader.EXIT_OK, "sheet", 1, 1,
        "sheet rendered: 1 chapters, 1 tables, 0 text regions, 0 column splits",
        list(reader.SHEET_NOT_CARRIED),
    ), "a sheet is a chapter, and the route line says what a spreadsheet cannot carry"


def test_a_relative_path_resolves_against_the_caller_s_cwd(tmp_path, monkeypatch, capsys):
    # GIVEN a cwd outside the package, holding the document under a relative name
    work = tmp_path / "sub"
    work.mkdir()
    three_page_fixture(work / "in.pdf")
    package = Path(reader.__file__).parent
    before = sorted(path.name for path in package.iterdir())
    monkeypatch.chdir(work)
    # WHEN it is rendered with a relative input and a relative --out under a directory that does
    # not exist yet
    code = main(["in.pdf", "--out", "nested/here.md"])
    printed = json.loads(capsys.readouterr().out)
    # THEN the render landed under that cwd, with the same bytes the reader returns
    assert (code, printed["out"], (work / "nested" / "here.md").read_text(encoding="ascii")) == (
        reader.EXIT_OK, "nested/here.md", reader.render_pdf(work / "in.pdf")[0]
    ), "a relative --out is resolved against the caller's cwd, never against the package"
    # AND the package directory gained nothing
    assert sorted(path.name for path in package.iterdir()) == before, (
        "the reader must hold no Path(__file__) output root"
    )


def test_the_cli_states_its_contract():
    # GIVEN the parser a caller reads through --help
    parser = reader.build_parser()
    # WHEN its options are enumerated
    flags = sorted(action.option_strings[-1] for action in parser._actions
                   if action.option_strings)
    # THEN the contract is the documented one
    assert flags == ["--help", "--out", "--self-check"], "the CLI surface is fixed"


def test_the_route_line_is_one_json_line_a_workflow_can_re_read(tmp_path):
    # GIVEN a three-page document rendered to disk by a fresh interpreter
    path = three_page_fixture(tmp_path / "fixture.pdf")
    out = tmp_path / "fixture.md"
    proc = subprocess.run([sys.executable, "-m", "brewdoc.cli", str(path), "--out", str(out)],
                          capture_output=True, text=True, timeout=300)
    # WHEN the caller reads stdout
    line = json.loads(proc.stdout)
    # THEN it is one JSON object naming the route, the output and what the route cannot carry
    assert (proc.returncode, line["file_ok"], line["route"], line["source"], line["out"],
            line["pages"], line["tables"], line["columns_split"], line["not_carried"]) == (
        0, True, "pdf", "fixture.pdf", str(out), 3, 3, 3, list(reader.PDF_NOT_CARRIED)
    ), "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr)
