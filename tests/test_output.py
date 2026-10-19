import importlib
import itertools
import json
import os
import stat
import unicodedata
from pathlib import Path

import pytest

import brewdoc
from brewdoc import common, selfcheck
from brewdoc.cli import main

from test_contract import HEADING, docx, pdf, rezipped, text_op
from test_epub import CONTAINER, write_epub, xhtml
from test_fixtures import HTML_SUPPORTED
from test_presentations import base_parts, paragraph, pptx, shape, slide
from test_spreadsheets import CALC_TABLE, FORMULA_KEYS, formula_book, refused, zero_sheet_book


def tree_state(root: Path) -> dict:
    """Map each path under root to its bytes and permission bits; directories map to None."""
    return {
        item.relative_to(root).as_posix():
            (item.read_bytes(), stat.S_IMODE(item.lstat().st_mode)) if item.is_file() else None
        for item in sorted(root.rglob("*"))
    }


def new_file_mode(directory: Path) -> int:
    """Measure the mode a fresh 0666 file receives under the current umask."""
    control = directory / "mode-control"
    control.touch(mode=0o666)
    return stat.S_IMODE(control.stat().st_mode)


def observable_mode(posix_mode: int) -> int:
    """Return the permission bits os.stat reports after chmod(posix_mode) on this platform."""
    # Windows chmod only toggles read-only, so stat reports 0o666 or 0o444 there.
    windows_mode = 0o666 if posix_mode & stat.S_IWRITE else 0o444
    return windows_mode if os.name == "nt" else posix_mode


@pytest.mark.parametrize(
    ("artifact_outputs", "reason"),
    [
        (["formula/sheet/000002"], "malformed artifact assignment: formula/sheet/000002"),
        (["missing/key=missing.json"], "unknown artifact key: missing/key"),
        (["formula/sheet/000002=a.json", "formula/sheet/000002=b.json"],
         "duplicate artifact key: formula/sheet/000002"),
    ],
)
def test_invalid_artifact_requests_fail_before_any_write(
        tmp_path, monkeypatch, capsys, artifact_outputs, reason):
    # GIVEN an invalid CLI artifact request resolved inside tmp_path and a valid Markdown destination
    monkeypatch.chdir(tmp_path)
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)
    # WHEN the request is validated
    code = main([str(path), "--out", str(tmp_path / "book.md"), "--sheet", "Calc",
                 *(f"--artifact={item}" for item in artifact_outputs)])
    # THEN stdout is the one exact refusal receipt line and nothing is written
    assert (code, capsys.readouterr().out, tree_state(tmp_path)) == (
        1, json.dumps(refused(reason), ensure_ascii=True, sort_keys=True) + "\n", before,
    ), "invalid artifact requests must be refused before any write"


@pytest.mark.parametrize(
    ("artifact_outputs", "reason"),
    [
        pytest.param([], "artifact outputs must map keys to paths", id="not-a-mapping"),
        pytest.param({"": "a.json"}, "malformed artifact assignment: ('', 'a.json')", id="empty-key"),
        pytest.param({1: "a.json"}, "malformed artifact assignment: (1, 'a.json')", id="integer-key"),
        pytest.param({"formula/sheet/000001": ""},
                     "malformed artifact assignment: ('formula/sheet/000001', '')", id="empty-path"),
        pytest.param({"formula/sheet/000001": 1},
                     "malformed artifact assignment: ('formula/sheet/000001', 1)", id="integer-path"),
    ],
)
def test_api_artifact_mapping_validation_refuses_before_any_write(tmp_path, artifact_outputs, reason):
    # GIVEN a valid workbook and an existing output that invalid assignments must not replace
    path = formula_book(tmp_path / "book.xlsx")
    out = tmp_path / "book.md"
    out.write_bytes(b"previous Markdown\n")
    before = tree_state(tmp_path)
    # WHEN the API validates a non-mapping or malformed key/path pair
    result = brewdoc.run(path, out, artifact_outputs=artifact_outputs)
    # THEN its full refusal and all previous bytes and modes remain unchanged
    assert (result, tree_state(tmp_path)) == ((1, refused(reason), ""), before), (
        "API artifact validation must precede output staging and preserve existing files"
    )


def test_canonically_equivalent_artifact_paths_collide_before_any_write(tmp_path):
    # GIVEN unequal filenames whose composed and decomposed forms have the same NFC spelling
    composed, decomposed = "caf\u00e9.json", "cafe\u0301.json"
    assert (composed == decomposed, unicodedata.normalize("NFC", composed),
            unicodedata.normalize("NFC", decomposed)) == (False, "café.json", "café.json"), (
        "the destinations must differ before normalization and coincide after NFC"
    )
    path = formula_book(tmp_path / "book.xlsx")
    out = tmp_path / "book.md"
    before = tree_state(tmp_path)
    assert (out.exists(), (tmp_path / composed).exists(), (tmp_path / decomposed).exists()) == (
        False, False, False,
    ), "collision validation must start with all output destinations absent"
    # WHEN two different artifact keys request those absent destinations
    result = brewdoc.run(path, out, artifact_outputs={
        FORMULA_KEYS[0]: tmp_path / composed, FORMULA_KEYS[1]: tmp_path / decomposed,
    })
    # THEN the full collision receipt is returned without files or stages
    assert (result, tree_state(tmp_path)) == (
        (1, refused("artifact outputs collide: formula/sheet/000001 and formula/sheet/000002"), ""),
        before,
    ), "canonical Unicode aliases must be rejected before either artifact is written"


@pytest.mark.parametrize(
    ("files", "dirs", "links", "out", "artifacts", "reason"),
    [
        pytest.param((), (), (), "book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-is-source"),
        pytest.param((), ("sub",), (), "sub/../book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-hardlink"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-is-source"),
        pytest.param((), ("sub",), (), "book.md", ((FORMULA_KEYS[0], "sub/../book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-hardlink"),
        pytest.param((), (), (), "same.out", ((FORMULA_KEYS[0], "same.out"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-same-path"),
        pytest.param(("book.md",), (), (("hardlink_to", "alias.json", "book.md"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-hardlink"),
        pytest.param(("book.md",), (), (("symlink_to", "alias.json", "book.md"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-symlink"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-same-path"),
        pytest.param((), ("sub",), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "sub/../calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-spelling"),
        pytest.param(("calc.json",), (), (("hardlink_to", "alias.json", "calc.json"),),
                     "book.md", ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "alias.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-hardlink"),
        pytest.param(("calc.json",), (), (("symlink_to", "alias.json", "calc.json"),),
                     "book.md", ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "alias.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-symlink"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "BOOK.md"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="casefold-markdown-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "Calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="casefold-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "café.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "CAFÉ.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-casefold-artifact-artifact"),
        pytest.param((), ("book.md",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-directory"),
        pytest.param((), ("calc.json",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {artifacts[0]}",
                     id="artifact-directory"),
        pytest.param(("real.md",), (), (("symlink_to", "book.md", "real.md"),), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-symlink"),
        pytest.param(("parent",), (), (), "parent/book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output parent is not a directory: {out.parent}",
                     id="markdown-parent-is-file"),
    ],
)
def test_invalid_output_plans_are_refused_before_any_write(
        tmp_path, files, dirs, links, out, artifacts, reason):
    # GIVEN a workbook, prepared filesystem state, and an output plan that aliases, collides
    # by spelling, NFC casefold, hardlink or symlink, or names a non-regular target
    path = formula_book(tmp_path / "book.xlsx")
    for name in files:
        (tmp_path / name).write_bytes(b"old " + name.encode("ascii"))
    for name in dirs:
        (tmp_path / name).mkdir()
    for method, link, target in links:
        getattr(tmp_path / link, method)(tmp_path / target)
    markdown_out = tmp_path / out
    outputs = [(key, tmp_path / name) for key, name in artifacts]
    before = tree_state(tmp_path)
    # WHEN the plan is requested through the public run path
    actual = brewdoc.run(path, markdown_out, artifact_outputs=dict(outputs))
    # THEN it is refused with the exact reason and no file or stage changes
    assert (actual, tree_state(tmp_path)) == (
        (1, refused(reason.format(out=markdown_out, artifacts=[item for _key, item in outputs])),
         ""), before,
    ), "invalid output plans must be refused before any byte is written"


def existing_targets(tmp_path: Path) -> list[Path]:
    """Return Markdown and artifact targets: two existing with their own modes, one new."""
    targets = [tmp_path / "book.md", tmp_path / "first.json", tmp_path / "later.json"]
    for target, data, mode in zip(targets, (b"old markdown", b"old artifact"), (0o640, 0o600)):
        target.write_bytes(data)
        target.chmod(mode)
    return targets


@pytest.mark.parametrize("failing_call", [1, 2, 3])
def test_failed_replace_restores_earlier_targets_and_removes_new_ones(
        tmp_path, monkeypatch, failing_call):
    # GIVEN existing and new targets and os.replace failing only on the Nth call
    path = formula_book(tmp_path / "book.xlsx")
    targets = existing_targets(tmp_path)
    before = tree_state(tmp_path)

    def fail(_source, _target):
        raise OSError("injected replace failure")

    steps = itertools.chain(itertools.repeat(os.replace, failing_call - 1), [fail],
                            itertools.repeat(os.replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN publishing fails part way through
    actual = brewdoc.run(path, targets[0], artifact_outputs=dict(zip(FORMULA_KEYS, targets[1:])))
    # THEN originals keep bytes and modes, the new target is absent, and no stage remains
    assert (actual, tree_state(tmp_path)) == ((1, refused(
        "output write failed: could not replace %s: injected replace failure"
        % targets[failing_call - 1]), ""), before,
    ), "a failed replace must restore every target and name the failed one"


def test_failed_restore_after_failed_replace_names_every_unrestored_target(
        tmp_path, monkeypatch):
    # GIVEN existing and new targets and os.replace failing from the third target onwards
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, first, later = existing_targets(tmp_path)
    markdown = brewdoc.run(path)[2]
    before = tree_state(tmp_path)

    def fail(_source, _target):
        raise OSError("injected failure")

    steps = itertools.chain(itertools.repeat(os.replace, 2), itertools.repeat(fail))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN the third replace and both restores fail
    actual = brewdoc.run(path, markdown_out, artifact_outputs=dict(zip(FORMULA_KEYS, (first, later))))
    # THEN run fails, names every unrestored target, and leaves new bytes but no stage
    assert (actual, tree_state(tmp_path)) == ((1, refused(
        "output write failed: could not replace %s: injected failure; could not restore "
        "%s (injected failure), %s (injected failure)" % (later, markdown_out, first)), ""), {
        **before, "book.md": (markdown.encode("ascii"), observable_mode(0o640)),
        "first.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[0]), observable_mode(0o600)),
    }), "an unrestored target must be reported, never silently left replaced"


def test_staging_failure_fails_with_a_receipt_and_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and os.fsync failing while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def fail(_descriptor):
        raise OSError("injected staging failure")

    steps = itertools.chain([os.fsync], [fail], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN staging fails after one payload was prepared
    actual = brewdoc.run(path, tmp_path / "book.md", artifact_outputs=dict(
        zip(FORMULA_KEYS, (tmp_path / "a.json", tmp_path / "b.json"))))
    # THEN the receipt reports the failure and neither outputs nor stages exist
    assert (actual, tree_state(tmp_path)) == (
        (1, refused("output write failed: injected staging failure"), ""), before,
    ), "a staging failure must leave the directory exactly as it was"


def test_keyboard_interrupt_during_staging_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and an interrupt while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def interrupt(_descriptor):
        raise KeyboardInterrupt

    steps = itertools.chain([os.fsync], [interrupt], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN the interrupt propagates out of run
    with pytest.raises(KeyboardInterrupt):
        brewdoc.run(path, tmp_path / "book.md", artifact_outputs=dict(
            zip(FORMULA_KEYS, (tmp_path / "a.json", tmp_path / "b.json"))))
    # THEN no output or .brewdoc-* stage file remains
    assert tree_state(tmp_path) == before, "an interrupt must not leak stages or partial outputs"


def test_successful_run_writes_exact_bytes_keeps_modes_and_leaves_no_stage(tmp_path):
    # GIVEN existing Markdown and artifact targets with their own modes and one new target
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, first, later = existing_targets(tmp_path)
    created_mode = new_file_mode(tmp_path)
    before = tree_state(tmp_path)
    # WHEN all three are published
    code, receipt, markdown = brewdoc.run(
        path, markdown_out, artifact_outputs=dict(zip(FORMULA_KEYS, (first, later))))
    # THEN bytes are exact, existing modes survive, the new file follows 0666 & ~umask
    assert (code, receipt["file_ok"], tree_state(tmp_path)) == (0, True, {
        **before,
        "book.md": (markdown.encode("ascii"), observable_mode(0o640)),
        "first.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[0]), observable_mode(0o600)),
        "later.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[1]), created_mode),
    }), "success must publish exact bytes with preserved or umask modes and no stage files"


def test_parent_symlink_retarget_cannot_redirect_publish_or_leak_stage(tmp_path, monkeypatch):
    # GIVEN an output parent symlink to directory A that is retargeted to B before publish
    path = formula_book(tmp_path / "book.xlsx")
    first_parent, second_parent = tmp_path / "dir-a", tmp_path / "dir-b"
    first_parent.mkdir()
    second_parent.mkdir()
    created_mode = new_file_mode(tmp_path)
    link = tmp_path / "out"
    link.symlink_to(first_parent, target_is_directory=True)

    def retarget_then_replace(source, target):
        link.unlink()
        link.symlink_to(second_parent, target_is_directory=True)
        return real_replace(source, target)

    real_replace = os.replace
    steps = itertools.chain([retarget_then_replace], itertools.repeat(real_replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN the Markdown is published through the lexical symlink path
    code, _receipt, markdown = brewdoc.run(path, link / "book.md")
    # THEN the parent captured before staging receives it and neither directory keeps a stage
    assert (code, tree_state(first_parent), tree_state(second_parent)) == (
        0, {"book.md": (markdown.encode("ascii"), created_mode)}, {},
    ), "publish must use the real parent captured before staging"


HTML_ONLY = pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
PARAGRAPH = "<w:p><w:r><w:t>%s</w:t></w:r></w:p>"
ENVELOPE = ('# "doc.pdf"\n\n<a id="brewdoc-contents"></a>\n## Contents\n\n'
            "- [Page 1](#brewdoc-page-000001)\n- [Page 2](#brewdoc-page-000002)\n\n")
FIRST, SECOND = '<a id="brewdoc-page-000001"></a>', '<a id="brewdoc-page-000002"></a>'
PAGES = ("page/000001", "page/000002")
PAGE_TWO = SECOND + "\n## Page 2\n\nworld\n"
EPUB_FENCED = ('<p id="f">First <a href="#f">self</a> <a href="c2.xhtml">next</a></p>'
               '<pre>&lt;a id="brewdoc-chapter-000002"&gt;&lt;/a&gt;\n## Chapter 2: "Two"</pre>')
HTML_PAGE = '<title>Page</title><p>Body café</p><pre>&lt;a id="brewdoc-chapter-000001"&gt;&lt;/a&gt;</pre>'
NOTE = '# Title\n\n<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "x"\n'
ARTICLE_MARKDOWN = (
    '# "page.html"\n\n<a id="brewdoc-metadata"></a>\n## Metadata\n\n| Field | Value |\n| --- | --- |\n'
    "| Markdown schema | brewdoc.markdown/2 |\n| Source name | \"page.html\" |\n| Source suffix | .html |\n"
    "| Source bytes | 63 |\n"
    "| Source SHA-256 | 065b98e8cf6b4873df3ba86a35fb73b46138e1529473351266891687ae293500 |\n"
    "| Route | html |\n| Unit kind | chapter |\n| Source unit count | 1 |\n| Rendered unit count | 1 |\n"
    "| Ordered selection keys | chapter/000001 |\n| Rendered body bytes | 63 |\n"
    "| Rendered body SHA-256 | 7ea188a9e0c9959d7bac56a121c1be840d676c321347612e7b4d796fe58b7bb3 |\n"
    "| Pages | 0 |\n| Sheets | 0 |\n| Chapters | 1 |\n| Tables | 0 |\n| Text regions | 1 |\n"
    "| Column splits | 0 |\n| Broken ligature words | 0 |\n| Dropped control chars | 0 |\n"
    "| Dropped soft hyphens | 0 |\n| Dropped nbsp | 0 |\n| Dropped pua glyphs | 0 |\n"
    "| Dropped cid survivors | 0 |\n| Dropped ligatures | 0 |\n| Dropped running heads | 0 |\n"
    "| Dropped page numbers | 0 |\n| Dropped non ascii replaced | 0 |\n"
    "| Formula capability | not applicable |\n| Selected formula count | not applicable |\n"
    "| VBA project capability | not applicable |\n| VBA project count | not applicable |\n"
    "| VBA source module capability | not applicable |\n| VBA source module count | not applicable |\n\n"
    "## Artifacts\n\nNone.\n\n## Known omissions\n\n"
    "- dynamic browser content, scripts and stylesheets\n"
    "- graphical SVG content, canvas pixels and MathML semantics\n- active media and embeds\n"
    "- inline code layout outside tables: rendered as fenced blocks\n- inline code monospace inside table cells\n"
    "- form controls and their values\n- images without alt text\n"
    "- navigation, header, footer and aside landmarks outside main or article content\n"
    "- body content outside the selected article or main subtree; complete page coverage is not claimed\n\n"
    '<a id="brewdoc-contents"></a>\n## Contents\n\n- [Chapter 1: "Page"](#brewdoc-chapter-000001)\n\n'
    '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Page"\n\nKept\n'
)


def epub(path: Path, *chapters: str) -> Path:
    """Write an EPUB 3 whose spine and navigation list chapters c1, c2, ... in order."""
    ids = ["c%d" % number for number in range(1, len(chapters) + 1)]
    package = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book</dc:title>'
               '<dc:identifier id="id">urn:synthetic</dc:identifier><dc:language>en</dc:language>'
               '</metadata><manifest>%s<item id="nav" href="nav.xhtml" '
               'media-type="application/xhtml+xml" properties="nav"/></manifest><spine>%s</spine>'
               '</package>' % ("".join('<item id="%s" href="%s.xhtml" media-type="application/'
                                       'xhtml+xml"/>' % (item, item) for item in ids),
                               "".join('<itemref idref="%s"/>' % item for item in ids)))
    nav = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           '<head><title>TOC</title></head><body><nav epub:type="toc"><ol>%s</ol></nav></body></html>'
           % "".join('<li><a href="%s.xhtml">%s</a></li>' % (item, item.upper()) for item in ids))
    return write_epub(path, {
        "mimetype": "application/epub+zip", "META-INF/container.xml": CONTAINER,
        "OPS/book.opf": package, "OPS/nav.xhtml": nav,
        **{"OPS/%s.xhtml" % item: xhtml(body) for item, body in zip(ids, chapters)},
    })


def written(path: Path, text: str) -> Path:
    """Write UTF-8 `text` to `path` and return it."""
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.mark.parametrize(
    ("markdown", "unit_keys", "expected"),
    [
        pytest.param(ENVELOPE, (), "", id="no-units"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\nhello\n", PAGES[:1], "hello\n", id="one-unit"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n", PAGES[:1], "", id="one-empty-unit"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n## Not a unit\n", PAGES[:1],
                     "## Not a unit\n", id="one-unit-keeps-source-heading"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\nhello\n\n" + PAGE_TWO, PAGES,
                     "## Page 1\n\nhello\n\n## Page 2\n\nworld\n", id="two-units"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n```text\n" + SECOND + "\n```\n\n" + PAGE_TWO,
                     PAGES, "## Page 1\n\n```text\n" + SECOND + "\n```\n\n## Page 2\n\nworld\n",
                     id="backtick-fence-with-info"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n~~~\n" + SECOND + "\n~~~\n\n" + PAGE_TWO,
                     PAGES, "## Page 1\n\n~~~\n" + SECOND + "\n~~~\n\n## Page 2\n\nworld\n",
                     id="tilde-fence"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n````\n```\n" + SECOND + "\n````\n\n" + PAGE_TWO,
                     PAGES, "## Page 1\n\n````\n```\n" + SECOND + "\n````\n\n## Page 2\n\nworld\n",
                     id="shorter-run-does-not-close"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n```\n~~~\n" + SECOND + "\n```\n\n" + PAGE_TWO,
                     PAGES, "## Page 1\n\n```\n~~~\n" + SECOND + "\n```\n\n## Page 2\n\nworld\n",
                     id="other-character-does-not-close"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n   ```\n" + SECOND + "\n   ```\t \n\n" + PAGE_TWO,
                     PAGES, "## Page 1\n\n   ```\n" + SECOND + "\n   ```\t \n\n## Page 2\n\nworld\n",
                     id="indented-fence-and-padded-close"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n    ```\n" + PAGE_TWO, PAGES,
                     "## Page 1\n\n    ```\n## Page 2\n\nworld\n", id="four-space-indent-is-no-fence"),
        pytest.param(ENVELOPE + FIRST + "\n## Page 1\n\n```a`b\n" + PAGE_TWO, PAGES,
                     "## Page 1\n\n```a`b\n## Page 2\n\nworld\n", id="backtick-info-is-no-fence"),
        pytest.param('<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "x"\n\n'
                     '<a id="brewdoc-chapter-000001-fragment-000001"></a>\nText\n', ("chapter/000001",),
                     '<a id="brewdoc-chapter-000001-fragment-000001"></a>\nText\n',
                     id="fragment-anchor-stays"),
    ],
)
def test_body_only_helper_strips_the_envelope_and_unit_anchors_outside_fences(
        markdown, unit_keys, expected):
    # GIVEN a schema 2 document shape and its ordered unit keys
    # WHEN only the body is derived from it
    body = common._body_only(markdown, unit_keys)
    # THEN envelope and unit anchors go, fenced lines stay, one heading only for several units
    assert body == expected, "body-only must remove generated navigation and keep source lines verbatim"


@pytest.mark.parametrize(
    ("name", "build", "options", "body"),
    [
        pytest.param("one.pdf", lambda path: pdf(path, text_op(72, 700, "hello")), {}, "hello\n",
                     id="pdf-one-page"),
        pytest.param("two.pdf", lambda path: pdf(path, text_op(72, 700, "hello"), text_op(72, 700, "world")),
                     {}, "## Page 1\n\nhello\n\n## Page 2\n\nworld\n", id="pdf-two-pages"),
        pytest.param("one.docx", lambda path: docx(path, PARAGRAPH % "Plain words"), {}, "Plain words\n",
                     id="docx-one-chapter"),
        pytest.param("two.docx", lambda path: docx(path, HEADING % "Intro" + PARAGRAPH % "First"
                                                   + HEADING % "Next" + PARAGRAPH % "Second"), {},
                     '## Chapter 1: "Intro"\n\nFirst\n\n## Chapter 2: "Next"\n\nSecond\n',
                     id="docx-two-chapters"),
        pytest.param("book.xlsx", formula_book, {"sheets": ["Calc"]}, CALC_TABLE,
                     id="xlsx-one-selected-sheet"),
        pytest.param("book.xlsx", formula_book, {},
                     '## Sheet 1: "Inputs"\n\n| 1.0 |\n| --- |\n| 2.0 |\n| 3.0 |\n\n'
                     '## Sheet 2: "Calc"\n\n' + CALC_TABLE + '\n## Sheet 3: "Empty"\n',
                     id="xlsx-three-sheets"),
        pytest.param("empty.xlsm", zero_sheet_book, {}, "", id="xlsm-no-sheets"),
        pytest.param("page.html", lambda path: written(path, HTML_PAGE), {},
                     'Body café\n\n```\n<a id="brewdoc-chapter-000001"></a>\n```\n',
                     id="html-one-chapter", marks=HTML_ONLY),
        pytest.param("one.epub", lambda path: epub(path, "<p>Only text</p>"), {}, "Only text\n",
                     id="epub-one-chapter"),
        pytest.param("two.epub", lambda path: epub(path, EPUB_FENCED, "<p>Second</p>"), {},
                     '## Chapter 1: "C1"\n\n<a id="brewdoc-chapter-000001-fragment-000001"></a>\n'
                     "First [self](#brewdoc-chapter-000001-fragment-000001) [next](#brewdoc-chapter-000002)"
                     '\n\n```\n<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Two"\n```\n\n'
                     '## Chapter 2: "C2"\n\nSecond\n', id="epub-two-chapters"),
        pytest.param("note.md", lambda path: written(path, NOTE), {}, NOTE, id="markdown-pass-through"),
        pytest.param("note.txt", lambda path: written(path, NOTE), {}, NOTE, id="text-pass-through"),
    ],
)
def test_body_only_run_returns_the_default_receipt_and_only_the_body(tmp_path, name, build, options, body):
    # GIVEN a single- or multi-unit document and its default successful run
    path = build(tmp_path / name)
    code, receipt, _markdown = brewdoc.run(path, **options)
    assert (code, receipt["file_ok"]) == (0, True), "the default run must succeed first"
    # WHEN the same document is rendered body-only
    result = brewdoc.run(path, **options, body_only=True)
    # THEN the receipt is unchanged and Markdown keeps only the unit body
    assert result == (0, receipt, body), "body-only must change only the Markdown, never the receipt"


def two_slides(path: Path, first: str) -> Path:
    """Write a two-slide PPTX whose untitled slides hold `first` and `two`."""
    parts = base_parts([("rId1", "slides/one.xml", False), ("rId2", "slides/two.xml", False)])
    parts["deck/slides/one.xml"] = slide(shape("Body", paragraph(first)))
    parts["deck/slides/two.xml"] = slide(shape("Body", paragraph("two")))
    return pptx(path, parts)


ODF_TEXT = "caf\u00e9 \u6771\u4eac"
ODT_NEXT = '</text:p><text:p>First</text:p><text:h text:outline-level="1">Next</text:h><text:p>Second'
ODP_NEXT = ("</text:p></draw:text-box></draw:frame></draw:page><draw:page draw:name=\"Two\">"
            "<draw:frame><draw:text-box><text:p>two")


@pytest.mark.parametrize(
    ("name", "build", "body"),
    [
        pytest.param("fence.docx", lambda path: docx(path, HEADING % "Intro" + PARAGRAPH % "```"
                                                     + PARAGRAPH % "First" + HEADING % "Next"
                                                     + PARAGRAPH % "Second"),
                     '## Chapter 1: "Intro"\n\n\\```\n\nFirst\n\n## Chapter 2: "Next"\n\nSecond\n',
                     id="docx-backticks"),
        pytest.param("fence.pptx", lambda path: two_slides(path, "```"),
                     '## Slide 1: "Untitled"\n\n\\```\n\n## Slide 2: "Untitled"\n\ntwo\n',
                     id="pptx-backticks"),
        pytest.param("fence.odt", lambda path: rezipped(path, selfcheck._synthetic_odf("odt"), ODF_TEXT,
                                                        "~~~" + ODT_NEXT),
                     '## Chapter 1: "Title"\n\n\\~~~\n\nFirst\n\n## Chapter 2: "Next"\n\nSecond\n',
                     id="odt-tildes"),
        pytest.param("fence.odp", lambda path: rezipped(path, selfcheck._synthetic_odf("odp"), ODF_TEXT,
                                                        "````" + ODP_NEXT),
                     '## Slide 1: "Title"\n\n\\````\n\n## Slide 2: "Two"\n\ntwo\n',
                     id="odp-long-backticks"),
        pytest.param("fence.pdf", lambda path: pdf(path, text_op(72, 700, "~~~") + text_op(72, 680, "hello"),
                                                   text_op(72, 700, "world")),
                     "## Page 1\n\n\\~~~\nhello\n\n## Page 2\n\nworld\n", id="pdf-tildes"),
    ],
)
def test_body_only_drops_later_unit_anchors_after_a_raw_route_fence_line(tmp_path, name, build, body):
    # GIVEN a two-unit raw-route document whose first unit holds an unclosed fence opener
    path = build(tmp_path / name)
    # WHEN only its body is rendered
    code, _receipt, markdown = brewdoc.run(path, body_only=True)
    # THEN the fence run is escaped, so no fence hides the second unit anchor from body-only
    assert (code, markdown) == (0, body), "source text must not keep a unit anchor in body-only output"


@pytest.mark.parametrize(
    ("name", "build", "options"),
    [
        pytest.param("absent.pdf", lambda path: path, {}, id="missing-file"),
        pytest.param("data.bin", lambda path: written(path, "data"), {}, id="unsupported-suffix"),
        pytest.param("broken.docx", lambda path: written(path, "not a zip"), {}, id="unreadable-docx"),
        pytest.param("doc.pdf", lambda path: pdf(path, text_op(72, 700, "hello")), {"article": True},
                     id="article-on-pdf"),
        pytest.param("doc.pdf", lambda path: pdf(path, text_op(72, 700, "hello")), {"sheets": ["A"]},
                     id="sheet-on-pdf"),
        pytest.param("book.xlsx", formula_book, {"sheets": ["Missing"]}, id="unknown-sheet"),
    ],
)
def test_body_only_refuses_exactly_like_the_default_run(tmp_path, name, build, options):
    # GIVEN a refused request, an existing output and the default refusal
    path = build(tmp_path / name)
    out = tmp_path / "kept.md"
    out.write_bytes(b"existing output\n")
    before = tree_state(tmp_path)
    default = brewdoc.run(path, out, **options)
    assert (default[0], default[2], tree_state(tmp_path)) == (1, "", before), (
        "the default request must refuse without writing")
    # WHEN the same request asks for body-only output
    result = brewdoc.run(path, out, **options, body_only=True)
    # THEN the refusal receipt is identical and nothing is written
    assert (result, tree_state(tmp_path)) == (default, before), "body-only must not change a refusal"


def test_body_only_keeps_the_full_document_output_limit(tmp_path, monkeypatch):
    # GIVEN a two-chapter EPUB whose Markdown limit is one byte under its full document
    path = epub(tmp_path / "two.epub", "<p>First</p>", "<p>Second</p>")
    out = tmp_path / "kept.md"
    out.write_bytes(b"existing output\n")
    size = len(brewdoc.run(path)[2].encode("utf-8"))
    monkeypatch.setitem(importlib.import_module("brewdoc.epub").LIMITS, "markdown_bytes", size - 1)
    default = brewdoc.run(path, out)
    assert (default[0], default[2], out.read_bytes()) == (1, "", b"existing output\n"), (
        "one byte under the full document must refuse the default run")
    # WHEN the much smaller body-only output is requested
    result = brewdoc.run(path, out, body_only=True)
    # THEN it refuses with the identical receipt and keeps the earlier output
    assert (result, out.read_bytes()) == (default, b"existing output\n"), (
        "limits apply to the full rendering, so body-only must not relax them")


@pytest.mark.parametrize(
    ("name", "build", "body"),
    [
        pytest.param("two.pdf", lambda path: pdf(path, text_op(72, 700, "hello"), text_op(72, 700, "world")),
                     "## Page 1\n\nhello\n\n## Page 2\n\nworld\n", id="pdf-two-pages"),
        pytest.param("page.html", lambda path: written(path, HTML_PAGE),
                     'Body café\n\n```\n<a id="brewdoc-chapter-000001"></a>\n```\n',
                     id="html-one-chapter", marks=HTML_ONLY),
    ],
)
def test_cli_body_only_prints_the_default_receipt_then_the_body(tmp_path, capsys, name, build, body):
    # GIVEN a document and the receipt line of its default CLI run
    path = build(tmp_path / name)
    code = main([str(path)])
    receipt = capsys.readouterr().out.partition("\n")[0]
    assert (code, json.loads(receipt)["file_ok"]) == (0, True), "the default CLI run must succeed"
    # WHEN the CLI is asked for body-only output on stdout
    code = main([str(path), "--body-only"])
    # THEN stdout is the same receipt line followed by the body
    assert (code, capsys.readouterr().out) == (0, receipt + "\n" + body), (
        "--body-only must change only the Markdown after the receipt line")


@pytest.mark.parametrize(
    ("name", "build", "body"),
    [
        pytest.param("two.pdf", lambda path: pdf(path, text_op(72, 700, "hello"), text_op(72, 700, "world")),
                     b"## Page 1\n\nhello\n\n## Page 2\n\nworld\n", id="pdf-ascii"),
        pytest.param("page.html", lambda path: written(path, HTML_PAGE),
                     'Body café\n\n```\n<a id="brewdoc-chapter-000001"></a>\n```\n'.encode("utf-8"),
                     id="html-utf8", marks=HTML_ONLY),
    ],
)
def test_cli_body_only_out_writes_body_bytes_in_the_route_encoding(tmp_path, capsys, name, build, body):
    # GIVEN a document and the stdout of its default CLI run into one output file
    path = build(tmp_path / name)
    out = tmp_path / "out.md"
    code = main([str(path), "--out", str(out)])
    receipt = capsys.readouterr().out
    assert (code, json.loads(receipt)["out"]) == (0, str(out)), "the default --out run must succeed"
    # WHEN the CLI writes body-only output to the same file
    code = main([str(path), "--body-only", "--out", str(out)])
    # THEN stdout is the same receipt and the file holds only the encoded body
    assert (code, capsys.readouterr().out, out.read_bytes()) == (0, receipt, body), (
        "--body-only --out must keep the receipt and write the body in the route encoding")


def test_cli_refuses_body_only_with_self_check(monkeypatch, capsys):
    # GIVEN a wide, colourless terminal, so argparse prints usage on one plain line
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    # WHEN body-only is combined with the self-check
    with pytest.raises(SystemExit) as raised:
        main(["--self-check", "--body-only"])
    captured = capsys.readouterr()
    # THEN argparse exits 2 with usage and the exact conflict on stderr
    assert (raised.value.code, captured.out, captured.err) == (
        2, "", "usage: brewdoc [-h] [--url URL] [--timeout SECONDS] [--render-js [{auto,always}]] [--article] [--no-links] "
               "[--body-only] [--out OUT] [--sheet SHEET] [--artifact ARTIFACT] [--self-check] "
               "[document]\nbrewdoc: error: --body-only cannot be combined with --self-check\n",
    ), "--body-only has no meaning for the self-check and must be a usage error"


def test_cli_reports_the_article_conflict_first_with_body_only_and_self_check(monkeypatch, capsys):
    # GIVEN a wide, colourless terminal, so argparse prints usage on one plain line
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    # WHEN article and body-only are both combined with the self-check
    with pytest.raises(SystemExit) as raised:
        main(["--article", "--body-only", "--self-check"])
    captured = capsys.readouterr()
    # THEN the earlier article conflict is the one reported
    assert (raised.value.code, captured.out, captured.err) == (
        2, "", "usage: brewdoc [-h] [--url URL] [--timeout SECONDS] [--render-js [{auto,always}]] [--article] [--no-links] "
               "[--body-only] [--out OUT] [--sheet SHEET] [--artifact ARTIFACT] [--self-check] "
               "[document]\nbrewdoc: error: --article cannot be combined with --self-check\n",
    ), "conflicts must be reported in their existing order"


def test_cli_refuses_no_links_with_self_check(monkeypatch, capsys):
    # GIVEN a wide, colourless terminal and an observable self-check boundary
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    calls = []
    monkeypatch.setattr(importlib.import_module("brewdoc.cli"), "self_check", lambda: calls.append("checked"))
    assert calls == [], "self-check must start idle"
    # WHEN --no-links is combined with the self-check
    with pytest.raises(SystemExit) as raised:
        main(["--no-links", "--self-check"])
    captured = capsys.readouterr()
    # THEN argparse exits 2 with usage and the exact conflict on stderr before any check runs
    assert (raised.value.code, calls, captured.out, captured.err) == (
        2, [], "", "usage: brewdoc [-h] [--url URL] [--timeout SECONDS] [--render-js [{auto,always}]] [--article] [--no-links] "
                   "[--body-only] [--out OUT] [--sheet SHEET] [--artifact ARTIFACT] [--self-check] "
                   "[document]\nbrewdoc: error: --no-links cannot be combined with --self-check\n",
    ), "--no-links has no meaning for the self-check and must be a usage error"


@HTML_ONLY
def test_body_only_composes_with_article_selection(tmp_path):
    # GIVEN an HTML page with prose outside its article and the default article run
    path = written(tmp_path / "page.html", "<title>Page</title><p>Outside</p><article><p>Kept</p></article>")
    code, receipt, markdown = brewdoc.run(path, article=True)
    assert (code, markdown) == (0, ARTICLE_MARKDOWN), "the article run must drop prose outside the article"
    # WHEN the article is rendered body-only
    result = brewdoc.run(path, article=True, body_only=True)
    # THEN the article receipt is unchanged and only the selected body remains
    assert result == (0, receipt, "Kept\n"), "body-only must derive the body from the article output"


@HTML_ONLY
def test_url_body_only_keeps_the_url_receipt_and_prints_the_body(monkeypatch, capsys):
    # GIVEN a static HTML page answered by the guarded request boundary and its default URL run
    html = "<title>Page</title><p>Body café</p>".encode("utf-8")
    monkeypatch.setattr(importlib.import_module("brewdoc.htmlurl"), "_request", lambda url, *args, **options: {
        "status": 200, "headers": (("Content-Type", "text/html; charset=utf-8"),),
        "entity": html, "body": html})
    code = main(["--url", "https://example.com/page"])
    receipt = capsys.readouterr().out.partition("\n")[0]
    assert (code, json.loads(receipt)["route"]) == (0, "html"), "the default URL run must succeed"
    # WHEN the same URL is requested body-only
    code = main(["--url", "https://example.com/page", "--body-only"])
    # THEN the URL receipt is unchanged and only the body follows it
    assert (code, capsys.readouterr().out) == (0, receipt + "\nBody café\n"), (
        "--url must pass --body-only through to the local conversion")
