"""Static package inspection preserves members, relationships and hidden source."""

import hashlib
import io
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from brewdoc import inspect_document, run
from brewdoc import _inspection_package as package_inspection
from brewdoc.common import _xml_preflight
from brewdoc._inspection_common import InspectionContext
from brewdoc._inspection_html import inspect_html
from brewdoc._inspection_package import inspect_package
from brewdoc.inspection import _inspect_document, read_document_artifact
from test_compound import compound_fixture
from test_documents import docx
from test_ole import package as ole_package
from test_odf import content as odf_content, members as odf_members

RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SCRIPT = "urn:oasis:names:tc:opendocument:xmlns:script:1.0"
FORM = "urn:oasis:names:tc:opendocument:xmlns:form:1.0"


def _package(entries):
    """Use ordered pairs so duplicate member names remain independently writable."""
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return result.getvalue()


def _inspect(data, name="source.docx", *, limits=None):
    """Exercise the scanner with the operation's actual context and injected callbacks."""
    context = InspectionContext(limits=limits)
    document = context.document(data, name, parent=None, location={"kind": "source"})
    nested = []

    def embedded(payload, child_name, *, parent, location, component=None):
        """Record the nested-reader handoff without executing document content."""
        nested.append((child_name, payload, parent, location, component))
        return "nested/%06d" % len(nested)

    members = inspect_package(data, name, context, document,
                              scan_html=inspect_html, scan_embedded=embedded)
    return context, document, members, nested


def _native_compound(wrapper):
    """Reuse the synthetic CFB fixture with a correctly chained native-wrapper stream."""
    source = bytearray(compound_fixture())
    encoded = "\x01Ole10Native\0".encode("utf-16le")
    source[1152:1216] = bytes(64)
    source[1152:1152 + len(encoded)] = encoded
    struct.pack_into("<H", source, 1152 + 64, len(encoded))
    struct.pack_into("<Q", source, 1024 + 120, 512)
    struct.pack_into("<Q", source, 1152 + 120, len(wrapper))
    sectors = (len(wrapper) + 63) // 64
    chain = [*range(1, sectors), 0xFFFFFFFE, *([0xFFFFFFFF] * (128 - sectors))]
    source[1536:2048] = struct.pack("<128I", *chain)
    source[2048:] = wrapper + bytes(512 - len(wrapper))
    return bytes(source)


def _directory_container(declarations, entries):
    """Keep directory children as original ODF member bytes with a source manifest."""
    declared = "".join('<manifest:file-entry manifest:full-path="%s" manifest:media-type="%s"/>'
                       % pair for pair in declarations)
    parent = [(part, payload.replace("</manifest:manifest>", declared + "</manifest:manifest>"))
              for part, payload in odf_members("odt", "<text:p>Parent body</text:p>")]
    return _package([*parent, *entries])


@pytest.mark.parametrize("suffix", [".docx", ".pptx", ".odt", ".odp", ".epub",
                                     ".xlsx", ".xlsm", ".xlsb", ".ods"])
def test_each_package_format_preserves_all_member_occurrences_and_exact_payloads(suffix):
    # GIVEN an unreferenced XML member, image bytes and a supported attachment
    entries = [("unused.xml", b"<root>hidden text</root>"),
               ("media/picture.png", b"synthetic-image-bytes"), ("attachments/source.txt", b"attached text\r\n")]
    data = _package(entries)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == [entry[0] for entry in entries], "source order must remain explicit"
    # WHEN inspection visits the complete archive independently of any readable adapter
    context, document, members, nested = _inspect(data, "source" + suffix)
    # THEN every byte occurrence, its size/hash and attachment callback are retained
    actual = [(item["location"]["member_ordinal"], item["location"]["part"], item["byte_size"],
               item["sha256"], context.payloads[item["artifact"]]) for item in context.components
              if item["kind"] in ("xml", "image", "embedded-document")]
    expected = [(ordinal, part, len(payload), hashlib.sha256(payload).hexdigest(), payload)
                for ordinal, (part, payload) in enumerate(entries, 1)]
    assert (members, actual, [(item[0], item[1], item[2]) for item in nested]) == (
        tuple((ordinal, part, payload) for ordinal, (part, payload) in enumerate(entries, 1)),
        expected, [("attachments/source.txt", b"attached text\r\n", document)],
    ), "all nine package suffixes must preserve archive-order bytes and recurse supported payloads"


def test_duplicate_relationship_members_and_ids_preserve_every_original_target():
    # GIVEN duplicate sidecar members and duplicate IDs with distinct destinations
    first = ('<Relationships xmlns="%s"><Relationship Id="same" Target="https://one.invalid/" '
             'Type="%s/hyperlink" TargetMode="External"/><Relationship Id="same" '
             'Target="https://two.invalid/" Type="%s/hyperlink" TargetMode="External"/>'
             '</Relationships>' % (RELATIONSHIPS, OFFICE, OFFICE)).encode()
    second = ('<Relationships xmlns="%s"><Relationship Id="same" Target="https://three.invalid/" '
              'Type="%s/hyperlink" TargetMode="External"/></Relationships>' % (RELATIONSHIPS, OFFICE)).encode()
    xml = ('<w:document xmlns:w="%s" xmlns:r="%s"><w:hyperlink r:id="same"/>'
           '</w:document>' % (WORD, OFFICE)).encode()
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = _package([("word/_rels/document.xml.rels", first),
                         ("word/_rels/document.xml.rels", second), ("word/document.xml", xml)])
    assert first.count(b'Id="same"') == 2, "the first source member must itself retain duplicate IDs"
    # WHEN inspection resolves every occurrence without a last-member or last-ID map
    context, document, members, _nested = _inspect(data)
    # THEN each original target and source ordinal survives and ambiguity is explicit
    actual = [(item["target"], item["location"]["member_ordinal"],
               item["location"]["relationship_ordinal"])
              for item in context.findings if item["kind"] == "relationship" and item["capability"] == "links"]
    assert (actual, len(members), context.coverage[document, "links"]["status"]) == (
        [("https://one.invalid/", 1, 1), ("https://two.invalid/", 1, 2),
         ("https://three.invalid/", 2, 1)], 3, "partial",
    ), "duplicates must remain separate evidence and ambiguous references cannot be complete"


def test_office_supplementary_text_fields_picture_actions_and_templates_keep_locations():
    # GIVEN unselected text branches, a split field and an unreferenced template relationship
    xml = ('<w:document xmlns:w="%s" xmlns:r="%s"><w:hdr><w:t>Header source</w:t></w:hdr>'
           '<w:del><w:delText>Deleted source</w:delText></w:del><w:r><w:fldChar w:fldCharType="begin"/>'
           '<w:instrText> HYPER</w:instrText><w:instrText>LINK "javascript:literal()" </w:instrText>'
           '<w:fldChar w:fldCharType="end"/></w:r><w:picture><w:hlinkClick r:id="picture" '
           'action="ppaction://hlinkshowjump?jump=nextslide"/></w:picture></w:document>' % (WORD, OFFICE)).encode()
    rels = ('<Relationships xmlns="%s"><Relationship Id="picture" Target="https://picture.invalid/exact" '
            'Type="%s/hyperlink" TargetMode="External"/><Relationship Id="orphan" '
            'Target="https://template.invalid/source.dotm" Type="%s/attachedTemplate" '
            'TargetMode="External"/></Relationships>' % (RELATIONSHIPS, OFFICE, OFFICE)).encode()
    data = _package([("word/document.xml", xml), ("word/_rels/document.xml.rels", rels)])
    assert xml.count(b"<w:instrText>") == 2, "the source field must be split across instruction runs"
    # WHEN inspection visits every XML branch and relationship occurrence
    context, _document, _members, _nested = _inspect(data)
    # THEN source text and every action target retain their part and exact original content
    actual = [(item["kind"], item["target"], item["text"], item["location"]["part"])
              for item in context.findings if item["kind"] in ("xml-text", "field-target", "relationship-reference")]
    assert actual == [
        ("xml-text", None, "Header source", "word/document.xml"),
        ("xml-text", None, "Deleted source", "word/document.xml"),
        ("field-target", "javascript:literal()", ' HYPERLINK "javascript:literal()" ', "word/document.xml"),
        ("relationship-reference", "https://picture.invalid/exact", None, "word/document.xml"),
    ], "headers, revisions, split field instructions and picture destinations must preserve source provenance"
    assert [(item["target"], item["details"]["attributes"]["Type"])
            for item in context.findings if item["kind"] == "relationship" and item["capability"] == "external_references"] == [
        ("https://picture.invalid/exact", OFFICE + "/hyperlink"),
        ("https://template.invalid/source.dotm", OFFICE + "/attachedTemplate"),
    ], "unreferenced external template relationships must remain visible"


@pytest.mark.parametrize("suffix", [".odt", ".odp"])
def test_odf_script_libraries_event_bindings_forms_and_unreferenced_text_are_inspected(suffix):
    # GIVEN script module source, an event target, form controls and auxiliary text
    xml = ('<root xmlns:script="%s" xmlns:form="%s" xmlns:xlink="http://www.w3.org/1999/xlink">'
           '<script:module>Sub HiddenSource\nEnd Sub</script:module>'
           '<script:event-listener script:event-name="dom:load" '
           'xlink:href="vnd.sun.star.script:Hidden.Main?language=Basic"/>'
           '<form:form form:name="Inputs" form:action="https://submit.invalid/exact">'
           '<form:text form:name="Secret" form:current-value="source-value"/></form:form>'
           '<note>Unreferenced note</note></root>' % (SCRIPT, FORM)).encode()
    data = _package([("Basic/Standard/Module1.xml", xml)])
    assert b"Sub HiddenSource\nEnd Sub" in xml, "literal module source must exist independently of the body"
    # WHEN the package scanner inspects scripts and forms outside readable content.xml
    context, document, _members, _nested = _inspect(data, "source" + suffix)
    # THEN code, bindings and both form records are retained exactly
    actual = [(item["capability"], item["kind"], item["target"], item["text"])
              for item in context.findings if item["capability"] in ("scripts", "forms")]
    assert (actual, context.coverage[document, "scripts"]["status"]) == ([
        ("scripts", "script-source", None, "Sub HiddenSource\nEnd Sub"),
        ("forms", "form-control", None, ""), ("forms", "form-control", None, ""),
    ], "complete"), "ODF library text and form structures must be inspected in both supported document types"
    assert [(item["target"], item["location"]["attribute"])
            for item in context.findings if item["kind"] == "xml-url" and item["capability"] == "links"] == [
        ("vnd.sun.star.script:Hidden.Main?language=Basic", "{http://www.w3.org/1999/xlink}href"),
        ("https://submit.invalid/exact", "{%s}action" % FORM),
    ], "ODF event and submission targets must remain exact and inert"


def test_epub_nonspine_html_and_external_script_resources_are_inspected():
    # GIVEN a resource not listed in any spine or readable manifest
    html = b'<div hidden><script>hiddenResourceProof();</script><a href="javascript:exact()">link</a></div>'
    script = b"externalResourceProof('source');\r\n"
    data = _package([("unused/hidden.xhtml", html), ("unused/code.js", script)])
    assert zipfile.is_zipfile(io.BytesIO(data)) is True, "the source must be an inspectable archive"
    # WHEN package inspection ignores readable spine selection
    context, document, _members, _nested = _inspect(data, "book.epub")
    # THEN both source scripts and the filtered dangerous target remain evidence
    sources = [(item["text"], item["location"]["part"])
               for item in context.findings if item["capability"] == "scripts" and item["text"] is not None]
    assert (sources, [(item["target"], item["location"]["part"])
                     for item in context.findings if item["capability"] == "links"],
            context.coverage[document, "scripts"]["status"]) == (
        [("hiddenResourceProof();", "unused/hidden.xhtml"),
         ("externalResourceProof('source');\r\n", "unused/code.js")],
        [("javascript:exact()", "unused/hidden.xhtml")], "complete",
    ), "nonspine HTML and script resources must retain source text and original destinations"


@pytest.mark.parametrize("payload,expected", [
    (b"<root><broken></root>", "XML unreadable"),
    (b"\xff\xfe\x00", "XML unreadable"),
])
def test_malformed_xml_retains_exact_bytes_and_cannot_claim_complete_text(payload, expected):
    # GIVEN a malformed unreferenced XML part beside readable text
    data = _package([("body.xml", b"<root>body</root>"), ("unused.xml", payload)])
    assert len(payload) == len(data[data.find(payload):data.find(payload) + len(payload)]), "the original member bytes must be present"
    # WHEN the archive is readable but an independent component cannot parse
    context, document, members, _nested = _inspect(data)
    # THEN original artifact bytes remain available and text coverage records the failure
    artifacts = [item for item in context.components if item["location"].get("part") == "unused.xml"]
    assert (members[-1], context.payloads[artifacts[0]["artifact"]],
            context.coverage[document, "text"]["status"],
            expected in context.coverage[document, "text"]["reason"]) == (
        (2, "unused.xml", payload), payload, "partial", True,
    ), "an unreadable unreferenced XML part cannot disappear into an empty complete report"


def test_encrypted_zip_member_is_inventoried_without_claiming_inspection():
    # GIVEN a member with the ZIP encrypted flag and no password
    source = bytearray(_package([("secret.xml", b"<root>secret</root>")]))
    central = source.index(b"PK\x01\x02")
    struct.pack_into("<H", source, 6, 1)
    struct.pack_into("<H", source, central + 8, 1)
    assert struct.unpack_from("<H", source, central + 8) == (1,), "the central directory must declare encryption"
    # WHEN static inspection cannot decode the encrypted member
    context, document, members, _nested = _inspect(bytes(source))
    # THEN its location and declared size remain while capability success stays unavailable
    assert (members, [(item["location"]["part"], item["disposition"], item["byte_size"],
                       item["details"]["declared_byte_size"]) for item in context.components
                      if item["kind"] != "encoded-package-member"],
            context.coverage[document, "text"]["status"]) == (
        ((1, "secret.xml", None),), [("secret.xml", "unreadable", None, 19)], "unavailable",
    ), "encrypted bytes must retain a located inventory boundary rather than false complete inspection"


def test_repeated_package_scan_preserves_deterministic_records_and_artifact_bytes():
    # GIVEN a package whose ZIP member order differs from lexical filename order
    data = _package([("z.xml", b"<root>last name first</root>"), ("a.xml", b"<root>second</root>")])
    assert data.index(b"z.xml") < data.index(b"a.xml"), "the archive must retain explicit source order"
    # WHEN two independent operation contexts inspect identical original bytes
    first, _document, _members, _nested = _inspect(data)
    second, _document2, _members2, _nested2 = _inspect(data)
    # THEN neither record identities, source locations nor returned artifact bytes drift
    assert (first.report({"name": "source.docx"}), first.payloads) == (
        second.report({"name": "source.docx"}), second.payloads,
    ), "package evidence must be deterministic across complete repeated inspections"


def test_odf_encrypted_xml_is_retained_without_parsing_plaintext_looking_bytes():
    # GIVEN a manifest declaring that an otherwise parseable member is encrypted
    manifest = (b'<manifest xmlns="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                b'<file-entry full-path="secret.xml"><encryption-data/></file-entry></manifest>')
    secret = b"<root>must not claim inspected</root>"
    data = _package([("secret.xml", secret), ("META-INF/manifest.xml", manifest)])
    assert b'encryption-data' in manifest, "encryption must be declared independently of payload appearance"
    # WHEN inspection reads the encryption manifest before parsing other XML members
    context, document, _members, _nested = _inspect(data, "source.odt")
    # THEN the encrypted member retains bytes and no XML-tree or text claims inspection
    assert ([(item["kind"], item["disposition"])
             for item in context.components if item["location"]["part"] == "secret.xml"],
            [(item["kind"], item["text"])
             for item in context.findings if item["location"]["part"] == "secret.xml"],
            context.coverage[document, "text"]["status"]) == (
        [("xml", "opaque")], [("encrypted-resource", None)], "unavailable",
    ), "declared encryption must override a payload's accidental XML appearance"


@pytest.mark.parametrize("limit", ["decoded_bytes", "retained_bytes"])
def test_cumulative_package_exhaustion_still_inventories_later_member_locations(limit):
    # GIVEN two members whose individual payloads fit but cumulative bytes do not
    data = _package([("first.bin", b"abcd"), ("second.bin", b"efgh"), ("third.bin", b"ijkl")])
    assert sum(len(payload) for _part, payload in [("first.bin", b"abcd"), ("second.bin", b"efgh")]) == 8, "the second admission must exceed six cumulative bytes"
    # WHEN a shared operation allows six bytes across the entire package
    context, document, members, _nested = _inspect(data, limits={limit: 6})
    # THEN later declarations remain located without more reads or false complete inventory
    assert (members, [(item["location"]["part"], item["disposition"],
                       item["details"]["declared_byte_size"]) for item in context.components
                      if item["kind"] != "encoded-package-member"],
            [item["limit"] for item in context.exhausted],
            context.coverage[document, "inventory"]["status"]) == (
        ((1, "first.bin", b"abcd"), (2, "second.bin", None), (3, "third.bin", None)),
        [("first.bin", "opaque", 4), ("second.bin", "unreadable", 4), ("third.bin", "unreadable", 4)],
        [limit], "unavailable",
    ), "budget exhaustion must preserve every remaining known archive boundary"


def test_compound_embedded_stream_with_supported_name_recurses_without_executing():
    # GIVEN an OLE container whose native mini stream names a supported text document
    source = bytearray(compound_fixture())
    encoded = "embedded.txt\0".encode("utf-16le")
    source[1152:1216] = bytes(64)
    source[1152:1152 + len(encoded)] = encoded
    struct.pack_into("<H", source, 1152 + 64, len(encoded))
    data = _package([("objects/object.bin", bytes(source))])
    assert source[2048:2051] == b"abc", "the exact native stream payload must exist"
    # WHEN package inspection inventories CFB and recognizes its supported native stream
    context, document, _members, nested = _inspect(data)
    # THEN native bytes recurse through the supplied callback and CFB artifacts stay exact
    assert ([(item[0], item[1], item[2], item[3]["path"]) for item in nested],
            [payload for payload in context.payloads.values() if payload == b"abc"]) == (
        [("embedded.txt", b"abc", document, "embedded.txt")], [b"abc"],
    ), "a supported document inside an OLE stream must be independently inspected from its original bytes"


def test_macro_bearing_package_extracts_vba_source_and_preserves_compiled_streams():
    # GIVEN the already licensed public VBA fixture wrapped in a minimal package
    fixture = Path(__file__).parent / "fixtures/xlsm/calamine-vba.xlsm"
    with zipfile.ZipFile(fixture) as archive:
        project = archive.read("xl/vbaProject.bin")
    assert project[:8] == bytes.fromhex("d0cf11e0a1b11ae1"), "the fixture must contain a real compound VBA project"
    data = _package([("word/vbaProject.bin", project)])
    # WHEN static package inspection invokes the bounded VBA reader on the admitted project
    context, document, _members, _nested = _inspect(data)
    # THEN module source and its compiled prefix remain separate retrievable evidence
    modules = [(item["details"]["module_name"], item["text"])
               for item in context.findings if item["kind"] == "vba_module"]
    assert (len(modules), modules[-1], context.coverage[document, "macros"]["status"],
            sorted(set(item["kind"] for item in context.artifacts
                       if item["kind"].startswith("vba_")))) == (
        5, ("testVBA", 'Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n'),
        "complete", ["vba_compiled_prefix", "vba_compressed_source", "vba_directory", "vba_source"],
    ), "VBA source extraction must be wired into package inspection while preserving compiled payloads"


def test_content_type_declarations_identify_extensionless_xml_images_and_script_source():
    # GIVEN opaque-looking member names with explicit OPC content type declarations
    types = (b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             b'<Override PartName="/hidden/text" ContentType="application/xml"/>'
             b'<Override PartName="/hidden/image" ContentType="image/png"/>'
             b'<Override PartName="/hidden/code" ContentType="text/javascript"/></Types>')
    data = _package([("hidden/text", b"<root>extensionless source</root>"),
                     ("hidden/image", b"\x89PNGsynthetic"), ("hidden/code", b"extensionlessProof();"),
                     ("[Content_Types].xml", types)])
    assert types.count(b"<Override ") == 3, "the source must declare all three nonstandard member names"
    # WHEN package inspection follows declared types independently of filename extensions
    context, _document, _members, _nested = _inspect(data)
    # THEN each recoverable source capability includes its exact source occurrence
    assert [(item["capability"], item["text"], item["target"], item["location"]["part"])
            for item in context.findings if item["capability"] in ("text", "scripts", "images")] == [
        ("text", "extensionless source", None, "hidden/text"),
        ("images", None, "hidden/image", "hidden/image"),
        ("scripts", "extensionlessProof();", None, "hidden/code"),
    ], "declared XML, image and script payloads must be recognized without conventional suffixes"


def test_malformed_destinations_remain_literal_evidence_without_interrupting_other_relationships():
    # GIVEN invalid URL syntax in external and internal relationship occurrences
    rels = ('<Relationships xmlns="%s"><Relationship Id="bad" Target="http://[bad" '
            'Type="%s/hyperlink"/><Relationship Id="next" Target="https://next.invalid/" '
            'Type="%s/hyperlink" TargetMode="External"/></Relationships>' % (RELATIONSHIPS, OFFICE, OFFICE)).encode()
    data = _package([("word/_rels/document.xml.rels", rels)])
    assert b'Target="http://[bad"' in rels, "the original malformed destination must be retained in source"
    # WHEN inspection treats every destination as inert original data
    context, document, _members, _nested = _inspect(data)
    # THEN invalid syntax is reported without losing either target occurrence
    assert ([(item["target"], item["location"]["relationship_ordinal"])
             for item in context.findings if item["kind"] == "relationship" and item["capability"] == "links"],
            context.coverage[document, "links"]["status"]) == (
        [("http://[bad", 1), ("https://next.invalid/", 2)], "partial",
    ), "malformed destinations must stay literal evidence and never terminate the remaining relationship scan"


def test_deep_xml_keeps_exact_element_ordinals_without_unbounded_xpath_strings():
    # GIVEN valid nested XML deeper than a readable path should grow
    xml = ('<n xmlns="urn:deep">' + '<n>' * 599 + '<t>deep source</t>' + '</n>' * 600).encode()
    data = _package([("deep.xml", xml)])
    assert xml.count(b"<n>") == 599, "the source must contain six hundred nested parent elements"
    # WHEN the scanner traverses deep XML without recursive calls or quadratic paths
    context, _document, _members, _nested = _inspect(data)
    # THEN the exact source element ordinal remains while the oversized XPath is omitted
    assert [(item["text"], item["location"].get("element_ordinal"), item["location"].get("xpath"))
            for item in context.findings if item["kind"] == "xml-text"] == [
        ("deep source", 601, None),
    ], "deep XML source locations must remain exact without building unbounded namespace paths"


def test_workbook_package_text_retains_supplementary_content_without_duplicating_cells():
    # GIVEN cell text plus headers, comments, drawings and unreferenced shared text
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = ('<worksheet xmlns="%s"><sheetData><row r="5"><c r="C5" t="inlineStr">'
             '<is><t>cell value</t></is></c></row></sheetData><headerFooter>'
             '<oddHeader>Header source</oddHeader></headerFooter></worksheet>' % namespace).encode()
    entries = [("xl/worksheets/sheet1.xml", sheet),
               ("xl/comments/comment1.xml", ('<comments xmlns="%s"><comment><text><t>Comment source</t>'
                                             '</text></comment></comments>' % namespace).encode()),
               ("xl/drawings/drawing1.xml", b'<root xmlns:a="urn:drawing"><a:t>Drawing source</a:t></root>'),
               ("xl/sharedStrings.xml", ('<sst xmlns="%s"><si><t>unused shared source</t></si></sst>' % namespace).encode())]
    data = _package(entries)
    assert b"cell value" in sheet, "cached cell projection must have a separate source subtree"
    # WHEN package scanning delegates addressed cells but inspects all supplementary XML
    context, document, _members, _nested = _inspect(data, "source.xlsx")
    # THEN non-cell text remains available and macro absence is explicitly complete
    assert ([(item["text"], item["location"]["part"])
             for item in context.findings if item["kind"] == "xml-text"],
            context.coverage[document, "macros"]["status"]) == (
        [("Header source", "xl/worksheets/sheet1.xml"),
         ("Comment source", "xl/comments/comment1.xml"),
         ("Drawing source", "xl/drawings/drawing1.xml"),
         ("unused shared source", "xl/sharedStrings.xml")], "complete",
    ), "workbook package coverage must preserve extra text and avoid a permanently pending macro scan"


def test_ole_native_html_payload_is_recursively_inspected_with_exact_source_metadata(tmp_path):
    # GIVEN an embedded Package wrapper with recoverable HTML code and an inert link
    html = b'<script>oleSourceProof();</script><a href="https://nested.invalid/exact">target</a>'
    wrapper, offset = ole_package(html, b"nested.html")
    path = tmp_path / "native.docx"
    path.write_bytes(_package([("word/embeddings/oleObject.bin", _native_compound(wrapper))]))
    assert wrapper[offset:offset + len(html)] == html, "the native payload must have an exact declared byte boundary"
    # WHEN independent inspection follows supported native payloads through the shared operation
    report, payloads = _inspect_document(path)
    # THEN HTML source, target and wrapper metadata retain separate original locations and bytes
    assert ([(item["name"], item["parent"], item["location"].get("native_data_offset"))
             for item in report["documents"]],
            [(item["text"], payloads[item["artifact"]]) for item in report["findings"]
             if item["capability"] == "scripts" and item["text"] == "oleSourceProof();"],
            [item["target"] for item in report["findings"] if item["capability"] == "links"],
            [(item["details"]["source_path"], payloads[item["artifact"]])
             for item in report["findings"] if item["kind"] == "ole-native-payload"]) == (
        [("native.docx", None, None), ("nested.html", "document/000001", offset)],
        [("oleSourceProof();", b"oleSourceProof();")], ["https://nested.invalid/exact"],
        [("C:\\source\\nested.html", html)],
    ), "native unwrapping must expose exact supported source and metadata without treating wrapper bytes as the document"


def test_linked_ole_native_metadata_never_opens_its_external_source_path():
    # GIVEN a linked Package wrapper naming an external HTML document
    wrapper, _offset = ole_package(b"", b"remote.html", mode=1)
    data = _package([("word/embeddings/oleObject.bin", _native_compound(wrapper))])
    assert b"remote.html" in wrapper, "the linked object must preserve original source metadata"
    # WHEN the package scanner inventories the OLE link without resolving it
    context, _document, _members, nested = _inspect(data)
    # THEN only a source-path reference is recorded and recursive reading is never invoked
    assert (nested, [(item["target"], item["details"]["status"], item["details"]["filename"])
                     for item in context.findings if item["kind"] == "ole-native-link"]) == (
        [], [("C:\\source\\remote.html", "linked", "remote.html")],
    ), "linked OLE native objects must remain inert source metadata without file or network access"


@pytest.mark.parametrize("namespace,body,kinds", [
    (WORD, '<rPr><vanish/></rPr><t>hidden text</t>', ["visibility-element"]),
    ("urn:oasis:names:tc:opendocument:xmlns:text:1.0", '<hidden-text is-hidden="true">hidden text</hidden-text>',
     ["visibility-element", "visibility-attribute"]),
])
def test_package_hidden_metadata_is_recorded_without_filtering_its_text(namespace, body, kinds):
    # GIVEN hidden-source metadata in an independently recoverable XML member
    xml = ('<root xmlns="%s">%s</root>' % (namespace, body)).encode()
    data = _package([("unused.xml", xml)])
    assert b"hidden text" in xml, "hidden source must remain present in the original XML"
    # WHEN package inspection visits hidden structures without applying their visibility
    context, document, _members, _nested = _inspect(data)
    # THEN visibility evidence and its source text are both retained with completed coverage
    assert ([item["kind"] for item in context.findings if item["capability"] == "hidden_structures"],
            [item["text"] for item in context.findings if item["capability"] == "text"],
            context.coverage[document, "hidden_structures"]["status"]) == (
        kinds, ["hidden text"], "complete",
    ), "package inspection must account for visibility metadata while preserving hidden text"


@pytest.mark.parametrize("media_type,status,kinds", [
    ("", "complete", ["empty-package-resource"]),
    ("text/xml", "partial", ["package-resource-types"]),
])
def test_empty_odf_resource_with_explicit_empty_media_type_is_not_claimed_malformed_xml(media_type, status, kinds):
    # GIVEN an empty resource whose manifest distinguishes arbitrary bytes from XML
    manifest = ('<manifest xmlns="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<file-entry full-path="config/current.xml" media-type="%s"/></manifest>' % media_type).encode()
    data = _package([("body.xml", b"<root>known text</root>"),
                     ("config/current.xml", b""), ("META-INF/manifest.xml", manifest)])
    assert b'media-type="%s"' % media_type.encode() in manifest, "the resource's declared media type must be explicit"
    # WHEN inspection classifies zero bytes using source manifest metadata
    context, document, members, _nested = _inspect(data, "source.odt")
    # THEN arbitrary empty resources remain inventory evidence while declared XML still fails
    assert (members[1], context.coverage[document, "text"]["status"],
            [item["kind"] for item in context.findings if item["location"]["part"] == "config/current.xml"]) == (
        (2, "config/current.xml", b""), status, kinds,
    ), "empty data and malformed declared XML must remain distinct without hiding a real parse failure"


def test_public_odt_embedded_workbook_is_a_logical_document_with_exact_original_member_refs():
    # GIVEN the licensed ODT fixture's native embedded spreadsheet directory
    path = Path(__file__).parent / "fixtures/odt/tika-testODFwithOOo3.odt"
    with zipfile.ZipFile(path) as archive:
        source_members = [(ordinal, item.filename, len(archive.read(item)),
                           hashlib.sha256(archive.read(item)).hexdigest())
                          for ordinal, item in enumerate(archive.infolist(), 1)
                          if item.filename.startswith("Object 1/")]
    assert source_members[0][:3] == (7, "Object 1/content.xml", 4105), "the public source must retain its recorded physical embedded member"
    # WHEN independent inspection dispatches admitted ODF directory scopes
    report, payloads = _inspect_document(path)
    # THEN a real logical ODS child has original member identities rather than fabricated ZIP bytes
    children = [item for item in report["documents"] if item.get("representation") == "odf-directory"]
    assert [(item["name"], item["suffix"], item["byte_size"], item["sha256"])
            for item in children] == [("Object 1/", ".ods", None, None)], "supported native ODF folders need their own truthful logical source identity"
    child = children[0]
    assert [(item["member_ordinal"], item["original_part"], item["byte_size"], item["sha256"])
            for item in child["source_members"]] == source_members, "logical child descriptors must point to every exact original physical occurrence"
    assert [payloads[item["artifact"]] for item in child["source_members"]] == [
        payloads[next(component["artifact"] for component in report["components"]
                      if component["id"] == item["component"])] for item in child["source_members"]
    ], "logical sources must reuse original retrievable payload artifacts without byte duplication"


def test_nested_odt_and_odp_scopes_inherit_parent_declarations_and_own_each_original_member_once(tmp_path):
    # GIVEN two nested supported folder scopes declared only in the outer manifest
    text = odf_content("odt", "<text:p>Child body</text:p>").encode()
    slides = odf_content("odp", '<draw:page><text:p>Nested body</text:p></draw:page>').encode()
    data = _directory_container([
        ("Object A/", "application/vnd.oasis.opendocument.text"),
        ("Object A/Nested/", "application/vnd.oasis.opendocument.presentation"),
    ], [("Object A/content.xml", text), ("Object A/Nested/content.xml", slides)])
    path = tmp_path / "nested.odt"
    path.write_bytes(data)
    with zipfile.ZipFile(path) as archive:
        original = [(ordinal, item.filename) for ordinal, item in enumerate(archive.infolist(), 1)]
    assert original[-2:] == [(4, "Object A/content.xml"), (5, "Object A/Nested/content.xml")], "nested content must retain distinct original source occurrences"
    # WHEN semantic ownership follows the nearest declared supported directory
    report, payloads = _inspect_document(path)
    # THEN each logical child keeps its real parent, exact text locations and original payloads
    assert ([(item["name"], item["parent"], item["suffix"]) for item in report["documents"]],
            [(item["document"], item["text"], item["location"]["part"])
             for item in report["findings"] if item["kind"] == "xml-text"],
            [(item["document"], item["location"]["member_ordinal"], item["location"]["part"])
             for item in report["components"] if item["reason"] ==
             "original member bytes; semantic findings are recorded separately"]) == (
        [("nested.odt", None, ".odt"), ("Object A/", "document/000001", ".odt"),
         ("Object A/Nested/", "document/000002", ".odp")],
        [("document/000001", "Parent body", "content.xml"),
         ("document/000002", "Child body", "Object A/content.xml"),
         ("document/000003", "Nested body", "Object A/Nested/content.xml")],
        [("document/000001", ordinal, part) for ordinal, part in original],
    ), "nested sources must share original inventory without duplicate parent semantic claims or fabricated ZIPs"
    child_refs = report["documents"][2]["source_members"]
    assert [(item["original_part"], item["member_ordinal"], payloads[item["artifact"]])
            for item in child_refs] == [("Object A/Nested/content.xml", 5, slides)], "nested child retrieval must reuse the original admitted source bytes"


def test_duplicate_odf_directory_declarations_preserve_source_locations_and_sticky_incompleteness(tmp_path):
    # GIVEN two declaration occurrences naming the same supported physical directory
    text = odf_content("odt", "<text:p>Child source</text:p>").encode()
    declaration = ("Object A/", "application/vnd.oasis.opendocument.text")
    path = tmp_path / "duplicate.odt"
    path.write_bytes(_directory_container([declaration, declaration], [("Object A/content.xml", text)]))
    assert path.read_bytes().count(b'manifest:full-path="Object A/"') == 2, "both original declaration occurrences must exist"
    # WHEN the available directory bytes are inspected once with ambiguous declaration metadata
    report, _payloads = _inspect_document(path)
    children = [item for item in report["documents"] if item.get("representation") == "odf-directory"]
    # THEN both source declaration locations survive and no child claims complete attachment inspection
    assert (len(children), [(item["location"]["part"], len(item["details"]["declarations"]))
                            for item in report["findings"] if item["kind"] == "odf-directory"],
            [(item["document"], item["status"]) for item in report["coverage"]
             if item["capability"] == "attachments"]) == (
        1, [("META-INF/manifest.xml", 2)],
        [("document/000001", "partial"), ("document/000002", "unavailable")],
    ), "duplicate directory declarations must remain visible without duplicate physical reads or erased failure evidence"


@pytest.mark.parametrize("target", ["../Escape/", "/Absolute/", "Object/../Escape/", "Object\\Escape/"])
def test_unsafe_odf_directory_prefixes_remain_inert_located_failures(tmp_path, target):
    # GIVEN an ODF subdocument declaration that is not a safe normalized relative folder
    path = tmp_path / "unsafe.odt"
    path.write_bytes(_directory_container([(target, "application/vnd.oasis.opendocument.text")], []))
    assert target.encode() in path.read_bytes(), "the exact unsafe source declaration must remain present"
    # WHEN scope discovery validates original package names without filesystem resolution
    report, _payloads = _inspect_document(path)
    # THEN its original destination is recorded and no logical external directory is inspected
    assert (len(report["documents"]), [(item["target"], item["location"]["part"])
                                      for item in report["findings"] if item["kind"] == "unsafe-odf-directory"],
            [item["status"] for item in report["coverage"] if item["capability"] == "attachments"]) == (
        1, [(target, "META-INF/manifest.xml")], ["partial"],
    ), "unsafe directory metadata must never trigger filesystem or network traversal or false complete coverage"


@pytest.mark.parametrize("limit,maximum", [("documents", 2), ("nesting", 1)])
def test_nested_odf_directories_share_document_and_depth_budgets(tmp_path, limit, maximum):
    # GIVEN two logical nested sources under one original ZIP operation
    text = odf_content("odt", "<text:p>Child body</text:p>").encode()
    data = _directory_container([
        ("Object A/", "application/vnd.oasis.opendocument.text"),
        ("Object A/Nested/", "application/vnd.oasis.opendocument.text"),
    ], [("Object A/content.xml", text), ("Object A/Nested/content.xml", text)])
    path = tmp_path / "limited.odt"
    path.write_bytes(data)
    assert len(data) == path.stat().st_size, "the complete original package must be admitted before logical descent"
    # WHEN a single operation allowance is exhausted by logical children
    report, payloads = _inspect_document(path, _limits={limit: maximum})
    # THEN exhausted limits remain explicit while all physical payloads are retrievable
    assert ([item["limit"] for item in report["limits"]["exhausted"]],
            len([item for item in report["components"] if item["reason"] ==
                 "original member bytes; semantic findings are recorded separately"]),
            sorted(payloads[item["key"]] for item in report["artifacts"]
                   if item["kind"] == "xml" and item["location"].find("Object A/") >= 0)) == (
        [limit], 5, sorted([text, text]),
    ), "logical ODF descendants must share cumulative budgets and preserve admitted original bytes on refusal"


def test_conflicting_empty_and_xml_manifest_entries_do_not_hide_an_empty_xml_failure():
    # GIVEN duplicate manifest entries that disagree whether an empty resource is XML
    manifest = (b'<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                b'<manifest:file-entry manifest:full-path="empty.xml" manifest:media-type=""/>'
                b'<manifest:file-entry manifest:full-path="empty.xml" manifest:media-type="text/xml"/>'
                b'</manifest:manifest>')
    data = _package([("body.xml", b"<root>known</root>"), ("empty.xml", b""), ("META-INF/manifest.xml", manifest)])
    assert manifest.count(b'manifest:full-path="empty.xml"') == 2, "both conflicting source declarations must be present"
    # WHEN empty-media metadata is reconciled with a nonempty XML declaration
    context, document, _members, _nested = _inspect(data, "conflict.odt")
    # THEN declared empty XML remains a parse failure rather than an arbitrary-data exemption
    assert (context.coverage[document, "text"]["status"],
            [(item["kind"], item["disposition"]) for item in context.components
             if item["location"].get("part") == "empty.xml"]) == (
        "partial", [("xml", "opaque"), ("xml-tree", "unreadable")],
    ), "an explicit arbitrary-data declaration cannot erase a conflicting XML inspection obligation"


@pytest.mark.parametrize("flags,method", [(1, 0), (0, 99)])
def test_unreadable_zip_members_preserve_exact_encoded_payload_artifacts(tmp_path, flags, method):
    # GIVEN encrypted or unsupported-codec metadata over recoverable original member bytes
    encoded = b"opaque encoded member bytes"
    source = bytearray(_package([("opaque.bin", encoded)]))
    central = source.index(b"PK\x01\x02")
    struct.pack_into("<HH", source, 6, flags, method)
    struct.pack_into("<HH", source, central + 8, flags, method)
    path = tmp_path / "source.docx"
    path.write_bytes(source)
    data_offset = 30 + len(b"opaque.bin")
    assert bytes(source[data_offset:data_offset + len(encoded)]) == encoded, "the original encoded byte range must be exact"
    # WHEN plaintext decoding is refused independently of recoverable source storage
    context, document, members, _nested = _inspect(bytes(source))
    # THEN encoded bytes are retrievable opaque evidence and never masquerade as plaintext
    actual = [(item["disposition"], item["byte_size"], item["sha256"],
               item["location"]["byte_offset"], context.payloads[item["artifact"]])
              for item in context.components if item["kind"] == "encoded-package-member"]
    assert (members, actual, context.coverage[document, "text"]["status"]) == (
        ((1, "opaque.bin", None),),
        [("opaque", len(encoded), hashlib.sha256(encoded).hexdigest(), data_offset, encoded)], "unavailable",
    ), "recoverable encoded payloads need exact source offsets and bytes while plaintext inspection stays unavailable"
    repeated, _document, _members, _nested2 = _inspect(bytes(source))
    assert (context.report({"name": "source.docx"}), context.payloads) == (
        repeated.report({"name": "source.docx"}), repeated.payloads,
    ), "opaque artifact identities, source locations and payload bytes must remain deterministic"
    assert read_document_artifact(path, context.artifacts[0]["key"]) == encoded, "the public generic retrieval API must return original encoded member bytes"


@pytest.mark.parametrize("offset,format,value,diagnostic", [
    (0, "<4s", b"BAD!", "signature"),
    (26, "<H", 65535, "boundary"),
    (18, "<I", 4000, "size or CRC"),
    (18, "<I", 0xFFFFFFFF, "ZIP64"),
])
def test_invalid_local_zip_metadata_never_manufactures_encoded_member_bytes(offset, format, value, diagnostic):
    # GIVEN central metadata for an unsupported codec and a malformed local header
    source = bytearray(_package([("opaque.bin", b"abc")]))
    central = source.index(b"PK\x01\x02")
    struct.pack_into("<H", source, 8, 99)
    struct.pack_into("<H", source, central + 10, 99)
    struct.pack_into(format, source, offset, value)
    assert struct.unpack_from(format, source, offset) == (value,), "the intended local source field must be malformed"
    # WHEN fallback validates local and central metadata before copying any encoded range
    context, document, members, _nested = _inspect(bytes(source))
    # THEN the known plaintext boundary survives and invalid ranges yield no artifact
    assert (members, [item["kind"] for item in context.artifacts],
            diagnostic in context.coverage[document, "attachments"]["reason"]) == (
        ((1, "opaque.bin", None),), [], True,
    ), "malformed local metadata must remain an explicit failure rather than guessed encoded bytes"


def test_truncated_local_zip_header_is_rejected_even_when_central_metadata_is_readable():
    # GIVEN a complete central directory whose local member has fewer than thirty header bytes
    original = bytearray(_package([("opaque.bin", b"abc")]))
    central = original.index(b"PK\x01\x02")
    struct.pack_into("<H", original, central + 10, 99)
    source = original[:20] + original[central:]
    end = source.index(b"PK\x05\x06")
    struct.pack_into("<I", source, end + 16, 20)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        declared = (archive.start_dir, archive.infolist()[0].header_offset)
    assert declared == (20, 0), "central metadata must still locate the truncated local source boundary"
    # WHEN encoded fallback checks its fixed local header against the next source boundary
    context, document, members, _nested = _inspect(bytes(source))
    # THEN no central-directory bytes are misidentified as member data
    assert (members, len(context.artifacts), "local ZIP header is outside" in
            context.coverage[document, "attachments"]["reason"]) == (
        ((1, "opaque.bin", None),), 0, True,
    ), "a truncated fixed header must never consume following central-directory bytes"


def test_zip64_encoded_member_preserves_exact_bytes_and_resolved_metadata(monkeypatch):
    # GIVEN a small real ZIP64 archive with sentinel local and central size fields
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        with archive.open("opaque.bin", "w", force_zip64=True) as member:
            member.write(b"abc")
    source = bytearray(output.getvalue())
    central = source.index(b"PK\x01\x02")
    struct.pack_into("<H", source, 6, 1)
    struct.pack_into("<H", source, central + 8, 1)
    assert (struct.unpack_from("<2I", source, 18), struct.unpack_from("<2I", source, central + 20)) == (
        (0xFFFFFFFF, 0xFFFFFFFF), (0xFFFFFFFF, 0xFFFFFFFF),
    ), "both original headers must require their ZIP64 metadata"
    # WHEN opaque evidence resolves validated ZIP64 extra fields without decoding data
    context, _document, _members, _nested = _inspect(bytes(source))
    # THEN source offsets, actual bytes and both size declarations remain exact
    assert [(item["location"]["byte_offset"], item["byte_size"], context.payloads[item["artifact"]],
             item["details"]["central_directory"]["plaintext_size"],
             item["details"]["local_header"]["encoded_size"])
            for item in context.components if item["kind"] == "encoded-package-member"] == [
        (60, 3, b"abc", 3, 3),
    ], "ZIP64 evidence must use exact local data offsets and validated resolved central sizes"


def test_encoded_member_retention_exhaustion_keeps_later_plaintext_declarations():
    # GIVEN an encrypted member before a later ordinary file and no retained-byte allowance
    source = bytearray(_package([("opaque.bin", b"abc"), ("later.bin", b"later")]))
    central = source.index(b"PK\x01\x02")
    struct.pack_into("<H", source, 6, 1)
    struct.pack_into("<H", source, central + 8, 1)
    assert struct.unpack_from("<H", source, 6) == (1,), "the first member must require opaque evidence"
    # WHEN encoded copying reaches the same shared retained-byte boundary
    context, document, members, _nested = _inspect(bytes(source), limits={"retained_bytes": 0})
    # THEN all original entries remain known and no unretained artifact is advertised
    assert (members, len(context.artifacts), [item["limit"] for item in context.exhausted],
            context.coverage[document, "text"]["status"],
            [item["location"]["part"] for item in context.components]) == (
        ((1, "opaque.bin", None), (2, "later.bin", None)), 0, ["retained_bytes"],
        "unavailable", ["opaque.bin", "later.bin"],
    ), "encoded retention must not create a fresh allowance or discard later known member inventory"


def test_encoded_evidence_can_survive_decoding_budget_exhaustion_without_more_decoding():
    # GIVEN individual stored payloads whose cumulative plaintext reads exceed six bytes
    source = _package([("first.bin", b"abcd"), ("second.bin", b"efgh"), ("third.bin", b"ijkl")])
    assert len(source) > 12, "the complete original container must already be available to opaque evidence"
    # WHEN the shared decoding budget refuses later plaintext but retained-byte capacity remains
    context, _document, _members, _nested = _inspect(source, limits={"decoded_bytes": 6})
    # THEN fallback retains exact encoded bytes without charging another decode operation
    assert (context.consumed["decoded_bytes"], [(item["location"]["part"], context.payloads[item["artifact"]])
                                              for item in context.components if item["kind"] == "encoded-package-member"]) == (
        4, [("second.bin", b"efgh"), ("third.bin", b"ijkl")],
    ), "encoded evidence must share retained bytes while remaining independent of exhausted plaintext decoding"


def test_invalid_deflate_stream_keeps_its_encoded_bytes_and_later_member_inventory():
    # GIVEN a corrupt compressed stream before an independently readable member
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("broken.bin", b"recoverable source " * 20, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("later.bin", b"later source")
    source = bytearray(output.getvalue())
    data_offset = 30 + len(b"broken.bin")
    encoded_size = struct.unpack_from("<I", source, 18)[0]
    source[data_offset] |= 7
    encoded = bytes(source[data_offset:data_offset + encoded_size])
    assert encoded[0] & 7 == 7, "the deflate block must declare the reserved invalid block type"
    # WHEN decoding raises a native codec error before producing plaintext
    context, document, members, _nested = _inspect(bytes(source))
    # THEN its raw storage survives and independent later components are still visited
    assert (members, [(item["location"]["part"], context.payloads[item["artifact"]])
                      for item in context.components if item["kind"] == "encoded-package-member"],
            context.coverage[document, "text"]["status"]) == (
        ((1, "broken.bin", None), (2, "later.bin", b"later source")),
        [("broken.bin", encoded)], "unavailable",
    ), "native decoder failure must preserve located encoded evidence and cannot terminate the remaining archive inventory"


@pytest.mark.parametrize("namespace", [WORD, "http://purl.oclc.org/ooxml/wordprocessingml/main"])
def test_public_docx_form_descendant_name_and_default_are_recovered_in_normal_markdown(tmp_path, namespace):
    # GIVEN a readable DOCX whose form values exist only on descendant property attributes
    body = ('<w:p><w:r><w:t>Readable body</w:t></w:r></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"><w:ffData>'
            '<w:name w:val="FormNameNeedle"/><w:enabled/>'
            '<w:textInput><w:default w:val="DefaultValueNeedle"/></w:textInput>'
            '</w:ffData></w:fldChar></w:r><w:r><w:instrText> FORMTEXT </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    path = docx(tmp_path / "form.docx", body, namespace=namespace)
    with zipfile.ZipFile(path) as archive:
        original = archive.read("word/document.xml")
    assert (b"FormNameNeedle" in original, b"DefaultValueNeedle" in original) == (True, True), "both source values must exist independently of readable rendering"
    # WHEN public inspection and ordinary conversion visit the complete source form
    report = inspect_document(path)
    code, receipt, markdown = run(path)
    properties = [item for item in report["findings"] if item["kind"] == "form-property"]
    # THEN individual properties retain their source QName and nearest control and become visible
    assert ([(item["details"]["property"], item["details"]["value"], item["details"]["qname"],
              item["details"]["control_qname"], item["location"]["part"])
             for item in properties], code, receipt["file_ok"],
            "FormNameNeedle" in markdown, "DefaultValueNeedle" in markdown) == (
        [("name", "FormNameNeedle", "{%s}name" % namespace, "{%s}ffData" % namespace, "word/document.xml"),
         ("enabled", None, "{%s}enabled" % namespace, "{%s}ffData" % namespace, "word/document.xml"),
         ("default", "DefaultValueNeedle", "{%s}default" % namespace, "{%s}textInput" % namespace, "word/document.xml")],
        0, True, True, True,
    ), "known form names and defaults cannot disappear behind complete form coverage or opaque whole-XML retrieval"


def test_legacy_form_checkbox_dropdown_and_macro_bindings_keep_scoped_original_values(tmp_path):
    # GIVEN separate legacy controls, Unicode entries and passive macros with available module source
    body = ('<w:p><w:r><w:rPr><w:rFonts w:ascii="FontNameNeedle"/></w:rPr><w:t>Readable body</w:t></w:r></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="CheckboxNeedle"/>'
            '<w:entryMacro w:val="testVBA.test"/><w:exitMacro w:val="ExitMacroNeedle"/>'
            '<w:checkBox><w:default w:val="1"/><w:checked w:val="0"/></w:checkBox></w:ffData></w:fldChar>'
            '<w:instrText> FORMCHECKBOX </w:instrText><w:fldChar w:fldCharType="end"/></w:r></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="DropdownNeedle"/><w:ddList>'
            '<w:result w:val="1"/><w:default w:val="0"/><w:listEntry w:val="Первый"/>'
            '<w:listEntry w:val="SecondNeedle"/></w:ddList></w:ffData></w:fldChar>'
            '<w:instrText> FORMDROPDOWN </w:instrText><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    path = docx(tmp_path / "controls.docx", body)
    fixture = Path(__file__).parent / "fixtures/xlsm/calamine-vba.xlsm"
    with zipfile.ZipFile(fixture) as archive:
        project = archive.read("xl/vbaProject.bin")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("word/vbaProject.bin", project)
    assert (body.count("<w:ffData>"), body.count("<w:listEntry ")) == (2, 2), "independent legacy controls and ordered entry sources must exist"
    # WHEN static inspection walks their properties without resolving or executing macro names
    report = inspect_document(path)
    code, _receipt, markdown = run(path)
    properties = [item for item in report["findings"] if item["kind"] == "form-property"]
    # THEN checkbox/default/selection data, original Unicode and macro bindings remain scoped
    assert [(item["details"]["property"], item["details"]["value"],
             item["details"]["control_qname"].split("}")[-1]) for item in properties] == [
        ("name", "CheckboxNeedle", "ffData"), ("entryMacro", "testVBA.test", "ffData"),
        ("exitMacro", "ExitMacroNeedle", "ffData"), ("default", "1", "checkBox"),
        ("checked", "0", "checkBox"), ("name", "DropdownNeedle", "ffData"),
        ("result", "1", "ddList"), ("default", "0", "ddList"),
        ("listEntry", "Первый", "ddList"), ("listEntry", "SecondNeedle", "ddList"),
    ], "legacy control source values and entry order must stay linked to their own nearest control"
    assert ([(item["target"], item["location"]["attribute"]) for item in report["findings"]
             if item["kind"] == "form-macro-binding"],
            [item["text"] for item in report["findings"] if item["kind"] == "vba_module"
             and item["details"]["module_name"] == "testVBA"],
            code, "CheckboxNeedle" in markdown, "SecondNeedle" in markdown, "Public Sub test()" in markdown) == (
        [("testVBA.test", "{%s}val" % WORD), ("ExitMacroNeedle", "{%s}val" % WORD)],
        ['Attribute VB_Name = "testVBA"\r\nPublic Sub test()\r\n    MsgBox "Hello from vba!"\r\nEnd Sub\r\n'],
        0, True, True, True,
    ), "bindings must remain literal and available VBA source must retain its independent passive extraction"


def test_nested_content_controls_keep_aliases_list_defaults_and_text_with_nearest_owners(tmp_path):
    # GIVEN nested content controls with independent aliases, dropdown metadata and default text
    body = ('<w:sdt><w:sdtPr><w:alias w:val="OuterAliasNeedle"/>'
            '<w:dropDownList w:lastValue="two"><w:listItem w:displayText="DisplayOne" w:value="one"/>'
            '<w:listItem w:displayText="DisplayTwo" w:value="two"/></w:dropDownList></w:sdtPr>'
            '<w:sdtContent><w:p><w:r><w:t>OuterDefaultNeedle</w:t></w:r></w:p>'
            '<w:sdt><w:sdtPr><w:alias w:val="InnerAliasNeedle"/><w:placeholder>'
            '<w:docPart w:val="PlaceholderNeedle"/></w:placeholder></w:sdtPr>'
            '<w:sdtContent><w:p><w:r><w:t>InnerDefaultNeedle</w:t></w:r></w:p></w:sdtContent>'
            '</w:sdt></w:sdtContent></w:sdt>')
    path = docx(tmp_path / "nested-controls.docx", body)
    assert body.count("<w:sdt>") == 2, "the source must contain distinct nested content-control scopes"
    # WHEN inspection records property and content-value sources in one depth-aware traversal
    report = inspect_document(path)
    code, _receipt, markdown = run(path)
    controls = [item for item in report["findings"] if item["kind"] == "form-control"]
    owners = [item["location"]["element_ordinal"] for item in controls]
    # THEN aliases/defaults/list entries retain the correct nearest original owner
    assert ([(item["details"]["value"], item["details"]["control_location"]["element_ordinal"])
             for item in report["findings"] if item["kind"] == "form-property" and item["details"]["property"] == "alias"],
            [(item["text"], item["details"]["control_location"]["element_ordinal"])
             for item in report["findings"] if item["kind"] == "form-value"],
            [(item["details"]["value"], item["details"]["attributes"]["{%s}displayText" % WORD])
             for item in report["findings"] if item["kind"] == "form-property" and item["details"]["property"] == "listItem"],
            code, "OuterAliasNeedle" in markdown, "InnerDefaultNeedle" in markdown) == (
        [("OuterAliasNeedle", owners[0]), ("InnerAliasNeedle", owners[1])],
        [("OuterDefaultNeedle", owners[0]), ("InnerDefaultNeedle", owners[1])],
        [("one", "DisplayOne"), ("two", "DisplayTwo")], 0, True, True,
    ), "nested content controls must not mix aliases, visible defaults or dropdown entry identities"


@pytest.mark.parametrize("control,diagnostic", [
    ('<w:ffData><w:textInput><w:default/></w:textInput></w:ffData>', "required value"),
    ('<w:ffData><w:unknown w:val="PreservedUnknown"/></w:ffData>', "unsupported Word form property"),
    ('<w:textInput><w:default w:val="OrphanDefault"/></w:textInput>', "no ffData scope"),
    ('<w:ffData><w:ddList><w:result w:val="bad"/></w:ddList></w:ffData>', "index is missing or malformed"),
    ('<w:ffData><w:checkBox><w:checked w:val="maybe"/></w:checkBox></w:ffData>', "checkbox state is malformed"),
])
def test_unknown_or_malformed_word_form_properties_remain_explicitly_incomplete(tmp_path, control, diagnostic):
    # GIVEN a readable package containing a malformed or unsupported source form property
    path = docx(tmp_path / "invalid-control.docx", '<w:p><w:r><w:t>Body</w:t>' + control + '</w:r></w:p>')
    assert path.is_file() is True, "the source must be an independently inspectable synthetic package"
    # WHEN supported properties are recovered without silently accepting unknown metadata
    report = inspect_document(path)
    # THEN form coverage cannot become falsely complete after the malformed source is visited
    assert [(item["status"], diagnostic in item["reason"]) for item in report["coverage"]
            if item["capability"] == "forms"] == [("partial", True)], "unknown or malformed form data must retain a located incomplete-coverage reason"


def test_nested_form_properties_use_no_subtree_text_walk_and_keep_deep_source_ordinals(monkeypatch):
    # GIVEN deep ODF forms whose Element objects prohibit repeated subtree text scans
    class SourceElement(ET.Element):
        """Reject a subtree text walk so the test observes bounded traversal behavior."""

        def itertext(self):
            """Fail any attempt to aggregate form subtrees outside the source-node walk."""
            raise AssertionError("form extraction must not rescan a whole descendant subtree")

    def parse(data, part, context):
        """Keep normal XML preflight while supplying source elements with a traversal guard."""
        _xml_preflight(data, part, context.zip_budget)
        return ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(element_factory=SourceElement)))

    monkeypatch.setattr(package_inspection, "_parse_xml", parse)
    depth = 400
    xml = ('<root xmlns:form="%s" xmlns:office="urn:office">' % FORM + '<form:form>' * depth
           + '<form:text><office:string>DeepFormNeedle</office:string></form:text>'
           + '</form:form>' * depth + '</root>').encode()
    assert xml.count(b"<form:form>") == depth, "the source must exercise deep form nesting independently of readable filtering"
    # WHEN inspection reuses the existing iterative XML traversal for every property
    context, _document, _members, _nested = _inspect(_package([("forms.xml", xml)]), "source.odt")
    # THEN per-node values retain exact deep locations without any repeated subtree aggregation
    assert (len([item for item in context.findings if item["kind"] == "form-control"]),
            [(item["text"], item["location"]["element_ordinal"], item["location"].get("xpath"),
              item["details"]["control_qname"]) for item in context.findings if item["kind"] == "form-property"]) == (
        depth + 1, [("DeepFormNeedle", depth + 3, None, "{%s}text" % FORM)],
    ), "deep form extraction must remain iterative and source-exact without quadratic text joins"
