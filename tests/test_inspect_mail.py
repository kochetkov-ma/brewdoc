"""Static MIME inspection retains alternatives, occurrences and exact bytes."""

import base64
import hashlib
import io
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest

from brewdoc._inspection_common import InspectionContext
from brewdoc._inspection_html import inspect_plain_targets
from brewdoc._inspection_mail import inspect_mail


def _attachment_cases():
    """Build native structure witnesses for every supported attachment MIME type."""
    from test_extraction_regressions import _image_button_pdf

    def package(members):
        """Create exact in-memory ZIP bytes with declared format members."""
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as archive:
            for name, data in members.items():
                archive.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
        return result.getvalue()

    return [
        ("application/pdf", ".pdf", _image_button_pdf()[0]),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx",
         package({"word/document.xml": b"<document/>"})),
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx",
         package({"ppt/presentation.xml": b"<presentation/>"})),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx",
         package({"xl/workbook.xml": b"<workbook/>"})),
        ("application/vnd.ms-excel.sheet.macroEnabled.12", ".xlsm",
         package({"xl/workbook.xml": b"<workbook/>"})),
        ("application/vnd.ms-excel.sheet.binary.macroEnabled.12", ".xlsb",
         package({"xl/workbook.bin": b"\x83\x01\x00"})),
        ("application/vnd.ms-excel", ".xls",
         (Path(__file__).parent / "fixtures/xls/calamine-xls_formula.xls").read_bytes()),
        ("application/vnd.oasis.opendocument.spreadsheet", ".ods",
         package({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet"})),
        ("application/vnd.oasis.opendocument.text", ".odt",
         package({"mimetype": b"application/vnd.oasis.opendocument.text"})),
        ("application/vnd.oasis.opendocument.presentation", ".odp",
         package({"mimetype": b"application/vnd.oasis.opendocument.presentation"})),
        ("application/epub+zip", ".epub", package({"META-INF/container.xml": b"<container/>"})),
        ("message/rfc822", ".eml", b"Subject: Inner\r\n\r\nbody"),
        ("text/html", ".html", b"<html><script>literalOnly()</script></html>"),
        ("text/markdown", ".md", b"# exact\r\n[link](https://attachment.invalid/exact)"),
        ("text/plain", ".txt", b"literal source\r\nhttps://attachment.invalid/exact"),
    ]


@pytest.mark.parametrize("filename,disposition", [(None, "attachment"),
                                                ("misleading.bin", 'attachment; filename="misleading.bin"')])
@pytest.mark.parametrize("media,suffix,payload", [pytest.param(*case, id=case[1]) for case in _attachment_cases()])
def test_supported_attachment_types_recurse_without_relying_on_filename(media, suffix, payload, filename, disposition):
    # GIVEN a supported source structure with missing or misleading filename metadata
    message = EmailMessage()
    maintype, subtype = media.split("/", 1)
    message.set_content(payload, maintype=maintype, subtype=subtype, cte="base64")
    message["Content-Disposition"] = disposition
    message.set_param("x-source", "retained")
    message.set_param("charset", "utf-8")
    source = message.as_bytes()
    assert message.get_payload(decode=True) == payload, "the transfer-decoded source must match original attachment bytes"
    # WHEN MIME metadata and bounded native structure select the recursive format
    context, html, embedded = inspect(source)
    expected_name = (filename or "mime-1") + suffix
    # THEN one recursive call preserves bytes, location and original filename metadata
    assert [(item[0], item[1], item[2]["mime_path"]) for item in embedded] == [
        (payload, expected_name, "1"),
    ], "every supported MIME attachment must recurse once with a validated source suffix"
    part = next(item for item in context.components if item["kind"] == "mime-part")
    assert (part["details"]["filename"], part["details"]["name_basis"], part["sha256"],
            context.payloads[part["artifact"]]) == (
        filename, "mime+structure", hashlib.sha256(payload).hexdigest(), payload,
    ), "synthetic dispatch names must not replace original filename or source identity"
    assert html == [], "HTML attachments must use one child inspection instead of duplicate full scans"


@pytest.mark.parametrize("media,payload", [
    ("application/pdf", b"%PDF-1.7\ntruncated"),
    ("application/pdf", b"ordinary non-PDF bytes"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK\x03\x04truncated"),
    ("application/vnd.ms-excel", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    ("message/rfc822", b"no message header or separator"),
])
def test_supported_mime_type_without_matching_structure_is_explicitly_unreadable(media, payload):
    # GIVEN a supported MIME claim with mismatching or truncated structure
    source = ("Content-Type: %s\r\nContent-Disposition: attachment; filename=bad.bin\r\n"
              "Content-Transfer-Encoding: base64\r\n\r\n" % media).encode() + base64.b64encode(payload)
    assert base64.b64decode(source.split(b"\r\n\r\n")[1]) == payload, "malformed attachment bytes must be exact"
    # WHEN signature validation rejects the claimed supported source
    context, html, embedded = inspect(source)
    # THEN retrieval survives while incomplete coverage prevents a false opaque success
    parts = [item for item in context.components if item["kind"] == "mime-part"]
    assert [(item["disposition"], context.payloads[item["artifact"]]) for item in parts] == [
        ("unreadable", payload),
    ], "malformed supported attachments must retain exact payload and unreadable state"
    assert context.coverage[("document/000001", "attachments")]["status"] == "partial", "failed supported structure must remain incomplete"
    assert (html, embedded) == ([], []), "unsupported structure must not reach a scanner under a guessed suffix"


@pytest.mark.parametrize("filename", [None, "misleading.bin"])
def test_public_unnamed_pdf_attachment_recovers_original_action_target(tmp_path, filename):
    from brewdoc import inspect_document, read_document_artifact
    from test_extraction_regressions import _image_button_pdf

    # GIVEN a real PDF action in an attachment with no filename
    payload, target = _image_button_pdf()
    message = EmailMessage()
    message.set_content("Harmless body")
    message.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    source = tmp_path / "unnamed.eml"
    source.write_bytes(message.as_bytes())
    assert message.get_payload()[1].get_filename() == filename, "the regression attachment must preserve its missing or misleading filename"
    # WHEN independent public inspection visits the MIME payload
    report = inspect_document(source)
    # THEN the exact original PDF action and attachment bytes survive recursive inspection
    assert sorted({(item["target"], item["document"], item["location"]["annotation_ordinal"])
                   for item in report["findings"] if item["target"] == target
                   and item["location"].get("annotation_ordinal") == 1}) == [
        (target, "document/000002", 1),
    ], "a filename-free PDF attachment must retain its native action target"
    parts = [item for item in report["components"] if item["kind"] == "mime-part"
             and item["details"].get("media_type") == "application/pdf"]
    assert [(item["details"]["filename"], item["location"]["mime_path"],
             read_document_artifact(source, item["artifact"])) for item in parts] == [
        (filename, "1.2", payload),
    ], "format recognition must preserve missing source filename and exact decoded source bytes"


def test_public_html_attachment_is_inspected_once_with_original_charset_bytes(tmp_path):
    from brewdoc import inspect_document, read_document_artifact

    # GIVEN an HTML attachment without a filename and with a MIME-only legacy charset
    script = "attachmentProof('café');"
    payload = ("<script>" + script + "</script>").encode("cp1252")
    message = EmailMessage()
    message.set_content("Harmless body")
    message.add_attachment(payload, maintype="text", subtype="html")
    message.get_payload()[1].set_param("charset", "windows-1252")
    source = tmp_path / "html-attachment.eml"
    source.write_bytes(message.as_bytes())
    assert message.get_payload()[1].get_payload(decode=True) == payload, "the original HTML bytes must retain their declared legacy encoding"
    # WHEN only the supported child document owns full HTML inspection
    report = inspect_document(source)
    scripts = [item for item in report["findings"] if item["kind"] == "inline_script"]
    # THEN one exact script finding binds the original payload bytes to its child and MIME path
    assert [(item["text"], item["document"], item["location"]["mime_path"],
             read_document_artifact(source, item["artifact"])) for item in scripts] == [
        (script, "document/000002", "1.2", script.encode("cp1252")),
    ], "HTML attachments must preserve declared charset and avoid duplicate parent/child parsing"


def test_binary_mime_attachment_uses_recognized_pdf_structure_without_type_guessing():
    from test_extraction_regressions import _image_button_pdf

    # GIVEN unknown MIME metadata around a validated supported PDF source
    payload, _ = _image_button_pdf()
    source = (b"Content-Type: application/octet-stream\r\n"
              b"Content-Disposition: attachment; filename=original.bin\r\n"
              b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.b64encode(payload))
    assert payload.startswith(b"%PDF-1.4"), "supported native source identity must come from actual bytes"
    # WHEN the bounded native signature selects an existing supported format
    context, _, embedded = inspect(source)
    part = next(item for item in context.components if item["kind"] == "mime-part")
    # THEN dispatch suffixes stay separate from original binary MIME metadata
    assert [(item[0], item[1]) for item in embedded] == [(payload, "original.bin.pdf")], "known source structure must not remain opaque solely due to binary metadata"
    assert (part["details"]["filename"], part["details"]["media_type"], part["details"]["name_basis"]) == (
        "original.bin", "application/octet-stream", "structure",
    ), "inferred format evidence must preserve every original metadata value"


def inspect(source, *, limits=None):
    """Capture injected scanner calls with the real shared accounting context."""
    context = InspectionContext(limits=limits)
    document = context.document(source, "sample.eml", parent=None, location={"kind": "source"})
    html, embedded = [], []

    def scan_html(data, name, context, document, *, location):
        """Capture exact admitted HTML input without execution."""
        html.append((data, name, location))

    def scan_embedded(data, name, *, parent, location, component):
        """Capture recursive input without opening documents or resources."""
        embedded.append((data, name, location, component))
        return "document/nested"

    inspect_mail(source, "sample.eml", context, document,
                 scan_html=scan_html, scan_embedded=scan_embedded,
                 scan_text=inspect_plain_targets)
    return context, html, embedded


def test_all_alternatives_headers_and_inline_bytes_are_preserved():
    # GIVEN duplicate headers and a hidden HTML alternative beside an inline image
    source = (b"Subject: =?utf-8?b?SGVsbG8=?=\r\nX-Test: first\r\nX-Test: second\r\n"
              b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
              b"--b\r\nContent-Type: text/plain\r\n\r\nharmless\r\n"
              b"--b\r\nContent-Type: text/html\r\n\r\n<script>neverRun()</script>\r\n"
              b"--b\r\nContent-Type: image/png\r\nContent-ID: <inline>\r\n"
              b"Content-Transfer-Encoding: base64\r\n\r\nAP8=\r\n--b--\r\n")
    assert base64.b64decode(b"AP8=") == b"\x00\xff", "the source image must decode exactly"
    # WHEN every MIME occurrence is inspected independently of body preference
    context, html, embedded = inspect(source)
    headers = [(item["location"]["header_ordinal"], item["text"])
               for item in context.findings if item["kind"] == "mime-header"
               and item["location"]["mime_path"] == "1"]
    # THEN duplicate order, decoded headers, HTML callbacks and exact image bytes survive
    assert headers == [(1, "Hello"), (2, "first"), (3, "second"),
                       (4, "multipart/mixed; boundary=b")], "all root header occurrences must remain ordered"
    assert html == [(b"<script>neverRun()</script>", "mime-1.2.html",
                     {"kind": "mime", "mime_path": "1.2", "content_id": None, "charset": "ascii"})], "unselected HTML must be scanned"
    images = [item for item in context.components if item["kind"] == "mime-part"
              and item["details"].get("media_type") == "image/png"]
    assert [(item["location"]["content_id"], context.payloads[item["artifact"]])
            for item in images] == [("<inline>", b"\x00\xff")], "inline decoded bytes must round trip"
    assert embedded == [], "unknown images must remain opaque without invented recursive inspection"


@pytest.mark.parametrize("media", ["message/rfc822", "message/global"])
def test_nested_messages_and_supported_attachments_recurse_with_exact_bytes(media):
    # GIVEN an attached message encoded as base64
    nested = b"Subject: Inner\r\nContent-Type: text/plain\r\n\r\ninner body"
    source = ("Content-Type: %s\r\nContent-Transfer-Encoding: base64\r\n\r\n" % media).encode() + base64.b64encode(nested)
    assert base64.b64decode(source.split(b"\r\n\r\n")[1]) == nested, "nested bytes must be present"
    # WHEN the message payload is decoded and dispatched
    context, html, embedded = inspect(source)
    # THEN recursion receives the exact source bytes and retained component identity
    assert [(item[0], item[1], item[2]["mime_path"]) for item in embedded] == [
        (nested, "mime-1.eml", "1")], "both nested message types must use the embedded scanner"
    assert context.payloads[context.components[0]["artifact"]] == nested, "nested message bytes must round trip"
    assert html == [], "plain nested messages require no outer HTML scan"


@pytest.mark.parametrize("headers,body", [
    (b"Content-Transfer-Encoding: base64", b"@@@@"),
    (b"Content-Transfer-Encoding: quoted-printable", b"bad=ZZ"),
    (b"Content-Type: text/plain; charset=utf-8", b"\xff"),
    (b"Subject: =?utf-8?b?@@@@?=", b"body"),
    (b"Content-Type: multipart/mixed; boundary=missing", b"no boundaries"),
])
def test_malformed_content_retains_raw_source_and_sticky_failure(headers, body):
    # GIVEN malformed transfer, charset, header or multipart syntax
    source = headers + b"\r\n\r\n" + body
    assert source.endswith(body), "malformed bytes must remain in the source"
    # WHEN static inspection refuses the malformed scope
    context, html, embedded = inspect(source)
    # THEN actual raw bytes remain retrievable and success cannot replace the failure
    assert source in context.payloads.values(), "raw failed input must remain retrievable"
    assert context.coverage[("document/000001", "text")]["status"] in (
        "partial", "unavailable"), "malformed content must never report complete text inspection"
    assert (html, embedded) == ([], []), "undecodable content must not be sent to consumers"


def test_decoding_budget_is_checked_before_payload_growth():
    # GIVEN decoded bytes exceeding the shared remaining allowance
    source = b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.b64encode(b"12345")
    assert len(base64.b64decode(source.split(b"\r\n\r\n")[1])) == 5, "the payload must exceed the allowance"
    # WHEN a four-byte decoding budget applies
    context, html, embedded = inspect(source, limits={"decoded_bytes": 4})
    # THEN decoding is refused before bytes are admitted
    assert context.consumed["decoded_bytes"] == 0, "refused decoding must not consume decoded output"
    assert [item["limit"] for item in context.exhausted] == ["decoded_bytes"], "the exhausted budget must be named"
    assert (html, embedded) == ([], []), "refused bytes must never reach nested scanners"


def test_bad_html_charset_does_not_skip_static_html_callback():
    # GIVEN an HTML part with an invalid charset and literal active content
    body = b"<script>neverRun()</script>\xff"
    source = (b"Content-Type: text/html; charset=unknown-charset\r\n"
              b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body)
    assert source.endswith(body), "HTML source must contain the invalid charset bytes"
    # WHEN text decoding fails but static HTML inspection can still consume raw bytes
    context, html, embedded = inspect(source)
    # THEN the callback receives original bytes and text coverage remains incomplete
    assert [item[0] for item in html] == [body], "charset failure must not suppress HTML inspection"
    assert context.coverage[("document/000001", "text")]["status"] == "partial", "decoding failure must stay sticky"
    assert embedded == [], "a body alternative must not become an invented attachment"


def test_supported_attachment_payload_and_report_are_deterministic():
    # GIVEN an inline supported attachment with an exact transfer-encoded payload
    body = b"# title\r\n[link](javascript:neverRun())\r\n"
    source = (b"Content-Type: text/markdown; charset=utf-8\r\n"
              b"Content-Disposition: inline; filename=note.md\r\n"
              b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.b64encode(body))
    assert base64.b64decode(source.split(b"\r\n\r\n")[1]) == body, "the source attachment must be exact"
    # WHEN the same source is inspected twice
    first, html, embedded = inspect(source)
    second, _, _ = inspect(source)
    # THEN decoded attachment bytes, callback input and all evidence remain identical
    assert [(item[0], item[1]) for item in embedded] == [(body, "note.md")], "supported attachments must recurse"
    assert (first.components, first.findings, first.coverage, first.payloads) == (
        second.components, second.findings, second.coverage, second.payloads,
    ), "identical bytes must produce identical evidence and artifact keys"
    assert html == [], "Markdown remains inert and requires no HTML execution"


def test_mime_depth_budget_preserves_known_raw_source():
    # GIVEN a multipart child exceeding the shared MIME depth allowance
    source = (b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
              b"--b\r\nContent-Type: text/plain\r\n\r\nchild\r\n--b--\r\n")
    assert source.count(b"--b\r\n") == 1, "the source must contain exactly one nested child"
    # WHEN only the root MIME depth is allowed
    context, html, embedded = inspect(source, limits={"nesting": 0})
    # THEN the depth failure identifies the child and retains actual original bytes
    assert [(item["limit"], item["location"]["mime_path"]) for item in context.exhausted] == [
        ("nesting", "1.1")], "the exhausted depth must locate its original MIME occurrence"
    assert source in context.payloads.values(), "depth refusal must preserve source retrieval"
    assert (html, embedded) == ([], []), "unadmitted nested content must not be dispatched"


def test_utf8_global_headers_preserve_raw_and_decoded_occurrences():
    # GIVEN an international header with exact UTF-8 bytes
    source = "Subject: café\r\nContent-Type: text/plain\r\n\r\nbody".encode("utf-8")
    assert source.startswith(b"Subject: caf\xc3\xa9\r\n"), "the source header must use UTF-8"
    # WHEN the header is inspected without address normalization
    context, _, _ = inspect(source)
    subject = context.findings[0]
    # THEN decoded Unicode and a reversible raw byte representation both survive
    assert (subject["text"], subject["details"]["raw"].encode("latin1")) == (
        "café", b"caf\xc3\xa9",
    ), "international headers must retain both exact raw bytes and decoded text"


def test_legacy_html_charset_is_forwarded_with_original_bytes():
    # GIVEN valid legacy HTML with no in-document charset declaration
    body = "<script>show('café');</script>".encode("cp1252")
    source = (b"Content-Type: text/html; charset=windows-1252\r\n"
              b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body)
    assert body.decode("cp1252") == "<script>show('café');</script>", "the source must contain exact legacy script text"
    # WHEN the MIME reader delegates to static HTML inspection
    context, html, _ = inspect(source)
    # THEN the original bytes and authoritative MIME charset travel together
    assert html == [(body, "mime-1.html", {"kind": "mime", "mime_path": "1",
                                          "content_id": None, "charset": "windows-1252"})], "HTML scanning must use MIME charset without rewriting source bytes"
    assert context.payloads[context.components[0]["artifact"]] == body, "legacy payload artifacts must remain byte-exact"


def test_unknown_attachment_remains_opaque_with_complete_mime_coverage():
    # GIVEN a binary attachment outside supported document formats
    body = b"\x00\xffopaque\x00"
    source = (b"Content-Type: application/octet-stream\r\nContent-ID: <resource>\r\n"
              b"Content-Disposition: attachment; filename=payload.bin\r\n"
              b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.b64encode(body))
    assert base64.b64decode(source.split(b"\r\n\r\n")[1]) == body, "opaque source bytes must be recoverable"
    # WHEN MIME structure is inspected without inventing a decoder for unknown content
    context, html, embedded = inspect(source)
    attachments = [item for item in context.findings if item["kind"] == "mime-attachment"]
    # THEN original metadata and exact bytes remain visible without failing native MIME coverage
    assert [(item["text"], item["location"]["content_id"], item["details"],
             context.payloads[item["artifact"]]) for item in attachments] == [
        ("payload.bin", "<resource>", {"filename": "payload.bin", "content_disposition": "attachment",
         "disposition": "opaque", "media_type": "application/octet-stream", "byte_size": len(body),
         "sha256": hashlib.sha256(body).hexdigest()}, body),
    ], "every opaque attachment must expose original metadata, hash and exact retrievable bytes"
    assert [context.coverage[("document/000001", capability)]["status"]
            for capability in ("inventory", "text", "links", "scripts", "attachments")] == [
        "complete", "complete", "complete", "complete", "complete",
    ], "unsupported attachment internals must not be conflated with native MIME inspection"
    assert (html, embedded) == ([], []), "unsupported attachment bytes must remain inert and opaque"


def test_encrypted_mime_stays_incomplete_and_retains_opaque_attachment():
    # GIVEN a recognized encrypted MIME payload
    body = b"encrypted-content"
    source = b"Content-Type: application/pkcs7-mime; smime-type=enveloped-data\r\n\r\n" + body
    assert source.endswith(body), "the encrypted source bytes must be present"
    # WHEN only the outer MIME structure can be inspected
    context, html, embedded = inspect(source)
    # THEN opaque attachment retention cannot erase encryption failure
    assert context.coverage[("document/000001", "text")]["status"] in (
        "partial", "unavailable"), "encrypted native content must not report complete inspection"
    assert [context.payloads[item["artifact"]] for item in context.findings
            if item["kind"] == "mime-attachment"] == [body], "encrypted bytes must remain retrievable"
    assert (html, embedded) == ([], []), "encrypted content must not be sent to plaintext scanners"


def test_legacy_html_real_scanner_preserves_script_bytes_and_mime_provenance():
    from brewdoc._inspection_html import inspect_html

    # GIVEN a MIME-declared legacy encoding without a conflicting HTML declaration
    script = "show('café');".encode("cp1252")
    body = b"<script>" + script + b"</script>"
    source = (b"Content-Type: text/html; charset=windows-1252\r\n"
              b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body)
    context = InspectionContext()
    document = context.document(source, "legacy.eml", parent=None, location={"kind": "source"})
    assert body[8:-9] == script, "the script byte span must belong to the original legacy HTML"
    # WHEN MIME dispatch invokes the real static HTML scanner
    inspect_mail(source, "legacy.eml", context, document,
                 scan_html=inspect_html, scan_embedded=None)
    scripts = [item for item in context.findings if item["kind"] == "inline_script"]
    # THEN decoding, literal extraction and byte provenance agree with the original payload
    assert [(item["text"], context.payloads[item["artifact"]], item["location"]["mime_path"],
             item["location"]["byte_start"], item["location"]["byte_end"]) for item in scripts] == [
        ("show('café');", script, "1", 8, len(body) - 9),
    ], "legacy script evidence must preserve exact bytes and original MIME-relative spans"
    assert context.coverage[(document, "scripts")]["status"] == "complete", "valid MIME-declared encoding must complete script coverage"


def test_plain_targets_keep_original_payload_byte_positions_and_literal_markup():
    # GIVEN plain MIME text with different target syntaxes and literal HTML-looking code
    targets = ["https://auto.invalid/path?q=1#part", "javascript:neverRun()", "https://free.invalid/value"]
    body = ("é\r\n<" + targets[0] + ">\r\n[go](" + targets[1] + ")\r\n"
            + targets[2] + "\r\n<script>literalOnly()</script>").encode()
    source = (b"Content-Type: text/plain; charset=utf-8\r\n"
              b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body)
    assert body.count(b"<script>") == 1, "the HTML-looking source must remain literal plain text"
    # WHEN only passive plain target extraction runs
    context, html, embedded = inspect(source)
    links = sorted((item for item in context.findings if item["capability"] == "links"),
                   key=lambda item: item["location"]["byte_start"])
    # THEN destinations and positions refer to original decoded payload bytes without active scripts
    assert [(item["target"], item["location"]["mime_path"], item["location"]["byte_start"],
             item["location"]["byte_end"], context.payloads[item["location"]["payload_artifact"]])
            for item in links] == [(target, "1", body.index(target.encode()),
                                     body.index(target.encode()) + len(target.encode()), body)
                                    for target in targets], "plain targets must preserve exact MIME payload positions"
    assert [item for item in context.findings if item["capability"] == "scripts"] == [], "literal plain markup must not become active content"
    assert (html, embedded) == ([], []), "plain text must not be dispatched as HTML or an invented attachment"


def test_nested_multipart_supplements_keep_exact_original_spans_and_legacy_bytes():
    # GIVEN nested multipart containers with recoverable text outside every boundary
    source = (b"Content-Type: multipart/mixed; boundary=outer; charset=windows-1252\r\n"
              b"Content-Transfer-Encoding: 8bit\r\n\r\npr\xe9\r\n--outer\r\n"
              b"Content-Type: multipart/alternative; boundary=inner\r\n\r\ninner pre\r\n"
              b"--inner\r\nContent-Type: text/plain\r\n\r\nbody\r\n--inner--\r\ninner tail\r\n"
              b"--outer--\r\nfin\xe9\r\n")
    expected = [("1", "preamble", b"pr\xe9\r\n", "pré\r\n"),
                ("1.1", "preamble", b"inner pre\r\n", "inner pre\r\n"),
                ("1.1", "epilogue", b"inner tail", "inner tail"),
                ("1", "epilogue", b"fin\xe9\r\n", "finé\r\n")]
    assert [source.count(raw) for _, _, raw, _ in expected] == [1, 1, 1, 1], "all supplementary source spans must be unique"
    # WHEN every original multipart occurrence is inspected
    context, html, embedded = inspect(source)
    supplements = [item for item in context.findings if item["kind"] in ("mime-preamble", "mime-epilogue")]
    # THEN recoverable text and exact artifacts bind to original whole-message byte positions
    assert [(item["location"]["mime_path"], item["location"]["mime_region"],
             context.payloads[item["artifact"]], item["text"], item["location"]["byte_start"],
             item["location"]["byte_end"]) for item in supplements] == [
        (path, region, raw, text, source.index(raw), source.index(raw) + len(raw))
        for path, region, raw, text in expected
    ], "all multipart supplementary text must retain exact original spans and bytes"
    assert (html, embedded) == ([], []), "supplementary plain text must remain passive"


def test_undecodable_preamble_retains_exact_bytes_and_sticky_text_failure():
    # GIVEN non-ASCII multipart preamble bytes without a declared charset
    source = (b"Content-Type: multipart/mixed; boundary=b\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
              b"\xff\r\n--b\r\nContent-Type: text/plain\r\n\r\nbody\r\n--b--\r\n")
    assert b"\xff\r\n--b" in source, "invalid ASCII preamble bytes must be present"
    # WHEN strict default MIME charset interpretation fails
    context, _, _ = inspect(source)
    failed = [item for item in context.components if item["kind"] == "mime-preamble"]
    # THEN the reader retains bytes without guessing a replacement charset
    assert [(item["disposition"], context.payloads[item["artifact"]]) for item in failed] == [
        ("unreadable", b"\xff\r\n")], "undecodable supplementary bytes must remain exact and explicitly unreadable"
    assert context.coverage[("document/000001", "text")]["status"] == "partial", "later valid child text must not erase preamble failure"
