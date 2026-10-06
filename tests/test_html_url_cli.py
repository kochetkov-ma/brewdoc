"""Explicit URL CLI routing preserves receipt and UTF-8 output contracts."""

import json

import pytest

from brewdoc import cli, htmlurl


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


@pytest.mark.parametrize("render_js", [False, True])
def test_url_cli_forwards_mode_and_prints_exact_receipt_then_unicode(render_js, monkeypatch, capsys):
    # GIVEN an acquisition result with UTF-8 Markdown and a versioned receipt
    calls = []

    def acquire(url, out=None, **kwargs):
        """Return a URL boundary result while recording the CLI mode contract."""
        calls.append((url, out, kwargs))
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire, raising=False)
    assert calls == [], "the optional boundary must remain lazy until the request"
    # WHEN requesting explicit static or JS acquisition
    arguments = ["--url", URL] + ["--render-js"] * render_js
    code = cli.main(arguments)
    # THEN mode is forwarded once and stdout preserves the exact result
    assert (code, calls, capsys.readouterr().out) == (
        0, [(URL, None, {"render_js": render_js, "timeout": 10.0, "sheets": None, "artifact_outputs": None})],
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
