import datetime
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import brewdoc
import pytest
from brewdoc import reader
from brewdoc.cli import main

HEAD = "BT /F1 10 Tf 1 0 0 1 72 740 Tm (%s) Tj ET\n"
BODY = "BT /F1 10 Tf 1 0 0 1 72 %d Tm (%s) Tj ET\n"
FOOT = "BT /F1 10 Tf 1 0 0 1 300 40 Tm (%s) Tj ET\n"


def three_page_fixture(path: Path) -> Path:
    path.write_bytes(reader.synthetic_pdf(reader._fixture_pages()))
    return path


def duplicate_heading_docx(path: Path) -> Path:
    """Write two identical heading-created chapters, the first empty."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Same</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Same</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>body</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(reader.DOC_PART, xml)
    return path


def literal_anchor_docx(path: Path) -> Path:
    """Write prose and a table cell that resemble private navigation anchors."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Actual</w:t></w:r></w:p>'
        '<w:p><w:r>'
        '<w:t>&lt;span id="brewdoc-chapter-999999"&gt;span&lt;/span&gt; </w:t>'
        '<w:t>&lt;div id=brewdoc-page-999999&gt;div&lt;/div&gt; </w:t>'
        '<w:t>&lt;a name="brewdoc-sheet-999999"&gt;name&lt;/a&gt; </w:t>'
        '<w:t>&lt;span id="brewdoc&amp;#45;chapter-777777"&gt;entity&lt;/span&gt; </w:t>'
        '<w:t>&lt;a name="&amp;#98;rewdoc-sheet-777777"&gt;encoded&lt;/a&gt;</w:t></w:r>'
        '</w:p><w:tbl><w:tr><w:tc><w:p><w:r>'
        '<w:t>&lt;span id="brewdoc-sheet-888888"&gt;table&lt;/span&gt; </w:t>'
        '<w:t>&lt;a name=brewdoc-chapter-888888&gt;cell&lt;/a&gt; </w:t>'
        '<w:t>&lt;div id="brewdoc&amp;#45;page-666666"&gt;table-entity&lt;/div&gt;</w:t>'
        '</w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(reader.DOC_PART, xml)
    return path


def expected_schema_two(path: Path, route: str, unit_kind: str, source_units: int,
                        body: str, tally: dict, links, capabilities) -> str:
    """Build an independent whole-document expectation for a small schema 2 render."""
    source = path.read_bytes()
    keys = [key for key, _display in links]
    rows = [
        ("Markdown schema", "brewdoc.markdown/2"),
        ("Source name", '"%s"' % reader._escape_markdown_text(path.name)),
        ("Source suffix", path.suffix.lower()),
        ("Source bytes", str(len(source))),
        ("Source SHA-256", hashlib.sha256(source).hexdigest()),
        ("Route", route), ("Unit kind", unit_kind),
        ("Source unit count", str(source_units)),
        ("Rendered unit count", str(len(keys))),
        ("Ordered selection keys", ", ".join(keys)),
        ("Rendered body bytes", str(len(body.encode("ascii")))),
        ("Rendered body SHA-256", hashlib.sha256(body.encode("ascii")).hexdigest()),
        ("Pages", str(tally["pages"])), ("Sheets", str(tally["sheets"])),
        ("Chapters", str(tally["chapters"])), ("Tables", str(tally["tables"])),
        ("Text regions", str(tally["text_regions"])),
        ("Column splits", str(tally["columns_split"])),
        ("Broken ligature words", str(tally["broken_ligature_words"])),
    ]
    rows += [("Dropped " + key.replace("_", " "), str(tally["dropped"][key]))
             for key in reader.DROP_KEYS]
    rows += list(capabilities)
    omissions = (reader.PDF_NOT_CARRIED if route == "pdf" else reader.DOC_NOT_CARRIED
                 if route == "doc" else reader.SHEET_NOT_CARRIED)
    out = ['# "%s"' % reader._escape_markdown_text(path.name), "",
           '<a id="brewdoc-metadata"></a>', "## Metadata", ""]
    out += reader.markdown_table([["Field", "Value"], *rows])
    out += ["", "## Artifacts", "", "None.", "", "## Known omissions", ""]
    out += ["- " + reader._escape_markdown_text(item) for item in omissions]
    out += ["", '<a id="brewdoc-contents"></a>', "## Contents", ""]
    out += ["- [%s](#%s)" % (display, reader._unit_anchor(key)) for key, display in links]
    out += ["", body.rstrip("\n")]
    return "\n".join(out) + "\n"


def test_self_check_is_green():
    # GIVEN the module's own synthetic fixtures
    # WHEN the self-check runs
    result = reader.self_check()
    # THEN it exits 0
    assert result == 0, "self-check must be green"


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
    lines = [line for line in reader.chapter_lines(markdown, "Growth regulators")
             if line.startswith("|")]
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
    headings = [line for line in markdown.splitlines() if line.startswith("## Chapter ")]
    # THEN the heading is the chapter, the break opens a new line and the deleted run is absent
    assert (headings, reader.chapter_lines(markdown, "Growth regulators")[:2],
            markdown.count("struck out"),
            (tally["chapters"], tally["tables"], tally["text_regions"])) == (
        ['## Chapter 1: "Growth regulators"'], ["first line", "second line"], 0, (1, 1, 1)
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
    assert ([row for row in markdown.splitlines() if row.startswith("## Sheet ")],
            rc, line["route"], line["sheets"], line["tables"], line["reason"],
            line["not_carried"]) == (
        ['## Sheet 1: "Zones"'], reader.EXIT_OK, "sheet", 1, 1,
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
    assert flags == ["--artifact", "--help", "--out", "--self-check", "--sheet"], (
        "the CLI surface must expose repeatable sheet and artifact requests"
    )


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


def test_cli_stdout_is_the_receipt_line_then_the_exact_out_file_bytes(tmp_path, capsys):
    # GIVEN a one-page PDF, its --out Markdown and its stdout-mode receipt
    path = tmp_path / "one.pdf"
    path.write_bytes(reader.synthetic_pdf([BODY % (700, "hello")]))
    out = tmp_path / "one.md"
    assert main([str(path), "--out", str(out)]) == 0, "the --out render must succeed"
    capsys.readouterr()
    receipt = reader.run(path)[1]
    # WHEN the CLI renders it without --out
    code = main([str(path)])
    # THEN stdout is one receipt line followed by exactly the --out file bytes
    line = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
    assert (code, capsys.readouterr().out) == (
        0, line + "\n" + out.read_text(encoding="ascii")
    ), "stdout Markdown must equal the --out file, with no extra trailing blank line"


def test_metadata_first_pdf_keeps_an_empty_physical_page_navigable(tmp_path):
    # GIVEN one text page followed by one physical page without text
    path = tmp_path / "partial.pdf"
    path.write_bytes(reader.synthetic_pdf([
        BODY % (700, "kept body"), "0 0 0 rg 100 100 20 20 re f\n",
    ]))
    # WHEN the PDF is rendered
    markdown, tally = reader.render_pdf(path)
    body = markdown[markdown.index('<a id="brewdoc-page-000001"></a>'):]
    # THEN metadata and contents precede two exact anchored units, including the empty page
    assert markdown.index("## Metadata") < markdown.index("## Artifacts") < markdown.index(
        "## Known omissions") < markdown.index("## Contents") < markdown.index("## Page 1"), (
        "schema 2 sections must have one route-neutral order"
    )
    assert body == (
        '<a id="brewdoc-page-000001"></a>\n## Page 1\n\nkept body\n\n'
        '<a id="brewdoc-page-000002"></a>\n## Page 2\n'
    ), "PDF content units must have exact stable anchors and headings"
    assert (tally["pages"], markdown.endswith("\n"), markdown.isascii()) == (2, True, True), (
        "all successful Markdown must be ASCII LF text with a final newline"
    )


def test_small_pdf_workbook_and_docx_match_the_whole_schema_two_contract(tmp_path):
    # GIVEN one small source for every successful route
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(reader.synthetic_pdf([BODY % (700, "hello")]))
    book = tmp_path / "zones.xlsx"
    reader._synthetic_xlsx(book)
    doc = tmp_path / "fixture.docx"
    doc.write_bytes(reader.synthetic_docx())
    # WHEN each route is rendered
    pdf_markdown, pdf_tally = reader.render_pdf(pdf)
    book_markdown, book_tally = reader.render_book(book)
    doc_markdown, doc_tally = reader.render_doc(doc)
    # THEN every byte matches the shared metadata-first contract and route-specific body
    not_applicable = (
        ("Formula capability", "not applicable"),
        ("Selected formula count", "not applicable"),
        ("VBA project capability", "not applicable"),
        ("VBA project count", "not applicable"),
        ("VBA source module capability", "not applicable"),
        ("VBA source module count", "not applicable"),
    )
    workbook_capabilities = (
        ("Formula capability", "available"), ("Selected formula count", "0"),
        ("VBA project capability", "unavailable"), ("VBA project count", "unknown"),
        ("VBA source module capability", "unavailable"),
        ("VBA source module count", "unknown"),
    )
    pdf_body = '<a id="brewdoc-page-000001"></a>\n## Page 1\n\nhello\n'
    book_body = (
        '<a id="brewdoc-sheet-000001"></a>\n## Sheet 1: "Zones"\n\n'
        '| Zone | Area | Sown |\n| --- | --- | --- |\n'
        '| North | 12.5 | 2026-07-18 |\n| South | 9.0 |  |\n'
    )
    doc_body = (
        '<a id="brewdoc-chapter-000001"></a>\n'
        '## Chapter 1: "Growth regulators"\n\nfirst line\nsecond line\n\n'
        '| Dose table |  |  |\n| --- | --- | --- |\n| Product | Dose | BBCH |\n'
        '| Moddus | 0.4 | 31 |\n'
    )
    assert pdf_markdown == expected_schema_two(
        pdf, "pdf", "page", 1, pdf_body, pdf_tally,
        (("page/000001", "Page 1"),), not_applicable,
    ), "small PDF Markdown must match the whole schema 2 document"
    assert book_markdown == expected_schema_two(
        book, "sheet", "sheet", 1, book_body, book_tally,
        (("sheet/000001", 'Sheet 1: "Zones"'),), workbook_capabilities,
    ), "small workbook Markdown must match the whole schema 2 document"
    assert doc_markdown == expected_schema_two(
        doc, "doc", "chapter", 1, doc_body, doc_tally,
        (("chapter/000001", 'Chapter 1: "Growth regulators"'),), not_applicable,
    ), "small DOCX Markdown must match the whole schema 2 document"


def test_duplicate_and_empty_docx_chapters_have_distinct_keys(tmp_path):
    # GIVEN consecutive duplicate Word headings, so the first chapter has no body
    path = duplicate_heading_docx(tmp_path / "duplicate.docx")
    # WHEN it is rendered
    markdown, tally = reader.render_doc(path)
    # THEN both heading-created chapters remain navigable by distinct source ordinals
    assert [line for line in markdown.splitlines() if line.startswith("## Chapter ")] == [
        '## Chapter 1: "Same"', '## Chapter 2: "Same"',
    ], "duplicate DOCX labels must not collapse chapter identity"
    assert markdown.count('[Chapter 1: "Same"](#brewdoc-chapter-000001)') == 1, (
        "the first empty chapter must remain in contents"
    )
    assert '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Same"\n\n' in markdown, (
        "heading-created empty chapters must retain an explicit anchor"
    )
    assert (tally["chapters"], reader.chapter_lines(markdown, "Same")) == (2, []), (
        "the first duplicate label must remain the empty first chapter"
    )


def test_docx_literal_anchor_content_cannot_forge_receipt_unit_keys(tmp_path):
    # GIVEN visible DOCX text that exactly resembles a private chapter anchor
    path = literal_anchor_docx(tmp_path / "literal.docx")
    # WHEN the public run path renders and receipts the document
    code, receipt, markdown = reader.run(path)
    # THEN only the trusted renderer unit appears in navigation and the receipt
    assert (code, receipt["unit_keys"]) == (0, ["chapter/000001"]), (
        "receipt unit keys must come from renderer structure, never rendered text parsing"
    )
    forged = (
        '<span id="brewdoc-chapter-999999">', '<div id=brewdoc-page-999999>',
        '<a name="brewdoc-sheet-999999">', '<span id="brewdoc-sheet-888888">',
        '<a name=brewdoc-chapter-888888>', '<span id="brewdoc&#45;chapter-777777">',
        '<a name="&#98;rewdoc-sheet-777777">', '<div id="brewdoc&#45;page-666666">',
    )
    assert [tag in markdown for tag in forged] == [False] * len(forged), (
        "source elements and id or name attributes must not forge contract anchors"
    )
    escaped = (
        '&lt;span id="brewdoc-chapter-999999"&gt;span&lt;/span&gt;',
        '&lt;div id=brewdoc-page-999999&gt;div&lt;/div&gt;',
        '&lt;a name="brewdoc-sheet-999999"&gt;name&lt;/a&gt;',
        '&lt;span id="brewdoc-sheet-888888"&gt;table&lt;/span&gt;',
        '&lt;a name=brewdoc-chapter-888888&gt;cell&lt;/a&gt;',
        '&lt;span id="brewdoc&#45;chapter-777777"&gt;entity&lt;/span&gt;',
        '&lt;a name="&#98;rewdoc-sheet-777777"&gt;encoded&lt;/a&gt;',
        '&lt;div id="brewdoc&#45;page-666666"&gt;table-entity&lt;/div&gt;',
    )
    assert [text in markdown for text in escaped] == [True] * len(escaped), (
        "forged prose and table tags must remain visible as escaped source text"
    )
    assert markdown.count('<a id="brewdoc-chapter-000001"></a>') == 1, (
        "the generated contract anchor must remain raw and unique"
    )
    assert '[Chapter 1: "Actual"](#brewdoc-chapter-000001)' in markdown, (
        "literal source text must not corrupt real contents navigation"
    )


def test_fixed_layout_block_keeps_source_angle_brackets_and_ampersands_literal(tmp_path):
    # GIVEN two aligned three-column code rows and one prose line, all with < > &
    path = tmp_path / "code.pdf"
    cells = [(72, 700, "if d > 0:"), (220, 700, "a < b"), (360, 700, "x & y"),
             (72, 688, "while c:"), (220, 688, "c > d"), (360, 688, "p & q"),
             (72, 600, "prose a < b & c > d")]
    path.write_bytes(reader.synthetic_pdf(
        ["".join("BT /F1 10 Tf 1 0 0 1 %d %d Tm (%s) Tj ET\n" % cell for cell in cells)]))
    # WHEN the PDF is rendered
    markdown, _tally = reader.render_pdf(path)
    # THEN fenced code keeps source characters literally while prose stays escaped
    assert markdown[markdown.index('<a id="brewdoc-page-000001"></a>'):] == (
        '<a id="brewdoc-page-000001"></a>\n## Page 1\n\n```\n'
        "if d > 0:            a < b              x & y\n"
        "while c:             c > d              p & q\n"
        "```\n\nprose a &lt; b & c &gt; d\n"
    ), "fenced code is literal Markdown, so HTML escaping there corrupts source text"


def test_workbook_only_options_fail_with_receipt_before_pdf_output(tmp_path):
    # GIVEN a valid PDF and a requested Markdown path
    path = three_page_fixture(tmp_path / "fixture.pdf")
    out = tmp_path / "fixture.md"
    # WHEN a workbook-only sheet option is supplied through the public API
    code, receipt, markdown = reader.run(path, out, sheets=("Sheet1",))
    # THEN the normal receipt reports exit 1 and no output is created
    assert (code, receipt["file_ok"], receipt["reason"], markdown) == (
        1, False, "--sheet and --artifact are workbook-only options", "",
    ), "workbook-only options on PDF or DOCX must be refused consistently"
    assert not out.exists(), "option validation must precede any Markdown write"


def test_argparse_syntax_errors_keep_exit_two():
    # GIVEN a repeatable option without its required value
    # WHEN argparse processes the invalid token sequence
    with pytest.raises(SystemExit) as raised:
        reader.main(["book.xlsx", "--sheet"])
    # THEN the established argparse usage code is preserved
    assert raised.value.code == 2, "CLI syntax errors must remain distinct from receipt failures"
