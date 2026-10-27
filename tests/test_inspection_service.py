"""Default inspection and transactional artifact delivery through the public service."""

import hashlib
import importlib
import itertools
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import replace

import pytest

from brewdoc import cli, inspection, service
from brewdoc.common import BrewdocError
from brewdoc.selfcheck import synthetic_pptx
from brewdoc.selfcheck import _synthetic_xlsx
from test_contract import docx
from test_epub import epub2_members, write_epub, xhtml
from test_extraction_regressions import _image_button_pdf
from test_inspect_pdf import _pdf


@pytest.mark.parametrize("prefix,source,expected", [
    ("readable\r\n", "Source inspection findings. Omission notices describe the readable projection.\n\n## Links\n\n- target\n", "readable\r\nSource inspection findings. Omission notices describe the readable projection.\n\n## Links\n\n- target\n"),
    ("", "Source inspection findings. Omission notices describe the readable projection.\n\n## Links\n", "Source inspection findings. Omission notices describe the readable projection.\n\n## Links\n"),
    ("", "```text\nSource inspection findings. Omission notices describe the readable projection.\n\n## Links\n```\n", "```text\nSource inspection findings. Omission notices describe the readable projection.\n\n## Links\n```\n"),
    ("", "Source inspection findings. Omission notices describe the readable projection.\n\n## Unknown\n", "Source inspection findings. Omission notices describe the readable projection.\n\n## Unknown\n"),
])
def test_readable_projection_preserves_exact_prefix_and_literal_or_unknown_content(prefix, source, expected):
    # GIVEN a generated boundary, a literal fence, or an unrecognized source heading
    markdown = prefix + source
    assert markdown.endswith(source), "the complete source tail must exist before boundary inspection"
    # WHEN production probes isolate their independently sealed readable prefix
    actual = inspection.readable_projection(markdown)
    # THEN only a known generated, unfenced boundary may remove content
    assert actual == expected, "boundary detection must preserve CRLF, empty bodies, literal fences and unknown content exactly"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["LF", "CRLF", "CR"])
@pytest.mark.parametrize("damage", ["none", "wrong-hash", "wrong-utf8-size", "huge-size"])
def test_readable_projection_checks_literal_utf8_binding_and_strict_line_endings(newline, damage):
    # GIVEN independently constructed framing for a non-ASCII readable prefix
    prefix = "café raw" + newline
    sizes = {"none": len(prefix.encode("utf-8")), "wrong-hash": len(prefix.encode("utf-8")),
             "wrong-utf8-size": len(prefix), "huge-size": "9" * 5000}
    digests = {"none": hashlib.sha256(prefix.encode("utf-8")).hexdigest(), "wrong-hash": "0" * 64,
               "wrong-utf8-size": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
               "huge-size": hashlib.sha256(prefix.encode("utf-8")).hexdigest()}
    marker = "<!-- brewdoc-inspection/1 readable-bytes=%s sha256=%s -->" % (sizes[damage], digests[damage])
    separators = {"\n": "\n", "\r\n": "\n", "\r": "\n\n"}
    markdown = prefix + separators[newline] + newline.join((marker, inspection.SUPPLEMENT_SCOPE,
                                                           "", "## Links", "- retained target", ""))
    expected = {"none": prefix, "wrong-hash": markdown, "wrong-utf8-size": markdown, "huge-size": markdown}
    assert prefix.encode("utf-8").startswith(b"caf\xc3\xa9"), "the binding precondition must require UTF-8 byte offsets rather than character counts"
    # WHEN projection consumes valid framing or an unbound lookalike
    actual = inspection.readable_projection(markdown)
    # THEN valid framing preserves exact prefix bytes and every unbound form stays whole
    assert actual == expected[damage], "only the exact hash-bound UTF-8 prefix may be separated across LF, CRLF or CR"


@pytest.mark.parametrize("line_separator", ["\u0085", "\u2028", "\u2029", "\v", "\f"])
def test_generated_boundary_ignores_unicode_line_separators_and_inline_backtick_spans(line_separator):
    # GIVEN legitimate source text whose Unicode separators and inline spans resemble fence openers
    prefix = "target ` before" + line_separator + "``` after `\n```inline content```\nretained source\n"
    supplement = inspection.SUPPLEMENT_SCOPE + "\n\n## Links\n\n- retained target\n"
    markdown = service._append_inspection(prefix, supplement)
    assert prefix.count("retained source") == 1, "the source paragraph must remain present before the actual generated tail"
    # WHEN the parser locates the real generated boundary with Markdown's strict line rules
    actual = inspection.readable_projection(markdown)
    # THEN Unicode line separators and inline code spans cannot hide or forge block fences
    assert actual == prefix, "only CR and LF line endings and valid block fences may influence boundary discovery"


@pytest.mark.parametrize("suffix", [".html", ".epub"])
def test_combined_markdown_limit_refusal_preserves_requested_report_and_script(tmp_path, monkeypatch, suffix):
    # GIVEN valid inspection, readable Markdown within its limit and existing output bytes
    from brewdoc import epub, htmltext

    builders = {
        ".html": lambda path: path.write_text("<p>ok</p><script>retained();</script>", encoding="utf-8"),
        ".epub": lambda path: write_epub(path, {**epub2_members(), "OPS/a.xhtml": xhtml(
            "<p>ok</p><script>retained();</script>")}),
    }
    source = tmp_path / ("source" + suffix)
    builders[suffix](source)
    renderers = {".html": htmltext.render_html, ".epub": epub.render_epub}
    direct = renderers[suffix](source)[0]
    report = inspection.inspect_document(source)
    script = next(item["artifact"] for item in report["findings"] if item["text"] == "retained();")
    limit = len(direct.encode("utf-8")) + 1
    setters = {".html": lambda: monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", limit),
               ".epub": lambda: monkeypatch.setitem(epub.LIMITS, "markdown_bytes", limit)}
    setters[suffix]()
    output, saved, code_path = tmp_path / "out.md", tmp_path / "report.json", tmp_path / "script.js"
    output.write_bytes(b"previous\r\n")
    assert inspection.read_document_artifact(source, script) == b"retained();", "inspection source must remain available independently of readable limits"
    # WHEN only the combined readable output and supplement exceeds the format budget
    code, receipt, markdown = service.run(source, output, artifact_outputs={
        "inspection/report": saved, script: code_path,
    })
    # THEN the readable refusal commits its requested inspection transaction without touching Markdown
    assert (code, receipt["file_ok"], markdown, output.read_bytes(), saved.is_file(), code_path.is_file()) == (
        1, False, "", b"previous\r\n", True, True,
    ), "post-render Markdown budget refusal must preserve requested inspection evidence"
    assert (json.loads(saved.read_bytes()), code_path.read_bytes()) == (
        report, b"retained();",
    ), "saved report and script bytes must equal the independently admitted source evidence"


def test_body_projection_refusal_keeps_inspection_artifact_delivery(tmp_path, monkeypatch):
    # GIVEN valid readable conversion followed by an explicitly refused body projection
    source = tmp_path / "source.html"
    source.write_bytes(b"<p>readable</p><script>literal();</script>")
    output, report_path = tmp_path / "out.md", tmp_path / "report.json"
    output.write_bytes(b"previous Markdown\r\n")
    report = inspection.inspect_document(source)

    def refuse_projection(*args):
        """Refuse after rendering but before any readable output can commit."""
        raise BrewdocError("synthetic body projection refused")

    monkeypatch.setattr(service, "_body_only", refuse_projection)
    assert report_path.exists() is False, "requested evidence must start absent"
    # WHEN body-only assembly refuses after original source inspection has completed
    code, receipt, markdown = service.run(source, output, body_only=True,
                                          artifact_outputs={"inspection/report": report_path})
    # THEN its report is saved and the readable failure never touches the destination
    assert (code, receipt["reason"], markdown, output.read_bytes(), json.loads(report_path.read_bytes())) == (
        1, "synthetic body projection refused", "", b"previous Markdown\r\n", report,
    ), "all readable projection refusals must use the same validated artifact-only transaction"


def test_combined_limit_with_unknown_artifact_still_prevents_all_writes(tmp_path, monkeypatch):
    # GIVEN a combined output limit and one unknown requested artifact key
    from brewdoc import htmltext

    source = tmp_path / "source.html"
    source.write_bytes(b"<p>ok</p><script>literal();</script>")
    maximum = len(htmltext.render_html(source)[0].encode("utf-8")) + 1
    monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", maximum)
    output, report_path, missing = tmp_path / "out.md", tmp_path / "report.json", tmp_path / "missing"
    output.write_bytes(b"previous Markdown")
    report_path.write_bytes(b"previous report")
    assert missing.exists() is False, "the unknown artifact must have no existing destination"
    # WHEN inspection preservation encounters an invalid key after readable assembly refuses
    code, receipt, markdown = service.run(source, output, artifact_outputs={
        "inspection/report": report_path, "inspection/missing": missing,
    })
    # THEN key validation still prevents the whole requested output transaction
    assert (code, receipt["reason"], markdown, output.read_bytes(), report_path.read_bytes(), missing.exists()) == (
        1, "unknown artifact key: inspection/missing", "", b"previous Markdown", b"previous report", False,
    ), "preserving inspection after readable refusal must never bypass artifact validation"


def test_source_scope_paragraphs_cannot_end_the_readable_document(tmp_path):
    # GIVEN source paragraphs that exactly impersonate the former scope-and-heading boundary
    scope = inspection.SUPPLEMENT_SCOPE
    body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % text for text in (
        scope, "## Links", "retained body text"))
    source = docx(tmp_path / "source.docx", body)
    import brewdoc

    direct = brewdoc.render_doc(source)[0]
    assert direct.count("retained body text") == 1, "the readable source must contain the legitimate paragraph"
    # WHEN normal conversion appends the real inspection supplement
    code, receipt, markdown = service.run(source)
    # THEN both production and legacy-test projections retain the complete original readable bytes
    from inspection_contract import readable_markdown

    assert (code, receipt["file_ok"], inspection.readable_projection(direct),
            inspection.readable_projection(markdown), readable_markdown(markdown)) == (
        0, True, direct, direct, direct,
    ), "legitimate source scope markers must never truncate an unextended or inspected readable document"


def test_workbook_supplement_starts_after_a_blank_markdown_block_separator(tmp_path):
    # GIVEN a readable workbook whose last source row ends a Markdown table
    import brewdoc

    source = tmp_path / "source.xlsx"
    _synthetic_xlsx(source)
    direct = brewdoc.render_book(source)[0]
    assert direct.endswith("| 3 | A3: South | B3: 9.0 | C3: [blank] |\n"), "the last readable block must be a table row"
    # WHEN the service exposes the independent findings after that table
    code, _receipt, markdown = service.run(source)
    appended = markdown[len(direct):]
    # THEN a blank line ends the table and projection removes only the generated separator
    assert (code, appended.startswith("\n"), inspection.readable_projection(markdown)) == (
        0, True, direct,
    ), "inspection prose must begin in its own block without changing original readable bytes"


def _unicode_script_pdf(path, script):
    source, _target = _image_button_pdf()
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    objects[1] = objects[1][:-2] + b" /OpenAction 9 0 R >>"
    literal = (b"\xfe\xff" + script.encode("utf-16be")).hex().encode()
    objects[9] = b"<< /S /JavaScript /JS <" + literal + b"> >>"
    path.write_bytes(_pdf(objects))
    return path


def _unicode_script_docx(path, script):
    docx(path, "<w:p><w:r><w:t>readable</w:t></w:r></w:p>")
    with zipfile.ZipFile(path, "a") as package:
        package.writestr("scripts/original.js", script.encode("utf-8"))
    return path


def _unicode_script_pptx(path, script):
    path.write_bytes(synthetic_pptx())
    with zipfile.ZipFile(path, "a") as package:
        package.writestr("scripts/original.js", script.encode("utf-8"))
    return path


@pytest.mark.parametrize("suffix,build", [
    (".pdf", _unicode_script_pdf), (".docx", _unicode_script_docx), (".pptx", _unicode_script_pptx),
])
def test_inspected_document_preserves_literal_unicode_script_in_utf8_api_file_and_console(
        tmp_path, suffix, build):
    # GIVEN a readable document with original non-ASCII script source
    script = "alert('café 東京');\r\n"
    source = build(tmp_path / ("script" + suffix), script)
    report = inspection.inspect_document(source)
    assert sorted({item["text"] for item in report["findings"] if item["capability"] == "scripts"}) == [
        script,
    ], "the independent reader must recover the exact original Unicode script before projection"
    output = tmp_path / "out.md"
    # WHEN service output and an ASCII-configured console publish extended Markdown
    code, receipt, markdown = service.run(source, output)
    process = subprocess.run([sys.executable, "-m", "brewdoc.cli", str(source)], capture_output=True,
                             env={**os.environ, "PYTHONIOENCODING": "ascii"}, timeout=30)
    first, newline, body = process.stdout.partition(os.linesep.encode("ascii"))
    # THEN the source remains literal UTF-8 and every full output byte agrees
    assert (code, script in markdown, output.read_bytes(), process.returncode, body, process.stderr,
            json.loads(first)["inspection"]) == (
        0, True, markdown.encode("utf-8"), 0, markdown.encode("utf-8"), b"", receipt["inspection"],
    ), "script text must remain literal across API, file and ASCII-configured stdout without entity substitution"


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_pass_through_writes_exact_source_bytes_and_independent_inspection(tmp_path, suffix):
    # GIVEN UTF-8 source with BOM, CRLF, whitespace and a dangerous embedded destination
    source = tmp_path / ("source" + suffix)
    original = b"\xef\xbb\xbf  [source](javascript:literal)\r\n<script>literal();</script>\r\n\r\n"
    source.write_bytes(original)
    output, report_path = tmp_path / "out.md", tmp_path / "report.json"
    assert source.read_bytes() == original, "pass-through precondition must preserve every byte"
    # WHEN normal conversion also saves the independent inspection artifact
    code, receipt, markdown = service.run(source, output, artifact_outputs={"inspection/report": report_path})
    # THEN source and Markdown bytes stay exact while evidence has its own saved report
    assert (code, output.read_bytes(), markdown.encode("utf-8"), report_path.is_file()) == (
        0, original, original, True,
    ), "pass-through output must remain byte-exact when inspection artifacts are requested"
    report = json.loads(report_path.read_bytes())
    assert (report["schema"], receipt["inspection"]["artifact"], report["source"]["sha256"],
            sorted({item["target"] for item in report["findings"] if item["target"] is not None})) == (
        "brewdoc.inspection/1", "inspection/report", hashlib.sha256(original).hexdigest(),
        ["javascript:literal"],
    ), "inspection must preserve exact targets separately from unchanged pass-through output"


def test_render_refusal_saves_only_requested_inspection_artifacts(tmp_path, monkeypatch):
    # GIVEN an inspected HTML source and a renderer that refuses its readable projection
    source = tmp_path / "source.html"
    source.write_bytes(b"<p>readable</p><script>literal();</script>")
    output, report_path = tmp_path / "out.md", tmp_path / "report.json"
    output.write_bytes(b"previous Markdown\r\n")
    script_path = tmp_path / "script.js"
    report = inspection.inspect_document(source)
    key = next(item["artifact"] for item in report["findings"] if item["kind"] == "inline_script")

    def refuse(*args, **options):
        """Refuse rendering after the source remains independently inspectable."""
        raise BrewdocError("synthetic readable projection refused")

    monkeypatch.setattr(service, "HTML_ROUTE", replace(service.HTML_ROUTE, render=refuse))
    assert inspection.read_document_artifact(source, key) == b"literal();", "script source must be retrievable before rendering"
    # WHEN run refuses Markdown after independent inspection has completed
    code, receipt, markdown = service.run(source, output, artifact_outputs={
        "inspection/report": report_path, key: script_path,
    })
    # THEN only requested sidecars commit and readable failure remains a failure
    assert (code, receipt["file_ok"], receipt["reason"], markdown, output.read_bytes(),
            json.loads(report_path.read_bytes()), script_path.read_bytes()) == (
        1, False, "synthetic readable projection refused", "", b"previous Markdown\r\n",
        report, b"literal();",
    ), "readable refusal must retain exact inspection evidence and leave the Markdown destination untouched"
    assert {item["key"]: item["out"] for item in receipt["artifacts"] if item["out"] is not None} == {
        "inspection/report": str(report_path), key: str(script_path),
    }, "refusal receipt must identify only the artifacts actually written"


def test_unknown_artifact_prevents_every_requested_destination_write(tmp_path):
    # GIVEN existing destinations and one nonexistent artifact key
    source = tmp_path / "source.html"
    source.write_bytes(b"<p>readable</p><script>literal();</script>")
    output, report_path = tmp_path / "out.md", tmp_path / "report.json"
    output.write_bytes(b"previous Markdown")
    report_path.write_bytes(b"previous report")
    assert (output.read_bytes(), report_path.read_bytes()) == (
        b"previous Markdown", b"previous report",
    ), "all destinations must exist before validation"
    # WHEN artifact validation encounters an unknown key after inspecting the source
    code, receipt, markdown = service.run(source, output, artifact_outputs={
        "inspection/report": report_path, "inspection/missing": tmp_path / "missing",
    })
    # THEN no part of the output transaction may be committed
    assert (code, receipt["reason"], markdown, output.read_bytes(), report_path.read_bytes(),
            (tmp_path / "missing").exists()) == (
        1, "unknown artifact key: inspection/missing", "", b"previous Markdown", b"previous report", False,
    ), "an unknown artifact key must leave every destination untouched"


def test_artifact_only_replace_failure_rolls_back_all_sidecars(tmp_path, monkeypatch):
    # GIVEN a refused readable projection and two existing inspection destinations
    source = tmp_path / "source.html"
    source.write_bytes(b"<p>readable</p><script>literal();</script>")
    output, report_path, script_path = (tmp_path / "out.md", tmp_path / "report.json", tmp_path / "script.js")
    output.write_bytes(b"previous Markdown")
    report_path.write_bytes(b"previous report")
    script_path.write_bytes(b"previous script")
    report = inspection.inspect_document(source)
    key = next(item["artifact"] for item in report["findings"] if item["kind"] == "inline_script")

    def refuse(*args, **options):
        """Force the artifact-only output path without changing source inspection."""
        raise BrewdocError("readable projection refused")

    def fail_replace(*args):
        """Fail the second publication after the first sidecar has changed."""
        raise OSError("injected artifact replace failure")

    monkeypatch.setattr(service, "HTML_ROUTE", replace(service.HTML_ROUTE, render=refuse))
    steps = itertools.chain((os.replace, fail_replace), itertools.repeat(os.replace))
    monkeypatch.setattr(os, "replace", lambda first, second: next(steps)(first, second))
    assert (report_path.read_bytes(), script_path.read_bytes()) == (
        b"previous report", b"previous script",
    ), "both sidecars must exist before the artifact-only transaction starts"
    # WHEN the second artifact replace fails after the first was published
    code, receipt, markdown = service.run(source, output, artifact_outputs={
        "inspection/report": report_path, key: script_path,
    })
    # THEN existing sidecars and Markdown bytes are restored without leaked stages
    assert (code, receipt["reason"], markdown, output.read_bytes(), report_path.read_bytes(),
            script_path.read_bytes(), sorted(item.name for item in tmp_path.iterdir())) == (
        1, "output write failed: could not replace %s: injected artifact replace failure" % script_path,
        "", b"previous Markdown", b"previous report", b"previous script",
        ["out.md", "report.json", "script.js", "source.html"],
    ), "artifact-only output failures must roll back every destination and remove all temporary stages"


def test_url_snapshot_identity_enters_inspection_before_report_serialization(tmp_path, monkeypatch):
    # GIVEN a frozen response and an observable inspection boundary without live networking
    htmlurl = importlib.import_module("brewdoc.htmlurl")
    body = b"<html><body><p>frozen source</p></body></html>"
    observed = []
    inspect = service._inspect_document

    def observe(path, **options):
        """Capture both report representations at the inspection boundary."""
        report, payloads = inspect(path, **options)
        observed.append((report["source"], json.loads(payloads["inspection/report"])["source"]))
        return report, payloads

    monkeypatch.setattr(service, "_inspect_document", observe)
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **options: {
        "final_url": "https://fixture.test/frozen", "status": 200, "redirects": (),
        "headers": (("Content-Type", "text/html; charset=utf-8"),), "entity": body,
        "body": body, "entity_sha256": hashlib.sha256(body).hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    })
    assert observed == [], "no inspection may precede snapshot acquisition"
    # WHEN explicit acquisition converts its local frozen snapshot
    code, receipt, _markdown = htmlurl._run_url("https://fixture.test/frozen", tmp_path / "out.md")
    # THEN both in-memory and saved evidence bind to the exact acquired snapshot identity
    first, serialized = observed[0]
    assert (code, first, serialized, receipt["inspection"]["source"]) == (
        0, serialized, first, first,
    ), "snapshot provenance must already be present in the serialized inspection report"
    assert (first["kind"], first["provenance"], first["sha256"]) == (
        "snapshot", {"snapshot": receipt["acquisition"]["snapshot"],
                     "requested_url": "https://fixture.test/frozen",
                     "final_url": "https://fixture.test/frozen"},
        receipt["acquisition"]["snapshot"]["sha256"],
    ), "inspection must identify the captured snapshot rather than claiming the original HTTP entity"


def test_cli_saves_generic_report_artifact_for_html(tmp_path, capsys):
    # GIVEN local HTML and an independent report destination
    source = tmp_path / "source.html"
    source.write_bytes(b"<p>text</p><script>literal();</script>")
    report_path = tmp_path / "report.json"
    assert report_path.exists() is False, "the CLI must create a new report artifact"
    # WHEN --artifact requests the standard report key for a non-workbook format
    code = cli.main([str(source), "--artifact", "inspection/report=" + str(report_path)])
    output = capsys.readouterr().out
    receipt_line, markdown = output.split("\n", 1)
    # THEN receipt framing remains one JSON line followed by normal inspected Markdown
    assert (code, json.loads(receipt_line)["file_ok"], json.loads(report_path.read_bytes())["schema"],
            "## Active content" in markdown, "literal();" in markdown) == (
        0, True, "brewdoc.inspection/1", True, True,
    ), "generic CLI artifacts must preserve receipt framing and expose literal active content by default"
