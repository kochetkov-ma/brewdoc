"""Original HTML and pass-through text inspection regressions."""

import socket
import html
import base64
import hashlib
import json
import subprocess
import sys

import pytest

from brewdoc._inspection_html import inspect_html, inspect_text, inspect_plain_targets
from brewdoc._inspection_common import InspectionContext


class RecordingContext:
    """Capture the frozen reader contract without orchestration dependencies."""

    def __init__(self):
        self.findings = []
        self.components = []
        self.payloads = {}
        self.coverage = {}
        self.problems = []

    def component(self, document, kind, location, **details):
        self.components.append((kind, location, details))
        return str(len(self.components))

    def finding(self, document, capability, kind, location, **details):
        self.findings.append((capability, kind, location, details))
        return str(len(self.findings))

    def payload(self, document, kind, location, data, media_type):
        key = str(len(self.payloads))
        self.payloads[key] = data
        return key

    def cover(self, document, capability, status, reason, locations=()):
        self.coverage[capability] = status

    def problem(self, document, capabilities, reason, location):
        self.problems.append((capabilities, reason, location))

    def check(self, limit, value, location):
        pass

    def charge(self, counter, amount, location):
        pass

    def remaining(self, counter):
        return 1_000_000


def test_original_html_retains_hidden_script_bytes_targets_and_source_locations(monkeypatch):
    # GIVEN hidden source, duplicate URL attributes and active constructs outside an article
    source = (b'<html><head><script>window.x = "&amp;";\r\n</script>'
              b'<script src="//outside.invalid/a.js"></script></head><body>'
              b'<article>Readable</article><div hidden>Secret</div>'
              b'<a href="javascript:alert(1)" href="second">x</a>'
              b'<form action="/send"><input name="token" value="secret">'
              b'<button formaction="/other">go</button></form>'
              b'<iframe src="/frame"></iframe><img srcset="/one 1x, /two 2x">'
              b'<div onclick="alert(2)" style="background:url(/image)"></div>'
              b'<meta http-equiv="refresh" content="0;url=/redirect"></body></html>')
    def refuse_network(*args, **kwargs):
        raise AssertionError("inspection attempted network access")
    monkeypatch.setattr(socket, "socket", refuse_network)
    context = RecordingContext()
    # WHEN the original bytes are inspected twice without executing their contents
    inspect_html(source, "original.html", context, "document/1")
    repeated = RecordingContext()
    inspect_html(source, "original.html", repeated, "document/1")
    # THEN literal source, discarded destinations and stable source locations survive
    targets = {record[3].get("target") for record in context.findings}
    assert targets >= {"javascript:alert(1)", "second", "//outside.invalid/a.js", "/send",
                       "/other", "/frame", "/one", "/two", "/image", "/redirect"}, (
        "all original active and resource targets must survive without URL filtering")
    scripts = [record for record in context.findings if record[1] == "inline_script"]
    assert context.payloads[scripts[0][3]["artifact"]] == b'window.x = "&amp;";\r\n', (
        "the script artifact must retain entity spelling and original CRLF bytes")
    hidden = [record for record in context.findings if record[3].get("text") == "Secret"]
    assert hidden[0][2]["xpath"] == "/html[1]/body[1]/div[1]/text()[1]", (
        "hidden text must have an exact stable source-tree path")
    assert context.findings == repeated.findings, "source traversal must be deterministic"
    assert context.problems == [], "valid supported HTML must not degrade coverage"


def test_markdown_links_have_original_utf8_byte_spans_and_crlf_line_columns():
    # GIVEN UTF-8 multibyte text and CRLF before an inline link and a script
    source = '\ufeffé\r\n[link](javascript:alert(1))\r\n<script>x()</script>'.encode()
    context = RecordingContext()
    # WHEN pass-through content is inspected separately from readable rendering
    inspect_text(source, "notes.md", context, "document/1")
    # THEN source coordinates refer to original bytes and the whole target is preserved
    links = [record for record in context.findings if record[1] == "markdown_link"]
    assert [(record[3]["target"], record[2]) for record in links] == [(
        "javascript:alert(1)", {"kind": "text", "part": "notes.md", "byte_start": 14,
                                 "byte_end": 33, "line": 2, "column": 8})], (
        "Markdown nested parentheses must retain the complete target and measured source span")
    assert b"x()" in context.payloads.values(), "embedded script source must be retrievable"


def test_undecodable_html_preserves_original_bytes_and_reports_problem():
    # GIVEN a truncated BOM-declared UTF-16 source that the accepted strict decoder refuses
    source = b'\xff\xfe<\x00p'
    context = RecordingContext()
    # WHEN inspection encounters an invalid source encoding
    inspect_html(source, "broken.html", context, "document/1")
    # THEN original evidence survives and text inspection cannot claim success
    assert source in context.payloads.values(), "undecodable original bytes must remain retrievable"
    assert len(context.problems) == 1, "the source decoding failure must be explicit"


def test_source_attribute_spans_preserve_entity_spelling_and_raw_text_controls():
    # GIVEN a handler with source entities and a textarea containing literal markup
    source = (b'<button ONCLICK="run(&quot;x&quot;)">go</button>\r\n'
              b'<textarea><a href="fake">&amp;</a></textarea>')
    context = InspectionContext()
    document = context.document(source, "source.html", parent=None, location={"kind": "source"})
    # WHEN inspection uses the real shared accounting and coverage implementation
    inspect_html(source, "source.html", context, document)
    # THEN artifact bytes and coordinates refer to original source while defaults stay inert
    handlers = [item for item in context.findings if item["kind"] == "event_handler"
                and item["capability"] == "scripts"]
    assert len(handlers) == 1, "one source handler must produce one script finding"
    handler = handlers[0]
    assert (handler["text"], context.payloads[handler["artifact"]],
            source[handler["location"]["byte_start"]:handler["location"]["byte_end"]]) == (
        'run("x")', b'run(&quot;x&quot;)', b'run(&quot;x&quot;)'), (
        "decoded handler evidence must retain its exact original source artifact and byte span")
    assert [item["text"] for item in context.findings if item["kind"] == "control_default"] == [
        '<a href="fake">&</a>'], "textarea defaults must preserve literal markup as text"
    assert [item["target"] for item in context.findings if item["target"] is not None] == [], (
        "markup inside a textarea must not invent active hyperlink destinations")
    assert context.coverage[(document, "scripts")]["status"] == "complete", (
        "initial pending coverage must become complete after a successful real-context scan")


def test_record_exhaustion_preserves_source_and_never_claims_complete_coverage():
    # GIVEN a source whose element count exceeds the operation-wide allowance
    source = b"<div>one</div><div>two</div>"
    context = InspectionContext(limits={"records": 4})
    document = context.document(source, "limited.html", parent=None, location={"kind": "source"})
    # WHEN source inventory succeeds but element inspection exhausts the shared budget
    inspect_html(source, "limited.html", context, document)
    # THEN the original bytes survive and every affected scan remains explicitly incomplete
    assert source in context.payloads.values(), "original evidence must survive record exhaustion"
    assert {context.coverage[(document, capability)]["status"]
            for capability in ("inventory", "text", "links", "scripts")} == {"unavailable"}, (
        "an interrupted scan with no findings must not become empty complete coverage")
    assert [item["limit"] for item in context.exhausted] == ["records"], (
        "the report must name the exhausted operation-wide counter")


@pytest.mark.parametrize("source,expected", [
    (b"<div>" * 20 + b"<script>literal()", b"literal()"),
    (b"<script>literal()\r\n", b"literal()\r\n"),
])
def test_unclosed_script_source_survives_ordinary_deep_html(source, expected):
    # GIVEN source with an unclosed raw script and optional deep HTML nesting
    context = InspectionContext()
    document = context.document(source, "raw.html", parent=None, location={"kind": "source"})
    # WHEN the source token scan reaches EOF without a script end tag
    inspect_html(source, "raw.html", context, document)
    # THEN all remaining script bytes survive independently of embedded-document depth
    scripts = [item for item in context.findings if item["kind"] == "inline_script"]
    assert [context.payloads[item["artifact"]] for item in scripts] == [expected], (
        "EOF must retain every byte of an unclosed script")
    assert context.coverage[(document, "scripts")]["status"] == "complete", (
        "ordinary HTML nesting must not exhaust the embedded-document nesting budget")


def test_recursive_srcdoc_uses_cumulative_nesting_not_reset_html_stack():
    # GIVEN three nested inline-frame documents and a nesting allowance of two
    source = "<p>end</p>"
    for _ in range(3):
        source = '<iframe srcdoc="%s"></iframe>' % html.escape(source, quote=True)
    data = source.encode()
    context = InspectionContext(limits={"nesting": 2})
    document = context.document(data, "frames.html", parent=None, location={"kind": "source"})
    # WHEN decoded srcdoc attributes recursively enter the same operation
    inspect_html(data, "frames.html", context, document)
    # THEN recursion fails explicitly rather than resetting its allowance at each frame
    assert [item["limit"] for item in context.exhausted] == ["nesting"], (
        "recursive srcdoc expansion must share the operation's nesting allowance")
    assert context.coverage[(document, "actions")]["status"] == "partial", (
        "a discovered but unscanned frame must prevent complete action coverage")


def test_plaintext_dom_never_invents_active_source_tokens():
    # GIVEN a plaintext element whose remaining bytes resemble active HTML
    source = b'<plaintext><script>fake()</script><a href="fake">literal'
    context = RecordingContext()
    # WHEN the full original DOM is inspected before readable cleaning
    inspect_html(source, "plain.html", context, "document/1")
    # THEN source spelling survives but neither script nor link is active DOM content
    assert [record for record in context.findings if record[1] == "inline_script"] == [], (
        "plaintext script spelling must not become an active-content finding")
    assert [record[3].get("target") for record in context.findings
            if record[3].get("target")] == [], "plaintext markup must not invent DOM destinations"


def test_css_tokenizer_recovers_escapes_imports_and_ignores_comments_and_strings():
    # GIVEN escaped CSS tokens plus inactive url spellings in comments and strings
    source = (br'<style>/*url(/fake)*/ x {content:"url(/also-fake)";'
              br'background:u\72l("/im\61ge")} @import/**/"/th\65me.css";</style>')
    context = RecordingContext()
    # WHEN passive CSS tokens are scanned without executing or fetching resources
    inspect_html(source, "style.html", context, "document/1")
    # THEN decoded targets and original source spellings survive at their occurrences
    assert [record[3].get("target") for record in context.findings
            if record[1] == "css_url"] == ["/image", "/theme.css"], (
        "CSS escapes and comments must follow token semantics without false URLs in strings")


def test_markdown_nested_labels_reference_shortcuts_and_escaped_targets():
    # GIVEN inline nested labels and three reference-link occurrence forms
    source = br'[outer [inner]](/a\(b\)) [shown][id] [id][] [id]' + b'\r\n' + br'[id]: /destination'
    context = RecordingContext()
    # WHEN every Markdown destination occurrence is inspected
    inspect_text(source, "links.md", context, "document/1")
    # THEN each use carries the resolved target at its own original location
    links = [record for record in context.findings if record[1] == "markdown_link"]
    assert [record[3]["target"] for record in links] == ["/a(b)", "/destination",
                                                         "/destination", "/destination"], (
        "nested labels, escaped destinations and every reference occurrence must be recovered")
    assert len({record[2]["byte_start"] for record in links}) == 4, (
        "reference occurrences must retain distinct original source positions")


def test_mime_charset_override_keeps_original_non_utf8_byte_spans():
    # GIVEN original MIME HTML bytes with charset metadata outside the HTML source
    source = '<p>Привет</p><a href="/цель">x</a>'.encode("cp1251")
    context = RecordingContext()
    # WHEN inspection receives original MIME bytes and its declared charset
    inspect_html(source, "body.html", context, "document/1", location={"charset": "windows-1251"})
    # THEN decoding and measured spans refer to those exact original bytes
    links = [record for record in context.findings if record[1] == "url_attribute"]
    assert [(record[3]["target"], source[record[2]["byte_start"]:record[2]["byte_end"]])
            for record in links] == [("/цель", "/цель".encode("cp1251"))], (
        "MIME charset decoding must preserve original byte offsets without UTF-8 rewriting")


def test_original_lexbor_dom_recovers_foreign_content_and_repaired_hidden_locations():
    # GIVEN SVG markup hidden from an HTML-only raw-style scanner and foster-parented text
    source = (b'<svg><style><a href="/foreign">foreign</a></style></svg>'
              b'<table><div hidden><a href="/moved">secret</a></table>')
    context = RecordingContext()
    # WHEN source spans and the complete original Lexbor DOM are inspected together
    inspect_html(source, "foreign.html", context, "document/1")
    # THEN foreign destinations and repaired DOM paths survive independently of readable filtering
    foreign = [record for record in context.findings if record[0] == "links"
               and record[3].get("target") == "/foreign"]
    assert [(record[2]["xpath"], record[2]["path_basis"]) for record in foreign] == [(
        "/html[1]/body[1]/svg[1]/style[1]/a[1]", "original_dom")], (
        "foreign-content URL recovery must use the actual original DOM path")
    hidden = [record for record in context.findings if record[1] == "dom_text"
              and record[3].get("text") == "secret"]
    assert [(record[2]["xpath"], record[3]["hidden"]) for record in hidden] == [(
        "/html[1]/body[1]/div[1]/a[1]/text()[1]", True)], (
        "DOM repair must retain hidden content at its repaired original-tree location")
    assert "byte_start" not in foreign[0][2], "serialized DOM positions must not impersonate source byte spans"


def test_plain_text_url_occurrences_retain_original_targets_and_spans():
    # GIVEN ordinary text with two identical URL occurrences on separate lines
    source = b"visit https://example.invalid/a?x=1#z\r\nhttps://example.invalid/a?x=1#z"
    context = RecordingContext()
    # WHEN text links are inspected independently of exact pass-through output
    inspect_text(source, "links.txt", context, "document/1")
    # THEN both original occurrences retain exact targets and distinct source spans
    links = [record for record in context.findings if record[1] == "text_url"]
    assert [(record[3]["target"], record[2]["byte_start"], record[2]["line"])
            for record in links] == [("https://example.invalid/a?x=1#z", 6, 1),
                                    ("https://example.invalid/a?x=1#z", 39, 2)], (
        "plain text URLs must be retained at every original occurrence")


def test_data_url_payloads_keep_exact_bytes_metadata_and_shared_recursive_callback():
    # GIVEN binary image bytes and a signature-valid embedded PDF available without network access
    pdf = b"%PDF-1.4\nsynthetic fixture for callback admission\n"
    pdf_url = "data:application/pdf;base64," + base64.b64encode(pdf).decode()
    source = ('<img src="data:image/png;base64,AQID"><object data="' + pdf_url + '"></object>'
              '<a href="data:text/plain;charset=windows-1251,%CF%F0">text</a>').encode()
    context = InspectionContext()
    document = context.document(source, "data.html", parent=None, location={"kind": "source"})
    calls = []
    def scan_embedded(data, name, child_context, *, parent, location, component=None):
        calls.append((data, name, child_context is context, parent, location, component))
        return "document/child"
    # WHEN URL payloads are decoded under the existing operation and source positions
    inspect_html(source, "data.html", context, document, scan_embedded=scan_embedded)
    # THEN payload artifacts preserve exact bytes and supported content reaches the injected scanner
    assert sorted((item["byte_size"], item["sha256"]) for item in context.artifacts
                  if item["kind"] == "data_url_payload") == sorted([
        (3, hashlib.sha256(b"\x01\x02\x03").hexdigest()),
        (len(pdf), hashlib.sha256(pdf).hexdigest()),
        (2, hashlib.sha256(b"\xcf\xf0").hexdigest()),
    ]), "each data URL source occurrence must retain exactly one decoded payload artifact"
    assert [(item[0], item[1], item[2], item[3]) for item in calls] == [
        (pdf, "inline-000002.pdf", True, document),
        (b"\xcf\xf0", "inline-000003.txt", True, document),
    ], "supported PDF and text payloads must recurse through the supplied shared-context callback"
    assert calls[0][4]["kind"] == "data_url", "decoded bytes must identify their data URL container provenance"
    assert "byte_start" not in calls[0][4], "decoded payload bytes must not claim original encoded-source offsets"


@pytest.mark.parametrize("url", ["data:image/png;base64,%%%%", "data:text/plain,%GG", "data:image/png;base64,AQ="])
def test_malformed_data_url_payloads_leave_explicit_incomplete_coverage(url):
    # GIVEN malformed inline bytes whose source URL remains intact
    source = ('<img src="' + url + '">').encode()
    context = InspectionContext()
    document = context.document(source, "bad.html", parent=None, location={"kind": "source"})
    # WHEN inline decoding refuses malformed base64 or percent escapes
    inspect_html(source, "bad.html", context, document)
    # THEN original targets survive and payload-related coverage remains incomplete
    assert any(item["target"] == url for item in context.findings), "the original malformed URL must survive"
    assert context.coverage[(document, "attachments")]["status"] in {"partial", "unavailable"}, (
        "malformed embedded payloads must not become complete-empty attachment coverage")
    assert any("data URL" in item["reason"] for item in context.coverage.values()), (
        "the decoded-payload refusal must identify the failing data URL boundary")


def test_inspection_html_metadata_is_json_ready_before_serialization():
    # GIVEN original duplicate attributes represented by HTMLParser tuples
    source = b'<div hidden><a href="javascript:x()">text</a></div>'
    context = InspectionContext()
    document = context.document(source, "json.html", parent=None, location={"kind": "source"})
    # WHEN the reader records components and findings
    inspect_html(source, "json.html", context, document)
    # THEN the public in-memory records and their serialized representations remain equal
    assert json.loads(json.dumps(context.components)) == context.components, (
        "HTML attributes must be JSON-ready records without tuple/list divergence")


def test_data_url_unknown_empty_percent_bytes_and_fragment_are_preserved_without_dispatch():
    # GIVEN opaque bytes, an empty image and a percent-encoded plus/hash with a URL fragment
    source = (b'<object data="data:application/x-opaque;name=a%3Bb,%00%2B%23#fragment"></object>'
              b'<img src="data:image/png;base64,">')
    context = InspectionContext()
    document = context.document(source, "opaque.html", parent=None, location={"kind": "source"})
    # WHEN unknown and empty local resources are decoded without an external reader
    inspect_html(source, "opaque.html", context, document)
    # THEN actual payload bytes, empty state and original metadata remain explicitly inventoried
    components = [item for item in context.components if item["kind"] == "data_url_payload"]
    assert [(context.payloads[item["artifact"]], item["disposition"], item["details"]["empty"])
            for item in components] == [(b"\x00+#", "opaque", False), (b"", "extracted", True)], (
        "unknown bytes must be opaque and retrievable while empty payloads remain explicit")
    assert (components[0]["details"]["parameters"], components[0]["location"]["fragment"]) == (
        {"name": "a;b"}, "fragment"), "encoded delimiters must remain parameter data and fragments remain metadata"


def test_data_url_decoding_budget_refusal_keeps_original_target_and_diagnostic():
    # GIVEN an inline payload that fits the source but not remaining decoder capacity
    source = b'<img src="data:image/png;base64,AQID">'
    context = InspectionContext(limits={"decoded_bytes": len(source) * 12 + 5})
    document = context.document(source, "limited.html", parent=None, location={"kind": "source"})
    # WHEN the shared source decode consumes most of the operation-wide allowance
    inspect_html(source, "limited.html", context, document)
    # THEN payload decoding stops explicitly with original evidence still retained
    assert source in context.payloads.values(), "source retention must precede payload-decoder refusal"
    assert any(item["target"] == "data:image/png;base64,AQID" for item in context.findings), (
        "the exact inline target must survive decoder exhaustion")
    assert [item["limit"] for item in context.exhausted] == ["decoded_bytes"], (
        "nested resource decoding must charge the shared operation allowance")
    assert context.coverage[(document, "attachments")]["status"] == "unavailable", (
        "decoder refusal must leave attachment inspection explicitly incomplete")


def test_srcdoc_embedded_data_document_reuses_the_supplied_operation_callback():
    # GIVEN an inline frame with its own data URL containing a PDF payload
    pdf = b"%PDF-1.4\ninline callback admission\n"
    inner = '<object data="data:application/pdf;base64,%s"></object>' % base64.b64encode(pdf).decode()
    source = ('<iframe srcdoc="' + html.escape(inner, quote=True) + '"></iframe>').encode()
    context = InspectionContext()
    document = context.document(source, "frame.html", parent=None, location={"kind": "source"})
    children = []
    def scan_embedded(data, name, child_context, *, parent, location, component=None):
        children.append((data, child_context is context, location))
        return "document/child"
    # WHEN srcdoc inspection expands a local payload under the parent operation
    inspect_html(source, "frame.html", context, document, scan_embedded=scan_embedded)
    # THEN callback identity and container provenance survive without invented source byte spans
    assert [(item[0], item[1]) for item in children] == [(pdf, True)], (
        "srcdoc payloads must dispatch exactly once with the same operation context")
    assert "byte_start" not in children[0][2]["container"], (
        "decoded srcdoc payloads must not claim offsets in rewritten source")


def test_plain_target_helper_preserves_mime_charset_without_interpreting_script_looking_text():
    # GIVEN inert MIME text with a URL and literal HTML-looking script markup
    source = '<https://example.invalid/цель>\r\n<script>literal()</script>'.encode("cp1251")
    context = InspectionContext()
    document = context.document(source, "body.txt", parent=None, location={"kind": "source"})
    # WHEN mail injects target-only scanning with original bytes and MIME provenance
    inspect_plain_targets(source, "body.txt", context, document,
                          location={"mime_path": "1.2"}, charset="windows-1251")
    # THEN destination spans preserve source encoding while script-looking bytes remain inert
    assert [(item["target"], item["location"]["mime_path"],
             source[item["location"]["byte_start"]:item["location"]["byte_end"]])
            for item in context.findings] == [(
        "https://example.invalid/цель", "1.2", "https://example.invalid/цель".encode("cp1251"))], (
        "target-only scanning must emit only the original URL occurrence with MIME source bytes")
    assert context.payloads == {}, "inert script-looking text must never become a script artifact"


_RUNTIME_INSPECTION = '''
import importlib.abc
import json
from pathlib import Path
import sys
import pdfplumber
import python_calamine

mode, directory = sys.argv[1], Path(sys.argv[2])
attempts = []
class NativeBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selectolax" or fullname.startswith("selectolax."):
            attempts.append(fullname)
            raise ModuleNotFoundError("simulated native parser absence")
sys.meta_path.insert(0, NativeBlocker())
if mode == "unsupported-version":
    sys.version_info = (3, 15, 0, "final", 0)
from brewdoc import inspect_document, read_document_artifact, run
from brewdoc.selfcheck import synthetic_docx
source, legacy = directory / "source.html", directory / "legacy.docx"
source.write_bytes(b'<script>literal()</script><a href="/target">text</a>')
legacy.write_bytes(synthetic_docx())
report = inspect_document(source)
repeated = inspect_document(source)
script = next(item for item in report["findings"] if item["kind"] == "inline_script")
payload = read_document_artifact(source, script["artifact"])
code, receipt, markdown = run(legacy)
print(json.dumps({
    "native_attempts": len(attempts), "script": payload.decode(),
    "targets": sorted({item["target"] for item in report["findings"] if item["target"]}),
    "script_status": next(item["status"] for item in report["coverage"] if item["capability"] == "scripts"),
    "dom_unavailable": any(item["kind"] == "html_dom" and item["disposition"] == "unreadable"
                           and item["reason"].startswith("HTML DOM unavailable:")
                           for item in report["components"]),
    "deterministic": report == repeated,
    "legacy": [code, receipt["file_ok"], receipt["route"], bool(markdown)]
}))
'''


@pytest.mark.parametrize("mode,attempts", [("unsupported-version", 0), ("missing-parser", 1)])
def test_source_inspection_keeps_lexical_bytes_and_legacy_conversion_without_native_retry(tmp_path, mode, attempts):
    # GIVEN a fresh process with unsupported runtime or a missing supported native binding
    directory = tmp_path / mode
    directory.mkdir()
    # WHEN repeated inspection, artifact retrieval and DOCX conversion share the cached binding
    completed = subprocess.run([sys.executable, "-c", _RUNTIME_INSPECTION, mode, str(directory)],
                               capture_output=True, timeout=30)
    # THEN source evidence survives, DOM coverage is explicit and unrelated formats still work
    assert (completed.returncode, completed.stderr) == (0, b""), (
        "runtime refusal must not prevent package import or source artifact inspection")
    assert json.loads(completed.stdout) == {
        "native_attempts": attempts, "script": "literal()", "targets": ["/target"],
        "script_status": "partial", "dom_unavailable": True, "deterministic": True,
        "legacy": [0, True, "doc", True],
    }, "native import attempts must remain cached while lexical inspection and DOCX conversion succeed"


@pytest.mark.parametrize("media,code", [("application/javascript", 'window.x = "é";\r\n'),
                                        ("text/javascript", "textSource();\r\n"),
                                        ("application/ecmascript", "")])
def test_script_data_resources_recover_literal_source_once_without_duplicate_payloads(media, code):
    # GIVEN available original script resource bytes in a data URL
    payload = code.encode()
    url = "data:%s;charset=utf-8;base64,%s" % (media, base64.b64encode(payload).decode())
    source = ('<script src="' + url + '"></script>').encode()
    context = InspectionContext()
    document = context.document(source, "script.html", parent=None, location={"kind": "source"})
    # WHEN original URL and later script-resource observations refer to one occurrence
    inspect_html(source, "script.html", context, document)
    # THEN decoded source is explicit, source-only and reuses the original payload artifact
    scripts = [item for item in context.findings if item["kind"] == "inline_data_script"]
    assert [(item["text"], item["details"]["mode"], context.payloads[item["artifact"]])
            for item in scripts] == [(code, "source_only", payload)], (
        "known script data resources must expose literal Unicode/CRLF source even when empty")
    assert len([item for item in context.artifacts if item["kind"] == "data_url_payload"]) == 1, (
        "link and script observations must share one retained payload")


def test_plain_mime_script_candidate_and_css_data_urls_recover_static_source_targets():
    # GIVEN a text/plain script candidate and a stylesheet containing a resource destination
    source = (b'<script src="data:text/plain;charset=utf-8,candidate%28%29%3B"></script>'
              b'<link href="data:text/css;charset=utf-8,x%7Bbackground%3Aurl%28%2Fimage%29%7D">')
    context = InspectionContext()
    document = context.document(source, "resource.html", parent=None, location={"kind": "source"})
    # WHEN available script/style bytes are inspected without runtime interpretation
    inspect_html(source, "resource.html", context, document)
    # THEN classic-script candidates and stylesheet targets remain explicit static evidence
    assert [(item["text"], item["details"]["mode"]) for item in context.findings
            if item["kind"] == "inline_data_script"] == [("candidate();", "source_only")], (
        "script resource context must recover text/plain source without claiming execution")
    assert [item["target"] for item in context.findings if item["kind"] == "css_url"] == ["/image"], (
        "decoded stylesheet URLs must use the existing passive CSS tokenizer")


def test_script_data_resource_unknown_charset_keeps_bytes_and_incomplete_source_coverage():
    # GIVEN script bytes with an invalid declared codec
    source = b'<script src="data:application/javascript;charset=not-a-codec,source%28%29"></script>'
    context = InspectionContext()
    document = context.document(source, "charset.html", parent=None, location={"kind": "source"})
    # WHEN literal script decoding cannot honor its declared charset
    inspect_html(source, "charset.html", context, document)
    # THEN raw bytes survive and script-source completeness cannot be claimed
    assert b"source()" in context.payloads.values(), "undecodable script bytes must remain retrievable"
    assert context.coverage[(document, "scripts")]["status"] == "partial", (
        "script declarations alone cannot complete unavailable literal-source decoding")


def test_normal_markdown_contains_literal_data_script_source_in_active_content(tmp_path):
    # GIVEN readable HTML whose script resource is available as UTF-8 data bytes
    from brewdoc import run
    code = 'window.message = "café";\r\n'
    url = "data:application/javascript;charset=utf-8;base64," + base64.b64encode(code.encode()).decode()
    source = tmp_path / "resource.html"
    source.write_text('<p>Readable</p><script src="' + url + '"></script>', encoding="utf-8")
    # WHEN normal conversion includes passive inspection findings
    status, receipt, markdown = run(source)
    active = markdown.split("## Active content\n", 1)[1].split("## Extracted content\n", 1)[0]
    # THEN the resource is represented by literal source rather than only its encoded destination
    assert (status, receipt["file_ok"]) == (0, True), "available static script source must preserve readable success"
    assert code.rstrip("\r\n") in active, "normal Active content must include decoded literal Unicode script source"
    assert '"mode":"source_only"' in active, "source recovery must explicitly avoid a runtime execution claim"


@pytest.mark.parametrize("label", ["iso-8859-1", "ascii"])
def test_html_whatwg_aliases_preserve_semantic_euro_and_actual_source_spans(label):
    # GIVEN Windows-1252 bytes declared using WHATWG aliases rather than Python codec semantics
    source = ('<meta charset="' + label + '"><script>').encode() + b'\x80</script><a href="/\x80">x</a>'
    context = InspectionContext()
    document = context.document(source, "alias.html", parent=None, location={"kind": "source"})
    # WHEN original source inspection shares the accepted readable HTML decoder
    inspect_html(source, "alias.html", context, document)
    # THEN source text and targets use WHATWG values while raw-byte artifacts remain exact
    scripts = [item for item in context.findings if item["kind"] == "inline_script"]
    links = [item for item in context.findings if item["kind"] == "url_attribute"]
    assert [(item["text"], context.payloads[item["artifact"]]) for item in scripts] == [("€", b"\x80")], (
        "WHATWG alias decoding must recover euro while retaining original script bytes")
    assert [(item["target"], source[item["location"]["byte_start"]:item["location"]["byte_end"]])
            for item in links] == [("/€", b"/\x80")], "semantic URL values must retain measured original spans"


def test_html_charset_meta_inside_script_never_changes_source_decoding():
    # GIVEN UTF-8 HTML whose script text contains a fake charset declaration
    code = 'const tag = \'<meta charset="iso-8859-1">\'; const value = "é";'
    source = ('<script>' + code + '</script><a href="/é">x</a>').encode()
    context = RecordingContext()
    # WHEN charset inspection excludes original raw-text elements
    inspect_html(source, "scope.html", context, "document/1")
    # THEN both script and URL retain their correct original Unicode values
    assert [item[3]["text"] for item in context.findings if item[1] == "inline_script"] == [code], (
        "a meta-looking string inside script must not influence the HTML charset")
    assert [item[3]["target"] for item in context.findings if item[1] == "url_attribute"] == ["/é"], (
        "fake raw-text declarations must not corrupt source targets")


@pytest.mark.parametrize("source,expected", [
    (b'<meta charset="gb18030"><script>\x80\xa8\xbc</script>', "€ḿ"),
    ('<meta charset="iso-2022-jp"><script>日本</script>'.encode("iso2022_jp"), "日本"),
])
def test_nonroundtrip_source_decoding_uses_explicit_decoded_artifact_and_positions(source, expected):
    # GIVEN WHATWG remapped or stateful bytes that cannot use per-character reencoding offsets
    context = InspectionContext()
    document = context.document(source, "mapping.html", parent=None, location={"kind": "source"})
    # WHEN original source decoding recovers script characters under the accepted charset policy
    inspect_html(source, "mapping.html", context, document)
    # THEN original evidence remains retained while source-only positions and artifact bytes stay honest
    scripts = [item for item in context.findings if item["kind"] == "inline_script"]
    assert [(item["text"], context.payloads[item["artifact"]]) for item in scripts] == [
        (expected, expected.encode())], "nonroundtrip scripts must use explicitly decoded UTF-8 artifacts"
    assert scripts[0]["location"]["position_basis"] == "decoded_text", (
        "stateful or remapped text must identify decoded line/column coordinates")
    assert "byte_start" not in scripts[0]["location"], "rewritten encodings must never masquerade as original byte spans"
    assert source in context.payloads.values(), "the complete original byte source must remain retrievable"


def test_html_accepted_legacy_fallback_keeps_original_source_and_recovered_characters():
    # GIVEN bytes that the accepted readable decoder recovers through its legacy fallback
    source = b'<meta charset="utf-8"><p>bad\xff</p>'
    context = RecordingContext()
    # WHEN the original HTML scanner follows the same accepted fallback decision
    inspect_html(source, "fallback.html", context, "document/1")
    # THEN source evidence and recovered characters agree with readable charset semantics
    assert [item[3]["text"] for item in context.findings if item[1] == "source_text"] == ["badÿ"], (
        "accepted legacy fallback must recover the same source characters as readable HTML")
    assert source in context.payloads.values(), "accepted fallback must preserve every original input byte"
    assert context.problems == [], "accepted recoverable fallback must not be mislabeled a decoding refusal"


def test_unmatched_markdown_brackets_use_linear_source_visits():
    # GIVEN many unmatched labels and a source wrapper counting direct character visits
    from brewdoc._inspection_html import _SourceParser, _markdown
    class CountedText(str):
        """Count character visits independently of wall-clock speed."""
        visits = 0
        def __getitem__(self, index):
            self.visits += 1
            return super().__getitem__(index)
    source = CountedText("[" * 1024)
    context = RecordingContext()
    parser = _SourceParser(source, source.encode(), "utf-8", "brackets.md", context, "document/1", None)
    # WHEN passive target inspection processes every unmatched opening bracket
    _markdown(source, parser)
    # THEN character visits remain linear without a machine-dependent wall-clock threshold
    assert source.visits <= len(source) * 12, "unmatched label parsing must not rescan every suffix"


def test_unmatched_markdown_bracket_storage_charges_before_shared_budget_growth():
    # GIVEN a bounded record allowance and many pending unmatched labels
    source = b"[" * 8000
    context = InspectionContext(limits={"records": 100})
    document = context.document(source, "brackets.txt", parent=None, location={"kind": "source"})
    # WHEN pending label state would exceed the shared operation allowance
    inspect_text(source, "brackets.txt", context, document)
    # THEN refusal is explicit and original pass-through evidence survives
    assert [item["limit"] for item in context.exhausted] == ["records"], (
        "pending bracket storage must charge before growth rather than perform unaccounted work")
    assert source in context.payloads.values(), "bounded label inspection must preserve all original source bytes"


def test_email_autolink_preserves_original_address_span_and_mailto_destination():
    # GIVEN a native Markdown email autolink after multibyte text and CRLF
    source = 'é\r\n<contact@example.invalid>'.encode()
    context = InspectionContext()
    document = context.document(source, "mail.md", parent=None, location={"kind": "source"})
    # WHEN CommonMark email autolinks are inspected
    inspect_text(source, "mail.md", context, document)
    # THEN the logical destination and exact original address remain distinct evidence
    links = [item for item in context.findings if item["kind"] == "email_autolink"]
    assert [(item["target"], item["details"]["source_address"], item["location"]["byte_start"],
             item["location"]["byte_end"], item["location"]["line"], item["location"]["column"])
            for item in links] == [("mailto:contact@example.invalid", "contact@example.invalid", 5, 28, 2, 2)], (
        "email autolinks must preserve their original span and mailto destination without source rewriting")


@pytest.mark.parametrize("source", ['`<contact@example.invalid>`', '```\n<contact@example.invalid>\n```',
                                    '    <contact@example.invalid>', '\\<contact@example.invalid>',
                                    '<span title="<contact@example.invalid>">text</span>',
                                    '<div>\n<contact@example.invalid>\n</div>', '<a@-bad.invalid>'])
def test_email_autolinks_do_not_appear_inside_code_or_raw_markup(source):
    # GIVEN source spelling that CommonMark does not recognize as an email autolink
    context = RecordingContext()
    # WHEN source targets are inspected
    inspect_text(source.encode(), "literal.md", context, "document/1")
    # THEN literal code, raw markup, escapes and invalid domains remain inert
    assert [item for item in context.findings if item[1] == "email_autolink"] == [], (
        "non-autolink source grammar must not invent mailto destinations")


@pytest.mark.parametrize("encoded,payload", [("YWxlcnQoMSk=", b"alert(1)"), ("YWxlcnQoMSk", b"alert(1)"),
                                            ("YWxlcnQo%0AMSk=", b"alert(1)"), ("YR==", b"a"),
                                            ("%09YQ%0C%0D%20", b"a")])
def test_data_url_forgiving_base64_preserves_exact_available_script_bytes(encoded, payload):
    # GIVEN valid Fetch/Infra forgiving-base64 forms of original inline script bytes
    url = "data:application/javascript;base64," + encoded
    source = ('<script src="' + url + '"></script>').encode()
    context = InspectionContext()
    document = context.document(source, "forgiving.html", parent=None, location={"kind": "source"})
    # WHEN available source is decoded passively under the shared allowance
    inspect_html(source, "forgiving.html", context, document)
    # THEN padding, ASCII whitespace and unused pad bits do not erase recoverable code
    scripts = [item for item in context.findings if item["kind"] == "inline_data_script"]
    assert [(item["text"], context.payloads[item["artifact"]]) for item in scripts] == [(payload.decode(), payload)], (
        "forgiving-base64 source must round trip to exact script bytes")
    assert [(item["byte_size"], item["sha256"]) for item in context.artifacts
            if item["kind"] == "data_url_payload"] == [(len(payload), hashlib.sha256(payload).hexdigest())], (
        "the payload artifact must expose actual decoded size and hash")
    assert any(item["target"] == url for item in context.findings), "the original encoded target must remain exact"


@pytest.mark.parametrize("encoded", ["A", "AQ=", "YWxl=cnQoMSk=", "YWxlcnQoMSk===", "YQ%0B==", "YQ%C2%A0==", "YQ?="])
def test_data_url_forgiving_base64_still_rejects_invalid_alphabet_and_padding(encoded):
    # GIVEN genuinely invalid alphabet, length, padding or non-ASCII whitespace
    source = ('<script src="data:application/javascript;base64,' + encoded + '"></script>').encode()
    context = InspectionContext()
    document = context.document(source, "invalid.html", parent=None, location={"kind": "source"})
    # WHEN forgiving decoding validates the remaining alphabet and padding
    inspect_html(source, "invalid.html", context, document)
    # THEN malformed input remains explicit instead of becoming empty successful source
    assert [item for item in context.findings if item["kind"] == "inline_data_script"] == [], (
        "invalid encodings must not invent decoded script source")
    assert context.coverage[(document, "scripts")]["status"] == "partial", (
        "invalid base64 source must prevent complete scripts coverage")


def test_many_text_targets_never_copy_preceding_source_prefixes():
    # GIVEN repeated URLs and a source wrapper that measures actual copied prefix characters
    from brewdoc._inspection_html import _SourceParser, _markdown
    class CountedPrefixes(str):
        """Count bytes-independent source copies made by omitted-start slices."""
        copied_characters = 0
        def __getitem__(self, index):
            result = super().__getitem__(index)
            self.copied_characters += len(result) * (isinstance(index, slice) and index.start is None)
            return result
    target, count = "https://same.invalid/x", 512
    source = CountedPrefixes((target + "\n") * count)
    raw = source.encode()
    context = InspectionContext()
    document = context.document(raw, "targets.txt", parent=None, location={"kind": "source"})
    parser = _SourceParser(source, raw, "utf-8", "targets.txt", context, document, None)
    assert (len(source), source.count(target)) == (11776, 512), "the source must contain the frozen repeated URL workload"
    # WHEN every original target receives text-specific coordinates
    _markdown(source, parser)
    # THEN full target/location evidence stays exact without copying earlier source per finding
    assert [(item["target"], item["location"]) for item in context.findings] == [
        (target, {"kind": "text", "part": "targets.txt", "line": index + 1, "column": 1,
                  "byte_start": index * 23, "byte_end": index * 23 + 22})
        for index in range(count)
    ], "every target, original span, line and column must retain the frozen source coordinates"
    assert context.exhausted == [], "the linear successful workload must not require a refusal or new allowance"
    assert source.copied_characters == 0, "location lookup must reuse admitted line starts without copying source prefixes"


@pytest.mark.parametrize("text,spans", [
    ("\ufeffé\r\nhttps://one.invalid/x\rhttps://two.invalid/y\nhttps://three.invalid/z", [
        (7, 28, 2, 1), (29, 50, 2, 23), (51, 74, 3, 1)]),
    ("https://one.invalid/x\nhttps://two.invalid/y\r\nhttps://three.invalid/z", [
        (0, 21, 1, 1), (22, 43, 2, 1), (45, 68, 3, 1)]),
])
def test_text_location_index_preserves_frozen_unicode_bom_and_newline_coordinates(text, spans):
    # GIVEN original UTF-8/BOM sources with CR, LF and CRLF at frozen target boundaries
    context = RecordingContext()
    # WHEN original text target positions use the shared admitted line index
    inspect_text(text.encode(), "newlines.txt", context, "document/1")
    # THEN all byte spans and decoded line/column fields preserve the established coordinate contract
    targets = [item for item in context.findings if item[1] == "text_url"]
    assert [(item[2]["byte_start"], item[2]["byte_end"], item[2]["line"], item[2]["column"])
            for item in targets] == spans, "line-index lookup must preserve the exact original newline and UTF-8 accounting"
