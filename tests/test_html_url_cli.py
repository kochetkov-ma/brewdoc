"""Explicit URL CLI routing preserves receipt and UTF-8 output contracts."""

import hashlib
import json

import pytest

from brewdoc import cli, htmlurl
from brewdoc.service import ROUTES
from test_html import HTML_OMISSIONS, LINKS_OMISSION, disclosed_document, expected_receipt, supported_html
from test_html_article import frozen_url, url_acquisition
from test_html_url import (MALFORMED_DISPOSITION, OCTET, SECRET_SHOWN, SECRET_URL, converted,
                           document_acquisition, ok, pdf_bytes, refused, serve, sha256)


URL = "https://example.com/article"
RECEIPT = {"receipt_schema": "brewdoc.receipt/2", "route": "html", "file_ok": True,
           "acquisition": {"mode": "static", "capture_status": "static"}}
MARKDOWN = "# Article\n\nПривет 世界\n"


@pytest.mark.parametrize("arguments", [
    ["page.html", "--url", URL],
    ["--render-js"],
    ["page.html", "--render-js"],
    ["--self-check", "--url", URL],
    ["--self-check", "--render-js"],
    ["--render-js", "auto"],
])
def test_invalid_url_mode_combinations_refuse_before_acquisition(arguments, monkeypatch, capsys):
    # GIVEN conflicting URL modes and an observable acquisition boundary
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **kwargs: calls.append(args), raising=False)
    assert calls == [], "acquisition has not started"
    # WHEN parsing a conflicting request
    with pytest.raises(SystemExit) as refused:
        cli.main(arguments)
    # THEN argparse returns usage two without an acquisition receipt or request
    assert (refused.value.code, calls, capsys.readouterr().out) == (2, [], ""), "invalid modes must not acquire"


def test_unknown_render_js_mode_is_an_argparse_error_before_acquisition(monkeypatch, capsys):
    # GIVEN an unsupported --render-js value and an observable acquisition boundary
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **kwargs: calls.append(args), raising=False)
    assert calls == [], "acquisition has not started"
    # WHEN parsing the request
    with pytest.raises(SystemExit) as refused:
        cli.main(["--url", URL, "--render-js", "never"])
    captured = capsys.readouterr()
    # THEN argparse names the allowed modes; quotes vary across 3.12 patch releases
    assert (refused.value.code, calls, captured.out, captured.err.splitlines()[-1].replace("'", "")) == (
        2, [], "", "brewdoc: error: argument --render-js: invalid choice: never (choose from auto, always)",
    ), "an unknown JS mode must be a usage error without acquisition"


@pytest.mark.parametrize("links", [True, False], ids=["links", "no-links"])
@pytest.mark.parametrize("flags,render_js", [
    ([], None), (["--render-js"], "always"), (["--render-js", "auto"], "auto"), (["--render-js", "always"], "always"),
], ids=["static", "bare-flag", "auto", "always"])
def test_url_cli_forwards_mode_and_prints_exact_receipt_then_unicode(flags, render_js, links, monkeypatch, capsys):
    # GIVEN an acquisition result with UTF-8 Markdown and a versioned receipt
    calls = []

    def acquire(url, out=None, **kwargs):
        """Return a URL boundary result while recording the CLI mode contract."""
        calls.append((url, out, kwargs))
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire, raising=False)
    assert calls == [], "the optional boundary must remain lazy until the request"
    # WHEN requesting explicit static or JS acquisition
    arguments = ["--url", URL, *flags] + ["--no-links"] * (not links)
    code = cli.main(arguments)
    # THEN mode is forwarded once and stdout preserves the exact result
    assert (code, calls, capsys.readouterr().out) == (
        0, [(URL, None, {"render_js": render_js, "timeout": 30.0, "sheets": None,
                        "artifact_outputs": None, "article": False,
                        "body_only": False, "links": links})],
        json.dumps(RECEIPT, ensure_ascii=True, sort_keys=True) + "\n" + MARKDOWN,
    ), "URL CLI must preserve Unicode and the acquisition receipt"


def test_url_out_prints_only_receipt_and_preserves_transaction_path(tmp_path, monkeypatch, capsys):
    # GIVEN a URL result and explicit output destination
    destination = tmp_path / "article.md"
    calls = []

    def acquire(url, out=None, **kwargs):
        """Publish the boundary's UTF-8 bytes to the requested destination."""
        calls.append((url, out))
        destination.write_bytes(MARKDOWN.encode("utf-8"))
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire, raising=False)
    assert not destination.exists(), "output starts absent"
    # WHEN the CLI requests file output
    code = cli.main(["--url", URL, "--out", str(destination)])
    # THEN stdout contains one receipt and the orchestrator owns the exact bytes
    assert (code, calls, capsys.readouterr().out, destination.read_bytes()) == (
        0, [(URL, str(destination))], json.dumps(RECEIPT, sort_keys=True) + "\n", MARKDOWN.encode("utf-8"),
    ), "URL output must retain the receipt-only stdout contract"


@pytest.mark.parametrize("option,value", [("--sheet", "Calc"), ("--artifact", "malformed")])
def test_url_workbook_options_refuse_without_http_or_output(option, value, tmp_path, monkeypatch, capsys):
    # GIVEN workbook-only options, a sentinel destination and blocked HTTP seam
    destination = tmp_path / "existing.md"
    destination.write_bytes(b"unchanged")
    calls = []
    monkeypatch.setattr(htmlurl, "_request", lambda *args: calls.append(args))
    assert destination.read_bytes() == b"unchanged", "refusal must preserve existing output"
    # WHEN requesting a workbook option with URL acquisition
    code = cli.main(["--url", URL, option, value, "--out", str(destination)])
    receipt = json.loads(capsys.readouterr().out)
    # THEN acquisition refuses as HTML before HTTP and output writes
    assert (code, calls, destination.read_bytes(), receipt["receipt_schema"],
            receipt["route"], receipt["units"], receipt["reason"]) == (
        1, [], b"unchanged", "brewdoc.receipt/2", "html", 0,
        "--sheet and --artifact are workbook-only options",
    ), "workbook URL requests must fail transactionally before network"


def test_url_hard_refusal_prints_receipt_without_markdown_or_fallback(monkeypatch, capsys):
    # GIVEN a refused requested JS mode and an observable local conversion boundary
    receipt = dict(RECEIPT, file_ok=False, reason="JS runtime unavailable")
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **kwargs: (1, receipt, ""), raising=False)
    monkeypatch.setattr(cli, "run", lambda *args, **kwargs: calls.append(args))
    assert calls == [], "no local/static fallback has started"
    # WHEN the CLI requests JS acquisition
    code = cli.main(["--url", URL, "--render-js"])
    # THEN only the exact refusal receipt is printed
    assert (code, calls, capsys.readouterr().out) == (
        1, [], json.dumps(receipt, ensure_ascii=True, sort_keys=True) + "\n",
    ), "requested JS must not silently retry static/local conversion"


def test_url_cli_writes_a_pdf_document_with_its_pdf_receipt(tmp_path, monkeypatch, capsys):
    # GIVEN a PDF served at a URL and its local conversion under the expected snapshot name
    url = "https://example.com/files/report"
    body = pdf_bytes(tmp_path)
    destination = tmp_path / "report.md"
    destination.write_bytes(b"previous output\n")
    name, (local_code, receipt, markdown) = converted(tmp_path, url, ".pdf", body)
    monkeypatch.setattr(htmlurl, "_request", lambda *args: ok((("Content-Type", "application/pdf"),), body))
    assert local_code == 0, "the synthetic PDF must convert as a local file"
    # WHEN the CLI acquires the URL into an output file
    code = cli.main(["--url", url, "--out", str(destination)])
    # THEN stdout is the PDF receipt and the file holds the PDF Markdown
    assert (code, json.loads(capsys.readouterr().out), destination.read_bytes()) == (
        0, dict(receipt, out=str(destination), source=url, receipt_schema="brewdoc.receipt/2",
                acquisition=document_acquisition(url, url, body, body)), markdown.encode("utf-8"),
    ), "--url must convert a PDF document through the PDF route"


def test_url_cli_malformed_disposition_prints_a_media_refusal_instead_of_crashing(monkeypatch, capsys):
    # GIVEN an octet-stream response whose Content-Disposition parameters are malformed
    calls = []
    hop = ok((OCTET, ("Content-Disposition", MALFORMED_DISPOSITION)), b"%PDF-")
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args: (calls.append(url), hop)[1])
    assert calls == [], "acquisition starts without a request"
    # WHEN the CLI acquires the URL
    code = cli.main(["--url", SECRET_URL])
    # THEN exit 1 prints the exact media refusal receipt with the redacted URL
    assert (code, calls, capsys.readouterr().out) == (
        1, [SECRET_URL], json.dumps(refused(ROUTES[".html"], SECRET_SHOWN, SECRET_URL, "media", SECRET_SHOWN,
                                            http_status=200),
                                    ensure_ascii=True, sort_keys=True) + "\n",
    ), "a malformed Content-Disposition must refuse at media, not crash the CLI"


LINKED_PAGE = ('<p>See <a href="https://example.com/docs">docs</a></p>'
               '<a href="https://example.com/home"><img alt="Home" src="https://example.com/h.png"></a>')


@supported_html
@pytest.mark.parametrize("render_js", [False, True], ids=["static", "frozen-js"])
@pytest.mark.parametrize("arguments,content,omissions", [
    pytest.param([], "See [docs](https://example.com/docs)\n\n"
                     "[![Home](https://example.com/h.png)](https://example.com/home)\n", [], id="standard-links"),
    pytest.param(["--no-links"], "See docs\n\n![Home](https://example.com/h.png)\n", [LINKS_OMISSION],
                 id="no-links"),
])
def test_url_cli_renders_standard_links_or_omits_destinations_with_exact_receipt(
        tmp_path, monkeypatch, capsys, render_js, arguments, content, omissions):
    # GIVEN a frozen linked page, its expected snapshot and an existing destination
    out = tmp_path / "replaced.md"
    out.write_bytes(b"replaced")
    snapshot = ("<html><head></head><body>" + LINKED_PAGE + "</body></html>").encode("utf-8")
    body, calls = frozen_url(monkeypatch, LINKED_PAGE, render_js)
    path = tmp_path / "sample.html"
    path.write_bytes(snapshot)
    name = "url-%s.html" % hashlib.sha256(URL.encode()).hexdigest()[:16]
    document = disclosed_document(path, content, 2, omissions).replace("sample.html", name)
    expected = dict(expected_receipt(path, out, 2), source=URL, not_carried=[*HTML_OMISSIONS, *omissions],
                    receipt_schema="brewdoc.receipt/2", acquisition=url_acquisition(body, snapshot, render_js))
    assert (calls, out.read_bytes()) == ([], b"replaced"), "URL conversion must start without requests"
    # WHEN static or frozen JS CLI conversion runs with or without --no-links
    code = cli.main(["--url", URL, "--out", str(out), *arguments] + ["--render-js"] * render_js)
    captured = capsys.readouterr()
    # THEN links are standard Markdown, or plain text with a disclosed omission, over the unchanged snapshot
    assert (code, captured.out, captured.err, calls, out.read_bytes()) == (
        0, json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n", "", [URL], document.encode("utf-8"),
    ), "URL HTML must carry standard links, or omit only destinations with --no-links"


def test_url_document_with_no_links_refuses_at_options_after_classification(tmp_path, monkeypatch, capsys):
    # GIVEN a PDF response requested with --no-links
    url = "https://example.com/files/report"
    body = pdf_bytes(tmp_path)
    calls = serve(htmlurl, monkeypatch, {url: ok((("Content-Type", "application/pdf"),), body)})
    response = {"entity_bytes": len(body), "entity_sha256": sha256(body),
                "body_bytes": len(body), "body_sha256": sha256(body)}
    expected = dict(refused(ROUTES[".pdf"], url, url, "options", None, final_url=url, response=response,
                            http_status=200), reason="--no-links is an HTML-only option")
    assert calls == [], "acquisition starts without a request"
    # WHEN the CLI acquires the document URL
    code = cli.main(["--url", url, "--no-links"])
    # THEN the detected PDF route refuses at options with the HTML-only message
    assert (code, calls, capsys.readouterr().out) == (
        1, [url], json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n",
    ), "--no-links with a document URL must refuse at options with the detected route"


def test_url_html_refusal_with_no_links_keeps_the_links_omission(monkeypatch, capsys):
    # GIVEN an octet-stream response refused at media and a --no-links request
    calls = []
    hop = ok((OCTET, ("Content-Disposition", MALFORMED_DISPOSITION)), b"%PDF-")
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args: (calls.append(url), hop)[1])
    expected = dict(refused(ROUTES[".html"], SECRET_SHOWN, SECRET_URL, "media", SECRET_SHOWN, http_status=200),
                    not_carried=[*HTML_OMISSIONS, LINKS_OMISSION])
    assert calls == [], "acquisition starts without a request"
    # WHEN the CLI acquires the URL without link destinations
    code = cli.main(["--url", SECRET_URL, "--no-links"])
    # THEN the HTML refusal receipt still discloses the requested omission
    assert (code, calls, capsys.readouterr().out) == (
        1, [SECRET_URL], json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n",
    ), "URL HTML refusals must keep the --no-links omission like --article keeps its own"
