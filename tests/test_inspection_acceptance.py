"""Public inspection evidence across every supported extension and real boundaries."""

import base64
import hashlib
import io
import json
import os
import re
import socket
import struct
import subprocess
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest

from brewdoc import inspect_document, read_document_artifact, run
from test_documents import document, paragraph
from test_extraction_regressions import _image_button_pdf
from test_inspect_workbook import _ods


FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "https://acceptance.invalid/exact?query=1#fragment"
SCRIPT = "staticAcceptanceProof('literal source');"
NESTED = ("[nested](" + TARGET + ")\r\n").encode()
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _archive(entries):
    """Keep original member bytes and deterministic archive occurrence order."""
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return result.getvalue()


def _mail():
    message = EmailMessage()
    message["Subject"] = "Public inspection source"
    message.set_content("Harmless body")
    message.add_alternative("<script>" + SCRIPT + "</script>", subtype="html")
    return message.as_bytes()


SYNTHETIC = [
    (".pdf", _image_button_pdf()[0], "target", _image_button_pdf()[1]),
    (".docx", _archive([("word/document.xml", document(paragraph("Office source")))]),
     "text", "Office source"),
    (".pptx", _archive([("ppt/slides/slide1.xml",
                         '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                         '<p:spTree><a:p><a:r><a:t>Office source</a:t></a:r></a:p></p:spTree></p:sld>')]),
     "text", "Office source"),
    (".ods", _ods('<table:table-row><table:table-cell office:value-type="string">'
                  '<text:p>Office source</text:p></table:table-cell></table:table-row>'),
     "cell", "Office source"),
    (".odt", _archive([("content.xml", '<office:document-content xmlns:office="%s" '
                        'xmlns:text="%s"><office:body><office:text><text:p>Office source</text:p>'
                        '</office:text></office:body></office:document-content>' % (OFFICE, TEXT))]),
     "text", "Office source"),
    (".odp", _archive([("content.xml", '<office:document-content xmlns:office="%s" '
                        'xmlns:text="%s"><office:body><office:presentation><text:p>Office source</text:p>'
                        '</office:presentation></office:body></office:document-content>' % (OFFICE, TEXT))]),
     "text", "Office source"),
    (".html", ('<div hidden><script>' + SCRIPT + '</script><a href="' + TARGET + '">link</a></div>').encode(),
     "script", SCRIPT),
    (".epub", _archive([("mimetype", "application/epub+zip"),
                         ("unused.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><script>'
                          + SCRIPT + '</script></html>')]), "script", SCRIPT),
    (".eml", _mail(), "script", SCRIPT),
    (".md", ("\ufeff[original](" + TARGET + ")\r\n").encode(), "target", TARGET),
    (".txt", ("\ufeff<" + TARGET + ">\r\n").encode(), "target", TARGET),
]


@pytest.mark.parametrize("suffix,payload,field,expected", SYNTHETIC, ids=[row[0] for row in SYNTHETIC])
def test_public_inspection_retains_native_positive_evidence_for_each_static_format(
        tmp_path, suffix, payload, field, expected):
    # GIVEN original native source bytes carrying an exact recoverable value or destination
    source = tmp_path / ("positive" + suffix)
    source.write_bytes(payload)
    assert source.read_bytes() == payload, "the source identity must be independent of rendering"
    # WHEN callers inspect and retrieve the deterministic report through public APIs
    report = inspect_document(source)
    saved = json.loads(read_document_artifact(source, "inspection/report"))
    observed = {
        "target": {item["target"] for item in report["findings"] if item["capability"] == "links"},
        "text": {item["text"] for item in report["findings"] if item["capability"] == "text"},
        "script": {item["text"] for item in report["findings"] if item["capability"] == "scripts"},
        "cell": {item["details"].get("value") for item in report["findings"] if item["kind"] == "cell"},
    }[field]
    # THEN a real positive finding, exact source hash and repeatable report survive every route
    assert (sorted(value for value in observed if value == expected), report["source"]["suffix"],
            report["source"]["sha256"], saved) == (
        [expected], suffix, hashlib.sha256(payload).hexdigest(), report,
    ), "public inspection must preserve native evidence and exact deterministic JSON for " + field


@pytest.mark.corpus
@pytest.mark.parametrize("relative,kind,field,expected", [
    ("xls/calamine-xls_formula.xls", "cell", "formula", "(A1*2)"),
    ("xlsb/calamine-issues.xlsb", "cell", "value", "test"),
    ("xlsm/calamine-vba.xlsm", "vba_module", "module_name", "testVBA"),
])
def test_public_inspection_retains_native_binary_workbook_evidence(relative, kind, field, expected):
    # GIVEN an existing licensed workbook whose binary records or VBA carry meaningful content
    source = FIXTURES / relative
    original = source.read_bytes()
    assert len(original) == source.stat().st_size, "licensed fixture bytes must be present unchanged"
    # WHEN independent public inspection reads native records rather than cached Markdown
    report = inspect_document(source)
    actual = [item["details"].get(field) for item in report["findings"] if item["kind"] == kind]
    # THEN the expected native formula, addressed value or module identity is exactly preserved
    assert (actual.count(expected), report["source"]["sha256"], inspect_document(source)) == (
        1, hashlib.sha256(original).hexdigest(), report,
    ), "native XLS, XLSB and VBA inspection must preserve deterministic positive source evidence"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_public_inspection_of_ooxml_workbooks_preserves_exact_offset_error(tmp_path, suffix):
    # GIVEN an addressed source error that calamine's Python projection erases
    from test_workbook_fidelity import fidelity_book

    source = fidelity_book(tmp_path / ("error" + suffix),
                           rows='<row r="5"><c r="C5" t="e"><v>#DIV/0!</v></c></row>', label="Inputs")
    with zipfile.ZipFile(source) as archive:
        native = archive.read("xl/worksheets/sheet1.xml")
    assert native.count(b"#DIV/0!") == 1, "the native source must contain the exact error cache"
    # WHEN public inspection reads original OOXML metadata independently of cached values
    report = inspect_document(source)
    # THEN source address, error type and literal token survive unchanged
    assert [(item["location"]["qualified_cell"], item["details"]["value_type"],
             item["details"]["error"]) for item in report["findings"] if item["kind"] == "cell"] == [
        ("'Inputs'!C5", "error", "#DIV/0!"),
    ], "both OOXML workbook extensions must preserve native source errors at exact addresses"


@pytest.mark.corpus
@pytest.mark.parametrize("relative", ["xls/calamine-xls_formula.xls", "xlsb/calamine-issues.xlsb",
                                      "xlsm/calamine-vba.xlsm"])
def test_binary_workbook_public_coverage_has_no_unfinished_initialization(relative):
    # GIVEN an existing supported workbook with native source evidence
    source = FIXTURES / relative
    assert source.is_file() is True, "the source must be a retained licensed workbook"
    # WHEN independent inspection finishes its real source traversal
    report = inspect_document(source)
    # THEN no capability remains at an initialization placeholder
    assert [(item["document"], item["capability"]) for item in report["coverage"]
            if item["reason"] == "inspection pending"] == [], (
        "a completed binary inspection must finalize each capability with evidence or a concrete failure")


@pytest.mark.parametrize("suffix,payload,_field,_expected", SYNTHETIC, ids=[row[0] for row in SYNTHETIC])
def test_completed_public_inspection_never_leaves_capabilities_pending(
        tmp_path, suffix, payload, _field, _expected):
    # GIVEN a supported readable native source with admitted positive evidence
    source = tmp_path / ("coverage" + suffix)
    source.write_bytes(payload)
    assert source.stat().st_size == len(payload), "coverage must describe the complete original source"
    # WHEN the public operation finishes traversing all admitted source components
    report = inspect_document(source)
    pending = [(item["document"], item["capability"]) for item in report["coverage"]
               if item["reason"] == "inspection pending"]
    # THEN every capability is finalized with supported completion or a concrete reason
    assert pending == [], "completed inspection must not leave initialization placeholders in final coverage"


def test_real_nested_package_preserves_siblings_and_reports_malformed_supported_attachment(tmp_path):
    # GIVEN equal supported siblings and an invalid PDF payload in an unreferenced package
    payload = b"[nested](https://nested.invalid/original)\r\n"
    source = tmp_path / "nested.docx"
    source.write_bytes(_archive([("word/document.xml", document(paragraph("body"))),
                                 ("embeddings/first.md", payload), ("embeddings/second.md", payload),
                                 ("embeddings/broken.pdf", b"invalid PDF payload")]))
    assert source.read_bytes().count(payload) == 2, "two sibling occurrences must carry identical original bytes"
    # WHEN real package callbacks recurse through the public operation
    report = inspect_document(source)
    children = [item for item in report["documents"] if item["parent"] == "document/000001"]
    broken = [item for item in report["coverage"] if item["document"] == "document/000004"]
    # THEN sibling equality is retained and malformed supported content cannot become complete-empty
    assert ([(item["name"], item["sha256"]) for item in children],
            {item["status"] for item in broken},
            [item["target"] for item in report["findings"] if item["capability"] == "links"]) == (
        [("embeddings/first.md", hashlib.sha256(payload).hexdigest()),
         ("embeddings/second.md", hashlib.sha256(payload).hexdigest()),
         ("embeddings/broken.pdf", hashlib.sha256(b"invalid PDF payload").hexdigest())],
        {"unavailable"}, ["https://nested.invalid/original", "https://nested.invalid/original"],
    ), "real nested occurrences and malformed-child coverage must retain their independent source identities"


def test_odf_inline_script_source_is_retrievable_independently_of_its_xml_container(tmp_path):
    # GIVEN source code stored inside an ODF Basic library XML element
    code = b"Sub Main\n  MsgBox 42\nEnd Sub"
    xml = (b'<script:module xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0">'
           + code + b"</script:module>")
    source = tmp_path / "library.odt"
    source.write_bytes(_archive([("Basic/Standard/Module1.xml", xml)]))
    assert xml.count(code) == 1, "the source must contain literal recoverable Basic code"
    # WHEN generic public inspection extracts source independently of normal reading
    report = inspect_document(source)
    finding = next(item for item in report["findings"] if item["capability"] == "scripts")
    refs = [item for item in report["artifacts"] if item["key"] == finding["artifact"]]
    # THEN source code has its own exact retrievable artifact rather than only a container reference
    assert [(item["byte_size"], item["sha256"]) for item in refs] == [
        (len(code), hashlib.sha256(code).hexdigest()),
    ], "generic script retrieval must expose extracted source bytes separately from the containing XML"
    assert read_document_artifact(source, finding["artifact"]) == code, "script artifact bytes must round trip exactly"


def test_inline_html_data_payloads_are_retrievable_and_supported_documents_are_inspected(tmp_path):
    # GIVEN source-embedded image bytes and a complete supported PDF in data URLs
    image = b"\x01\x02\x03"
    pdf, target = _image_button_pdf()
    source = tmp_path / "embedded.html"
    source.write_text('<img src="data:image/png;base64,' + base64.b64encode(image).decode()
                      + '"><object data="data:application/pdf;base64,'
                      + base64.b64encode(pdf).decode() + '"></object>', encoding="ascii")
    assert base64.b64encode(pdf).decode() in source.read_text(), "the complete PDF must be embedded source bytes"
    # WHEN passive inspection decodes available payloads without fetching any resource
    report = inspect_document(source)
    images = [item for item in report["artifacts"] if item["sha256"] == hashlib.sha256(image).hexdigest()]
    children = [item for item in report["documents"] if item["parent"] == "document/000001"]
    # THEN local inline bytes and supported nested content have independent retrieval and provenance
    assert ([(item["byte_size"], item["sha256"]) for item in images],
            [(item["suffix"], item["sha256"]) for item in children],
            sorted({item["target"] for item in report["findings"] if item["document"] != "document/000001"
                    and item["target"] is not None})) == (
        [(3, hashlib.sha256(image).hexdigest())], [(".pdf", hashlib.sha256(pdf).hexdigest())], [target],
    ), "data URLs contain recoverable local payloads, not merely opaque destination strings"
    assert read_document_artifact(source, images[0]["key"]) == image, "inline image payload bytes must round trip exactly"


@pytest.mark.parametrize("suffix,part,namespace,root", [
    (".docx", "word/header1.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "hdr"),
    (".docx", "word/footer1.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "ftr"),
    (".docx", "word/footnotes.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "footnotes"),
    (".docx", "word/endnotes.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "endnotes"),
    (".docx", "word/comments.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "comments"),
    (".pptx", "ppt/notesSlides/notesSlide1.xml", "http://schemas.openxmlformats.org/drawingml/2006/main", "p"),
    (".pptx", "ppt/comments/comment1.xml", "http://schemas.openxmlformats.org/presentationml/2006/main", "cmLst"),
    (".pptx", "ppt/slideMasters/slideMaster1.xml", "http://schemas.openxmlformats.org/drawingml/2006/main", "p"),
])
def test_unreferenced_office_auxiliary_parts_keep_exact_text_and_part_locations(
        tmp_path, suffix, part, namespace, root):
    # GIVEN native auxiliary XML not connected to a readable body relationship
    marker = "Auxiliary source text"
    xml = '<n:%s xmlns:n="%s"><n:r><n:t>%s</n:t></n:r></n:%s>' % (root, namespace, marker, root)
    source = tmp_path / ("auxiliary" + suffix)
    source.write_bytes(_archive([(part, xml)]))
    assert xml.count(marker) == 1, "the auxiliary member must contain the exact original text"
    # WHEN independent inspection scans every member without following readable relationships
    report = inspect_document(source)
    # THEN recoverable source text retains the actual original member occurrence
    assert [(item["text"], item["location"]["part"], item["location"]["member_ordinal"])
            for item in report["findings"] if item["kind"] == "xml-text"] == [
        (marker, part, 1),
    ], "headers, footers, notes, comments and masters need source-backed supplementary text"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xlsb", ".ods"])
def test_workbook_auxiliary_comment_text_is_extracted_beside_native_cells(tmp_path, suffix):
    # GIVEN a native workbook comment part outside every worksheet cell record
    marker = "Hidden workbook comment"
    xml = ('<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<commentList><comment ref="C5"><text><r><t>' + marker
           + '</t></r></text></comment></commentList></comments>')
    source = tmp_path / ("comments" + suffix)
    source.write_bytes(_archive([("xl/comments1.xml", xml)]))
    assert marker in xml, "the comment must carry recoverable text independently of cached cell values"
    # WHEN full package inspection supplements the source cell reader
    report = inspect_document(source)
    # THEN workbook-specific suppression cannot erase other package text
    assert [(item["text"], item["location"]["part"]) for item in report["findings"]
            if item["kind"] == "xml-text"] == [
        (marker, "xl/comments1.xml"),
    ], "readable cell deduplication must not hide comments or other auxiliary workbook text"


def test_plain_mime_body_links_are_inspected_when_no_html_alternative_exists(tmp_path):
    # GIVEN a plain-text MIME body containing an original URL destination
    target = "https://plain-mail.invalid/original?keep=1#fragment"
    source = tmp_path / "plain.eml"
    source.write_bytes(("Content-Type: text/plain; charset=utf-8\r\n\r\n<" + target + ">\r\n").encode())
    assert source.read_bytes().count(target.encode()) == 1, "the destination must occur in original body bytes"
    # WHEN full MIME inspection examines every selected or unselected native text part
    report = inspect_document(source)
    # THEN plain content is not falsely called link-complete while its destination is absent
    assert [(item["target"], item["location"].get("mime_path")) for item in report["findings"]
            if item["capability"] == "links"] == [
        (target, "1"),
    ], "plain MIME parts need destination findings independently of an HTML callback"


def test_mime_preamble_and_epilogue_recoverable_text_have_source_findings(tmp_path):
    # GIVEN original text outside a multipart boundary but inside the message body
    preamble, epilogue = "Recoverable preamble source", "Recoverable epilogue source"
    source = tmp_path / "boundaries.eml"
    source.write_bytes(('Content-Type: multipart/mixed; boundary="bound"\r\n\r\n'
                        + preamble + '\r\n--bound\r\nContent-Type: text/plain\r\n\r\nInside part'
                        '\r\n--bound--\r\n' + epilogue + '\r\n').encode())
    assert (source.read_bytes().count(preamble.encode()), source.read_bytes().count(epilogue.encode())) == (
        1, 1,
    ), "both recoverable source areas must be present beyond MIME child payloads"
    # WHEN independent inspection accounts for all recoverable message components
    report = inspect_document(source)
    texts = [item["text"].strip() for item in report["findings"]
             if item["capability"] == "text" and item["text"] is not None]
    # THEN preserving one raw message artifact cannot replace extracted source text
    assert (texts.count(preamble), texts.count(epilogue)) == (
        1, 1,
    ), "multipart preamble and epilogue must remain discoverable recoverable text with provenance"


def test_passive_inspection_preserves_scripts_without_network_or_process_execution(tmp_path, monkeypatch):
    # GIVEN hidden code and remote resources that must remain literal evidence
    sentinel = tmp_path / "executed.txt"
    script = "__import__('pathlib').Path(%r).write_text('executed')" % str(sentinel)
    source = tmp_path / "passive.html"
    source.write_text('<div hidden><script src="' + TARGET + '">' + script
                      + '</script><iframe src="' + TARGET + '"></iframe></div>', encoding="utf-8")
    assert sentinel.exists() is False, "the execution sentinel must begin absent"

    def forbidden(*_args, **_kwargs):
        """Fail if passive evidence collection crosses a network or process boundary."""
        pytest.fail("static inspection attempted networking or process execution")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    # WHEN independent inspection traverses active source and remote destinations
    report = inspect_document(source)
    # THEN no code or discovered target is evaluated, spawned or fetched
    assert (sentinel.exists(), sorted({item["target"] for item in report["findings"]
                                      if item["target"] is not None}),
            {item["text"] for item in report["findings"] if item["capability"] == "scripts"
             and item["text"] is not None}) == (
        False, [TARGET], {script},
    ), "document script and remote destinations must remain passive exact evidence"


def _package_safety(payload):
    """Append unreferenced source and an original external relationship to a native ZIP."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    relation = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="proof" Type="hyperlink" Target="' + TARGET
                + '" TargetMode="External"/></Relationships>')
    return _archive([*entries, ("unused/source.js", SCRIPT), ("unused/_rels/source.js.rels", relation),
                     ("attachments/nested.md", NESTED), ("attachments/macro.xls", _safety_xls(None)[0])])


def _safety_pdf(_directory):
    from test_inspect_pdf import _features_pdf

    source, target, _script, _attachment = _features_pdf()
    return source, target


def _safety_xls(_directory):
    from test_biff import compound_book, record, stream

    encoded = (TARGET + "\0").encode("utf-16le")
    hyperlink = (struct.pack("<HHHH", 4, 4, 2, 2)
                 + bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b")
                 + struct.pack("<II", 2, 3) + bytes.fromhex("e0c9ea79f9bace118c8200aa004ba90b")
                 + struct.pack("<I", len(encoded)) + encoded)
    tokens = b"\x17\x0c\x00static-proof" + b"\x22\x01\x6e\x00"
    formula = struct.pack("<HHH", 4, 2, 0) + struct.pack("<d", 0) + bytes(6)
    native = stream(record(0x0006, formula + struct.pack("<H", len(tokens)) + tokens)
                    + record(0x01B8, hyperlink), sheet_type=1, visibility=2)
    return compound_book(native), TARGET


def _safety_xlsb(_directory):
    from test_biff12 import _cell, _formula, _members, _record, _row, _wide

    tokens = b"\x17\x0c\x00" + "static-proof".encode("utf-16le") + b"\x22\x01\x6e\x00"
    native = (_row(4) + _record(9, _cell(2, struct.pack("<dH", 0, 0) + _formula(tokens)))
              + _record(494, struct.pack("<IIII", 4, 4, 2, 2)
                        + _wide("link") + _wide("") + _wide("") + _wide("source")))
    entries = [(part, data.replace(b"https://example.invalid/exact?a=1#f", TARGET.encode()))
               for _ordinal, part, data in _members(native)]
    return _package_safety(_archive(entries)), TARGET


def _safety_ooxml(directory):
    from test_workbook_fidelity import fidelity_book

    source = fidelity_book(directory / "native.xlsx", rows='<row r="5"><c r="C5"><f>42</f><v>42</v></c></row>')
    return _package_safety(source.read_bytes()), TARGET


def _safety_package(payload):
    return lambda _directory: (_package_safety(payload), TARGET)


def _safety_markup(_directory):
    inline = "data:text/markdown;base64," + base64.b64encode(NESTED).decode()
    return ('<div hidden><script>' + SCRIPT + '</script><a href="' + TARGET
            + '">source</a><object data="' + inline + '"></object></div>').encode(), TARGET


def _safety_mail(_directory):
    message = EmailMessage()
    message.set_content("Harmless plain source")
    message.add_alternative(_safety_markup(None)[0].decode(), subtype="html")
    message.add_attachment(NESTED, maintype="text", subtype="markdown", filename="nested.md")
    return message.as_bytes(), TARGET


_STATIC_SAFETY = {suffix: _safety_package(payload) for suffix, payload, _field, _expected in SYNTHETIC}
_STATIC_SAFETY.update({".pdf": _safety_pdf, ".xls": _safety_xls, ".xlsb": _safety_xlsb,
                       ".xlsx": _safety_ooxml, ".xlsm": _safety_ooxml,
                       ".html": _safety_markup, ".md": _safety_markup, ".txt": _safety_markup,
                       ".eml": _safety_mail})


@pytest.mark.parametrize("suffix", sorted(_STATIC_SAFETY))
def test_all_fifteen_public_native_routes_are_passive_with_exact_repeatable_artifacts(
        tmp_path, monkeypatch, suffix):
    # GIVEN native source containing a real discovered destination and recoverable active evidence
    payload, target = _STATIC_SAFETY[suffix](tmp_path)
    source = tmp_path / ("passive" + suffix)
    source.write_bytes(payload)
    assert source.read_bytes() == payload, "source bytes must be fixed before disabling dangerous capabilities"

    def forbidden(*_args, **_kwargs):
        """Reject discovered-resource requests, document subprocesses and shell execution."""
        raise AssertionError("static document inspection attempted network or code execution")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    # WHEN every public native reader inspects, repeats and retrieves its actual payload artifacts
    first = inspect_document(source)
    first_json = read_document_artifact(source, "inspection/report")
    second_json = read_document_artifact(source, "inspection/report")
    retrieved = [(item, read_document_artifact(source, item["key"])) for item in first["artifacts"]]
    # THEN source targets and every listed byte/hash survive without any denied capability
    assert (sorted({item["target"] for item in first["findings"] if item["target"] == target}),
            first_json, json.loads(first_json), first["source"]["sha256"]) == (
        [target], second_json, first, hashlib.sha256(payload).hexdigest(),
    ), "every supported suffix must exercise real passive findings and deterministic public report bytes"
    assert [(len(data), hashlib.sha256(data).hexdigest()) for _item, data in retrieved] == [
        (item["byte_size"], item["sha256"]) for item, _data in retrieved
    ], "every retrievable artifact must preserve its exact admitted size and source digest"


@pytest.mark.parametrize("suffix", [".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".ods",
                                     ".odt", ".odp", ".epub", ".eml", ".html", ".md", ".txt"])
def test_each_package_mime_and_text_consumer_uses_real_supported_nested_inspection(tmp_path, suffix):
    # GIVEN source-contained supported Markdown bytes under the actual native suffix dispatch
    payload, _target = _STATIC_SAFETY[suffix](tmp_path)
    source = tmp_path / ("children" + suffix)
    source.write_bytes(payload)
    assert source.read_bytes() == payload, "the parent must contain the original admitted nested source bytes"
    # WHEN the public operation dispatches actual embedded documents instead of a test callback
    report = inspect_document(source)
    digest = hashlib.sha256(NESTED).hexdigest()
    children = [item for item in report["documents"] if item["parent"] is not None and item["sha256"] == digest]
    destinations = {item["target"] for item in report["findings"]
                    if item["document"] in {child["id"] for child in children} and item["capability"] == "links"}
    # THEN native consumers share the real recursive reader and preserve original child identities
    assert ({item["suffix"] for item in children}, destinations) == (
        {".md"}, {TARGET},
    ), "supported child payloads must produce real nested source findings for every native consumer"


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_pass_through_source_resembling_inspection_headers_remains_byte_identical(tmp_path, suffix):
    # GIVEN real source whose literal content resembles generated inspection sections
    payload = (b"\xef\xbb\xbfSource inspection findings. Omission notices describe the readable projection.\r\n"
               b"\r\n## Links\r\n\r\n<https://source.invalid/original>\r\n"
               b"## Active content\r\n<script>literalSource();</script>\r\n\t ")
    source, output = tmp_path / ("literal" + suffix), tmp_path / "output.md"
    source.write_bytes(payload)
    assert source.read_bytes() == payload, "header-shaped bytes must be original source, not a generated supplement"
    # WHEN normal conversion writes the actual public output alongside independent findings
    code, receipt, markdown = run(source, output)
    # THEN no helper or service layer strips, inserts or normalizes source-shaped sections
    assert (code, receipt["file_ok"], markdown.encode("utf-8"), output.read_bytes(),
            receipt["inspection"]["source_sha256"]) == (
        0, True, payload, payload, hashlib.sha256(payload).hexdigest(),
    ), "MD/TXT pass-through must remain exact even when source text matches inspection sentinels"


def test_xls_native_ole_attachment_recurses_with_original_wrapper_and_source_locations(tmp_path):
    # GIVEN a valid CFB workbook and a separate native Package stream holding supported HTML
    from test_biff import compound_book, record, stream
    from test_ole import package as ole_package

    body = ('<script>' + SCRIPT + '</script><a href="' + TARGET + '">source</a>' + ' ' * 4096).encode()
    wrapper, payload_offset = ole_package(body, b"nested.html")
    source_bytes = bytearray(compound_book(stream(record(0x0203, struct.pack("<HHHd", 0, 0, 0, 42)))))
    name = "\x01Ole10Native\0".encode("utf-16le")
    directory = 1024 + 256
    source_bytes[directory:directory + len(name)] = name
    struct.pack_into("<HBBIII", source_bytes, directory + 64, len(name), 2, 1,
                     0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<IQ", source_bytes, directory + 116, 10, len(wrapper))
    struct.pack_into("<I", source_bytes, 1024 + 128 + 72, 2)
    sectors = (len(wrapper) + 511) // 512
    for sector in range(sectors - 1):
        struct.pack_into("<I", source_bytes, 512 + (10 + sector) * 4, 11 + sector)
    struct.pack_into("<I", source_bytes, 512 + (9 + sectors) * 4, 0xFFFFFFFE)
    source_bytes.extend(wrapper + bytes(sectors * 512 - len(wrapper)))
    source = tmp_path / "nested.xls"
    source.write_bytes(source_bytes)
    assert bytes(source_bytes)[5632 + payload_offset:5632 + payload_offset + len(body)] == body, (
        "the supported attachment must occupy an exact original source range in a native CFB stream")
    # WHEN public workbook inspection visits every original CFB component independently of BIFF rendering
    report = inspect_document(source)
    children = [item for item in report["documents"] if item["parent"] == "document/000001"]
    # THEN embedded supported content is inspected with its exact bytes and original stream provenance
    assert [(item["suffix"], item["sha256"]) for item in children] == [
        (".html", hashlib.sha256(body).hexdigest()),
    ], "XLS native OLE payloads must recurse instead of remaining falsely complete opaque bytes"
    assert sorted({item["target"] for item in report["findings"] if item["target"] == TARGET}) == [
        TARGET,
    ], "the embedded original destination must be visible without following or executing it"


@pytest.mark.corpus
@pytest.mark.parametrize("suffix", [".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".ods",
                                     ".odt", ".odp", ".epub"])
def test_all_package_consumers_recover_available_vba_and_supported_ole_payloads(tmp_path, suffix):
    # GIVEN admitted native package bytes plus real VBA and a supported native OLE attachment
    from test_inspect_package import _native_compound
    from test_ole import package as ole_package

    original, _target = _STATIC_SAFETY[suffix](tmp_path)
    html = ('<script>' + SCRIPT + '</script><a href="' + TARGET + '">source</a>').encode()
    wrapper, _offset = ole_package(html, b"nested.html")
    with zipfile.ZipFile(FIXTURES / "xlsm/calamine-vba.xlsm") as archive:
        project = archive.read("xl/vbaProject.bin")
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        entries = [(item.filename, archive.read(item)) for item in archive.infolist()]
    source = tmp_path / ("native" + suffix)
    source.write_bytes(_archive([*entries, ("embeddings/vbaProject.bin", project),
                                 ("embeddings/object.bin", _native_compound(wrapper))]))
    assert (project[:8], html in wrapper) == (bytes.fromhex("d0cf11e0a1b11ae1"), True), (
        "source macros and wrapped supported HTML must have actual native original bytes")
    # WHEN real per-suffix public dispatch reaches the same bounded package helper branches
    report = inspect_document(source)
    modules = [item for item in report["findings"] if item["kind"] == "vba_module"]
    children = [item for item in report["documents"] if item["suffix"] == ".html"]
    # THEN original module source/compiled artifacts and supported OLE child identities survive
    assert ([item["details"]["module_name"] for item in modules],
            [(item["sha256"], item["parent"]) for item in children]) == (
        ["ThisWorkbook", "Sheet1", "Sheet2", "Sheet3", "testVBA"],
        [(hashlib.sha256(html).hexdigest(), "document/000001")],
    ), "every package suffix must recover source and supported OLE bytes through its real consumer path"
    assert [(item["location"]["sheet_ordinal"], item["details"]["sheet"])
            for item in report["findings"] if item["kind"] == "xlm-sheet"] == [
        (1, "Bob's input"),
    ], "embedded Excel/XLM payloads must receive native macro-sheet inspection even in ODF containers"
    module = modules[-1]
    expected = b'Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n'
    assert read_document_artifact(source, module["artifact"]) == expected, (
        "available VBA source must retain exact bytes independently of its preserved compiled content")
    artifacts = {item["key"]: item for item in report["artifacts"]}
    compiled = read_document_artifact(source, module["details"]["compiled_prefix"])
    assert (len(compiled), hashlib.sha256(compiled).hexdigest()) == (
        artifacts[module["details"]["compiled_prefix"]]["byte_size"],
        artifacts[module["details"]["compiled_prefix"]]["sha256"],
    ), "compiled prefix retention must remain exact without claiming disassembly or execution"


@pytest.mark.parametrize("suffix", [".ods", ".odt", ".odp"])
def test_each_odf_consumer_keeps_script_events_form_defaults_and_drawing_targets(tmp_path, suffix):
    # GIVEN native ODF event, form, script and drawing source outside readable content
    base, _target = _STATIC_SAFETY[suffix](tmp_path)
    xml = ('<office:document-content xmlns:office="' + OFFICE
           + '" xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0" '
           'xmlns:form="urn:oasis:names:tc:opendocument:xmlns:form:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
           'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
           'xmlns:xlink="http://www.w3.org/1999/xlink"><script:module>' + SCRIPT
           + '</script:module><script:event-listener script:event-name="dom:load" xlink:href="' + TARGET
           + '"/><form:form form:action="' + TARGET + '"><form:text form:current-value="source-default"/>'
           '</form:form><draw:image xlink:href="' + TARGET + '"/><table:dde-links><table:dde-link>'
           '<office:dde-source office:dde-application="literal-service" office:dde-topic="literal-topic" '
           'office:dde-item="literal-item"/></table:dde-link></table:dde-links></office:document-content>')
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    source = tmp_path / ("forms" + suffix)
    source.write_bytes(_archive([*entries, ("Basic/Standard/Module1.xml", xml)]))
    assert xml.count(SCRIPT) == 1, "each ODF source must contain real recoverable code outside its body"
    # WHEN all native package consumers inspect the same required source structures
    report = inspect_document(source)
    scripts = [item for item in report["findings"] if item["kind"] == "script-source" and item["text"] == SCRIPT
               and item["location"]["part"] == "Basic/Standard/Module1.xml"]
    defaults = [value for item in report["findings"] if item["kind"] == "form-control"
                for key, value in item["details"]["attributes"].items() if key.endswith("}current-value")]
    events = [item["location"]["part"] for item in report["findings"] if item["kind"] == "event-listener"]
    # THEN source code, event provenance, form defaults and external drawing targets are exact
    assert (len(scripts), defaults, events,
            sorted({item["target"] for item in report["findings"]
                    if item["capability"] == "links" and item["target"] == TARGET})) == (
        1, ["source-default"], ["Basic/Standard/Module1.xml"], [TARGET],
    ), "ODT, ODP and ODS must preserve native events, forms and drawing destinations independently"
    assert read_document_artifact(source, scripts[0]["artifact"]) == SCRIPT.encode(), (
        "ODF script-source artifacts must preserve the exact extracted source bytes")
    assert sorted({item["target"] for item in report["findings"] if item["kind"] == "dde-reference"}) == [
        "literal-item", "literal-service", "literal-topic",
    ], "native ODF DDE sources must preserve application, topic and item as literal references"


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_ooxml_workbook_native_xlm_dde_controls_drawings_and_hyperlinks_are_recovered(tmp_path, suffix):
    # GIVEN actual workbook relationship types and independent DDE, control and drawing parts
    from test_workbook_fidelity import fidelity_book

    native = fidelity_book(tmp_path / "original.xlsx", rows='<row r="5"><c r="C5"><f>42</f><v>42</v></c></row>')
    with zipfile.ZipFile(native) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    relation_type = b"http://schemas.microsoft.com/office/2006/relationships/xlMacrosheet"
    entries = [(part, payload.replace(b"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", relation_type))
               for part, payload in entries]
    entries = [(part, payload.replace(b"</worksheet>", b'<hyperlinks><hyperlink ref="C5" location="Inputs!C5"/></hyperlinks></worksheet>'))
               for part, payload in entries]
    dde = ('<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<ddeLink ddeService="literal-service" ddeTopic="literal-topic"><ddeItems>'
           '<ddeItem name="literal-item"/></ddeItems></ddeLink></externalLink>')
    controls = '<controls><control name="source-control" value="source-default" macro="Auto_Open"/></controls>'
    drawing = ('<a:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
               '<a:hlinkClick r:id="remote"/></a:drawing>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="remote" Type="hyperlink" Target="' + TARGET
            + '" TargetMode="External"/></Relationships>')
    source = tmp_path / ("active" + suffix)
    source.write_bytes(_archive([*entries, ("xl/externalLinks/externalLink1.xml", dde),
                                 ("xl/controls/control1.xml", controls), ("xl/drawings/drawing1.xml", drawing),
                                 ("xl/drawings/_rels/drawing1.xml.rels", rels)]))
    assert source.read_bytes().count(relation_type) == 1, "the sheet must be source-declared as XLM rather than inferred from text"
    # WHEN independent inspection traverses workbook cells and every unreferenced native part
    report = inspect_document(source)
    # THEN named native references and action values remain literal source evidence
    assert ([(item["location"]["sheet_ordinal"], item["details"]["sheet_type"])
             for item in report["findings"] if item["kind"] == "xlm-sheet"],
            [item["details"]["source_attributes"] for item in report["findings"] if item["kind"] == "ddeLink"],
            [item["details"]["attributes"] for item in report["findings"] if item["kind"] == "form-control"],
            [item["target"] for item in report["findings"] if item["kind"] == "relationship-reference"
             and item["location"]["part"] == "xl/drawings/drawing1.xml"],
            [(item["location"]["range"], item["target"]) for item in report["findings"]
             if item["kind"] == "cell-hyperlink"]) == (
        [(1, "xlMacrosheet")], [{"ddeService": "literal-service", "ddeTopic": "literal-topic"}],
        [{"name": "source-control", "value": "source-default", "macro": "Auto_Open"}], [TARGET], [("C5", "Inputs!C5")],
    ), "both OOXML suffixes must inspect native XLM, DDE and control/drawing destinations without execution"


@pytest.mark.parametrize("suffix,part", [(".docx", "word/alternate.xml"), (".pptx", "ppt/slides/alternate.xml")])
def test_office_inspection_preserves_both_alternate_branches_and_revision_text(tmp_path, suffix, part):
    # GIVEN alternate and revision branches that readable projection may select or omit
    xml = ('<root xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
           '<mc:AlternateContent><mc:Choice Requires="optional"><t>Choice source</t></mc:Choice>'
           '<mc:Fallback><t>Fallback source</t></mc:Fallback></mc:AlternateContent>'
           '<ins><t>Inserted source</t></ins><del><t>Deleted source</t></del></root>')
    source = tmp_path / ("branches" + suffix)
    source.write_bytes(_archive([(part, xml)]))
    assert xml.count("source</t>") == 4, "all four original source branches must exist independently"
    # WHEN public package inspection traverses original XML rather than readable branch selection
    report = inspect_document(source)
    # THEN no alternate or revision source is discarded and every finding retains its original part
    assert [(item["text"], item["location"]["part"]) for item in report["findings"] if item["kind"] == "xml-text"] == [
        ("Choice source", part), ("Fallback source", part), ("Inserted source", part), ("Deleted source", part),
    ], "DOCX and PPTX inspection must preserve both alternatives and all recoverable revision text"


NORMAL_PACKAGES = {
    ".docx": "docx/tika-testWORD.docx", ".pptx": "pptx/poi-testPPT.pptx",
    ".xlsm": "xlsm/calamine-vba.xlsm", ".xlsb": "xlsb/calamine-issues.xlsb",
    ".ods": "ods/calamine-any_sheets.ods", ".odt": "odt/tika-testODFwithOOo3.odt",
    ".odp": "odp/tika-testMasterFooter.odp", ".epub": "epub/epubcheck-minimal.epub",
}


def _readable_source(directory, suffix):
    """Use original licensed native packages where the minimal inspection fixture cannot render."""
    if suffix in NORMAL_PACKAGES:
        return _package_safety((FIXTURES / NORMAL_PACKAGES[suffix]).read_bytes()), TARGET, SCRIPT
    if suffix == ".pdf":
        from test_inspect_pdf import _features_pdf, _pdf, _stream

        data, target, script, _attachment = _features_pdf()
        objects = {int(number): body for number, body in re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", data)}
        objects[4] = _stream(b"BT /F1 12 Tf 72 700 Td (Readable body) Tj ET\n")
        return _pdf(objects), target, script.decode().rstrip()
    payload, target = _STATIC_SAFETY[suffix](directory)
    return payload, target, {".xls": 'EXEC("static-proof")'}.get(suffix, SCRIPT)


@pytest.mark.corpus
@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".xls",
                                     ".ods", ".odt", ".odp", ".epub", ".html", ".eml"])
def test_normal_conversion_of_each_parsed_format_exposes_exact_original_active_content(tmp_path, suffix):
    # GIVEN a real readable native source with an original active target and source code
    payload, target, script = _readable_source(tmp_path, suffix)
    source = tmp_path / ("normal" + suffix)
    source.write_bytes(payload)
    assert source.read_bytes() == payload, "native readable inputs must preserve their admitted source bytes"
    # WHEN default public conversion renders and atomically publishes actual inspected Markdown
    output = tmp_path / "out.md"
    code, receipt, markdown = run(source, output)
    # THEN each parsed format exposes real findings without any projection-normalization helper
    assert (code, receipt["file_ok"], target in markdown, script in markdown,
            "## Links" in markdown, "## Active content" in markdown) == (
        0, True, True, True, True, True,
    ), "every native parsed format must expose exact original targets and literal active source by default"
    assert output.read_bytes() == markdown.encode("utf-8"), "the on-disk Markdown must match full actual public output bytes"


@pytest.mark.corpus
def test_native_xls_workbook_recovers_real_vba_sources_and_preserved_compiled_prefixes(tmp_path):
    # GIVEN original licensed VBA streams plus a valid native BIFF workbook stream in the same CFB
    from test_biff import record, stream
    from brewdoc._compound import read_compound
    from brewdoc._inspection_common import InspectionContext
    from brewdoc._vba import decompress_vba

    with zipfile.ZipFile(FIXTURES / "xlsm/calamine-vba.xlsm") as archive:
        original = archive.read("xl/vbaProject.bin")
    expected = b'Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n'
    original_module = next(item["data"] for item in read_compound(original, InspectionContext(), "fixture")
                           if item["path"] == "VBA/testVBA")
    assert decompress_vba(original_module[785:]) == expected, (
        "the fixture's original module source boundary must independently produce the declared exact source")
    data = bytearray(original)
    fat = list(struct.unpack_from("<128I", data, 512))
    directory_sectors, sector = [], 1
    while sector != 0xFFFFFFFE:
        directory_sectors.append(sector)
        sector = fat[sector]
    offsets = [(value + 1) * 512 + within * 128 for value in directory_sectors for within in range(4)]
    assert (len(directory_sectors), data[offsets[15] + 66], struct.unpack_from("<I", data, offsets[1] + 72)[0]) == (
        4, 0, 0xFFFFFFFF,
    ), "the original source must expose the independently verified free directory entry and VBA sibling slot"
    native = stream(record(0x0203, struct.pack("<HHHd", 4, 2, 0, 42)), sheet_name="Inputs")
    entry, start = offsets[15], len(data) // 512 - 1
    name = "Book\0".encode("utf-16le")
    data[entry:entry + len(name)] = name
    struct.pack_into("<HBBIII", data, entry + 64, len(name), 2, 0, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<IQ", data, entry + 116, start, 4096)
    struct.pack_into("<I", data, offsets[1] + 72, 15)
    data[offsets[1] + 67] = data[offsets[13] + 67] = 1
    fat[start:start + 8] = [*range(start + 1, start + 8), 0xFFFFFFFE]
    data[512:1024] = struct.pack("<128I", *fat)
    data.extend(native + bytes(4096 - len(native)))
    source = tmp_path / "native-vba.xls"
    source.write_bytes(data)
    # WHEN the real XLS public path inspects both the workbook and its original VBA storage
    report = inspect_document(source)
    modules = [item for item in report["findings"] if item["kind"] == "vba_module"]
    cells = [(item["location"]["qualified_cell"], item["details"]["value"])
             for item in report["findings"] if item["kind"] == "cell"]
    # THEN native workbook content and all available module source survive independently
    assert (cells, [item["details"]["module_name"] for item in modules]) == (
        [("'Inputs'!C5", 42.0)], ["ThisWorkbook", "Sheet1", "Sheet2", "Sheet3", "testVBA"],
    ), "a valid native XLS must inspect both original BIFF cells and its available VBA modules"
    assert read_document_artifact(source, modules[-1]["artifact"]) == expected, (
        "XLS source modules must round trip exact bytes without interpreting compiled content")
    assert read_document_artifact(source, modules[-1]["details"]["compiled_prefix"]) == original_module[:785], (
        "the original XLS VBA compiled prefix must remain a separate exact-byte artifact")


def test_epub_manifest_spine_navigation_and_nonspine_sources_keep_original_member_provenance(tmp_path):
    # GIVEN a native publication with different ZIP, navigation and spine orders plus an unreferenced resource
    from test_epub import epub3_members

    parts = epub3_members()
    parts["OPS/unused.xhtml"] = '<html><script>' + SCRIPT + '</script></html>'
    source = tmp_path / "publication.epub"
    source.write_bytes(_archive(parts.items()))
    assert '<itemref idref="b"/><itemref idref="a" linear="no"/>' in parts["OPS/book.opf"], (
        "native spine order must be independently declared rather than inherited from archive order")
    # WHEN independent inspection traverses all original publication resources
    report = inspect_document(source)
    navigation = [item["target"] for item in report["findings"] if item["capability"] == "links"
                  and item["location"].get("part") == "OPS/nav.xhtml"]
    scripts = [(item["text"], item["location"]["part"]) for item in report["findings"]
               if item["capability"] == "scripts" and item["text"] == SCRIPT]
    manifests = [item for item in report["components"] if item["location"].get("part") == "OPS/book.opf"
                 and item["byte_size"] is not None]
    # THEN manifest bytes, both navigation targets and nonspine source are independently preserved
    assert (navigation, scripts, read_document_artifact(source, manifests[0]["artifact"])) == (
        ["a.xhtml", "b.xhtml"], [(SCRIPT, "OPS/unused.xhtml")], parts["OPS/book.opf"].encode(),
    ), "EPUB extraction must preserve original publication declarations and every nonspine source resource"
