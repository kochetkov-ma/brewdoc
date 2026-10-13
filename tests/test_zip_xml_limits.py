"""Synthetic OOXML limits cover owned reads before payload and tree retention."""

import io
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

import pytest

import brewdoc
from brewdoc import common
from brewdoc.cli import main
from test_contract import zero_tally
from test_documents import docx
from test_output import tree_state
from test_presentations import (base_parts, body as presentation_body, paragraph, picture,
                                pptx, relationship, relationships, shape, slide)


SMALL_LIMITS = {
    "members": 1_000, "member_bytes": 1_000_000, "consumed_bytes": 1_000_000,
    "document_elements": 1_000, "total_elements": 10_000, "depth": 100,
    "document_characters": 1_000_000, "total_characters": 1_000_000,
}
XML_BYTES = b'<r a="ab">c<a/>de</r>'
XML_TREE = ("r", {"a": "ab"}, "c", None, (("a", {}, None, "de", ()),))
DOC_OMISSIONS = [
    "images, charts and the text drawn inside them",
    "tracked changes, comments, footnotes, headers and footers",
    "a table's own formatting - only its cells, row by row",
]


def limits(monkeypatch, **overrides):
    """Install an isolated policy without requiring the new production symbol on RED."""
    monkeypatch.setattr(common, "ZIP_XML_LIMITS", {**SMALL_LIMITS, **overrides}, raising=False)


def xml_package(path, payload=XML_BYTES, method=zipfile.ZIP_STORED):
    """Write one consumed XML part and one ignored member."""
    with zipfile.ZipFile(path, "w", compression=method) as package:
        package.writestr("part.xml", payload)
        package.writestr("ignored.bin", b"")
    return path


def tree(root):
    """Expose namespace, attributes, body and tail without serializer differences."""
    return (root.tag, dict(root.attrib), root.text, root.tail, tuple(tree(child) for child in root))


def doc_refusal(path, reason):
    """Return the complete existing zero-unit document refusal contract."""
    return {
        "file_ok": False, "route": "doc", "reason": reason, "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter",
        "units": 0, "tables": 0, "text_regions": 0, "columns_split": 0,
        "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": DOC_OMISSIONS,
    }


@pytest.mark.parametrize("key,boundary,reason", [
    ("members", 2, "OOXML members limit exceeded: 1"),
    ("member_bytes", 21, "OOXML member_bytes limit exceeded: part.xml: 20"),
    ("consumed_bytes", 21, "OOXML consumed_bytes limit exceeded: part.xml: 20"),
    ("document_elements", 2, "OOXML document_elements limit exceeded: part.xml: 1"),
    ("total_elements", 2, "OOXML total_elements limit exceeded: part.xml: 1"),
    ("depth", 2, "OOXML depth limit exceeded: part.xml: 1"),
    ("document_characters", 5, "OOXML document_characters limit exceeded: part.xml: 4"),
    ("total_characters", 5, "OOXML total_characters limit exceeded: part.xml: 4"),
])
def test_each_limit_accepts_its_boundary_and_refuses_one_over(tmp_path, monkeypatch, key, boundary, reason):
    # GIVEN two members and XML with two elements, depth two and five decoded characters
    path = xml_package(tmp_path / "bounds.zip")
    with zipfile.ZipFile(path) as package:
        assert (len(package.infolist()), package.getinfo("part.xml").file_size) == (2, 21), (
            "the independent fixture boundary requires two members and 21 XML bytes")
        # WHEN the selected policy is inclusive, then decreases by exactly one
        limits(monkeypatch, **{key: boundary})
        accepted = tree(common._xml_part(package, "part.xml"))
        limits(monkeypatch, **{key: boundary - 1})
        # THEN exact input semantics survive at the boundary and the overrun is named
        assert accepted == XML_TREE, "the inclusive bound must preserve the complete XML tree"
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")


@pytest.mark.parametrize("key", ["depth", "document_elements", "total_elements",
                                     "document_characters", "total_characters"])
def test_structural_refusal_happens_before_element_tree_allocation(tmp_path, monkeypatch, key):
    # GIVEN a valid XML member whose selected structural budget is zero
    path = xml_package(tmp_path / "allocation.zip")
    allocations = []
    fromstring = ET.fromstring

    def record_allocation(payload):
        """Observe the allocation edge without replacing parsing behavior."""
        allocations.append(payload)
        return fromstring(payload)

    limits(monkeypatch, **{key: 0})
    monkeypatch.setattr(ET, "fromstring", record_allocation)
    assert allocations == [], "tree instrumentation must begin without retained XML"
    # WHEN the real common reader encounters the first structural overrun
    with zipfile.ZipFile(path) as package:
        with pytest.raises(brewdoc.BrewdocError, match="^OOXML " + key + " limit exceeded: part.xml: 0$"):
            common._xml_part(package, "part.xml")
    # THEN no forbidden member reaches the tree constructor
    assert allocations == [], "structural bounds must refuse before ElementTree allocation"


def test_actual_member_bytes_override_misleading_declared_length(tmp_path, monkeypatch):
    # GIVEN a stream returning 22 bytes while its member declares 21
    path = xml_package(tmp_path / "actual.zip")
    requests = []

    class ObservedStream(io.BytesIO):
        """Record requested bytes at the ZIP stream edge."""

        def read(self, size=-1):
            """Retain requested sizes even after the rejected stream closes."""
            requests.append(size)
            return super().read(size)

    stream = ObservedStream(XML_BYTES + b" ")
    limits(monkeypatch, member_bytes=21)
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("part.xml").file_size == 21, "the declared size must fit the byte cap"
        monkeypatch.setattr(package, "open", lambda *args, **kwargs: stream)
        # WHEN the controlled I/O edge yields the real one-byte overrun
        with pytest.raises(brewdoc.BrewdocError, match="^OOXML member_bytes limit exceeded: part.xml: 21$"):
            common._xml_part(package, "part.xml")
    # THEN the reader probes only one extra byte and closes the rejected stream
    assert (requests, stream.closed) == ([22], True), (
        "actual-byte checks must bound the read request and close the overrun stream")


@pytest.mark.parametrize("key,boundary", [
    ("consumed_bytes", 1825), ("total_elements", 43), ("total_characters", 327),
])
def test_presentation_aggregate_limits_charge_small_members_and_physical_rereads(
        tmp_path, monkeypatch, key, boundary):
    # GIVEN distinct relationships that both consume the same legal slide member
    parts = base_parts([("rId1", "slides/shared.xml", False), ("rId2", "slides/shared.xml", False)])
    parts["deck/slides/shared.xml"] = slide(shape("Body", paragraph("x")))
    path = pptx(tmp_path / "repeated.pptx", parts)
    assert tuple(len(payload.encode()) for payload in parts.values()) == (252, 267, 366, 470), (
        "the fixture must keep four individually small members with a repeated 470-byte slide")
    limits(monkeypatch, member_bytes=470, **{key: boundary})
    # WHEN the operation has its exact aggregate allowance, then one unit less
    accepted = brewdoc.render_presentation(path)
    limits(monkeypatch, member_bytes=470, **{key: boundary - 1})
    # THEN both occurrences render, but each repeated physical read charges the aggregate
    assert accepted[1] == {**zero_tally(), "slides": 2, "text_regions": 2}, (
        "the inclusive operation budget must preserve both declared slide occurrences")
    reason = "OOXML %s limit exceeded: deck/slides/shared.xml: %d" % (key, boundary - 1)
    with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
        brewdoc.render_presentation(path)


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED], ids=["stored", "deflated"])
def test_supported_selected_compression_preserves_exact_xml(tmp_path, monkeypatch, method):
    # GIVEN a consumed member using one supported compression method
    path = xml_package(tmp_path / "supported.zip", method=method)
    limits(monkeypatch)
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("part.xml").compress_type == method, "the fixture must use the selected codec"
        # WHEN the common reader decompresses the required XML
        actual = tree(common._xml_part(package, "part.xml"))
    # THEN compression does not change document semantics
    assert actual == XML_TREE, "stored and deflated members must produce the same full XML tree"


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_unsupported_selected_compression_refuses_before_stream_open(tmp_path, monkeypatch, method):
    # GIVEN readable XML encoded with a codec whose internal output is not bounded
    path = xml_package(tmp_path / "codec.zip", method=method)
    opened = []
    limits(monkeypatch)
    with zipfile.ZipFile(path) as package:
        original_open = package.open

        def record_open(*args, **kwargs):
            """Observe decoder creation while preserving the real stream."""
            opened.append(args[0])
            return original_open(*args, **kwargs)

        monkeypatch.setattr(package, "open", record_open)
        assert package.getinfo("part.xml").compress_type == method, "the unsupported codec must be real"
        # WHEN a selected member would enter that decoder
        reason = "OOXML ZIP compression is unsupported: part.xml: %d" % method
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")
    # THEN rejection precedes opening the decoder stream
    assert opened == [], "unsupported selected compression must refuse before package.open"


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_ignored_unsupported_compression_does_not_refuse_required_xml(tmp_path, monkeypatch, method):
    # GIVEN supported XML beside an ignored member using another codec
    path = xml_package(tmp_path / "ignored.zip")
    with zipfile.ZipFile(path, "a") as package:
        package.writestr("unread.bin", b"ignored", compress_type=method)
    limits(monkeypatch)
    # WHEN only the supported member is consumed
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("unread.bin").compress_type == method, "the ignored codec must be real"
        actual = tree(common._xml_part(package, "part.xml"))
    # THEN unrelated compressed content retains its existing ignored behavior
    assert actual == XML_TREE, "compression admission must apply only to consumed members"


@pytest.mark.parametrize("payload,characters,expected", [
    (b'<r a="&quot;">&amp;</r>', 2, ("r", {"a": '"'}, "&", None, ())),
    ('<r>é🙂</r>'.encode(), 2, ("r", {}, "é🙂", None, ())),
    ('<r>é🙂</r>'.encode("utf-16"), 2, ("r", {}, "é🙂", None, ())),
    (b'<!DOCTYPE r><r>ab</r>', 2, ("r", {}, "ab", None, ())),
    (b'<!DOCTYPE r [<!ENTITY x "ab">]><r a="&x;">&x;<a/> &x;</r>', 7,
     ("r", {"a": "ab"}, "ab", None, (("a", {}, None, " ab", ()),))),
], ids=["escaped-values", "unicode", "utf16", "benign-doctype", "expanded-entity-and-tail"])
def test_decoded_character_limits_preserve_encoding_entities_attributes_and_tails(
        tmp_path, monkeypatch, payload, characters, expected):
    # GIVEN encoding and expansion cases whose full tree has an independent character count
    path = xml_package(tmp_path / "characters.zip", payload)
    limits(monkeypatch, document_characters=characters)
    assert tree(ET.fromstring(payload)) == expected, "the synthetic XML must have the declared exact semantics"
    # WHEN the character allowance is exact, then one decoded code point too small
    with zipfile.ZipFile(path) as package:
        actual = tree(common._xml_part(package, "part.xml"))
        limits(monkeypatch, document_characters=characters - 1)
        # THEN compatibility survives and expanded text, tails and attribute values all count
        assert actual == expected, "character accounting must preserve the complete existing XML tree"
        reason = "OOXML document_characters limit exceeded: part.xml: %d" % (characters - 1)
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")


@pytest.mark.parametrize("payload,diagnostic", [
    (b"<", "unclosed token: line 1, column 0"),
    (b"<worksheet><sheetData>", "no element found: line 1, column 22"),
    (b"<r>&missing;</r>", "undefined entity: line 1, column 3"),
    (b"<x:r/>", "unbound prefix: line 1, column 0"),
    (b'<r a="1" a="2"/>', "duplicate attribute: line 1, column 9"),
])
def test_malformed_xml_keeps_existing_exact_parser_diagnostics(tmp_path, monkeypatch, payload, diagnostic):
    # GIVEN malformed XML with a pinned parser position
    path = xml_package(tmp_path / "malformed.zip", payload)
    limits(monkeypatch)
    # WHEN the common reader reports the parse boundary
    with zipfile.ZipFile(path) as package:
        assert package.read("part.xml") == payload, "the ZIP must contain the exact malformed payload"
        reason = "cannot parse OOXML part part.xml: " + diagnostic
        # THEN preflight preserves the existing public parse diagnostic
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")


def test_unknown_xml_encoding_keeps_existing_lookup_error(tmp_path, monkeypatch):
    # GIVEN an unknown declaration accepted by neither parser
    payload = b'<?xml version="1.0" encoding="unknown"?><r/>'
    path = xml_package(tmp_path / "encoding.zip", payload)
    limits(monkeypatch)
    assert payload.count(b"unknown") == 1, "the encoding refusal must have one unknown declaration"
    # WHEN the existing common boundary attempts decoding
    with zipfile.ZipFile(path) as package:
        # THEN the outer reader can still classify its original LookupError
        with pytest.raises(LookupError, match="^unknown encoding: unknown$"):
            common._xml_part(package, "part.xml")


def test_docx_refusal_preserves_api_cli_destinations_and_closes_resources(tmp_path, monkeypatch, capsys):
    # GIVEN a valid DOCX, prior output and a zero-depth policy
    path = docx(tmp_path / "sample.docx", '<w:p><w:r><w:t>x</w:t></w:r></w:p>')
    out = tmp_path / "preserved.md"
    out.write_bytes(b"previous Markdown\n")
    before = tree_state(tmp_path)
    packages, streams = [], []
    original_init, original_open = zipfile.ZipFile.__init__, zipfile.ZipFile.open

    def record_init(package, *args, **kwargs):
        """Keep the real archive object for post-refusal closure evidence."""
        original_init(package, *args, **kwargs)
        packages.append(package)

    def record_open(package, *args, **kwargs):
        """Keep the real member stream for post-refusal closure evidence."""
        stream = original_open(package, *args, **kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(zipfile.ZipFile, "__init__", record_init)
    monkeypatch.setattr(zipfile.ZipFile, "open", record_open)
    limits(monkeypatch, depth=0)
    reason = "OOXML depth limit exceeded: _rels/.rels: 0"
    expected = doc_refusal(path, reason)
    assert out.read_bytes() == b"previous Markdown\n", "the destination must start with its sentinel"
    # WHEN API and CLI refuse before output staging
    result = brewdoc.run(path, out)
    code = main([str(path), "--out", str(out)])
    captured = capsys.readouterr()
    # THEN the complete refusal, file tree and every owned resource are exact
    assert (result, code, captured.out, captured.err, tree_state(tmp_path),
            [package.fp for package in packages], [stream.closed for stream in streams]) == (
        (1, expected, ""), 1, json.dumps(expected, ensure_ascii=True, sort_keys=True) + "\n", "",
        before, [None, None], [True, True],
    ), "limit refusal must preserve both public contracts, destinations and resource closure"
    limits(monkeypatch)
    assert brewdoc.run(path)[0] == 0, "an independent operation must receive a fresh allowance"


@pytest.mark.parametrize("key,cap,reason", [
    ("members", 1, "OOXML members limit exceeded: 1"),
    ("member_bytes", 20, "OOXML member_bytes limit exceeded: part.xml: 20"),
])
def test_archive_count_and_declared_bytes_refuse_before_member_open(tmp_path, monkeypatch, key, cap, reason):
    # GIVEN an otherwise readable member whose archive metadata exceeds one cap
    path = xml_package(tmp_path / "metadata.zip")
    opened = []
    with zipfile.ZipFile(path) as package:
        assert (len(package.infolist()), package.getinfo("part.xml").file_size) == (2, 21), (
            "the synthetic archive must exceed the selected metadata cap by one")
        original_open = package.open

        def record_open(*args, **kwargs):
            """Observe stream creation while preserving the real ZIP reader."""
            opened.append(args[0])
            return original_open(*args, **kwargs)

        monkeypatch.setattr(package, "open", record_open)
        limits(monkeypatch, **{key: cap})
        # WHEN metadata already proves that the selected work cannot fit
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")
    # THEN the refusal precedes any decompressor or payload stream
    assert opened == [], "known metadata overruns must refuse before package.open"


@pytest.mark.parametrize("key", ["member_bytes", "consumed_bytes"])
def test_actual_byte_overrun_yields_only_accepted_chunks_and_closes_the_tail(tmp_path, monkeypatch, key):
    # GIVEN 65,547 actual bytes hidden behind a legal four-byte declaration
    path = xml_package(tmp_path / "chunks.zip", b"<r/>")
    payload = b"<r>" + b"x" * 65_540 + b"</r>"
    requests, accepted = [], []

    class ObservedStream(io.BytesIO):
        """Expose bounded requests and retained chunks at the controlled I/O edge."""

        def read(self, size=-1):
            """Keep request sizes available after stream closure."""
            requests.append(size)
            return super().read(size)

    stream = ObservedStream(payload)
    limits(monkeypatch, **{key: 65_546})
    budget = common._ZipBudget()
    with zipfile.ZipFile(path) as package:
        assert (len(payload), package.getinfo("part.xml").file_size) == (65_547, 4), (
            "the actual overrun must require an accepted full chunk and a refused tail")
        monkeypatch.setattr(package, "open", lambda *args, **kwargs: stream)
        # WHEN the real chunk reader reaches its one-byte overrun
        reason = "OOXML %s limit exceeded: part.xml: 65546" % key
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            accepted.extend(common._zip_member_chunks(package, "part.xml", budget))
    # THEN the final eleven-byte chunk is neither yielded nor charged
    assert (accepted, requests, budget.consumed_bytes, stream.closed) == (
        [payload[:65_536]], [65_536, 11], 65_536, True,
    ), "only accepted chunks may escape or consume the operation allowance"


@pytest.mark.parametrize("key", ["member_bytes", "consumed_bytes"])
def test_large_actual_xml_overrun_never_reaches_preflight_or_tree(tmp_path, monkeypatch, key):
    # GIVEN a multichunk actual overrun and spies on complete-member parsing
    path = xml_package(tmp_path / "unjoined.zip", b"<r/>")
    stream = io.BytesIO(b"<r>" + b"x" * 65_540 + b"</r>")
    parsed = []
    original_preflight, original_fromstring = common._xml_preflight, ET.fromstring

    def record_preflight(*args):
        """Observe complete retained payloads without replacing preflight."""
        parsed.append("preflight")
        return original_preflight(*args)

    def record_tree(*args):
        """Observe tree allocation without replacing the XML parser."""
        parsed.append("tree")
        return original_fromstring(*args)

    limits(monkeypatch, **{key: 65_546})
    monkeypatch.setattr(common, "_xml_preflight", record_preflight)
    monkeypatch.setattr(ET, "fromstring", record_tree)
    with zipfile.ZipFile(path) as package:
        assert package.getinfo("part.xml").file_size == 4, "the declaration must pass early byte checks"
        monkeypatch.setattr(package, "open", lambda *args, **kwargs: stream)
        # WHEN the XML reader rejects the tail bytes
        reason = "OOXML %s limit exceeded: part.xml: 65546" % key
        with pytest.raises(brewdoc.BrewdocError, match="^" + re.escape(reason) + "$"):
            common._xml_part(package, "part.xml")
    # THEN no complete joined member reaches preflight or ElementTree
    assert (parsed, stream.closed) == ([], True), (
        "actual-byte refusal must stop before complete payload parsing and close the stream")


def picture_parts():
    """Declare two pictures consuming the same stored opaque image member."""
    parts = base_parts([("rId1", "slides/media.xml", False)])
    parts["deck/slides/media.xml"] = slide(picture("rPic"), picture("rPic"))
    parts["deck/slides/_rels/media.xml.rels"] = relationships(
        relationship("rPic", "image", "../media/image.bin"))
    return parts


def stored_picture_package(path):
    """Keep image corruption deterministic by disabling ZIP compression."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as package:
        for name, payload in picture_parts().items():
            package.writestr(name, payload)
        package.writestr("deck/media/image.bin", b"x" * 70_000)
    return path


def observe_member_reads(monkeypatch):
    """Record real returned bytes and resources without changing member lifetime."""
    reads, streams, archives = {}, [], []
    original_open = zipfile.ZipFile.open

    def record_open(package, member, *args, **kwargs):
        """Track each consumed ZipInfo and its real stream."""
        stream = original_open(package, member, *args, **kwargs)
        streams.append(stream)
        archives.append(package)
        original_read = stream.read

        def record_read(size=-1):
            """Record returned lengths while preserving CRC validation."""
            chunk = original_read(size)
            reads.setdefault(member.filename, []).append((size, len(chunk)))
            return chunk

        monkeypatch.setattr(stream, "read", record_read)
        return stream

    monkeypatch.setattr(zipfile.ZipFile, "open", record_open)
    return reads, streams, archives


def presentation_refusal(path, reason):
    """Pin the complete existing zero-slide presentation refusal."""
    return {
        **doc_refusal(path, reason), "route": "presentation", "unit_kind": "slide",
        "not_carried": ["charts, SmartArt and embedded objects", "image pixels, audio and video",
                        "animations, transitions, layout and master text, styling and visual positioning"],
    }


def test_repeated_embedded_images_drain_bounded_reads_and_charge_actual_bytes(tmp_path, monkeypatch):
    # GIVEN five XML parts and two physical reads of one 70,000-byte image
    path = stored_picture_package(tmp_path / "pictures.pptx")
    out = tmp_path / "preserved.md"
    out.write_bytes(b"previous Markdown\n")
    before = tree_state(tmp_path)
    limits(monkeypatch, member_bytes=70_000, consumed_bytes=141_696)
    assert tuple(len(payload.encode()) for payload in picture_parts().values()) == (252, 236, 232, 742, 234), (
        "the XML workload must contribute exactly 1,696 bytes before image drains")
    reads, streams, archives = observe_member_reads(monkeypatch)
    materialized = []
    original_bytes = common._zip_member_bytes

    def record_materialization(*args, **kwargs):
        """Observe byte-producing reads while preserving real XML retention."""
        materialized.append(args[1])
        return original_bytes(*args, **kwargs)

    monkeypatch.setattr(common, "_zip_member_bytes", record_materialization)
    # WHEN the operation has its exact aggregate allowance, then one byte less
    markdown, tally = brewdoc.render_presentation(path)
    accepted_reads = reads["deck/media/image.bin"].copy()
    accepted_materializations = materialized.copy()
    limits(monkeypatch, member_bytes=70_000, consumed_bytes=141_695)
    result = brewdoc.run(path, out)
    reason = "OOXML consumed_bytes limit exceeded: deck/media/image.bin: 141695"
    image_line = ('Image: name="Photo"; size=120x80 EMU; alt="Alt"; caption="Caption"; '
                  'source=embedded; bytes=70000')
    # THEN both exact image facts survive, while repeated work refuses before output replacement
    assert (presentation_body(markdown), tally, accepted_reads, accepted_materializations,
            result, tree_state(tmp_path), [stream.closed for stream in streams],
            [package.fp for package in archives]) == (
        '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Untitled"\n\n'
        + image_line + "\n\n" + image_line + "\n",
        {**zero_tally(), "slides": 1, "text_regions": 2},
        [(65_536, 65_536), (4_465, 4_464), (1, 0)] * 2,
        list(picture_parts()), (1, presentation_refusal(path, reason), ""), before,
        [True] * 14, [None] * 14,
    ), "image counting must bound reads, avoid payload materialization and charge every physical occurrence"


def test_corrupt_stored_image_crc_refuses_and_preserves_destinations(tmp_path, monkeypatch):
    # GIVEN a stored image whose payload is corrupted before conversion without updating CRC
    path = stored_picture_package(tmp_path / "corrupt.pptx")
    encoded = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as package:
        info = package.getinfo("deck/media/image.bin")
    name_bytes, extra_bytes = struct.unpack_from("<HH", encoded, info.header_offset + 26)
    offset = info.header_offset + 30 + name_bytes + extra_bytes
    assert (info.compress_type, info.file_size, encoded[offset]) == (zipfile.ZIP_STORED, 70_000, 120), (
        "the CRC probe must start with a real stored payload and an identified first byte")
    encoded[offset] ^= 1
    path.write_bytes(encoded)
    out = tmp_path / "preserved.md"
    out.write_bytes(b"previous Markdown\n")
    before = tree_state(tmp_path)
    limits(monkeypatch, member_bytes=70_000)
    _reads, streams, archives = observe_member_reads(monkeypatch)
    # WHEN image counting exhausts the real ZIP stream and validates its CRC
    result = brewdoc.run(path, out)
    reason = "presentation unreadable: %s: Bad CRC-32 for file 'deck/media/image.bin'" % path
    # THEN binary counting keeps EOF validation and refuses without output or resource leaks
    assert (result, tree_state(tmp_path), [stream.closed for stream in streams],
            [package.fp for package in archives]) == (
        (1, presentation_refusal(path, reason), ""), before, [True] * 6, [None] * 6,
    ), "image drain must retain CRC refusal, prior destinations and all owned resource cleanup"
