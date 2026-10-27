"""Static PDF actions, forms, streams and object locations retain original evidence."""

import hashlib
import io
import re
import socket
import subprocess
import zlib
from pathlib import Path

import pdfplumber
import pytest

from brewdoc.inspection import _inspect_document, inspect_document, read_document_artifact
from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc._inspection_pdf import _decode_stream
from brewdoc.common import BrewdocError
from pdfminer.pdftypes import PDFStream
from pdfminer.psparser import LIT
from pdfminer.arcfour import Arcfour
from pdfminer.pdfdocument import PDFDocument, PDFStandardSecurityHandler
from pdfminer.pdfparser import PDFParser
from test_extraction_regressions import _image_button_pdf
from test_pdf_regressions import (FONT_NAME, OVERRIDDEN_PROGRAM, PDF_DIFFERENCES, PRODUCT_PROGRAM,
                                  SUMMATION_PROGRAM, font_identity_pdf, type1_program)


def _pdf(objects):
    """Serialize synthetic objects with exact offsets, following selfcheck.synthetic_pdf."""
    output, offsets = io.BytesIO(), {}
    output.write(b"%PDF-1.4\n")
    for number, payload in sorted(objects.items()):
        offsets[number] = output.tell()
        output.write(("%d 0 obj\n" % number).encode() + payload + b"\nendobj\n")
    start, count = output.tell(), max(objects) + 1
    output.write(("xref\n0 %d\n0000000000 65535 f \n" % count).encode())
    for number in range(1, count):
        output.write(("%010d 00000 n \n" % offsets[number]).encode())
    output.write(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (count, start)).encode())
    return output.getvalue()


def _stream(payload, attributes=b""):
    return (b"<< /Length " + str(len(payload)).encode() + b" " + attributes
            + b" >>\nstream\n" + payload + b"\nendstream")


def _features_pdf():
    """Cover independent document, page, annotation, field and orphan evidence."""
    base, button = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", base)}
    script = b"app.alert('literal-document-script');\n"
    attachment = b"# Attached source\r\n[retained](https://nested.example.invalid/exact)\r\n"
    xfa = (b'<template><subform name="original"><field name="answer"><value><text>42</text>'
           b'</value></field><event activity="click"><script contentType="application/x-javascript">'
           b'xfaProof("literal");</script></event><submit target="https://xfa.example.invalid/send"/>'
           b'</subform></template>')
    objects[1] = (b"<< /Type /Catalog /Pages 2 0 R /OpenAction 9 0 R /AA << /WC 11 0 R >> "
                  b"/AcroForm << /Fields [6 0 R] /XFA [(template) 14 0 R] >> "
                  b"/Names << /EmbeddedFiles << /Names [(note.md) 15 0 R] >> >> "
                  b"/OCProperties << /OCGs [] /D << /OFF [] >> >> >>")
    objects[3] = objects[3][:-2] + b" /AA << /O 11 0 R >> >>"
    objects[6] = objects[6][:-2] + b" /V (selected) /AA << /U 13 0 R >> >>"
    objects[9] = b"<< /S /JavaScript /JS 10 0 R /Next [11 0 R 12 0 R] >>"
    objects[10] = _stream(zlib.compress(script), b"/Filter /FlateDecode")
    objects[11] = b"<< /S /URI /URI (https://page.example.invalid/original#anchor) >>"
    objects[12] = b"<< /S /Launch /Win << /F (tool.exe) /P (--original) >> /Next 13 0 R >>"
    objects[13] = (b"<< /S /SubmitForm /F << /FS /URL /F (https://form.example.invalid/submit) >> "
                   b"/Fields [6 0 R] /Flags 4 >>")
    objects[14] = _stream(zlib.compress(xfa), b"/Filter /FlateDecode")
    objects[15] = b"<< /Type /Filespec /F (note.md) /UF (note.md) /EF << /F 16 0 R >> >>"
    objects[16] = _stream(attachment.hex().encode() + b">", b"/Type /EmbeddedFile /Filter /ASCIIHexDecode")
    objects[17] = _stream(b"BT /F1 12 Tf 72 700 Td (orphan form text) Tj ET\n",
                          b"/Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                          b"/Resources << /Font << /F1 5 0 R >> >>")
    objects[18] = rb"<< /S /JavaScript /JS (orphanProof\(42\);) >>"
    objects[19] = b"<< /Type /Annot /Subtype /Link /Dest [3 0 R /Fit] /Contents (recoverable note) >>"
    return _pdf(objects), button, script, attachment


def _forbid(*_args, **_kwargs):
    pytest.fail("passive PDF inspection must not fetch or execute discovered content")


def test_pdf_inspection_recovers_actions_forms_xfa_orphans_and_exact_payloads(tmp_path, monkeypatch):
    # GIVEN textless image content with linked actions, XFA, an embedding and unreferenced objects
    source, button, script, attachment = _features_pdf()
    path = tmp_path / "features.pdf"
    path.write_bytes(source)
    assert source.count(b"/Type /EmbeddedFile") == 1, "the PDF must contain the synthetic embedded file stream"
    monkeypatch.setattr(socket, "create_connection", _forbid)
    monkeypatch.setattr(subprocess, "Popen", _forbid)
    # WHEN independent inspection visits the original object graph without readable conversion
    report = inspect_document(path)
    targets = sorted({item["target"] for item in report["findings"]
                      if item["document"] == "document/000001" and item["target"] is not None})
    scripts = sorted({item["text"] for item in report["findings"] if item["capability"] == "scripts"})
    embedded = next(item for item in report["findings"] if item["kind"] == "pdf-embedded-file")
    button_locations = sorted({(item["location"].get("page"), item["location"].get("annotation_ordinal"))
                               for item in report["findings"] if item["target"] == button
                               and "annotation_ordinal" in item["location"]})
    coverage = [(item["capability"], item["status"]) for item in report["coverage"]
                if item["document"] == "document/000001"]
    form_text = [(item["text"], item["location"]["object_id"]) for item in report["findings"]
                 if item["kind"] == "pdf-xobject-text"]
    shared_action_locations = sorted({item["location"]["dictionary_path"] for item in report["findings"]
                                      if item["capability"] == "actions"
                                      and item["target"] == "https://page.example.invalid/original#anchor"})
    # THEN exact source, stream bytes and every trigger location remain deterministic and retrievable
    assert (targets, scripts, button_locations, read_document_artifact(path, embedded["artifact"]),
            report["source"]["sha256"], report == inspect_document(path)) == (
        [button, "https://form.example.invalid/submit", "https://page.example.invalid/original#anchor",
         "https://xfa.example.invalid/send", "tool.exe"],
        [script.decode(), "orphanProof(42);", 'xfaProof("literal");'], [(1, 1)], attachment,
        hashlib.sha256(source).hexdigest(), True,
    ), "actions, scripts, PDF locations and decoded attachment bytes must preserve their source evidence"
    assert form_text == [("orphan form text\n", 17)], "unreferenced Form XObjects must retain independently decoded text"
    assert shared_action_locations == ["Catalog/AA/WC", "Catalog/OpenAction/Next[1]",
                                       "Catalog/Pages/Kids[1]/AA/O"], (
        "a shared action object must preserve each document, page and chained trigger occurrence"
    )
    assert coverage == [
        ("inventory", "complete"), ("text", "complete"), ("links", "complete"),
        ("scripts", "complete"), ("actions", "complete"), ("forms", "complete"),
        ("attachments", "complete"), ("images", "complete"), ("cells", "not_applicable"),
        ("formulas", "not_applicable"), ("names", "not_applicable"),
        ("external_references", "complete"), ("hidden_structures", "complete"), ("macros", "not_applicable"),
    ], "fully decoded supported PDF features must complete every applicable capability"


@pytest.mark.parametrize("failure", ["action-cycle", "unknown-action", "bad-flate", "exhausted-flate"])
def test_pdf_inspection_reports_cycles_unsupported_actions_and_bounded_decoder_failures(tmp_path, failure):
    # GIVEN a PDF with one localized unsupported or exhausted static component
    source, _target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    additions = {
        "action-cycle": (b"<< /S /URI /URI (https://cycle.example.invalid/) /Next 7 0 R >>", {}),
        "unknown-action": (b"<< /S /FutureAction /F (opaque-target) >>", {}),
        "bad-flate": (_stream(b"not-deflate", b"/Filter /FlateDecode"), {}),
        "exhausted-flate": (_stream(zlib.compress(b"x" * 512), b"/Filter /FlateDecode"),
                             {"decoded_bytes": 256}),
    }
    replacement, limits = additions[failure]
    objects[7] = replacement
    path = tmp_path / (failure + ".pdf")
    path.write_bytes(_pdf(objects))
    assert path.read_bytes().count(replacement) == 1, "the malformed component must occur in the original input"
    # WHEN inspection reaches the malformed graph or its bounded stream decoder
    report, _payloads = _inspect_document(path, _limits=limits)
    failures = {item["capability"]: item for item in report["coverage"]
                if item["status"] in ("partial", "unavailable")}
    expected = {"action-cycle": "actions", "unknown-action": "actions",
                "bad-flate": "text", "exhausted-flate": "inventory"}[failure]
    # THEN the affected capability retains an explicit reason and source location
    assert (expected in failures, len(failures[expected]["reason"]) > 0,
            len(failures[expected]["locations"]) > 0) == (True, True, True), (
        "malformed, cyclic, unsupported and exhausted components must never become complete-empty evidence"
    )


@pytest.mark.parametrize("filter_name,encoded,expected", [
    ("FlateDecode", zlib.compress(b"ABC"), b"ABC"),
    ("ASCIIHexDecode", b"414243>", b"ABC"),
    ("ASCII85Decode", b"z~>", b"\x00\x00\x00\x00"),
    ("RunLengthDecode", b"\x02ABC\x80", b"ABC"),
    ("LZWDecode", bytes.fromhex("801048443808"), b"ABC"),
])
def test_pdf_stream_decoders_preserve_exact_bytes_and_reuse_admitted_data(filter_name, encoded, expected):
    # GIVEN one original PDF filter stream with a known byte sequence
    stream = PDFStream({"Filter": LIT(filter_name)}, encoded)
    context, location = InspectionContext(), {"kind": "pdf", "object_id": 1}
    assert stream.rawdata == encoded, "the decoder must receive original encoded stream bytes"
    # WHEN the same stream is decoded twice within one inspection
    first = _decode_stream(stream, context, location)
    second = _decode_stream(stream, context, location)
    # THEN exact decoded bytes and their cumulative accounting remain stable
    assert (first, second, stream.rawdata, context.consumed["decoded_bytes"]) == (
        expected, expected, encoded, len(expected),
    ), "decoding must retain original bytes and charge an admitted decoded stream once"


@pytest.mark.parametrize("filter_name,encoded", [
    ("FlateDecode", zlib.compress(b"ABC")), ("ASCIIHexDecode", b"414243>"),
    ("ASCII85Decode", b"z~>"), ("RunLengthDecode", b"\x02ABC\x80"),
    ("LZWDecode", bytes.fromhex("801048443808")),
])
def test_pdf_stream_decoders_record_exhaustion_before_excess_output(filter_name, encoded):
    # GIVEN a valid encoded stream whose decoded bytes exceed the shared allowance
    stream = PDFStream({"Filter": LIT(filter_name)}, encoded)
    context = InspectionContext(limits={"decoded_bytes": 2})
    location = {"kind": "pdf", "object_id": 1}
    assert context.remaining("decoded_bytes") == 2, "the decoder must begin with the explicit shared allowance"
    # WHEN a bounded decoder would need to admit a third output byte
    with pytest.raises(InspectionLimit, match="decoded_bytes limit exceeded: 2"):
        _decode_stream(stream, context, location)
    # THEN no decoded output is admitted and the exact exhausted source location remains
    assert (context.consumed["decoded_bytes"], context.exhausted, stream.rawdata) == (
        0, [{"limit": "decoded_bytes", "maximum": 2, "location": location}], encoded,
    ), "every expansion codec must record the cumulative limit without retaining excess decoded bytes"


@pytest.mark.parametrize("case,object_id,expected_status,expected_kind,expected_disposition", [
    ("reserved-zero", 0, "complete", "pdf-xref-entry", "extracted"),
    ("actual-object-at-zero", 7, "unavailable", "pdf-object", "unreadable"),
])
def test_reserved_xref_zero_is_metadata_while_invalid_actual_object_stays_unreadable(
        tmp_path, case, object_id, expected_status, expected_kind, expected_disposition):
    # GIVEN the Quartz reserved-zero entry pattern or a damaged actual object's offset
    source, _target = _image_button_pdf(text="")
    action_offset = source.index(b"7 0 obj\n")
    damaged = {
        "reserved-zero": source.replace(b"0000000000 65535 f \n", b"0000000000 00000 n \n", 1),
        "actual-object-at-zero": source.replace(("%010d 00000 n \n" % action_offset).encode(),
                                                b"0000000000 00000 n \n", 1),
    }[case]
    path = tmp_path / (case + ".pdf")
    path.write_bytes(damaged)
    assert damaged.count(b"0000000000 00000 n \n") == 1, "the original xref must contain one explicit zero-offset in-use row"
    # WHEN independent inspection distinguishes reserved metadata from actual object bytes
    report = inspect_document(path)
    component = next(item for item in report["components"]
                     if item["location"].get("object_id") == object_id)
    inventory = next(item for item in report["coverage"] if item["capability"] == "inventory")
    # THEN reserved object zero is inventoried while a corrupt nonzero reference remains open
    assert (inventory["status"], component["kind"], component["disposition"],
            component["location"]["xref_offset"], report["source"]["sha256"],
            inspect_document(path) == report) == (
        expected_status, expected_kind, expected_disposition, 0, hashlib.sha256(damaged).hexdigest(), True,
    ), "handling reserved object zero must not suppress a malformed actual object at offset zero"


def _structural_pdf(case):
    source, _target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    if case == "glyph-name-A":
        objects[5] = (b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 600 700] "
                      b"/FontMatrix [.001 0 0 .001 0 0] /CharProcs << /A 9 0 R >> "
                      b"/Encoding << /Type /Encoding /Differences [65 /A] >> "
                      b"/FirstChar 65 /LastChar 65 /Widths [600] >>")
        objects[9] = _stream(b"600 0 d0\n")
    else:
        objects[6] = objects[6].replace(b"/FT /Btn", b"/FT /Sig")[:-2] + b" /V 9 0 R >>"
        objects[9] = (b"<< /Type /Sig /Filter /Adobe.PPKLite /Contents <0000> "
                      b"/Reference [<< /Type /SigRef /TransformMethod /DocMDP /Data 1 0 R "
                      b"/TransformParams << /Type /TransformParams /P 2 /V /1.2 >> >>] >>")
    return _pdf(objects)


@pytest.mark.parametrize("case,source_marker", [
    ("glyph-name-A", b"/CharProcs << /A 9 0 R >>"),
    ("signature-data-catalog", b"/Data 1 0 R"),
])
def test_pdf_resource_names_and_signature_data_edges_are_not_action_failures(tmp_path, case, source_marker):
    # GIVEN a resource key named A or a signature reference to its signed document root
    source = _structural_pdf(case)
    path = tmp_path / (case + ".pdf")
    path.write_bytes(source)
    assert source.count(source_marker) == 1, "the structural source edge must occur exactly once"
    # WHEN independent inspection retains the source structure without treating it as executable action flow
    report = inspect_document(path)
    open_rows = [(item["capability"], item["reason"]) for item in report["coverage"]
                 if item["status"] in ("partial", "unavailable")]
    # THEN every applicable capability remains complete and its original source identity stays bound
    assert (open_rows, report["source"]["sha256"]) == (
        [], hashlib.sha256(source).hexdigest(),
    ), "glyph resource names and signed-data backreferences must not manufacture action or cycle failures"


def _password_pdf():
    """Encrypt synthetic strings and streams using the PDF revision-2 security envelope."""
    source, target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    padding = PDFStandardSecurityHandler.PASSWORD_PADDING
    identifier = hashlib.md5(b"synthetic-password-pdf").digest()
    padded_user = (b"secret" + padding)[:32]
    owner_key = hashlib.md5((b"owner" + padding)[:32]).digest()[:5]
    owner = Arcfour(owner_key).encrypt(padded_user)
    key = hashlib.md5(padded_user + owner + b"\xfc\xff\xff\xff" + identifier).digest()[:5]
    user = Arcfour(key).encrypt(padding)

    def encrypt(payload, object_id):
        """Apply the source object's revision-2 key to its exact payload bytes."""
        object_key = hashlib.md5(key + object_id.to_bytes(3, "little") + b"\x00\x00").digest()[:10]
        return Arcfour(object_key).encrypt(payload)

    objects[4] = _stream(encrypt(b"q 20 0 0 20 100 100 cm /Im1 Do Q\n", 4))
    objects[6] = objects[6].replace(b"(image-button)", b"<" + encrypt(b"image-button", 6).hex().encode() + b">")
    objects[7] = b"<< /S /URI /URI <" + encrypt(target.encode(), 7).hex().encode() + b"> >>"
    objects[8] = _stream(encrypt(b"\xff\x00\x00", 8), b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
                         b"/ColorSpace /DeviceRGB /BitsPerComponent 8")
    objects[9] = (b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 /O <" + owner.hex().encode()
                  + b"> /U <" + user.hex().encode() + b"> >>")
    encrypted = _pdf(objects).replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Encrypt 9 0 R /ID [<"
                                      + identifier.hex().encode() + b"><" + identifier.hex().encode() + b">] >>")
    return encrypted, target


def test_pdf_password_encryption_stays_explicitly_unavailable_without_credentials(tmp_path):
    # GIVEN a password-encrypted PDF whose action and image decrypt correctly with its known password
    source, target = _password_pdf()
    unlocked = PDFDocument(PDFParser(io.BytesIO(source)), password="secret")
    assert (unlocked.getobj(7)["URI"], unlocked.getobj(8).get_data()) == (
        target.encode(), b"\xff\x00\x00",
    ), "the source must contain genuinely encrypted action strings and image stream bytes"
    path = tmp_path / "password.pdf"
    path.write_bytes(source)
    # WHEN independent inspection has no password or credential input
    report = inspect_document(path)
    inventory = next(item for item in report["coverage"] if item["capability"] == "inventory")
    # THEN encryption cannot become a complete empty inspection report
    assert (inventory["status"], "PDFPasswordIncorrect" in inventory["reason"],
            report["findings"], report["source"]["sha256"]) == (
        "unavailable", True, [], hashlib.sha256(source).hexdigest(),
    ), "password-protected input must retain source identity and an explicit authentication failure"


@pytest.mark.parametrize("fonts,encoding,cid,expected", [
    ((("F1", SUMMATION_PROGRAM), ("F2", PRODUCT_PROGRAM)), "", 80,
     [(5, 6, 80, "summationtext", "type1-header", "[sum]"),
      (7, 8, 80, "productdisplay", "type1-header", "[product]")]),
    ((("F1", OVERRIDDEN_PROGRAM),), PDF_DIFFERENCES, 255,
     [(5, None, 255, "productdisplay", "pdf-encoding", "[product]")]),
    ((("F1", type1_program(((80, "bracehtipdownleft"),))),), "", 80,
     [(5, 6, 80, "bracehtipdownleft", "type1-header", None)]),
], ids=["same-basefont-distinct-resources", "explicit-pdf-encoding", "named-glyph-without-unicode"])
def test_pdf_named_glyph_source_identity_preserves_resource_and_encoding_precedence(
        tmp_path, fonts, encoding, cid, expected):
    # GIVEN source-declared glyph names that the font engine cannot map to Unicode
    source = font_identity_pdf(fonts, encoding, cid)
    path = tmp_path / "named-source.pdf"
    path.write_bytes(source)
    assert source.count(b"/BaseFont /" + FONT_NAME.encode()) == len(fonts), "each source font must retain its own same-name resource identity"
    # WHEN independent text inspection recovers the literal encoding rather than inventing Unicode
    report = inspect_document(path)
    glyphs = [item for item in report["findings"] if item["kind"] == "pdf-named-glyph"]
    actual = [(item["location"]["font_object_id"], item["location"].get("font_file_object_id"),
               item["details"]["cid"], item["details"]["glyph_name"],
               item["details"]["encoding_origin"], item["details"]["readable_token"]) for item in glyphs]
    text = next(item for item in report["coverage"] if item["capability"] == "text")
    # THEN every named occurrence remains exact, scoped and explicitly distinct from Unicode decoding
    assert (actual, {(item["details"]["representation"], item["details"]["unicode_mapping"]) for item in glyphs},
            text["status"], "named glyph" in text["reason"]) == (
        expected, {("glyph-name", "unavailable")}, "complete", True,
    ), "source glyph identity must not mix resources, override explicit Encoding or claim an invented Unicode value"


@pytest.mark.parametrize("program", [type1_program(()), type1_program(((80, "summationtext"), (80, "productdisplay")))],
                         ids=["undeclared-code", "ambiguous-code"])
def test_pdf_undeclared_or_ambiguous_glyphs_keep_text_coverage_open(tmp_path, program):
    # GIVEN a used code that has no unambiguous source encoding identity
    source = font_identity_pdf((("F1", program),), "", 80)
    path = tmp_path / "unresolved-source.pdf"
    path.write_bytes(source)
    assert source.count(b"\\120") == 1, "the source must use the specific unresolved character code"
    # WHEN independent inspection considers font names only as declared source evidence
    report = inspect_document(path)
    text = next(item for item in report["coverage"] if item["capability"] == "text")
    glyphs = [item for item in report["findings"] if item["kind"] == "pdf-named-glyph"]
    # THEN a generic font/CID placeholder cannot turn the missing mapping into completeness
    assert (text["status"], glyphs) == (
        "unavailable", [],
    ), "undeclared and duplicate code assignments must remain explicit incomplete text evidence"


def test_pdf_named_glyph_preserves_exact_header_bytes_and_component_provenance(tmp_path):
    # GIVEN one declared Type1 glyph whose Unicode identity is unavailable
    program = type1_program(((80, "summationtext"),))
    path = tmp_path / "font-source.pdf"
    source = font_identity_pdf((("F1", program),), "", 80)
    path.write_bytes(source)
    assert program.count("dup 80 /summationtext put") == 1, "the header must declare one exact source glyph assignment"
    # WHEN independent inspection retains source identity instead of assigning a Unicode substitute
    report = inspect_document(path)
    glyph = next(item for item in report["findings"] if item["kind"] == "pdf-named-glyph")
    header = next(item for item in report["components"] if item["id"] == glyph["component"])
    font_file = next(item for item in report["components"]
                     if item["location"].get("object_id") == 6 and item["kind"] == "pdf-stream")
    # THEN the report binds the exact font, source occurrence and retrievable header boundary
    section = _last_xref(source)
    page_start = source.index(b"3 0 obj\n")
    page_end = source.index(b"endobj", page_start) + len(b"endobj")
    entry_start = section + len(b"xref\n0 7\n") + 3 * len(b"0000000000 65535 f \n")
    revision = "xref@%d" % section
    assert (glyph["text"], glyph["location"], header["location"], header["sha256"],
            read_document_artifact(path, glyph["artifact"]), glyph["details"]["font_file_artifact"]) == (
        "[font-glyph:5:80:summationtext]",
        {"kind": "pdf", "page": 1, "object_id": 3, "dictionary_path": "Page/Contents",
         "font_object_id": 5, "font_file_object_id": 6, "text_show_ordinal": 1, "glyph_ordinal": 1,
         "byte_basis": "file", "byte_start": page_start, "byte_end": page_end,
         "generation": 0, "revision": 0, "revision_id": revision, "revision_status": "linked",
         "reference_revision": 0, "reference_revision_id": revision,
         "source_revision": 0, "source_revision_id": revision,
         "xref_entry_basis": "file", "xref_entry_start": entry_start, "xref_entry_end": entry_start + 20,
         "xref_entry_kind": "in_use", "xref_entry_ordinal": 4, "xref_offset": page_start,
         "xref_section_offset": section, "xref_section_role": "main"},
        {"kind": "pdf", "object_id": 6, "dictionary_path": "Object/Type1Header", "byte_start": 0,
         "byte_end": len(program.encode()), "byte_basis": "decoded_fontfile"},
        hashlib.sha256(program.encode()).hexdigest(), program.encode(), font_file["artifact"],
    ), "source glyphs must retain exact header bytes and measured locations without claiming Unicode decoding"


@pytest.mark.corpus
def test_public_dpo_inspection_recovers_all_seventeen_named_font_codes_without_unicode_invention():
    # GIVEN the licensed DPO fixture with a source-declared Computer Modern encoding
    path = Path(__file__).parent / "fixtures" / "pdf" / "arxiv-2305.18290v3.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "92cb3a2b71362acda98a789b03d88688fd33cf5fcf13f81d2b1de30ee7d3b67a"
    ), "the font evidence must come from the unchanged licensed DPO source"
    # WHEN the original Type1 header supplies exact names missing from the Unicode mapping
    report, payloads = _inspect_document(path)
    glyphs = [item for item in report["findings"] if item["kind"] == "pdf-named-glyph"
              and item["location"]["font_object_id"] == 228]
    recovered = sorted({(item["details"]["cid"], item["details"]["glyph_name"]) for item in glyphs})
    text = next(item for item in report["coverage"] if item["capability"] == "text")
    header = payloads[glyphs[0]["artifact"]]
    # THEN every code is accounted for by source name, including names without any semantic token
    assert (recovered, text["status"], {(item["details"]["representation"], item["details"]["unicode_mapping"])
                                      for item in glyphs}, len(header), hashlib.sha256(header).hexdigest(),
            {(item["location"]["font_file_object_id"], item["details"]["font_descriptor_object_id"])
             for item in glyphs}) == (
        [(2, "bracketleftbig"), (3, "bracketrightbig"), (8, "braceleftbig"), (9, "bracerightbig"),
         (16, "parenleftBig"), (17, "parenrightBig"), (18, "parenleftbigg"), (19, "parenrightbigg"),
         (20, "bracketleftbigg"), (21, "bracketrightbigg"), (80, "summationtext"),
         (88, "summationdisplay"), (89, "productdisplay"), (122, "bracehtipdownleft"),
         (123, "bracehtipdownright"), (124, "bracehtipupleft"), (125, "bracehtipupright")],
        "complete", {("glyph-name", "unavailable")}, 1931,
        "0595b24b8f4e9af6cb235020bf6653ec20c901f9fe16549333d20ad566554bc0", {(899, 900)},
    ), "named source recovery must close the real text gap without inventing Unicode for brace-tip glyph names"


def _script_pdf(script, *, kind="text-string", version="1.4", catalog_version=None,
                stream_payload=None, stream_attributes=b""):
    source, _target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    extra = b" /OpenAction 9 0 R"
    if catalog_version is not None:
        extra += b" /Version /" + catalog_version.encode()
    objects[1] = objects[1][:-2] + extra + b" >>"
    if kind == "text-stream":
        objects[9] = b"<< /S /JavaScript /JS 10 0 R >>"
        objects[10] = _stream(script if stream_payload is None else stream_payload, stream_attributes)
    else:
        objects[9] = b"<< /S /JavaScript /JS <" + script.hex().encode() + b"> >>"
    return _pdf(objects).replace(b"%PDF-1.4", b"%PDF-" + version.encode(), 1)


@pytest.mark.parametrize("kind", ["text-string", "text-stream"])
def test_pdf_javascript_ambiguous_utf8_bytes_use_declared_pdf_text_encoding(tmp_path, kind):
    # GIVEN PDF 1.4 source bytes that are valid PDFDocEncoding and also happen to form UTF-8
    script = b"app.alert('\xc3\xa9');"
    path = tmp_path / "script-encoding.pdf"
    path.write_bytes(_script_pdf(script, kind=kind))
    assert script.hex() == "6170702e616c6572742827c3a927293b", "the source must contain the exact ambiguous C3 A9 bytes"
    # WHEN independent inspection decodes the document's text string or text stream
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    # THEN UTF-8 validity alone cannot change the original program's character sequence
    assert [item["text"] for item in scripts] == ["app.alert('Ã©');"], (
        "PDF 1.4 JavaScript must use PDFDocEncoding unless its specified Unicode BOM is present"
    )
    assert [(item["details"]["encoding"], item["details"]["pdf_version"],
             read_document_artifact(path, item["artifact"])) for item in scripts] == [
        ("PDFDocEncoding", "1.4", script),
    ], "the interpreted program must retain its exact source bytes and explicit encoding provenance"


def test_pdf_xfa_script_artifact_contains_only_decoded_script_source(tmp_path):
    # GIVEN an XFA XML packet containing one original script body
    source, _button, _script, _attachment = _features_pdf()
    path = tmp_path / "xfa-script.pdf"
    path.write_bytes(source)
    assert b"/XFA [(template) 14 0 R]" in source, "the source must retain its separate XFA packet reference"
    # WHEN script retrieval follows the finding's own artifact reference
    report = inspect_document(path)
    finding = next(item for item in report["findings"] if item["kind"] == "xfa-script")
    script = read_document_artifact(path, finding["artifact"])
    # THEN script bytes are separate from the preserved XML container and retain their source path
    assert script == b'xfaProof("literal");', "an XFA script artifact must contain exactly its decoded script body"
    container = read_document_artifact(path, finding["details"]["container_artifact"])
    assert (finding["details"]["encoding"], finding["location"]["xpath"], len(container)) == (
        "UTF-8", "/template[1]/subform[1]/event[1]/script[1]", 274,
    ), "XFA script provenance must preserve its container reference, character encoding and exact source xpath"


def test_pdf_predictor_javascript_stream_preserves_native_decoded_source(tmp_path):
    # GIVEN a standard PNG-Up predictor on a JavaScript text stream
    script = b"app.alert('predictor-source');"
    encoded = zlib.compress(b"\x02" + script)
    parameters = {"Predictor": 12, "Columns": len(script), "Colors": 1, "BitsPerComponent": 8}
    native = PDFStream({"Filter": LIT("FlateDecode"), "DecodeParms": parameters}, encoded).get_data()
    assert native == script, "the existing PDF decoder must independently recover the original predicted bytes"
    attributes = ("/Filter /FlateDecode /DecodeParms << /Predictor 12 /Columns %d "
                  "/Colors 1 /BitsPerComponent 8 >>" % len(script)).encode()
    path = tmp_path / "predictor-script.pdf"
    path.write_bytes(_script_pdf(script, kind="text-stream", stream_payload=encoded, stream_attributes=attributes))
    # WHEN independent inspection applies bounded declared binary transforms
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    # THEN prediction must neither erase a recoverable program nor manufacture an empty successful source
    assert [item["text"] for item in scripts] == [script.decode("ascii")], (
        "standard predictor transforms must recover JavaScript source without code execution or OCR"
    )


@pytest.mark.parametrize("version,catalog,script,expected,encoding,basis", [
    ("1.4", None, b"\xfe\xff" + "proof('é');".encode("utf-16be"), "proof('é');", "UTF-16BE", "header"),
    ("1.4", None, b"\xff\xfe" + b"A\x00", "ÿþA\x00", "PDFDocEncoding", "header"),
    ("1.4", None, b"\xef\xbb\xbf" + "proof('é');".encode(), "ï»¿proof('Ã©');", "PDFDocEncoding", "header"),
    ("2.0", None, b"\xef\xbb\xbf" + "proof('é');".encode(), "proof('é');", "UTF-8", "header"),
    ("2.0", None, b"proof('\xc3\xa9');", "proof('Ã©');", "PDFDocEncoding", "header"),
    ("1.4", "2.0", b"\xef\xbb\xbf" + "proof('é');".encode(), "proof('é');", "UTF-8", "catalog"),
])
def test_pdf_javascript_bom_and_version_rules_preserve_exact_program_characters(
        tmp_path, version, catalog, script, expected, encoding, basis):
    # GIVEN explicit source bytes and a measured PDF header/catalog version declaration
    source = _script_pdf(script, version=version, catalog_version=catalog)
    path = tmp_path / "versioned-script.pdf"
    path.write_bytes(source)
    native = PDFDocument(PDFParser(io.BytesIO(source)))
    assert native.getobj(9)["JS"] == script, "the source PDF decoder must preserve every original text-string byte"
    # WHEN passive inspection applies only the encoding admitted by the declared version and BOM
    report = inspect_document(path)
    finding = next(item for item in report["findings"] if item["kind"] == "pdf-javascript")
    # THEN marker-free UTF-8 validity and a little-endian-looking prefix cannot override PDF text rules
    assert (finding["text"], finding["details"]["encoding"], finding["details"]["version_source"],
            read_document_artifact(path, finding["artifact"])) == (
        expected, encoding, basis, script,
    ), "JavaScript characters, raw source bytes and explicit encoding/version provenance must remain exact"


@pytest.mark.parametrize("version,catalog,script", [
    ("2.1", None, b"\xef\xbb\xbfproof();"), ("1.4", "banana", b"proof();"),
    ("1.0", None, b"proof();"),
    ("1.4", None, b"\xfe\xff\x00"),
])
def test_pdf_javascript_invalid_version_or_unicode_retains_unavailable_source_artifact(tmp_path, version, catalog, script):
    # GIVEN an unsupported version declaration or a malformed normative Unicode string
    source = _script_pdf(script, version=version, catalog_version=catalog)
    path = tmp_path / "invalid-encoding.pdf"
    path.write_bytes(source)
    assert source.count(script.hex().encode()) == 1, "the original program bytes must remain in the source text string"
    # WHEN inspection cannot establish a valid normative character interpretation
    report = inspect_document(path)
    valid = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    unavailable = next(item for item in report["findings"] if item["kind"] == "pdf-javascript-unavailable")
    coverage = next(item for item in report["coverage"] if item["capability"] == "scripts")
    # THEN unknown source characters cannot become an empty successful program
    assert (valid, unavailable["details"]["encoding"], coverage["status"],
            read_document_artifact(path, unavailable["artifact"])) == (
        [], "unavailable", "partial", script,
    ), "undecodable JavaScript must retain exact source bytes with explicitly incomplete coverage"


@pytest.mark.parametrize("predictor,colors,columns,bits,encoded,expected", [
    (2, 1, 8, 1, b"\xeb", b"\xb2"), (2, 1, 4, 2, b"\x6e", b"\x78"),
    (2, 1, 3, 2, b"\x6e", b"\x7a"),
    (2, 1, 2, 4, b"\xa9", b"\xa3"),
    (2, 3, 2, 8, bytes((10, 20, 30, 5, 255, 10)), bytes((10, 20, 30, 15, 19, 40))),
    (2, 1, 2, 16, b"\x10\x00\xff\xff", b"\x10\x00\x0f\xff"),
    (15, 1, 8, 1, b"\x00\xb2", b"\xb2"), (15, 1, 4, 2, b"\x00\x78", b"\x78"),
    (15, 1, 3, 2, b"\x00\x7a", b"\x7a"),
    (15, 1, 2, 4, b"\x00\xa3", b"\xa3"),
    (15, 3, 2, 8, bytes((1, 10, 20, 30, 5, 255, 10)), bytes((10, 20, 30, 15, 19, 40))),
    (15, 1, 2, 16, b"\x01\x10\x00\xff\xff", b"\x10\x00\x0f\xff"),
    (12, 1, 3, 8, b"\x00ABC", b"ABC"), (12, 1, 3, 8, b"\x01A\x01\x01", b"ABC"),
    (12, 1, 3, 8, b"\x02ABC\x02\x01\x01\x01", b"ABCBCD"),
    (12, 1, 3, 8, b'\x03A""\x03"\x01\x01', b"ABCBCD"),
    (12, 1, 3, 8, b"\x04A\x01\x01\x04\x01\x01\x01", b"ABCBCD"),
])
def test_pdf_predictors_recover_packed_samples_and_all_png_row_tags(predictor, colors, columns, bits, encoded, expected):
    # GIVEN checked source rows covering packed/16-bit TIFF and every standard PNG byte predictor
    compressed = zlib.compress(encoded)
    stream = PDFStream({"Filter": LIT("FlateDecode"), "DecodeParms": {
        "Predictor": predictor, "Colors": colors, "Columns": columns, "BitsPerComponent": bits,
    }}, compressed)
    assert zlib.decompress(compressed) == encoded, "the native compression layer must preserve the exact predicted row bytes"
    # WHEN both binary transforms share the inspection operation's decoded-byte allowance
    context = InspectionContext()
    actual = _decode_stream(stream, context, {"kind": "pdf", "object_id": 1})
    # THEN packed components, wraparound, byte padding and actual row tags recover deterministic source bytes
    assert (actual, context.consumed["decoded_bytes"]) == (
        expected, len(encoded) + len(expected),
    ), "every supported predictor must preserve exact bytes and charge both admitted transform results"


def _literal_lzw(payload):
    bits = "".join(format(value, "09b") for value in (256, *payload, 257))
    length = (len(bits) + 7) // 8
    return int(bits.ljust(length * 8, "0"), 2).to_bytes(length, "big")


@pytest.mark.parametrize("codec", ["LZWDecode", "ASCIIHexDecode+FlateDecode"])
def test_pdf_predictors_follow_lzw_and_chained_filter_order(codec):
    # GIVEN PNG-Up rows inside a literal LZW stream or an ASCIIHex/Flate chain
    predicted, expected = b"\x02ABC\x02\x01\x01\x01", b"ABCBCD"
    parameters = {"Predictor": 12, "Columns": 3, "Colors": 1, "BitsPerComponent": 8}
    data, attributes = {
        "LZWDecode": (_literal_lzw(predicted), {"Filter": LIT("LZWDecode"), "DecodeParms": parameters}),
        "ASCIIHexDecode+FlateDecode": (zlib.compress(predicted).hex().encode() + b">", {
            "Filter": [LIT("ASCIIHexDecode"), LIT("FlateDecode")], "DecodeParms": [None, parameters],
        }),
    }[codec]
    assert PDFStream(attributes, data).get_data() == expected, "the installed PDF binding must independently verify the encoded filter chain"
    # WHEN inspection decodes each declared filter and its predictor in source order
    actual = _decode_stream(PDFStream(attributes, data), InspectionContext(), {"kind": "pdf", "object_id": 1})
    # THEN prediction applies to the correct intermediate result without fetching or executing code
    assert actual == expected, "LZW and chained Flate prediction must recover the same exact original source bytes"


@pytest.mark.parametrize("parameters,encoded", [
    ({"Predictor": 12, "Columns": 3}, b"\x02AB"),
    ({"Predictor": 12, "Columns": 3}, b"\x05ABC"),
    ({"Predictor": 2, "Columns": 2, "Colors": 3}, b"ABCDE"),
    ({"Predictor": 12, "Columns": 0}, b"\x00"),
    ({"Predictor": 12, "Colors": 0}, b"\x00"),
    ({"Predictor": 12, "BitsPerComponent": 3}, b"\x00"),
    ({"Predictor": 3}, b"\x00"),
    ({"Predictor": True}, b"ABC"),
])
def test_pdf_malformed_predictors_cannot_be_reused_as_empty_success(parameters, encoded):
    # GIVEN malformed dimensions, unsupported prediction or incomplete/invalid source rows
    compressed = zlib.compress(encoded)
    stream = PDFStream({"Filter": LIT("FlateDecode"), "DecodeParms": parameters}, compressed)
    assert stream.rawdata == compressed, "the original compressed source must remain available before admission"
    # WHEN a failed decoder is encountered again after a caller's empty-cache sentinel
    context, location = InspectionContext(), {"kind": "pdf", "object_id": 1}
    with pytest.raises(BrewdocError, match="PDF"):
        _decode_stream(stream, context, location)
    stream.data = b""
    with pytest.raises(BrewdocError, match="PDF"):
        _decode_stream(stream, context, location)
    # THEN the failed transform retains its original source rather than authorizing an empty script
    assert stream.rawdata == compressed, "negative decoder caching must preserve the exact original encoded bytes"


def test_pdf_predictor_dimension_bomb_is_refused_before_row_allocation():
    # GIVEN one decoded byte and a declared row larger than the per-payload allowance
    encoded = zlib.compress(b"\x00")
    stream = PDFStream({"Filter": LIT("FlateDecode"), "DecodeParms": {
        "Predictor": 12, "Columns": 2 ** 31, "BitsPerComponent": 8,
    }}, encoded)
    context, location = InspectionContext(limits={"payload_bytes": 16}), {"kind": "pdf", "object_id": 1}
    assert len(encoded) == 9, "the input must fit its allowance while its claimed row does not"
    # WHEN a predictor would allocate a declared multi-gigabyte row
    with pytest.raises(InspectionLimit, match="payload_bytes limit exceeded: 16"):
        _decode_stream(stream, context, location)
    # THEN no predicted result is admitted and the precise exhausted source boundary remains explicit
    assert (stream.data, stream.rawdata, context.consumed["decoded_bytes"], context.exhausted) == (
        None, encoded, 1, [{"limit": "payload_bytes", "maximum": 16, "location": location}],
    ), "predictor dimension bounds must precede buffer growth and retain exact source/exhaustion evidence"


def test_pdf_xfa_unicode_script_artifact_preserves_xml_character_data_and_original_packet(tmp_path):
    # GIVEN a UTF-16 XML packet whose source characters are independent of PDF text-string encoding
    script = "proof('é');"
    packet = ('<?xml version="1.0" encoding="UTF-16"?><template><script>' + script + '</script></template>').encode("utf-16")
    source, _target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", source)}
    objects[1] = objects[1].replace(b"/Fields [6 0 R]", b"/Fields [6 0 R] /XFA 9 0 R")
    objects[9] = _stream(zlib.compress(packet), b"/Filter /FlateDecode")
    path = tmp_path / "unicode-xfa.pdf"
    path.write_bytes(_pdf(objects))
    assert packet[:2] == b"\xff\xfe", "the XML container must carry its original declared UTF-16 byte order"
    # WHEN extraction serializes the exact decoded script characters as a separate UTF-8 artifact
    report = inspect_document(path)
    finding = next(item for item in report["findings"] if item["kind"] == "xfa-script")
    # THEN script characters, output encoding and original packet bytes retain distinct provenance
    assert (finding["text"], finding["details"]["encoding"], finding["details"]["encoding_source"],
            read_document_artifact(path, finding["artifact"]),
            read_document_artifact(path, finding["details"]["container_artifact"])) == (
        script, "UTF-8", "decoded-xml-character-data", script.encode(), packet,
    ), "XML script retrieval must preserve exact decoded characters and separately retain the original encoded packet"


def test_pdf_unsupported_script_filter_preserves_source_without_empty_success(tmp_path):
    # GIVEN a script stream whose declared filter has no admitted decoder
    script = b"opaqueProof();"
    path = tmp_path / "opaque-script.pdf"
    path.write_bytes(_script_pdf(script, kind="text-stream", stream_attributes=b"/Filter /UnknownProof"))
    assert path.read_bytes().count(script) == 1, "the undecoded original stream bytes must remain present in the PDF"
    # WHEN the graph revisits the failed stream after object inventory
    report = inspect_document(path)
    valid = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    unavailable = next(item for item in report["findings"] if item["kind"] == "pdf-javascript-unavailable")
    coverage = next(item for item in report["coverage"] if item["capability"] == "scripts")
    # THEN the exact opaque payload remains retrievable without an invented empty program
    assert (valid, unavailable["details"]["encoding"], coverage["status"],
            read_document_artifact(path, unavailable["artifact"])) == (
        [], "unavailable", "partial", script,
    ), "unsupported stream transforms must remain incomplete and retain their exact original source payload"


def _revision_catalog(*, generation=0, version=None, action=True):
    extra = (b" /OpenAction 5 " + str(generation).encode() + b" R") if action else b""
    extra += (b" /Version /" + version.encode()) if version else b""
    return b"<< /Type /Catalog /Pages 2 0 R" + extra + b" >>"


def _revision_base(script=b"oldProof();", *, indirect=False):
    objects = {
        1: _revision_catalog(), 2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << >> /Contents 4 0 R >>",
        4: _stream(b""), 5: b"<< /S /JavaScript /JS <" + script.hex().encode() + b"> >>",
    }
    if indirect:
        objects[5] = b"<< /S /JavaScript /JS 6 0 R >>"
        objects[6] = _stream(zlib.compress(script), b"/Filter /FlateDecode")
    return _pdf(objects).replace(b"%PDF-1.4", b"%PDF-1.7", 1)


def _last_xref(source):
    return int(re.findall(rb"startxref\s+(\d+)", source)[-1])


def _table_revision(source, updates=(), *, free=(), supplement=None, previous=None):
    """Append valid source occurrences without replacing any bytes from an older revision."""
    output, offsets, rows = io.BytesIO(), {}, {}
    output.write(source + b"\n")
    for object_id, generation, body in updates:
        offsets[object_id] = output.tell()
        output.write(("%d %d obj\n" % (object_id, generation)).encode() + body + b"\nendobj\n")
        rows[object_id] = ("%010d %05d n \n" % (offsets[object_id], generation)).encode()
    for object_id, generation in free:
        rows[object_id] = ("0000000000 %05d f \n" % generation).encode()
    section = output.tell()
    output.write(b"xref\n")
    for object_id, row in sorted(rows.items()):
        output.write(("%d 1\n" % object_id).encode() + row)
    size = max(int(re.findall(rb"/Size\s+(\d+)", source)[-1]), max(rows, default=0) + 1)
    parent = section if previous == "self" else _last_xref(source) if previous is None else previous
    extra = b" /XRefStm " + str(offsets[supplement]).encode() if supplement is not None else b""
    output.write(("trailer\n<< /Root 1 0 R /Size %d /Prev %d" % (size, parent)).encode() + extra
                 + (" >>\nstartxref\n%d\n%%%%EOF\n" % section).encode())
    return output.getvalue(), {"section": section, "objects": offsets, "previous": parent}


def _xref_stream_entry(kind, target, generation):
    return bytes((kind,)) + target.to_bytes(4, "big") + generation.to_bytes(2, "big")


def _compressed_revisions(mode="stream", *, header_id=5):
    """Create reused ObjStm IDs with exact table, stream or multi-Index source rows."""
    old, new = b"oldCompressed();", b"newCompressed();"
    old_body = b"<< /S /JavaScript /JS <" + old.hex().encode() + b"> >>\n"
    new_body = b"<< /S /JavaScript /JS <" + new.hex().encode() + b"> >>\n"
    header = ("%d 0 " % header_id).encode()
    prefix = _revision_base().split(b"5 0 obj\n", 1)[0]
    offsets = {int(match[1]): match.start() for match in re.finditer(rb"(?m)^(\d+) 0 obj\n", prefix)}
    container = len(prefix)
    body = _stream(zlib.compress(header + old_body),
                   ("/Type /ObjStm /N 1 /First %d /Filter /FlateDecode" % len(header)).encode())
    prefix += b"6 0 obj\n" + body + b"\nendobj\n"
    section = len(prefix)
    entries = {0: _xref_stream_entry(0, 0, 65535), 5: _xref_stream_entry(2, 6, 0),
               6: _xref_stream_entry(1, container, 0), 7: _xref_stream_entry(1, section, 0)}
    entries.update({number: _xref_stream_entry(1, offset, 0) for number, offset in offsets.items()})
    index = b"[0 1 1 4 5 3]" if mode == "multi-index" else b"[0 8]"
    xref = _stream(zlib.compress(b"".join(entries[number] for number in range(8))),
                   b"/Type /XRef /Size 8 /Root 1 0 R /W [1 4 2] /Index " + index + b" /Filter /FlateDecode")
    original = prefix + b"7 0 obj\n" + xref + b"\nendobj\n" + ("startxref\n%d\n%%%%EOF\n" % section).encode()
    new_container_body = _stream(zlib.compress(header + new_body),
                                 ("/Type /ObjStm /N 1 /First %d /Filter /FlateDecode" % len(header)).encode())
    if mode == "hybrid":
        supplement = _stream(_xref_stream_entry(2, 6, 0),
                              b"/Type /XRef /Size 9 /Root 1 0 R /W [1 4 2] /Index [5 1]")
        revised, metadata = _table_revision(original, ((6, 0, new_container_body), (8, 0, supplement)), supplement=8)
        current_container = metadata["objects"][6]
    else:
        current_container = len(original) + 1
        revised = original + b"\n6 0 obj\n" + new_container_body + b"\nendobj\n"
        current_section = len(revised)
        data = (_xref_stream_entry(2, 6, 0) + _xref_stream_entry(1, current_container, 0)
                + _xref_stream_entry(1, current_section, 0))
        index = b"[5 1 6 2]" if mode == "multi-index" else b"[5 3]"
        xref = _stream(zlib.compress(data), ("/Type /XRef /Size 8 /Root 1 0 R /Prev %d " % section).encode()
                       + b"/W [1 4 2] /Index " + index + b" /Filter /FlateDecode")
        revised += b"7 0 obj\n" + xref + b"\nendobj\n" + ("startxref\n%d\n%%%%EOF\n" % current_section).encode()
    return original, revised, {"old_container": container, "new_container": current_container,
                                "old_body": old_body, "new_body": new_body, "first": len(header)}


@pytest.mark.parametrize("kind", ["literal", "indirect-stream", "versioned"])
def test_pdf_incremental_sources_keep_old_new_programs_with_revision_resolution(tmp_path, kind):
    # GIVEN source revisions whose same-numbered actions or script streams have different meanings
    old, new = b"oldProof();", b"newProof();"
    marker = b"\xef\xbb\xbfproof('\xc3\xa9');"
    fixtures = {
        "literal": (_revision_base(old), ((5, 0, b"<< /S /JavaScript /JS <" + new.hex().encode() + b"> >>"),),
                    ["oldProof();", "newProof();"]),
        "indirect-stream": (_revision_base(old, indirect=True), ((6, 0, _stream(zlib.compress(new), b"/Filter /FlateDecode")),),
                            ["oldProof();", "newProof();"]),
        "versioned": (_revision_base(marker), ((1, 0, _revision_catalog(version="2.0")),
                      (5, 0, b"<< /S /JavaScript /JS <" + marker.hex().encode() + b"> >>")),
                      ["ï»¿proof('Ã©');", "proof('é');"]),
    }
    original, updates, expected = fixtures[kind]
    revised, _metadata = _table_revision(original, updates)
    path = tmp_path / (kind + ".pdf")
    path.write_bytes(revised)
    assert (revised[:len(original)], len(PDFDocument(PDFParser(io.BytesIO(revised))).xrefs)) == (
        original, 2,
    ), "the input must retain its original bytes and two independently parseable xref revisions"
    # WHEN original-document inspection visits each retained source occurrence in its own revision view
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    # THEN an older dictionary cannot be silently substituted or resolved through the newest script bytes
    assert sorted({item["text"] for item in scripts}) == sorted(expected), (
        "both original and current programs must survive, including revision-specific indirect and version semantics"
    )
    assert sorted({(item["location"]["revision"], item["text"]) for item in scripts}) == [
        (0, expected[1]), (1, expected[0]),
    ], "historical program occurrences must retain their revision instead of acquiring a current-document identity"


@pytest.mark.parametrize("mode", ["stream", "multi-index", "hybrid"])
def test_pdf_reused_object_stream_ids_preserve_both_compressed_source_occurrences(tmp_path, mode):
    # GIVEN two valid compressed-object revisions that reuse the same ObjStm container number
    original, revised, metadata = _compressed_revisions(mode)
    old = PDFDocument(PDFParser(io.BytesIO(original))).getobj(5)["JS"]
    new = PDFDocument(PDFParser(io.BytesIO(revised))).getobj(5)["JS"]
    assert (old, new) == (b"oldCompressed();", b"newCompressed();"), (
        "independent native source views must verify each original compressed action before inspection"
    )
    path = tmp_path / (mode + ".pdf")
    path.write_bytes(revised)
    # WHEN inspection resolves each occurrence with a fresh revision-scoped object-stream cache
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    # THEN reused IDs and hybrid/multi-Index layouts cannot discard the older compressed program
    assert sorted({item["text"] for item in scripts}) == ["newCompressed();", "oldCompressed();"], (
        "both physical ObjStm occurrences must retain their exact historical source program"
    )
    containers = sorted({(item["location"]["revision"], item["location"]["xref_offset"])
                         for item in report["components"] if item["kind"] == "pdf-stream"
                         and item["location"].get("object_id") == 6})
    assert containers == [(0, metadata["new_container"]), (1, metadata["old_container"])], (
        "compressed source provenance must identify both physical container offsets and their original revisions"
    )


def test_public_pdf_inline_image_preserves_sample_ending_lf_and_original_source_spans(tmp_path):
    # GIVEN one native inline RGB image whose third sample byte is a real LF
    payload = b"\xff\x00\n"
    content = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID " + payload + b"EI Q"
    source = _pdf({
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << >> /Contents 4 0 R >>",
        4: _stream(content),
    })
    path = tmp_path / "inline-rgb.pdf"
    path.write_bytes(source)
    with pdfplumber.open(path) as native:
        native_images = [(len(page.chars), len(page.images), page.images[0]["srcsize"]) for page in native.pages]
    assert (native_images, content[32:35], source.count(content)) == (
        [(0, 1, (1, 1))], payload, 1,
    ), "an independent PDF parser and original bytes must establish the textless inline image and its exact samples"
    # WHEN the public inspection API inventories original content independently of readable conversion
    report = inspect_document(path)
    inventory = [item for item in report["findings"] if item["kind"] == "pdf-inline-image"]
    occurrences = [item for item in report["findings"] if item["kind"] == "pdf-inline-image-occurrence"]
    # THEN one physical image and its original page occurrence retain the final LF and decoded offsets
    assert (len(inventory), len(occurrences)) == (1, 1), (
        "public inspection must expose both the original inline payload and its available page usage"
    )
    item, occurrence = inventory[0], occurrences[0]
    payload_refs = [entry for entry in report["artifacts"] if entry["kind"] == "image"]
    encoded = occurrence["details"]["encoded_container_artifacts"]
    decoded = occurrence["details"]["decoded_container_artifacts"]
    assert (read_document_artifact(path, item["artifact"]), occurrence["artifact"],
            [(entry["key"], entry["byte_size"], entry["sha256"]) for entry in payload_refs],
            [(occurrence["location"]["page"], occurrence["location"]["object_id"],
              occurrence["location"]["operator_ordinal"], occurrence["location"]["span_basis"],
              occurrence["location"]["payload_span"])],
            [(part["source"]["object_id"], part["span"])
             for part in occurrence["location"]["source_segments"]],
            [read_document_artifact(path, key) for key in encoded],
            [read_document_artifact(path, key) for key in decoded], report == inspect_document(path)) == (
        payload, item["artifact"], [(item["artifact"], 3, hashlib.sha256(payload).hexdigest())],
        [(1, 4, 1, "decoded-stream", [32, 35])], [(4, [32, 35])], [content], [content], True,
    ), "image bytes, hash, source segment and both original content artifacts must round-trip deterministically"


def test_public_pdf_shared_image_retains_both_page_declarations_and_draw_locations(tmp_path):
    # GIVEN the same physical image and content stream referenced and drawn on two pages
    original, _target = _image_button_pdf(text="")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", original)}
    objects[2] = b"<< /Type /Pages /Kids [3 0 R 9 0 R] /Count 2 >>"
    objects[9] = objects[3]
    source = _pdf(objects)
    path = tmp_path / "shared-image.pdf"
    path.write_bytes(source)
    with pdfplumber.open(path) as native:
        native_images = [(page.page_number, len(page.images), page.images[0]["name"]) for page in native.pages]
    assert native_images == [(1, 1, "Im1"), (2, 1, "Im1")], (
        "an independent native parser must establish both original page draws of the shared image"
    )
    # WHEN public inspection resolves image inventory separately from declaration and operator occurrences
    report = inspect_document(path)
    inventory = [item for item in report["findings"] if item["kind"] == "pdf-image"]
    uses = [item for item in report["findings"] if item["kind"] == "pdf-image-occurrence"]
    # THEN every page and draw survives while all uses share one physical payload and component
    assert [(item["location"]["page"], item["location"]["object_id"],
             item["location"]["occurrence_kind"], item["location"]["resource_name"],
             item["location"].get("operator_ordinal"), item["location"].get("operator_span")) for item in uses] == [
        (1, 8, "resource", "Im1", None, None), (1, 4, "draw", "Im1", 3, [23, 30]),
        (2, 8, "resource", "Im1", None, None), (2, 4, "draw", "Im1", 3, [23, 30]),
    ], "original declarations and decoded Do operator spans must retain both page usage locations"
    assert len(inventory) == 1, "a reused physical image must have exactly one source inventory record"
    image = inventory[0]
    payload_refs = [entry for entry in report["artifacts"] if entry["kind"] == "image"]
    assert (image["location"]["object_id"], image["location"]["xref_offset"],
            read_document_artifact(path, image["artifact"]),
            {item["artifact"] for item in uses}, {item["component"] for item in uses},
            [(entry["key"], entry["byte_size"], entry["sha256"]) for entry in payload_refs],
            report == inspect_document(path)) == (
        8, source.index(b"8 0 obj\n"), b"\xff\x00\x00", {image["artifact"]}, {image["component"]},
        [(image["artifact"], 3, hashlib.sha256(b"\xff\x00\x00").hexdigest())], True,
    ), "shared image source offsets, exact encoded bytes and hashes must remain distinct from page occurrences"


def test_public_pdf_hybrid_nonselected_source_retains_program_and_original_row_identity(tmp_path):
    # GIVEN current table/supplement definitions with different literal programs and a retained older compressed action
    original, _revised, _metadata = _compressed_revisions()
    body = b"5 0 << /S /JavaScript /JS (alternateProof) >>\n"
    container = _stream(body, b"/Type /ObjStm /N 1 /First 4")
    supplement = _stream(_xref_stream_entry(2, 6, 0),
                          b"/Type /XRef /Size 9 /Root 1 0 R /W [1 4 2] /Index [5 1]")
    source, metadata = _table_revision(original, ((5, 0, b"<< /S /JavaScript /JS (tableProof) >>"),
                                                  (6, 0, container), (8, 0, supplement)), supplement=8)
    path = tmp_path / "nonselected.pdf"
    path.write_bytes(source)
    assert PDFDocument(PDFParser(io.BytesIO(source))).getobj(5)["JS"] == b"tableProof", (
        "the native table precedence must independently verify the authoritative program"
    )
    # WHEN public inspection inventories each original row and resolves references in its applicable view
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    alternate = [item for item in scripts if item["text"] == "alternateProof"]
    # THEN the nonselected source stays recoverable with explicit compressed-row provenance and complete coverage
    assert (sorted({item["text"] for item in scripts}),
            {(item["location"]["revision"], item["location"]["xref_section_offset"],
              item["location"]["source_selection"]) for item in alternate},
            [(item["capability"], item["status"]) for item in report["coverage"]
             if item["status"] in ("partial", "unavailable")],
            len([item for item in report["findings"] if item["kind"] == "pdf-xref-conflict"])) == (
        ["alternateProof", "oldCompressed();", "tableProof"],
        {(0, metadata["objects"][8], "nonselected-xref-row")}, [], 1,
    ), "deterministic lookup precedence must not erase another fully recoverable physical source occurrence"


@pytest.mark.parametrize("shared", [True, False])
def test_literal_javascript_payload_identity_follows_physical_actions_and_preserves_every_trigger(tmp_path, shared):
    # GIVEN one physical literal action referenced by two distinct document trigger occurrences
    original = _revision_base(b"sharedProof();")
    objects = {int(number): body for number, body in
               re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", original)}
    action_id, physical = {True: (5, 1), False: (6, 2)}[shared]
    objects.update({True: {}, False: {6: objects[5]}}[shared])
    objects[1] = objects[1][:-2] + (" /AA << /WC %d 0 R >> >>" % action_id).encode()
    source = _pdf(objects)
    path = tmp_path / "shared-script.pdf"
    path.write_bytes(source)
    assert source.count(b"/S /JavaScript") == physical, "the original bytes must establish shared versus distinct actual action dictionaries"
    # WHEN public inspection emits every trigger while retaining original script source through the shared source cache
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    payloads = [item for item in report["artifacts"] if item["kind"] == "script"]
    # THEN source bytes are stored once while exact trigger and object locations stay distinct
    assert ([(item["location"]["dictionary_path"], item["location"]["object_id"], item["text"]) for item in scripts],
            len({item["artifact"] for item in scripts}),
            [(item["byte_size"], item["sha256"], read_document_artifact(path, item["key"])) for item in payloads]) == (
        [("Catalog/OpenAction", 5, "sharedProof();"), ("Catalog/AA/WC", action_id, "sharedProof();")], physical,
        [(14, hashlib.sha256(b"sharedProof();").hexdigest(), b"sharedProof();")] * physical,
    ), "repeated triggers must share one payload while equal bytes in distinct physical actions stay distinct"


def test_free_action_target_keeps_referring_source_separate_from_target_xref_metadata(tmp_path):
    # GIVEN an original action reference followed by its free tombstone in a retained update
    original = _revision_base()
    source, metadata = _table_revision(original, free=((5, 1),))
    path = tmp_path / "free-action.pdf"
    path.write_bytes(source)
    assert source[:len(original)] == original, "the referring catalog and original action must retain their exact physical bytes"
    # WHEN the checked current graph reports the original reference's now-free target
    report = inspect_document(path)
    inventory = next(item for item in report["coverage"] if item["capability"] == "inventory")
    affected = [item for item in inventory["locations"]
                if item.get("object_id") == 5 and item.get("revision") == 0]
    reference_start = original.index(b"5 0 R")
    # THEN target metadata has no invented object bytes and referring syntax keeps its own original source location
    assert [(item.get("xref_entry_kind"), item.get("generation"), item.get("requested_generation"),
             sorted(set(item) & {"byte_start", "byte_end", "byte_basis", "xref_offset"}),
             item.get("referring_source")) for item in affected] == [(
        "free", 1, 0, [],
        {"kind": "pdf", "object_id": 1, "generation": 0, "xref_offset": original.index(b"1 0 obj\n"),
         "reference_start": reference_start, "reference_end": reference_start + len(b"5 0 R"),
         "dictionary_path": "Catalog/OpenAction", "revision": 0, "revision_id": "xref@%d" % metadata["section"],
         "source_revision": 1, "source_revision_id": "xref@%d" % _last_xref(original)},
    )], "a free target cannot inherit storage spans from its referring catalog object"


def test_literal_script_payload_shared_by_duplicate_xref_rows_uses_one_physical_storage_identity(tmp_path):
    # GIVEN two current xref rows pointing to the same original literal action storage
    original = _revision_base(b"sharedProof();")
    action = b"<< /S /JavaScript /JS (sharedProof();) >>"
    supplement = _stream(_xref_stream_entry(1, len(original) + 1, 0),
                          b"/Type /XRef /Size 7 /Root 1 0 R /W [1 4 2] /Index [5 1]")
    source, _metadata = _table_revision(original, ((5, 0, action), (6, 0, supplement)), supplement=6)
    path = tmp_path / "shared-xref-source.pdf"
    path.write_bytes(source)
    assert source.count(b"/S /JavaScript") == 2, "one historical and one current physical action must remain in source"
    # WHEN both current row occurrences and the original historical action are independently inspected
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    payloads = [item for item in report["artifacts"] if item["kind"] == "script"]
    # THEN distinct row records do not manufacture a third copy of the two actual physical programs
    assert (len(scripts), len(payloads), len({item["component"] for item in scripts}),
            [(item["byte_size"], read_document_artifact(path, item["key"])) for item in payloads]) == (
        3, 2, 3, [(14, b"sharedProof();"), (14, b"sharedProof();")],
    ), "physical payload sharing must remain independent from retained xref row/component occurrences"


@pytest.mark.parametrize("kind", ["indirect", "compressed"])
def test_public_revision_reports_and_every_listed_artifact_repeat_exactly(tmp_path, monkeypatch, kind):
    # GIVEN retained historical sources, including an overwritten script stream or a reused compressed container
    indirect, _metadata = _table_revision(_revision_base(indirect=True),
                                          ((6, 0, _stream(zlib.compress(b"newProof();"), b"/Filter /FlateDecode")),))
    _original, compressed, _metadata = _compressed_revisions("hybrid")
    source, expected = {"indirect": (indirect, ["newProof();", "oldProof();"]),
                        "compressed": (compressed, ["newCompressed();", "oldCompressed();"])}[kind]
    path = tmp_path / (kind + ".pdf")
    path.write_bytes(source)
    assert source.count(b"startxref") == 2, "both actual physical revision footers must remain in the source"
    monkeypatch.setattr(socket, "create_connection", _forbid)
    monkeypatch.setattr(subprocess, "Popen", _forbid)
    # WHEN repeated independent public inspections recover the same original storage and semantic views
    first, payloads = _inspect_document(path)
    repeated, repeated_payloads = _inspect_document(path)
    scripts = [item for item in first["findings"] if item["kind"] == "pdf-javascript"]
    # THEN complete records, artifact bytes and hashes remain exact without fetching or executing source
    assert (repeated, repeated_payloads,
            [(item["key"], len(payloads[item["key"]]), hashlib.sha256(payloads[item["key"]]).hexdigest())
             for item in first["artifacts"]], sorted({item["text"] for item in scripts}),
            sorted({read_document_artifact(path, item["artifact"]) for item in scripts})) == (
        first, payloads, [(item["key"], item["byte_size"], item["sha256"]) for item in first["artifacts"]], expected,
        sorted(item.encode() for item in expected),
    ), "source provenance, operation counters and every retrievable representation must repeat byte-for-byte"


@pytest.mark.corpus
def test_public_linearized_nist_source_keeps_three_revisions_and_completes_valid_content():
    # GIVEN the unchanged public linearized source with its original base and two later updates
    path = Path(__file__).parent / "fixtures" / "pdf" / "nist-sp800-145.pdf"
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == "7b0c1a9fdfc67218b8ba2098f448c100c27070db91736b3c87fed63bfa21d418", (
        "the linearization evidence must come from the unchanged licensed original"
    )
    # WHEN public inspection groups only the measured first-page/main base sections
    report, payloads = _inspect_document(path)
    sections = [item for item in report["components"] if item["kind"] == "pdf-xref-section"]
    # THEN both physical base sections survive without inventing a rootless historical document
    assert (sorted((item["location"]["xref_section_offset"], item["location"]["xref_section_role"]) for item in sections),
            [(item["capability"], item["status"]) for item in report["coverage"] if item["status"] in ("partial", "unavailable")],
            report["limits"]["exhausted"],
            [payloads[item["key"]] for item in report["artifacts"] if item["kind"] == "pdf-source"]) == (
        [(116, "main"), (70055, "linearized-main"), (79781, "main"), (85522, "main")], [], [], [source],
    ), "valid linearized content and its original container must remain complete and exactly retrievable"


@pytest.mark.corpus
def test_public_eia_historical_free_structure_root_cannot_borrow_current_compressed_object():
    # GIVEN the unchanged EIA source whose current revision repairs an original free structure-tree target
    path = Path(__file__).parent / "fixtures" / "pdf" / "eia-steo-table1.pdf"
    source = path.read_bytes()
    assert source[136625:136645] == b"0000000020 65535 f\r\n", "the historical object-19 free row must reproduce in exact original bytes"
    # WHEN public inspection resolves current and historical roots through their own checked tables
    report, payloads = _inspect_document(path)
    current = next(item for item in report["components"] if item["kind"] == "pdf-object"
                   and item["location"].get("object_id") == 19 and item["location"].get("revision") == 0)
    free = next(item for item in report["components"] if item["kind"] == "pdf-xref-entry"
                and item["location"].get("object_id") == 19 and item["location"].get("revision") == 1)
    coverage = next(item for item in report["coverage"] if item["capability"] == "inventory")
    # THEN current compressed evidence remains available while the genuine historical reference stays explicitly incomplete
    assert (current["location"]["object_stream"], current["location"]["object_stream_index"],
            free["location"]["generation"], payloads[free["artifact"]], coverage["status"], coverage["reason"],
            [(item["object_id"], item["revision"], item["xref_entry_kind"],
              sorted(set(item) & {"byte_start", "byte_end", "byte_basis", "xref_offset"})) for item in coverage["locations"]],
            [payloads[item["key"]] for item in report["artifacts"] if item["kind"] == "pdf-source"]) == (
        1532, 8, 65535, source[136625:136645], "unavailable", "PDF reference targets a missing or free object",
        [(19, 1, "free", [])], [source],
    ), "historical inspection must disclose the original free reference instead of substituting a later definition"


@pytest.mark.parametrize("updated", [False, True])
def test_distinct_inline_action_members_keep_separate_payloads_across_source_views(tmp_path, updated):
    # GIVEN two equal literal actions at distinct physical dictionary members of one catalog
    action = rb"<< /S /JavaScript /JS (sameProof\(\);) >>"
    original = _pdf({
        1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction " + action + b" /AA << /WC " + action + b" >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R /Resources << >> >>",
        4: _stream(b""),
    })
    revised, _metadata = _table_revision(original, ((5, 0, b"null"),))
    source, views = {False: (original, 1), True: (revised, 2)}[updated]
    native = PDFDocument(PDFParser(io.BytesIO(source)))
    assert (source.count(b"/S /JavaScript"), len({id(native.catalog["OpenAction"]), id(native.catalog["AA"]["WC"])})) == (
        2, 2,
    ), "native source must establish two distinct inline dictionaries without copying their original storage in the update"
    path = tmp_path / "inline-actions.pdf"
    path.write_bytes(source)
    # WHEN every checked view inspects its action occurrences and stable original member paths
    report = inspect_document(path)
    scripts = [item for item in report["findings"] if item["kind"] == "pdf-javascript"]
    payloads = [item for item in report["artifacts"] if item["kind"] == "script"]
    # THEN distinct inline members remain distinct while revisiting those same members does not duplicate their payloads
    assert (len(scripts), len(payloads), len({item["artifact"] for item in scripts}),
            [(item["location"]["revision"], item["location"]["dictionary_path"], item["text"],
              item["details"].get("source_member_path")) for item in scripts],
            [read_document_artifact(path, item["key"]) for item in payloads], report == inspect_document(path)) == (
        2 * views, 2, 2,
        [(revision, path, "sameProof();", members) for revision in range(views)
         for path, members in (("Catalog/OpenAction", ["OpenAction", "JS"]), ("Catalog/AA/WC", ["AA", "WC", "JS"]))],
        [b"sameProof();", b"sameProof();"], True,
    ), "physical action members must survive separately without using unstable native IDs as persistent storage keys"


def test_nonselected_hybrid_form_inspects_its_root_without_shadowing_canonical_references(tmp_path):
    # GIVEN an alternate Form 5 whose resource points to the different authoritative table Form 5
    original = _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
                     3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << >> /Contents 4 0 R >>",
                     4: _stream(b"")})
    alternate = _stream(b"/Canonical Do BI /W 1 /H 1 /CS /G /BPC 8 ID b EI",
                         b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << /XObject << /Canonical 5 0 R >> >>")
    canonical = _stream(b"q Q", b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << >>")
    supplement = _stream(_xref_stream_entry(1, len(original) + 1, 0),
                          b"/Type /XRef /Size 7 /Root 1 0 R /W [1 4 2] /Index [5 1]")
    source, metadata = _table_revision(original, ((5, 0, alternate), (5, 0, canonical), (6, 0, supplement)), supplement=6)
    assert (PDFDocument(PDFParser(io.BytesIO(source))).getobj(5).rawdata,
            source[len(original) + 1:len(original) + 9]) == (
        b"q Q", b"5 0 obj\n",
    ), "checked table precedence and the supplement's separate original root storage must be independently established"
    path = tmp_path / "alternate-form.pdf"
    path.write_bytes(source)
    # WHEN original root occurrence identity stays separate from authoritative indirect lookup maps
    report = inspect_document(path)
    images = [item for item in report["findings"] if item["kind"] == "pdf-inline-image"]
    canonical_uses = [item for item in report["findings"] if item["kind"] == "pdf-form-occurrence"
                      and item["location"].get("resource_name") == "Canonical"]
    # THEN the valid source completes, preserving the alternate inline payload and its actual canonical target
    assert ([(item["capability"], item["reason"]) for item in report["coverage"] if item["status"] in ("partial", "unavailable")],
            [read_document_artifact(path, item["artifact"]) for item in images],
            [(item["details"]["target_source"]["xref_offset"], item["location"].get("page")) for item in canonical_uses],
            len([item for item in report["findings"] if item["kind"] == "pdf-xref-conflict"])) == (
        [], [b"b"], [(metadata["objects"][5], None)], 1,
    ), "a nonselected source root cannot replace the checked reference map or manufacture a false resource mismatch"


@pytest.mark.parametrize("field", ["Resources", "BBox"])
def test_malformed_orphan_form_preprocessing_preserves_valid_page_and_later_form_text(tmp_path, field):
    # GIVEN a readable page and later valid Form beside one malformed orphan source
    original, _target = _image_button_pdf()
    objects = {int(number): body for number, body in re.findall(rb"(?ms)^(\d+) 0 obj\n(.*?)\nendobj\n", original)}
    bad = {"Resources": b"/BBox [0 0 1 1] /Resources 99 0 R", "BBox": b"/BBox 99 0 R /Resources << >>"}[field]
    objects[9] = _stream(b"q Q", b"/Type /XObject /Subtype /Form " + bad)
    objects[10] = _stream(b"BT /F1 12 Tf 1 0 0 1 0 0 Tm (later form body) Tj ET",
                           b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /Font << /F1 5 0 R >> >>")
    path = tmp_path / "malformed-orphan.pdf"
    path.write_bytes(_pdf(objects))
    with pdfplumber.open(path) as native:
        readable = native.pages[0].extract_text()
    assert readable == "readable body", "the independent native page remains readable despite an unrelated missing Form reference"
    # WHEN text source preprocessing resolves each orphan inside its own recovery boundary
    report = inspect_document(path)
    text = [item for item in report["findings"] if item["kind"] in ("pdf-page-text", "pdf-xobject-text")]
    coverage = next(item for item in report["coverage"] if item["capability"] == "text")
    # THEN valid text on both sides of the bad source survives and the malformed source stays explicitly incomplete
    assert ([(item["kind"], item["location"]["object_id"], item["text"]) for item in text],
            coverage["status"], "missing or free" in coverage["reason"], "PDF revision unreadable" in coverage["reason"]) == (
        [("pdf-page-text", 3, "readable body\n"), ("pdf-xobject-text", 10, "later form body\n")], "partial", True, False,
    ), "an invalid orphan Resources or BBox cannot erase independent page text or later valid Form content"
