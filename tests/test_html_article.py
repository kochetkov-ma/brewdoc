"""Article mode selects visible main or article subtrees, else falls back to the whole page."""

import hashlib
import inspect
import json

import pytest

from inspection_contract import (rendering_bytes, rendering_result, rendering_stdout)

import brewdoc
from brewdoc import cli, htmljs, htmltext, htmlurl, service
from test_contract import docx, zero_tally
from test_html import HTML_OMISSIONS, expected_document, expected_receipt, supported_html


ARTICLE_OMISSION = (
    "body content outside the selected article or main subtree; complete page coverage is not claimed"
)


def refused_receipt(path, reason):
    """Pin the complete failure envelope without success-only fields."""
    return {
        "file_ok": False, "route": "html", "reason": reason, "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter",
        "units": 0, "tables": 0, "text_regions": 0, "columns_split": 0,
        "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": [*HTML_OMISSIONS, ARTICLE_OMISSION],
    }


def article_document(path, content, regions, *, title="Untitled", tables=0):
    """Wrap literal selected content in the existing full document envelope."""
    return expected_document(path, content, regions, title=title, tables=tables).replace(
        "- " + HTML_OMISSIONS[-1] + "\n", "- " + HTML_OMISSIONS[-1] + "\n- " + ARTICLE_OMISSION + "\n"
    )


def article_receipt(path, out, regions, *, tables=0):
    """Extend the existing independent receipt expectation with article disclosure."""
    return dict(expected_receipt(path, out, regions, tables=tables), out=out and str(out),
                not_carried=[*HTML_OMISSIONS, ARTICLE_OMISSION])


def fallback_receipt(path, out, regions, *, tables=0):
    """Add the single whole-page fallback signal to the default receipt."""
    return dict(expected_receipt(path, out, regions, tables=tables), out=out and str(out),
                article_fallback=True)


@supported_html
def test_single_article_keeps_unicode_and_excludes_surrounding_body(tmp_path):
    # GIVEN one explicit article between unrelated visible page paragraphs
    path, out = tmp_path / "sample.html", tmp_path / "article.md"
    path.write_text('<p>Outside before</p><article><p>café 東京</p></article><p>Outside after</p>',
                    encoding="utf-8")
    assert out.exists() is False, "article output must start absent"
    # WHEN the public service selects article mode
    result = rendering_result(brewdoc.run(path, out, article=True), output=out)
    # THEN the exact document and complete receipt describe only selected content
    expected = article_document(path, "café 東京\n", 1)
    assert (result, rendering_bytes(out.read_bytes())) == (
        (0, article_receipt(path, out, 1), expected), expected.encode("utf-8")
    ), "article mode must preserve Unicode, exclude outer paragraphs and disclose its scope"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<main><h1>Title</h1><p>Prose</p></main><p>Outside</p>',
                 "### Title\n\nProse\n", 2, 0, id="suitable-main"),
    pytest.param('<main><p>Outside</p><section><custom-story><article><p>Kept</p>'
                 '</article></custom-story></section></main>', "Outside\n\nKept\n", 2, 0,
                 id="main-with-wrapped-article-renders-whole"),
    pytest.param('<article><p>Outside main</p></article><main><article><p>Scoped</p>'
                 '</article></main>', "Scoped\n", 1, 0, id="main-limits-search-scope"),
    pytest.param('<article><p>Before</p><main><p>Inside main</p></main><p>After</p></article>',
                 "Before\n\nInside main\n\nAfter\n", 3, 0, id="nested-main-keeps-outer-article"),
    pytest.param('<article><p>Before</p><article><p>Nested</p></article><p>After</p></article>',
                 "Before\n\nNested\n\nAfter\n", 3, 0, id="nested-articles-stay-in-subtree"),
    pytest.param('<section hidden><article><p>Hidden</p></article></section>'
                 '<article><p>Visible</p></article>', "Visible\n", 1, 0, id="hidden-ancestor"),
    pytest.param('<section aria-hidden="true"><main><article><p>Hidden</p></article></main>'
                 '</section><main><article><p>Visible</p></article></main>',
                 "Visible\n", 1, 0, id="aria-hidden-main-ancestor"),
    pytest.param('<section style="display:none"><article><p>Hidden</p></article></section>'
                 '<article><p>Visible</p></article>', "Visible\n", 1, 0, id="style-hidden-ancestor"),
    pytest.param('<nav><article><p>Navigation</p></article></nav><aside><main><p>Side</p>'
                 '</main></aside><header><article><p>Header</p></article></header>'
                 '<footer><article><p>Footer</p></article></footer><form><article><p>Form</p>'
                 '</article></form><article><p>Visible</p></article>', "Side\n", 1, 0,
                 id="main-in-aside-is-the-only-root"),
    pytest.param('<article><img alt="café 東京" src="pic.png"></article>',
                 '![café 東京](pic.png)\n', 1, 0, id="image-only-article"),
    pytest.param('<article><table><caption>Values</caption><tr><th>Kind</th><th>Dose</th>'
                 '</tr><tr><td>A</td><td>2</td></tr></table></article>',
                 'Caption: Values\n\n| Kind | Dose |\n| --- | --- |\n| A | 2 |\n',
                 1, 1, id="table-only-article-with-caption"),
    pytest.param('<article><h1>Heading</h1><p>By Maksim <time>2026-10-13</time></p>'
                 '<p><a href="/facts">café 東京</a></p><nav><p>Inside navigation</p></nav>'
                 '<p>Comment body</p></article>',
                 '### Heading\n\nBy Maksim 2026-10-13\n\n[café 東京](/facts)\n\n'
                 'Inside navigation\n\nComment body\n', 5, 0, id="visible-metadata-links-and-descendants"),
    pytest.param('<main><h1>Title</h1><h1 hidden>Hidden heading</h1><p>Prose</p>'
                 '<form hidden><input></form></main>', "### Title\n\nProse\n", 2, 0,
                 id="hidden-heading-and-form-do-not-disqualify-main"),
    pytest.param('<main><nav><h1>Title</h1></nav><custom-prose>Plain prose</custom-prose></main>',
                 "### Title\n\nPlain prose\n", 2, 0, id="visible-navigation-heading-and-custom-prose"),
    pytest.param('<main><h1>Title</h1>Plain prose</main>', "### Title\n\nPlain prose\n", 2, 0,
                 id="bare-main-prose"),
    pytest.param('<div role="button"><article><p>Control label</p></article></div>'
                 '<article><p>Visible</p></article>', "Control label\n\nVisible\n", 2, 0,
                 id="role-button-no-longer-excludes-candidate"),
    pytest.param('<div role="main"><p>R</p></div><p>Out</p>', "R\n", 1, 0, id="role-main"),
    pytest.param('<div role="MAIN landmark"><p>R</p></div><p>Out</p>', "R\n", 1, 0,
                 id="role-main-first-token-ascii-lowercased"),
    pytest.param('<main><p>No heading</p></main>', "No heading\n", 1, 0, id="main-without-h1"),
    pytest.param('<main><h1> </h1><p>Prose</p></main>', "Prose\n", 1, 0, id="main-with-blank-h1"),
    pytest.param('<main><h1>One</h1><h1>Two</h1><p>Prose</p></main>',
                 "### One\n\n### Two\n\nProse\n", 3, 0, id="main-with-two-h1"),
    pytest.param('<main><h1>Title</h1><nav><p>Only navigation</p></nav></main>',
                 "### Title\n\nOnly navigation\n", 2, 0, id="main-with-only-navigation"),
    pytest.param('<main><h1>Title</h1><table><tr><td>Only table</td></tr></table></main>',
                 "### Title\n\n| Only table |\n| --- |\n", 1, 1, id="main-with-only-table"),
    pytest.param('<main><h1>Title</h1><input value="Only control"></main>', "### Title\n", 1, 0,
                 id="main-with-only-control"),
    pytest.param('<main><h1>Title</h1><p>Prose</p><form></form></main>', "### Title\n\nProse\n",
                 2, 0, id="main-with-visible-empty-form"),
    pytest.param('<main><h1>Title</h1><div role="button">Only control label</div></main>',
                 "### Title\n\nOnly control label\n", 2, 0, id="main-with-role-button-prose"),
    pytest.param('<div role="button"><article><p>Control label</p></article></div>',
                 "Control label\n", 1, 0, id="role-button-only-candidate"),
    pytest.param('<main><article><p>One</p></article></main><main></main>', "One\n", 1, 0,
                 id="empty-peer-main"),
    pytest.param('<main><article><p>One</p></article></main><main><article><p>Two</p>'
                 '</article></main>', "One\n\nTwo\n", 2, 0, id="articles-across-two-mains"),
    pytest.param('<main><p>A</p></main><main><p>B</p></main>', "A\n\nB\n", 2, 0,
                 id="two-mains-without-articles"),
    pytest.param('<main></main><main><p>B</p></main>', "B\n", 1, 0, id="empty-then-filled-main"),
    pytest.param('<main><p>A</p></main><p>Out</p><div role="main"><p>B</p></div>', "A\n\nB\n", 2, 0,
                 id="main-and-role-main-in-order"),
    pytest.param('<article><p>One</p></article><article><p>Two</p></article>', "One\n\nTwo\n", 2, 0,
                 id="peer-articles"),
    pytest.param('<main><p>Intro</p><article><p>One</p></article><article><p>Two</p></article>'
                 '</main>', "Intro\n\nOne\n\nTwo\n", 3, 0, id="main-with-peer-articles-renders-whole"),
    pytest.param('<main><p>Intro</p><article><p>Art</p></article><p>B</p></main>',
                 "Intro\n\nArt\n\nB\n", 3, 0, id="main-with-article-and-trailing-prose-renders-whole"),
    pytest.param('<div role="xyz main"><p>M</p></div><p>Out</p>', "M\n", 1, 0,
                 id="role-main-after-unknown-token"),
    pytest.param('<nav><main><p>InNav</p></main></nav><p>Out</p>', "InNav\n", 1, 0,
                 id="main-inside-landmark-is-selected"),
    pytest.param('<aside><main><p>Side</p></main></aside><p>Body</p>', "Side\n", 1, 0,
                 id="main-inside-aside-is-selected"),
    pytest.param('<article><p>One</p></article><article></article>', "One\n", 1, 0,
                 id="empty-peer-article"),
    pytest.param('<article hidden><p>H</p></article><article><p>V</p></article>', "V\n", 1, 0,
                 id="hidden-peer-article-skipped"),
    pytest.param('<form><article><p>F</p></article></form><article><p>V</p></article>',
                 "F\n\nV\n", 2, 0, id="form-wrapped-article-kept"),
    pytest.param('<article><table><tr><td>A</td></tr></table></article><table><tr><td>Out</td>'
                 '</tr></table><article><table><tr><td>B</td></tr></table></article>',
                 "| A |\n| --- |\n\n| B |\n| --- |\n", 0, 2, id="tables-only-inside-selected-roots"),
    pytest.param('<nav role="main"><p>N</p></nav><p>Out</p>', "N\n", 1, 0,
                 id="explicit-main-role-wins-over-nav-tag"),
    pytest.param('<article><p>Out</p></article><article role="main"><p>In</p></article>', "In\n", 1, 0,
                 id="article-with-main-role-is-a-main"),
    pytest.param('<main><p>A</p><main><p>B</p></main><p>C</p></main><p>Out</p>', "A\n\nB\n\nC\n", 3, 0,
                 id="nested-main-renders-once-inside-outer"),
    pytest.param('<article><p>A</p></article><article><p hidden>H</p><p>&nbsp;</p><table></table>'
                 '</article><article><p>B</p></article>', "A\n\nB\n", 2, 0,
                 id="empty-middle-root-adds-no-blocks-or-counts"),
])
def test_article_selection_preserves_exact_supported_descendants(tmp_path, source, content, regions, tables):
    # GIVEN explicit document structure with independent retained-content expectations
    path = tmp_path / "sample.html"
    path.write_text(source, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == source, "selection input must retain its exact source"
    # WHEN article mode selects every visible root and renders its descendants in order
    result = rendering_result(brewdoc.run(path, article=True))
    # THEN source order, Unicode, metadata and counters match the full expected envelope
    assert result == (0, article_receipt(path, None, regions, tables=tables),
                      article_document(path, content, regions, tables=tables)), (
        "article selection must retain exactly the supported selected descendants")


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p>Unmarked prose</p>', "Unmarked prose\n", 1, 0, id="unmarked-page"),
    pytest.param('<nav><p>Menu</p></nav><p>Unmarked</p><button>Go</button><footer><p>Foot</p>'
                 '</footer>', "Unmarked\n", 1, 0, id="clean-profile-drops-landmarks-and-controls"),
    pytest.param('<p>Lead</p><table><tr><td>A</td></tr></table>', "Lead\n\n| A |\n| --- |\n", 1, 1,
                 id="whole-body-tables"),
    pytest.param('<article hidden><p>H</p></article><p>Out</p>', "Out\n", 1, 0,
                 id="hidden-only-article"),
    pytest.param('<main hidden><p>H</p></main><p>Out</p>', "Out\n", 1, 0, id="hidden-only-main"),
    pytest.param('<article><p hidden>H</p></article><p>Out</p>', "Out\n", 1, 0,
                 id="empty-attempt-adds-no-counts"),
    pytest.param('<main></main><p>Out</p>', "Out\n", 1, 0, id="empty-main"),
    pytest.param('<main></main><main></main>', "", 0, 0, id="two-empty-mains"),
    pytest.param('<article></article>', "", 0, 0, id="empty-selected-article"),
    pytest.param('<article><p hidden>Hidden</p><script>ignored</script></article>', "", 0, 0,
                 id="selected-article-renders-empty"),
    pytest.param('<nav><article><p>Only navigation article</p></article></nav>', "", 0, 0,
                 id="landmark-only-candidate"),
    pytest.param('<header><article><p>Head</p></article></header><p>Body</p>', "Body\n", 1, 0,
                 id="header-candidate-skipped"),
    pytest.param('<div role="navigation"><article><p>N</p></article></div><p>Body</p>', "Body\n", 1, 0,
                 id="aria-landmark-candidate-skipped"),
    pytest.param('<html hidden><body><article><p>Secret</p></article></body></html>', "", 0, 0,
                 id="hidden-html-ancestor"),
    pytest.param('<html><body hidden><article><p>Secret</p></article></body></html>', "", 0, 0,
                 id="hidden-body"),
    pytest.param('<main><blockquote> </blockquote></main><p>Out</p>', "Out\n", 1, 0,
                 id="empty-quote-root-falls-back"),
    pytest.param('<article><blockquote><p hidden>x</p></blockquote></article><p>Out</p>', "Out\n", 1, 0,
                 id="hidden-only-quote-root-falls-back"),
    pytest.param('<main><ul><li></li></ul></main><p>Out</p>', "-\n\nOut\n", 1, 0,
                 id="empty-item-root-falls-back"),
])
def test_article_fallback_renders_clean_whole_body_with_one_receipt_signal(
        tmp_path, source, content, regions, tables):
    # GIVEN a page without a visible nonempty candidate and an existing output file
    path, out = tmp_path / "sample.html", tmp_path / "replaced.md"
    path.write_text(source, encoding="utf-8")
    out.write_bytes(b"replaced output\n")
    assert out.read_bytes() == b"replaced output\n", "the fallback destination must start populated"
    # WHEN article mode finds nothing to select
    result = rendering_result(brewdoc.run(path, out, article=True), output=out)
    # THEN the default clean document is published; the receipt adds only article_fallback
    expected = expected_document(path, content, regions, tables=tables)
    assert (result, rendering_bytes(out.read_bytes())) == (
        (0, fallback_receipt(path, out, regions, tables=tables), expected), expected.encode("utf-8")
    ), "article fallback must equal the default run except for the article_fallback receipt key"


@supported_html
def test_render_html_article_fallback_exposes_the_tally_signal(tmp_path):
    # GIVEN an unmarked page
    path = tmp_path / "sample.html"
    path.write_text('<p>Unmarked</p>', encoding="utf-8")
    assert path.is_file() is True, "the unmarked source must exist"
    # WHEN the public renderer falls back in article mode
    rendered = brewdoc.render_html(path, article=True)
    # THEN Markdown is the default document and the tally carries the fallback key
    assert rendered == (expected_document(path, "Unmarked\n", 1),
                        dict(zero_tally(), chapters=1, text_regions=1, article_fallback=True)), (
        "render_html must expose the same fallback signal that run reports")


@supported_html
@pytest.mark.parametrize("html_attribute", ['hidden', 'aria-hidden="true"', 'style="display:none"'])
def test_default_mode_renders_nothing_under_a_hidden_body_ancestor(tmp_path, html_attribute):
    # GIVEN visible body prose under a hidden html element
    path = tmp_path / "sample.html"
    path.write_text('<html %s><body><p>Secret</p></body></html>' % html_attribute, encoding="utf-8")
    assert path.is_file() is True, "the hidden-ancestor source must exist"
    # WHEN default mode converts the page
    result = rendering_result(brewdoc.run(path))
    # THEN no body content is carried and the receipt has no fallback key
    assert result == (0, dict(expected_receipt(path, None, 0), out=None),
                      expected_document(path, "", 0)), (
        "a hidden ancestor of body must hide the whole body in default mode")


@supported_html
def test_default_local_mode_preserves_complete_markdown_and_receipt(tmp_path):
    # GIVEN a page with article candidates and unrelated visible content
    path = tmp_path / "sample.html"
    path.write_text('<p>Before</p><article><p>First</p></article><article><p>Second</p>'
                    '</article><p>After</p>', encoding="utf-8")
    expected = (0, dict(expected_receipt(path, None, 4), out=None),
                expected_document(path, "Before\n\nFirst\n\nSecond\n\nAfter\n", 4))
    assert path.is_file() is True, "the complete-page source must exist"
    # WHEN the public API is called positionally and with explicit article=False
    results = (rendering_result(brewdoc.run(path)), rendering_result(brewdoc.run(path, article=False)))
    # THEN default mode keeps every original byte and its complete unchanged receipt
    assert results == (expected, expected), "article mode must remain opt-in for local conversion"


@supported_html
def test_public_article_arguments_are_keyword_only_and_render_html_agrees_with_run(tmp_path):
    # GIVEN the unchanged public exports and a single article
    path = tmp_path / "sample.html"
    path.write_text('<article><p>Kept</p></article><p>Outside</p>', encoding="utf-8")
    assert (brewdoc.run, brewdoc.render_html) == (service.run, htmltext.render_html), (
        "public exports must retain their original identities")
    # WHEN both public APIs render the article with the new keyword
    rendered = brewdoc.render_html(path, article=True)
    result = rendering_result(brewdoc.run(path, article=True))
    # THEN return shapes, complete tally and keyword-only default are preserved
    tally = dict(zero_tally(), chapters=1, text_regions=1)
    assert (rendered, result, tuple((inspect.signature(function).parameters["article"].kind,
                                   inspect.signature(function).parameters["article"].default)
                                  for function in (brewdoc.run, brewdoc.render_html))) == (
        (article_document(path, "Kept\n", 1), tally),
        (0, article_receipt(path, None, 1), article_document(path, "Kept\n", 1)),
        ((inspect.Parameter.KEYWORD_ONLY, False), (inspect.Parameter.KEYWORD_ONLY, False)),
    ), "article API extension must preserve export, tuple and default contracts"


@supported_html
@pytest.mark.parametrize("outside,depth_limit,reason", [
    pytest.param('<table><tr><td colspan="2">Outside</td></tr></table>',
                 256, "HTML table cells limit exceeded: 1", id="outside-table-expansion"),
    pytest.param('<table><tr><td>One</td></tr></table><table><tr><td>Two</td></tr></table>',
                 256, "HTML table cells limit exceeded: 1", id="aggregate-outside-table-budget"),
    pytest.param('<section><div><p>Deep</p></div></section>',
                 3, "HTML depth limit exceeded: 3", id="outside-depth-before-selection"),
])
def test_complete_body_resource_limits_apply_before_article_selection(
        tmp_path, monkeypatch, outside, depth_limit, reason):
    # GIVEN two peer articles and resource overflow elsewhere in the complete body
    path, out = tmp_path / "sample.html", tmp_path / "preserved.md"
    path.write_text('<article><p>One</p></article><article><p>Two</p></article>' + outside,
                    encoding="utf-8")
    out.write_bytes(b"preserved")
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 1)
    monkeypatch.setattr(htmltext, "_DEPTH_LIMIT", depth_limit)
    assert out.read_bytes() == b"preserved", "resource refusal output must start intact"
    # WHEN article extraction validates the complete source before selecting roots
    result = rendering_result(brewdoc.run(path, out, article=True), output=out)
    # THEN complete-tree and complete-body budgets take priority over article selection
    assert (result, out.read_bytes()) == ((1, refused_receipt(path, reason), ""), b"preserved"), (
        "selection must never bypass global table or tree resource guards")


@supported_html
@pytest.mark.parametrize("arguments", [["--article", "--self-check"], ["--self-check", "--article"]])
def test_article_self_check_combination_returns_usage_without_running_check(monkeypatch, capsys, arguments):
    # GIVEN article mode with self-check and an observable self-check boundary
    calls = []
    monkeypatch.setattr(cli, "self_check", lambda: calls.append("checked"))
    assert calls == [], "self-check must start idle"
    # WHEN argparse receives the incompatible options
    with pytest.raises(SystemExit) as refusal:
        cli.main(arguments)
    # THEN usage exits two before self-check or receipt output
    assert (refusal.value.code, calls, capsys.readouterr().out) == (2, [], ""), (
        "article and self-check must be refused as a usage error")


SELECTED, FALLBACK = (article_document, article_receipt), (expected_document, fallback_receipt)
UNSELECTABLE_OR_PEER_PAGES = [
    pytest.param('<p>Unmarked prose</p>', "Unmarked prose\n", 1, FALLBACK, id="no-candidate-fallback"),
    pytest.param('<main></main><main></main>', "", 0, FALLBACK, id="empty-mains-fallback"),
    pytest.param('<article><p>One</p></article><article></article>', "One\n", 1, SELECTED,
                 id="peer-articles-selected"),
    pytest.param('<article></article>', "", 0, FALLBACK, id="empty-selected-fallback"),
]


@supported_html
@pytest.mark.parametrize("source,content,regions,builders", UNSELECTABLE_OR_PEER_PAGES)
def test_local_article_cli_publishes_selected_or_fallback_output_without_refusing(
        tmp_path, capsys, source, content, regions, builders):
    # GIVEN HTML without a nonempty candidate, or with peer articles, and an existing destination
    path, out = tmp_path / "sample.html", tmp_path / "replaced.md"
    path.write_text(source, encoding="utf-8")
    out.write_bytes(b"replaced")
    assert out.read_bytes() == b"replaced", "CLI destination must start populated"
    # WHEN local CLI conversion requests article extraction
    code = cli.main([str(path), "--article", "--out", str(out)])
    captured = capsys.readouterr()
    complete = brewdoc.run(path, article=True)[2]
    assert out.read_bytes() == complete.encode("utf-8"), "CLI files must retain all inspected article findings"
    # THEN stdout is one success receipt, the fallback key appears only on fallback
    document, receipt = builders
    assert (code, rendering_stdout(captured.out), captured.err, rendering_bytes(out.read_bytes())) == (
        0, json.dumps(receipt(path, out, regions), ensure_ascii=True, sort_keys=True) + "\n", "",
        document(path, content, regions).encode("utf-8"),
    ), "article CLI must publish selected or fallback output instead of refusing"


@supported_html
def test_non_html_article_api_and_cli_return_normal_refusal_before_output(tmp_path, capsys):
    # GIVEN a readable synthetic DOCX and existing destination
    path, out = tmp_path / "sample.docx", tmp_path / "preserved.md"
    docx(path, '<w:p><w:r><w:t>Ordinary document</w:t></w:r></w:p>')
    out.write_bytes(b"preserved")
    assert rendering_result(brewdoc.run(path))[0] == 0, "the non-HTML source must otherwise convert successfully"
    expected = dict(refused_receipt(path, "--article is an HTML-only option"), route="doc",
                    not_carried=["images, charts and the text drawn inside them",
                                 "tracked changes, comments, footnotes, headers and footers",
                                 "a table's own formatting - only its cells, row by row"])
    # WHEN public API and CLI request article mode for a non-HTML document
    result = rendering_result(brewdoc.run(path, out, article=True), output=out)
    code = cli.main([str(path), "--article", "--out", str(out)])
    captured = capsys.readouterr()
    # THEN both return the complete ordinary refusal and preserve the destination
    assert (result, code, rendering_stdout(captured.out), captured.err, out.read_bytes()) == (
        (1, expected, ""), 1, json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n",
        "", b"preserved",
    ), "non-HTML article requests must fail with a normal receipt before output publication"


@supported_html
def test_local_article_cli_stdout_contains_exact_receipt_then_unicode_markdown(tmp_path, capsys):
    # GIVEN one article and an unrelated body paragraph
    path = tmp_path / "sample.html"
    path.write_text('<article><p>café 東京</p></article><p>Outside</p>', encoding="utf-8")
    assert path.is_file() is True, "the article source must exist"
    # WHEN CLI article conversion uses stdout output
    code = cli.main([str(path), "--article"])
    captured = capsys.readouterr()
    # THEN receipt framing precedes the exact selected UTF-8 Markdown
    assert (code, rendering_stdout(captured.out), captured.err) == (
        0, json.dumps(article_receipt(path, None, 1), ensure_ascii=True, sort_keys=True) + "\n"
        + article_document(path, "café 東京\n", 1), "",
    ), "article CLI stdout must retain one JSON receipt followed by exact Unicode Markdown"


@supported_html
def test_article_mode_preserves_document_title_without_promoting_visible_metadata(tmp_path):
    # GIVEN a document title differing from article heading and visible byline
    path = tmp_path / "sample.html"
    path.write_text('<title>Page title</title><p>Outside</p><article><h1>Article heading</h1>'
                    '<p>By Maksim</p></article>', encoding="utf-8")
    assert path.is_file() is True, "the titled source must exist"
    # WHEN article mode selects the explicit subtree
    result = rendering_result(brewdoc.run(path, article=True))
    # THEN the existing title remains the chapter title and byline remains ordinary content
    assert result == (0, article_receipt(path, None, 2),
                      article_document(path, "### Article heading\n\nBy Maksim\n", 2, title="Page title")), (
        "article extraction must preserve document-title behavior without inventing metadata")


@supported_html
def test_article_cli_invalid_artifact_retains_article_omission_in_normal_refusal(tmp_path, capsys):
    # GIVEN an article source, malformed artifact assignment and intact destination
    path, out = tmp_path / "sample.html", tmp_path / "preserved.md"
    path.write_text('<article><p>Kept</p></article>', encoding="utf-8")
    out.write_bytes(b"preserved")
    assert out.read_bytes() == b"preserved", "malformed-option refusal must start with intact output"
    # WHEN the CLI refuses malformed artifact syntax before conversion
    code = cli.main([str(path), "--article", "--artifact", "malformed", "--out", str(out)])
    captured = capsys.readouterr()
    # THEN the complete article refusal envelope includes its required omission
    assert (code, rendering_stdout(captured.out), captured.err, out.read_bytes()) == (
        1, json.dumps(refused_receipt(path, "malformed artifact assignment: malformed"),
                      ensure_ascii=True, sort_keys=True) + "\n", "", b"preserved",
    ), "early CLI refusals in article mode must retain its omission and normal receipt schema"


URL = "https://example.com/article"
UNIX_RESOURCES = ["stylesheets", "images", "subframes", "media", "service_workers", "websockets",
                  "web_rtc", "navigation", "layout", "canvas", "shadow_roots", "browser_state",
                  "parser_blocking_order", "async_script_order"]
STATIC_RESOURCES = ["scripts", "stylesheets", "images", "subframes", "media", "browser_state"]


def frozen_url(monkeypatch, source, render_js):
    """Replace acquisition I/O and JS execution with fixed source and captured DOM."""
    payloads = {False: source.encode("utf-8"), True: b"<p>Original pre-JS source</p>"}
    body = payloads[render_js]
    calls = []

    def fetch(url, *, budget, **options):
        """Record one successful guarded acquisition at the external I/O seam."""
        calls.append(url)
        budget["accepted_requests"] += 1
        return {"status": 200, "redirects": (), "final_url": url, "body": body, "entity": body,
                "headers": (("Content-Type", "text/html"),),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "entity_sha256": hashlib.sha256(body).hexdigest()}

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)
    monkeypatch.setattr(htmljs, "render", lambda *args, **options: {
        "html": source, "capture_status": "partial", "errors": [{"category": "script", "resource_url": None}],
        "error_count": 1,
        "pending": {"requests": 0, "timers": 0, "modules": 0, "promises": 0, "jobs": False},
        "counts": {"promise_jobs": 0, "timer_callbacks": 0},
    })
    return body, calls


def url_acquisition(body, snapshot, render_js):
    """Describe independent frozen source hashes and static or partial JS provenance."""
    mode = {
        False: {"mode": "static", "engine": None, "capture_status": "static", "errors": [],
                "error_count": 0, "pending": dict.fromkeys(
                    ("requests", "timers", "modules", "promises", "jobs")),
                "unsupported_resources": STATIC_RESOURCES, "soft_window_seconds": None,
                "soft_window_origin": None},
        True: {"mode": "javascript", "engine": {"name": "quickjs-ng", "version": "0.17.0.1",
                "dom_name": "linkedom", "dom_version": "0.18.13"}, "capture_status": "partial",
               "errors": [{"category": "script", "resource_url": None}], "error_count": 1,
               "pending": {"requests": 0, "timers": 0, "modules": 0, "promises": 0, "jobs": False},
               "unsupported_resources": UNIX_RESOURCES, "soft_window_seconds": 12.0,
               "soft_window_origin": "request"},
    }
    return dict(mode[render_js], requested_url=URL, final_url=URL, effective_base_url=URL,
                url_sha256=hashlib.sha256(URL.encode()).hexdigest(),
                response={"entity_bytes": len(body), "entity_sha256": hashlib.sha256(body).hexdigest(),
                          "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest()},
                snapshot={"kind": {False: "static_html", True: "javascript_dom"}[render_js],
                          "bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest()},
                counts={"requests": 1, "promise_jobs": 0, "timer_callbacks": 0},
                refusal_stage=None, timeout_seconds=30.0, http_status=200, redirect_count=0,
                challenge=None, charset="UTF-8", charset_source="default",
                render_js={False: None, True: "always"}[render_js])


@supported_html
@pytest.mark.parametrize("render_js", [False, True], ids=["static", "frozen-partial-js"])
@pytest.mark.parametrize("article", [False, True], ids=["default-full-page", "selected-article"])
def test_url_article_and_default_modes_preserve_complete_frozen_receipts_and_hashes(
        tmp_path, monkeypatch, render_js, article):
    # GIVEN frozen HTML distinct from the JS response source and unrelated visible content
    source = '<article><p>Kept</p></article><p>Outside</p>'
    snapshot = ('<html><head></head><body>' + source + '</body></html>').encode("utf-8")
    path = tmp_path / "sample.html"
    path.write_bytes(snapshot)
    body, calls = frozen_url(monkeypatch, source, render_js)
    options = {False: {"content": "Kept\n\nOutside\n", "regions": 2},
               True: {"content": "Kept\n", "regions": 1}}[article]
    document = {False: expected_document, True: article_document}[article](path, **options)
    name = "url-%s.html" % hashlib.sha256(URL.encode()).hexdigest()[:16]
    document = document.replace("sample.html", name)
    expected = {False: expected_receipt, True: article_receipt}[article](path, None, options["regions"])
    expected.update(source=URL, out=None, receipt_schema="brewdoc.receipt/2",
                    acquisition=url_acquisition(body, snapshot, render_js))
    assert calls == [], "the URL boundary must start without requests"
    # WHEN existing URL orchestration converts the final frozen snapshot
    result = rendering_result(htmlurl._run_url(URL, render_js=render_js, article=article))
    # THEN default bytes, article scope and complete source/snapshot/partial evidence are exact
    assert (result, calls) == ((0, expected, document), [URL]), (
        "article selection must act only on the frozen snapshot without changing URL provenance")


@supported_html
@pytest.mark.parametrize("render_js", [False, True], ids=["static", "frozen-js"])
@pytest.mark.parametrize("source,content,regions,builders", UNSELECTABLE_OR_PEER_PAGES)
def test_url_article_cli_publishes_selected_or_fallback_output_with_complete_acquisition(
        tmp_path, monkeypatch, capsys, render_js, source, content, regions, builders):
    # GIVEN a frozen page without a nonempty candidate, or with peer articles, and an existing destination
    out = tmp_path / "replaced.md"
    out.write_bytes(b"replaced")
    snapshot = ('<html><head></head><body>' + source + '</body></html>').encode("utf-8")
    body, calls = frozen_url(monkeypatch, source, render_js)
    path = tmp_path / "sample.html"
    path.write_bytes(snapshot)
    name = "url-%s.html" % hashlib.sha256(URL.encode()).hexdigest()[:16]
    document, receipt = builders
    document = document(path, content, regions).replace("sample.html", name)
    expected = dict(receipt(path, out, regions), source=URL,
                    receipt_schema="brewdoc.receipt/2", acquisition=url_acquisition(body, snapshot, render_js))
    arguments = ["--url", URL, "--article", "--out", str(out)] + ["--render-js"] * render_js
    converted = []
    local_run = htmlurl.run

    def observe(*args, **options):
        """Capture full local conversion without replacing its behavior or writer."""
        result = local_run(*args, **options)
        converted.append(result[2])
        return result

    monkeypatch.setattr(htmlurl, "run", observe)
    assert (calls, out.read_bytes()) == ([], b"replaced"), "URL conversion must start without requests"
    # WHEN static or frozen JS CLI conversion requests article selection
    code = cli.main(arguments)
    captured = capsys.readouterr()
    assert len(converted) == 1, "URL acquisition must invoke exactly one real local conversion"
    assert out.read_bytes() == converted[0].encode("utf-8"), "URL files must preserve the full inspected snapshot Markdown"
    # THEN the success receipt keeps URL provenance and the snapshot document is published
    assert (code, rendering_stdout(captured.out), captured.err, calls, rendering_bytes(out.read_bytes())) == (
        0, json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n", "", [URL],
        document.encode("utf-8"),
    ), "URL article mode must publish selected or fallback output with exact acquisition evidence"
