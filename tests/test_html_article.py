"""Article mode selects one explicit subtree and preserves conversion boundaries."""

import hashlib
import inspect
import json

import pytest

import brewdoc
from brewdoc import cli, htmljs, htmltext, htmlurl, service
from test_contract import docx, zero_tally
from test_html import HTML_OMISSIONS, expected_document, expected_receipt, supported_html


ARTICLE_OMISSION = (
    "body content outside the selected article or main subtree; complete page coverage is not claimed"
)
NO_ROOT = "HTML article extraction refused: no eligible article or suitable main"
MAINS = "HTML article extraction refused: ambiguous mains"
ARTICLES = "HTML article extraction refused: ambiguous articles"
EMPTY = "HTML article extraction refused: empty selected content"


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


@supported_html
def test_single_article_keeps_unicode_and_excludes_surrounding_body(tmp_path):
    # GIVEN one explicit article between unrelated visible page paragraphs
    path, out = tmp_path / "sample.html", tmp_path / "article.md"
    path.write_text('<p>Outside before</p><article><p>café 東京</p></article><p>Outside after</p>',
                    encoding="utf-8")
    assert out.exists() is False, "article output must start absent"
    # WHEN the public service selects article mode
    result = brewdoc.run(path, out, article=True)
    # THEN the exact document and complete receipt describe only selected content
    expected = article_document(path, "café 東京\n", 1)
    assert (result, out.read_bytes()) == (
        (0, article_receipt(path, out, 1), expected), expected.encode("utf-8")
    ), "article mode must preserve Unicode, exclude outer paragraphs and disclose its scope"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<main><h1>Title</h1><p>Prose</p></main><p>Outside</p>',
                 "### Title\n\nProse\n", 2, 0, id="suitable-main"),
    pytest.param('<main><p>Outside</p><section><custom-story><article><p>Kept</p>'
                 '</article></custom-story></section></main>', "Kept\n", 1, 0,
                 id="ordinary-and-custom-wrappers"),
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
                 '</article></form><article><p>Visible</p></article>', "Visible\n", 1, 0,
                 id="candidate-context-exclusions"),
    pytest.param('<article><img alt="café 東京" src="pic.png"></article>',
                 'Image: alt="café 東京"; title="none"; source=relative; target="pic\\.png"\n',
                 1, 0, id="image-only-article"),
    pytest.param('<article><table><caption>Values</caption><tr><th>Kind</th><th>Dose</th>'
                 '</tr><tr><td>A</td><td>2</td></tr></table></article>',
                 'Caption: Values\n\n| Kind | Dose |\n| --- | --- |\n| A | 2 |\n',
                 1, 1, id="table-only-article-with-caption"),
    pytest.param('<article><h1>Heading</h1><p>By Maksim <time>2026-10-13</time></p>'
                 '<p><a href="/facts">café 東京</a></p><nav><p>Inside navigation</p></nav>'
                 '<p>Comment body</p></article>',
                 '### Heading\n\nBy Maksim 2026\\-10\\-13\n\n[café 東京](/facts)\n\n'
                 'Inside navigation\n\nComment body\n', 5, 0, id="visible-metadata-links-and-descendants"),
    pytest.param('<main><h1>Title</h1><h1 hidden>Hidden heading</h1><p>Prose</p>'
                 '<form hidden><input></form></main>', "### Title\n\nProse\n", 2, 0,
                 id="hidden-heading-and-form-do-not-disqualify-main"),
    pytest.param('<main><nav><h1>Title</h1></nav><custom-prose>Plain prose</custom-prose></main>',
                 "### Title\n\nPlain prose\n", 2, 0, id="visible-navigation-heading-and-custom-prose"),
    pytest.param('<main><h1>Title</h1>Plain prose</main>', "### Title\n\nPlain prose\n", 2, 0,
                 id="bare-main-prose"),
    pytest.param('<div role="button"><article><p>Control label</p></article></div>'
                 '<article><p>Visible</p></article>', "Visible\n", 1, 0,
                 id="role-button-candidate-excluded"),
])
def test_article_selection_preserves_exact_supported_descendants(tmp_path, source, content, regions, tables):
    # GIVEN explicit document structure with independent retained-content expectations
    path = tmp_path / "sample.html"
    path.write_text(source, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == source, "selection input must retain its exact source"
    # WHEN article mode selects one root and renders its descendants
    result = brewdoc.run(path, article=True)
    # THEN source order, Unicode, metadata and counters match the full expected envelope
    assert result == (0, article_receipt(path, None, regions, tables=tables),
                      article_document(path, content, regions, tables=tables)), (
        "article selection must retain exactly the supported selected descendants")


REFUSALS = [
    pytest.param('<p>Unmarked prose</p>', NO_ROOT, id="unmarked-page"),
    pytest.param('<main><p>No heading</p></main>', NO_ROOT, id="main-needs-h1"),
    pytest.param('<main><h1> </h1><p>Prose</p></main>', NO_ROOT, id="empty-main-heading"),
    pytest.param('<main><h1>One</h1><h1>Two</h1><p>Prose</p></main>', NO_ROOT, id="two-main-headings"),
    pytest.param('<main><h1>Title</h1><nav><p>Only navigation</p></nav></main>', NO_ROOT,
                 id="navigation-is-not-main-prose"),
    pytest.param('<main><h1>Title</h1><table><tr><td>Only table</td></tr></table></main>',
                 NO_ROOT, id="table-is-not-main-prose"),
    pytest.param('<main><h1>Title</h1><input value="Only control"></main>', NO_ROOT,
                 id="control-is-not-main-prose"),
    pytest.param('<main><h1>Title</h1><p>Prose</p><form></form></main>', NO_ROOT,
                 id="visible-empty-form-disqualifies-main"),
    pytest.param('<main><article><p>One</p></article></main><main></main>', MAINS,
                 id="empty-peer-main-is-ambiguous"),
    pytest.param('<main><article><p>One</p></article></main><main><article><p>Two</p>'
                 '</article></main>', MAINS, id="two-mains"),
    pytest.param('<article><p>One</p></article><article><p>Two</p></article>', ARTICLES,
                 id="peer-articles"),
    pytest.param('<article><p>One</p></article><article></article>', ARTICLES,
                 id="empty-peer-article-is-ambiguous"),
    pytest.param('<article></article>', EMPTY, id="empty-selected-article"),
    pytest.param('<article><p hidden>Hidden</p><script>ignored</script></article>', EMPTY,
                 id="selected-article-renders-empty"),
    pytest.param('<nav><article><p>Only navigation article</p></article></nav>', NO_ROOT,
                 id="excluded-only-candidate"),
    pytest.param('<html hidden><body><article><p>Secret</p></article></body></html>', NO_ROOT,
                 id="hidden-html-ancestor"),
    pytest.param('<html><body hidden><article><p>Secret</p></article></body></html>', NO_ROOT,
                 id="hidden-body-ancestor"),
    pytest.param('<div role="button"><article><p>Control label</p></article></div>', NO_ROOT,
                 id="role-button-only-candidate"),
    pytest.param('<main><h1>Title</h1><div role="button">Only control label</div></main>', NO_ROOT,
                 id="role-button-is-not-main-prose"),
]


@supported_html
@pytest.mark.parametrize("source,reason", REFUSALS)
def test_article_refusals_preserve_the_destination_and_complete_failure_receipt(tmp_path, source, reason):
    # GIVEN unsuitable or ambiguous content and an existing output file
    path, out = tmp_path / "sample.html", tmp_path / "preserved.md"
    path.write_text(source, encoding="utf-8")
    out.write_bytes(b"preserved output\n")
    assert out.read_bytes() == b"preserved output\n", "the refusal sentinel must start intact"
    # WHEN article mode attempts selection without a full-page fallback
    result = brewdoc.run(path, out, article=True)
    # THEN the fixed reason, zero-unit envelope and destination are exact
    assert (result, out.read_bytes()) == (
        (1, refused_receipt(path, reason), ""), b"preserved output\n"
    ), "article refusal must preserve its exact safe reason and existing output"


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
    results = (brewdoc.run(path), brewdoc.run(path, article=False))
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
    result = brewdoc.run(path, article=True)
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
    # GIVEN two ambiguous articles and resource overflow elsewhere in the complete body
    path, out = tmp_path / "sample.html", tmp_path / "preserved.md"
    path.write_text('<article><p>One</p></article><article><p>Two</p></article>' + outside,
                    encoding="utf-8")
    out.write_bytes(b"preserved")
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 1)
    monkeypatch.setattr(htmltext, "_DEPTH_LIMIT", depth_limit)
    assert out.read_bytes() == b"preserved", "resource refusal output must start intact"
    # WHEN article extraction validates the complete source before resolving ambiguity
    result = brewdoc.run(path, out, article=True)
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


@supported_html
@pytest.mark.parametrize("source,reason", REFUSALS)
def test_local_article_cli_prints_only_safe_refusal_and_preserves_output(tmp_path, capsys, source, reason):
    # GIVEN unsuitable HTML and an existing destination
    path, out = tmp_path / "sample.html", tmp_path / "preserved.md"
    path.write_text(source, encoding="utf-8")
    out.write_bytes(b"preserved")
    assert out.read_bytes() == b"preserved", "CLI sentinel must start intact"
    # WHEN local CLI conversion requests article extraction
    code = cli.main([str(path), "--article", "--out", str(out)])
    captured = capsys.readouterr()
    # THEN stdout is exactly one complete refusal receipt with empty stderr
    assert (code, captured.out, captured.err, out.read_bytes()) == (
        1, json.dumps(refused_receipt(path, reason), ensure_ascii=True, sort_keys=True) + "\n",
        "", b"preserved",
    ), "article CLI refusals must use the normal transactional output contract"


@supported_html
def test_non_html_article_api_and_cli_return_normal_refusal_before_output(tmp_path, capsys):
    # GIVEN a readable synthetic DOCX and existing destination
    path, out = tmp_path / "sample.docx", tmp_path / "preserved.md"
    docx(path, '<w:p><w:r><w:t>Ordinary document</w:t></w:r></w:p>')
    out.write_bytes(b"preserved")
    assert brewdoc.run(path)[0] == 0, "the non-HTML source must otherwise convert successfully"
    expected = dict(refused_receipt(path, "--article is an HTML-only option"), route="doc",
                    not_carried=["images, charts and the text drawn inside them",
                                 "tracked changes, comments, footnotes, headers and footers",
                                 "a table's own formatting - only its cells, row by row"])
    # WHEN public API and CLI request article mode for a non-HTML document
    result = brewdoc.run(path, out, article=True)
    code = cli.main([str(path), "--article", "--out", str(out)])
    captured = capsys.readouterr()
    # THEN both return the complete ordinary refusal and preserve the destination
    assert (result, code, captured.out, captured.err, out.read_bytes()) == (
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
    assert (code, captured.out, captured.err) == (
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
    result = brewdoc.run(path, article=True)
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
    assert (code, captured.out, captured.err, out.read_bytes()) == (
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
        return {"final_url": url, "body": body, "entity": body, "headers": (),
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
               "unsupported_resources": UNIX_RESOURCES, "soft_window_seconds": 30.0,
               "soft_window_origin": "request"},
    }
    return dict(mode[render_js], requested_url=URL, final_url=URL, effective_base_url=URL,
                url_sha256=hashlib.sha256(URL.encode()).hexdigest(),
                response={"entity_bytes": len(body), "entity_sha256": hashlib.sha256(body).hexdigest(),
                          "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest()},
                snapshot={"kind": {False: "static_html", True: "javascript_dom"}[render_js],
                          "bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest()},
                counts={"requests": 1, "promise_jobs": 0, "timer_callbacks": 0},
                refusal_stage=None, timeout_seconds=30.0)


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
    escaped = name.replace("-", "\\-").replace(".", "\\.")
    document = document.replace("sample\\.html", escaped)
    expected = {False: expected_receipt, True: article_receipt}[article](path, None, options["regions"])
    expected.update(source=URL, out=None, receipt_schema="brewdoc.receipt/2",
                    acquisition=url_acquisition(body, snapshot, render_js))
    assert calls == [], "the URL boundary must start without requests"
    # WHEN existing URL orchestration converts the final frozen snapshot
    result = htmlurl._run_url(URL, render_js=render_js, article=article)
    # THEN default bytes, article scope and complete source/snapshot/partial evidence are exact
    assert (result, calls) == ((0, expected, document), [URL]), (
        "article selection must act only on the frozen snapshot without changing URL provenance")


@supported_html
@pytest.mark.parametrize("render_js", [False, True], ids=["static", "frozen-js"])
@pytest.mark.parametrize("source,reason", [
    pytest.param('<p>No candidate</p>', NO_ROOT, id="no-root"),
    pytest.param('<main></main><main></main>', MAINS, id="ambiguous-mains"),
    pytest.param('<article><p>One</p></article><article></article>', ARTICLES, id="ambiguous-articles"),
    pytest.param('<article></article>', EMPTY, id="empty-selected"),
])
def test_url_article_cli_retains_safe_selection_reason_and_existing_destination(
        tmp_path, monkeypatch, capsys, render_js, source, reason):
    # GIVEN a frozen unsuitable page and an existing output destination
    out = tmp_path / "preserved.md"
    out.write_bytes(b"preserved")
    snapshot = ('<html><head></head><body>' + source + '</body></html>').encode("utf-8")
    body, calls = frozen_url(monkeypatch, source, render_js)
    path = tmp_path / "sample.html"
    expected = refused_receipt(path, reason)
    acquisition = dict(url_acquisition(body, snapshot, render_js), capture_status="refused",
                       refusal_stage="conversion")
    expected.update(source=URL, receipt_schema="brewdoc.receipt/2", acquisition=acquisition)
    arguments = ["--url", URL, "--article", "--out", str(out)] + ["--render-js"] * render_js
    assert (calls, out.read_bytes()) == ([], b"preserved"), "URL refusal must start without output changes"
    # WHEN static or frozen JS CLI conversion attempts article selection
    code = cli.main(arguments)
    captured = capsys.readouterr()
    # THEN the exact safe reason survives URL sanitization and no Markdown is published
    assert (code, captured.out, captured.err, calls, out.read_bytes()) == (
        1, json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n", "", [URL], b"preserved"
    ), "URL article refusal must retain the safe reason, complete acquisition and transaction"
