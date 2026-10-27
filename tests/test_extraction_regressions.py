"""Semantic regressions for independently inspected static document content."""

import io
import hashlib
import json
import re
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pdfplumber
import pytest

from brewdoc import run
from test_documents import OFFICE, PACKAGE, document, package, paragraph, relationships
from test_workbook_fidelity import fidelity_book


def _image_button_pdf(text="BT /F1 12 Tf 72 700 Td (readable body) Tj ET\n"):
    """Build an image button with a URI action using the self-check PDF pattern."""
    target = "https://button.example.invalid/exact?source=image#fragment"
    image = b"\xff\x00\x00"
    content = (text + "q 20 0 0 20 100 100 cm /Im1 Do Q\n").encode("ascii")
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 8 0 R >> >> "
            b"/Contents 4 0 R /Annots [6 0 R] >>"),
        4: ("<< /Length %d >>\nstream\n" % len(content)).encode() + content + b"endstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: (b"<< /Type /Annot /Subtype /Widget /FT /Btn /Ff 65536 /T (image-button) "
            b"/Rect [100 100 120 120] /A 7 0 R >>"),
        7: ("<< /S /URI /URI (%s) >>" % target).encode("ascii"),
        8: (b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\nstream\n"
            + image + b"\nendstream"),
    }
    buffer, offsets = io.BytesIO(), {}
    buffer.write(b"%PDF-1.4\n")
    for number, payload in objects.items():
        offsets[number] = buffer.tell()
        buffer.write(("%d 0 obj\n" % number).encode() + payload + b"\nendobj\n")
    start = buffer.tell()
    buffer.write(b"xref\n0 9\n0000000000 65535 f \n")
    for number in range(1, 9):
        buffer.write(("%010d 00000 n \n" % offsets[number]).encode())
    buffer.write(("trailer\n<< /Size 9 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % start).encode())
    return buffer.getvalue(), target


def test_normal_pdf_output_preserves_image_button_action_target(tmp_path):
    # GIVEN a readable PDF containing an image and a push-button URI action
    source, target = _image_button_pdf()
    path = tmp_path / "image-button.pdf"
    path.write_bytes(source)
    with pdfplumber.open(path) as document_pdf:
        facts = (len(document_pdf.pages[0].images), document_pdf.pages[0].annots[0]["uri"])
    assert facts == (1, target), "the source must contain the image and exact button URI"
    # WHEN normal conversion renders readable text and inspection findings
    code, receipt, markdown = run(path)
    # THEN the original image-button destination appears without being discarded
    assert (code, receipt["file_ok"], sorted(set(re.findall(re.escape(target), markdown)))) == (
        0, True, [target],
    ), "normal PDF conversion must preserve the complete image-button action destination"


def test_textless_pdf_refusal_preserves_requested_inspection_artifact(tmp_path):
    # GIVEN an image-only PDF with a button URI and an existing Markdown destination
    source, target = _image_button_pdf(text="")
    path = tmp_path / "textless.pdf"
    path.write_bytes(source)
    destination = tmp_path / "existing.md"
    previous = b"previous Markdown\r\n"
    destination.write_bytes(previous)
    report_path = tmp_path / "inspection.json"
    with pdfplumber.open(path) as document_pdf:
        facts = (len(document_pdf.pages[0].chars), len(document_pdf.pages[0].images),
                 document_pdf.pages[0].annots[0]["uri"])
    assert facts == (0, 1, target), "the PDF must have no text layer and retain its image button"
    # WHEN readable conversion refuses but passive inspection output was requested
    code, receipt, markdown = run(path, destination, artifact_outputs={"inspection/report": report_path})
    # THEN refusal preserves Markdown while publishing the requested independent evidence
    assert (code, receipt["file_ok"], markdown, destination.read_bytes(), report_path.is_file()) == (
        1, False, "", previous, True,
    ), "image-only PDF refusal must save inspection evidence without replacing failed Markdown output"
    report = json.loads(report_path.read_bytes())
    action_locations = sorted({
        (finding["location"].get("page"), finding["location"].get("annotation_ordinal"))
        for finding in report["findings"]
        if finding["target"] == target and "annotation_ordinal" in finding["location"]
    })
    image_keys = {component["artifact"] for component in report["components"]
                  if component["location"].get("object_id") == 8 and component["artifact"] is not None}
    image_payloads = sorted({(artifact["byte_size"], artifact["sha256"])
                             for artifact in report["artifacts"] if artifact["key"] in image_keys})
    assert (report["schema"], report["source"]["sha256"], action_locations, image_payloads) == (
        "brewdoc.inspection/1", hashlib.sha256(source).hexdigest(), [(1, 1)],
        [(3, hashlib.sha256(b"\xff\x00\x00").hexdigest())],
    ), "saved evidence must bind exact source bytes, button annotation location and original image payload"


def test_normal_docx_output_preserves_unreferenced_relationship_target(tmp_path):
    # GIVEN an unused hyperlink relationship in an otherwise readable package
    target = "https://orphan.example.invalid/original?keep=1#part"
    orphan = ('<Relationships xmlns="%s"><Relationship Id="unused" Type="%s/hyperlink" '
              'Target="%s" TargetMode="External"/></Relationships>' % (PACKAGE, OFFICE, target))
    path = package(tmp_path / "unused.docx", {
        "_rels/.rels": relationships(), "word/document.xml": document(paragraph("body")),
        "word/_rels/unreferenced.xml.rels": orphan,
    })
    with zipfile.ZipFile(path) as archive:
        original = archive.read("word/_rels/unreferenced.xml.rels").decode("utf-8")
    assert original == orphan, "the relationship must exist independently of body references"
    # WHEN normal conversion inspects all package members
    code, receipt, markdown = run(path)
    # THEN an unreferenced relationship still contributes its exact destination
    assert (code, receipt["file_ok"], sorted(set(re.findall(re.escape(target), markdown)))) == (
        0, True, [target],
    ), "package inspection must not depend on references from the readable document body"


@pytest.mark.parametrize("article", [False, True], ids=["normal", "article"])
def test_normal_html_output_preserves_hidden_script_source(tmp_path, article):
    # GIVEN literal script source hidden outside the readable main subtree
    script = "hiddenExtractionProof('original-source');"
    source = ("<html><body><main><p>Readable harmless text.</p></main>"
              "<div hidden><script>" + script + "</script></div></body></html>")
    path = tmp_path / "hidden.html"
    path.write_text(source, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == source, "inspection must receive the original hidden script"
    # WHEN normal or article conversion chooses its readable projection
    code, receipt, markdown = run(path, article=article)
    # THEN inspection source survives regardless of hidden content or article filtering
    assert (code, receipt["file_ok"], sorted(set(re.findall(re.escape(script), markdown)))) == (
        0, True, [script],
    ), "normal HTML output must expose literal hidden script source outside the chosen article"


def test_normal_eml_output_inspects_html_despite_plain_text_alternative(tmp_path):
    # GIVEN harmless plain text before an HTML alternative containing active code
    script = "alternateExtractionProof('html-only-source');"
    message = EmailMessage()
    message["Subject"] = "Synthetic alternative"
    message.set_content("Harmless plain alternative.")
    message.add_alternative("<p>HTML alternative.</p><script>" + script + "</script>", subtype="html")
    path = tmp_path / "alternative.eml"
    path.write_bytes(message.as_bytes(policy=policy.SMTP))
    parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/alternative", "text/plain", "text/html",
    ], "both MIME alternatives must be present independently of selected readable body"
    # WHEN the public service selects plain text for readable rendering
    code, receipt, markdown = run(path)
    # THEN inspection includes the unselected HTML script's original source
    assert (code, receipt["file_ok"], sorted(set(re.findall(re.escape(script), markdown)))) == (
        0, True, [script],
    ), "a harmless selected plain part must not hide an active HTML alternative"


@pytest.mark.parametrize("address,value,expected", [
    ("C5", '<c r="C5" t="inlineStr"><is><t>offset value</t></is></c>', "C5: offset value"),
    ("AA100", '<c r="AA100"><v>42</v></c>', "AA100: 42.0"),
    ("D5", '<c r="D5" t="e"><v>#DIV/0!</v></c>', "D5: #DIV/0!"),
], ids=["offset-C5-first-row-is-data", "column-AA-row-100", "error-token"])
def test_normal_workbook_output_preserves_original_cell_addresses(tmp_path, address, value, expected):
    # GIVEN one populated offset cell whose original location is explicit in XML
    row = re.search(r"\d+$", address).group()
    rows = '<row r="%s">%s</row>' % (row, value)
    path = fidelity_book(tmp_path / "offset.xlsx", rows=rows, label="Inputs")
    with zipfile.ZipFile(path) as archive:
        original = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert re.findall(re.escape(value), original) == [value], "the exact addressed source cell must be present"
    # WHEN normal workbook conversion emits the first occupied row as data
    code, receipt, markdown = run(path)
    # THEN cell addresses and errors appear beside their original values
    assert (code, receipt["file_ok"], sorted(set(re.findall(re.escape(expected), markdown)))) == (
        0, True, [expected],
    ), "workbook Markdown must preserve source addresses and error tokens without treating data as headers"
