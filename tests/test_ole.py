"""Exact OLE native wrapper boundaries and passive link handling."""

import hashlib
from io import BytesIO
import struct
from zipfile import ZIP_STORED, ZipFile

import pytest

from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc._ole import embedded_document_name, inspect_compound_payloads, unwrap_ole_native


def native(payload):
    """Wrap bytes in the MS-OLEDS NativeDataSize field."""
    return struct.pack("<I", len(payload)) + payload


def package(payload, filename=b"note.md", *, mode=3):
    """Build the Package layout independently from the reader."""
    source = b"C:\\source\\" + filename
    temp = b"C:\\temp\\" + filename + b"\0"
    header = b"\x02\x00" + filename + b"\0" + source + b"\0" + struct.pack("<HHI", 0, mode, len(temp)) + temp
    body = header + struct.pack("<I", len(payload)) + payload
    return native(body), 4 + len(header) + 4


def document_package():
    """Build a minimal synthetic Word package with source XML."""
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Embedded</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


@pytest.mark.parametrize("filename,payload", [(b"note.md", b"# Embedded\n"),
                                              (b"file.pdf", b"%PDF-1.7\n%%EOF"),
                                              (b"file.docx", document_package())])
def test_embedded_package_preserves_metadata_and_exact_payload(filename, payload):
    # GIVEN an embedded Package object with independent source metadata.
    wrapper, offset = package(payload, filename)
    # WHEN its declared fields are decoded.
    result = unwrap_ole_native(wrapper, InspectionContext())
    # THEN original source offsets and exact payload bytes survive.
    assert result["status"] == "embedded", "embedded Package framing must be recognized"
    assert result["filename"] == filename.decode("ascii"), "original display filename must survive"
    assert result["source_path"] == "C:\\source\\" + filename.decode("ascii"), "original source metadata must survive"
    assert result["temp_path"] == "C:\\temp\\" + filename.decode("ascii"), "original temporary path metadata must survive"
    assert (result["payload_offset"], result["payload_length"], result["payload"]) == (offset, len(payload), payload), "declared payload boundary must be exact"
    assert hashlib.sha256(wrapper[offset:offset + result["payload_length"]]).digest() == hashlib.sha256(payload).digest(), "payload hash must match its exact original slice"


def test_native_data_size_without_package_metadata():
    # GIVEN the normative MS-OLEDS size-prefixed native bytes.
    payload = b"%PDF-1.4\n%%EOF"
    # WHEN unwrapped without guessing application-specific metadata.
    result = unwrap_ole_native(native(payload), InspectionContext())
    # THEN the byte offset starts immediately after NativeDataSize.
    assert (result["status"], result["payload_offset"], result["payload"]) == ("embedded", 4, payload), "native data size framing must preserve exact bytes"
    assert result["filename"] is None, "no filename may be invented for raw native data"


def test_linked_package_is_metadata_only():
    # GIVEN linked Package metadata with no embedded payload.
    wrapper, _ = package(b"", b"remote.md", mode=1)
    # WHEN the object type is inspected.
    result = unwrap_ole_native(wrapper, InspectionContext())
    # THEN source paths remain references and no file is opened.
    assert result["status"] == "linked", "linked Package must be classified explicitly"
    assert result["payload"] is None, "linked objects must not manufacture embedded bytes"
    assert result["source_path"] == "C:\\source\\remote.md", "link target metadata must survive"


@pytest.mark.parametrize("wrapper", [b"", b"\x01\x00", struct.pack("<I", 4000) + b"short",
                                     native(b"\x02\x00unterminated"), native(b"\x02\x00x\0y\0\0\0\x03\0\xff\xff\xff\xff")])
def test_truncated_and_huge_fields_are_invalid(wrapper):
    # GIVEN a malformed native size or Package field.
    # WHEN bounds are checked before slicing.
    result = unwrap_ole_native(wrapper, InspectionContext())
    # THEN invalid bytes cannot become successful payloads.
    assert result["status"] == "invalid", "malformed framing must be explicit"
    assert result["payload"] is None, "invalid wrappers must not yield speculative payload bytes"


def test_unknown_package_mode_is_unsupported():
    # GIVEN a package object type outside the known linked/embedded modes.
    wrapper, _ = package(b"%PDF fake", mode=99)
    # WHEN package metadata is walked structurally.
    result = unwrap_ole_native(wrapper, InspectionContext())
    # THEN embedded signature bytes do not trigger recovery.
    assert result["status"] == "unsupported", "unknown Package modes must stay opaque"
    assert result["payload"] is None, "signature searching must never manufacture payloads"


def test_payload_budget_checks_before_growth():
    # GIVEN a payload larger than the remaining decoder allowance.
    context = InspectionContext(limits={"decoded_bytes": 1})
    # WHEN a normative native stream is unwrapped.
    with pytest.raises(InspectionLimit):
        unwrap_ole_native(native(b"abc"), context)
    # THEN refused bytes are not charged or materialized.
    assert context.consumed["decoded_bytes"] == 0, "decoder must check before admitting payload bytes"


def test_unicode_package_metadata_preserves_ansi_and_original_spans():
    # GIVEN a Package2 trailer containing three UTF-16 metadata strings.
    wrapper, offset = package(b"# text", b"fallback.md")
    values = ["C:\\temp\\Текст.md", "Текст.md", "C:\\source\\Текст.md"]
    trailer = b"".join(struct.pack("<I", len(value)) + value.encode("utf-16le") for value in values)
    source = native(wrapper[4:] + trailer)
    # WHEN Unicode extensions are read by their declared character lengths.
    result = unwrap_ole_native(source, InspectionContext())
    # THEN both original ANSI metadata and Unicode display names survive.
    assert result["filename"] == "Текст.md", "Unicode display filename must be available to the recursive scanner"
    assert result["label"] == "fallback.md", "original ANSI display metadata must not be overwritten"
    assert result["unicode_source_path"] == values[2], "Unicode source metadata must survive"
    assert (result["payload_offset"], result["payload_length"], result["payload"]) == (offset, 6, b"# text"), "Unicode trailers must not alter embedded payload boundaries"
    span = result["metadata_spans"]["unicode_label"]
    assert source[span["offset"]:span["offset"] + span["length"]] == "Текст.md".encode("utf-16le"), "Unicode metadata span must address original bytes"


def test_declared_payload_length_cannot_overrun_wrapper():
    # GIVEN an embedded payload size larger than the declared native stream.
    wrapper, offset = package(b"abc")
    malformed = bytearray(wrapper)
    struct.pack_into("<I", malformed, offset - 4, 0xFFFFFFFF)
    # WHEN payload bounds are verified.
    result = unwrap_ole_native(bytes(malformed), InspectionContext())
    # THEN no bytes are allocated using the hostile declared size.
    assert (result["status"], result["payload"]) == ("invalid", None), "oversized payload must be refused before slicing"


def test_unknown_package_tail_cannot_change_payload_offset():
    # GIVEN valid payload framing followed by an unknown tail.
    wrapper, _ = package(b"%PDF-1.4\n%%EOF")
    malformed = native(wrapper[4:] + b"arbitrary trailing bytes")
    # WHEN known Package and Unicode tail layouts are validated.
    result = unwrap_ole_native(malformed, InspectionContext())
    # THEN source-like signatures cannot be searched out as replacement payloads.
    assert result["status"] == "invalid", "unknown trailer must prevent a complete wrapper claim"
    assert result["payload"] is None, "unrecognized layout must not produce invented inner boundaries"


def admitted_entry(context, document, data, *, ordinal=1, name="\x01Ole10Native"):
    """Admit one original stream with the same direct references as the CFB reader."""
    where = {"kind": "compound", "entry_id": ordinal, "path": name,
             "sectors": [10 + ordinal], "sector_kind": "regular", "sector_size": 512}
    artifact = context.payload(document, "compound_stream", where, data, "application/octet-stream")
    component = context.component(document, "compound_stream", where, data=data, artifact=artifact)
    return {"name": name, "path": name, "kind": "stream", "entry_id": ordinal,
            "data": data, "location": where, "component": component, "artifact": artifact}


def test_compound_native_children_preserve_parent_refs_and_duplicate_siblings():
    # GIVEN two separate stream occurrences holding identical original HTML bytes.
    from brewdoc.inspection import _scan

    context = InspectionContext()
    root = context.document(b"parent", "parent.xls", parent=None, location={"kind": "source"})
    body = b'<script>nativeProof();</script><a href="https://native.invalid/exact">link</a>'
    wrapper, offset = package(body, b"nested.html")
    entries = [admitted_entry(context, root, wrapper, ordinal=number) for number in (1, 2)]
    # WHEN the orchestrator's existing ancestry callback inspects both siblings.
    inspect_compound_payloads(entries, "parent.xls", context, root,
                              scan_embedded=lambda data, name, **keywords: _scan(data, name, context, **keywords))
    # THEN equal sibling hashes remain separate documents with exact parent links.
    assert [(item["name"], item["sha256"], item["location"]["native_data_offset"])
            for item in context.documents[1:]] == [("nested.html", hashlib.sha256(body).hexdigest(), offset)] * 2, "duplicate siblings must not be mistaken for recursive cycles"
    native = [item for item in context.findings if item["kind"] == "ole-native-payload"]
    assert [(item["details"]["wrapper_component"], item["details"]["wrapper_artifact"])
            for item in native] == [(entry["component"], entry["artifact"]) for entry in entries], "native evidence must directly reference the original wrapper occurrence"
    assert [item["component"] for item in context.documents[1:]] == [item["component"] for item in native], "child document component must identify its exact retained native payload"
    assert [item["text"] for item in context.findings if item["capability"] == "scripts" and item["text"] == "nativeProof();"] == ["nativeProof();"] * 2, "each child's exact script source must be inspected without execution"


def test_compound_payload_without_callback_remains_incomplete():
    # GIVEN a retained supported attachment and no recursive inspection callback.
    context = InspectionContext()
    root = context.document(b"parent", "parent.xls", parent=None, location={"kind": "source"})
    wrapper, _ = package(b"# source", b"nested.md")
    entry = admitted_entry(context, root, wrapper)
    # WHEN the projection-only caller inspects stream boundaries.
    inspect_compound_payloads((entry,), "parent.xls", context, root, scan_embedded=None)
    # THEN retained source does not imply recursively inspected source.
    assert len(context.documents) == 1, "projection-only scan must not invent an inspected child"
    assert context.coverage[(root, "attachments")]["status"] == "partial", "missing recursion must remain explicit incomplete coverage"


def test_compound_callback_preserves_existing_ancestry_cycle_guard():
    # GIVEN a supported payload already present in the orchestrator's active ancestry.
    from brewdoc.inspection import _scan

    body = b"# same ancestor"
    context = InspectionContext()
    root = context.document(body, "ancestor.md", parent=None, location={"kind": "source"})
    context.active_hashes.append(hashlib.sha256(body).hexdigest())
    wrapper, _ = package(body, b"nested.md")
    entry = admitted_entry(context, root, wrapper)
    # WHEN the shared callback reaches the repeated content hash.
    inspect_compound_payloads((entry,), "parent.xls", context, root,
                              scan_embedded=lambda data, name, **keywords: _scan(data, name, context, **keywords))
    # THEN recursion stops at one located child with explicit cycle evidence.
    assert len(context.documents) == 2, "a cycle boundary must be inventoried exactly once"
    assert context.coverage[(context.documents[1]["id"], "inventory")]["reason"] == "embedded document ancestry cycle", "the existing ancestry guard must remain authoritative"
    assert context.active_hashes == [hashlib.sha256(body).hexdigest()], "the helper must not alter caller-owned ancestry"


@pytest.mark.parametrize("filename,mode,expected", [(b"unknown.exe", 3, "ole-native-payload"),
                                                   (b"remote.html", 1, "ole-native-link")])
def test_unknown_and_linked_compound_payloads_do_not_recurse(filename, mode, expected):
    # GIVEN an unsupported native type or an external linked object.
    context = InspectionContext()
    wrapper, _ = package(b"opaque", filename, mode=mode)
    entry = admitted_entry(context, "d", wrapper)
    called = []
    # WHEN the helper encounters source metadata that does not admit local document inspection.
    inspect_compound_payloads((entry,), "source.xls", context, "d",
                              scan_embedded=lambda *args, **kwargs: called.append((args, kwargs)))
    # THEN metadata and wrapper bytes survive without any callback or path access.
    assert called == [], "unknown and linked objects must remain passive"
    assert [item["kind"] for item in context.findings] == [expected], "the original object disposition must remain explicit"
    assert context.payloads[entry["artifact"]] == wrapper, "opaque original wrapper must remain retrievable"


def test_compound_payload_refusal_keeps_wrapper_and_records_limit():
    # GIVEN an admitted wrapper and an exhausted cumulative decoder allowance.
    context = InspectionContext(limits={"decoded_bytes": 0})
    wrapper, _ = package(b"# source", b"nested.md")
    entry = admitted_entry(context, "d", wrapper)
    called = []
    # WHEN unwrapping reaches its first bounded metadata field.
    inspect_compound_payloads((entry,), "source.xls", context, "d",
                              scan_embedded=lambda *args, **kwargs: called.append((args, kwargs)))
    # THEN refusal retains original wrapper bytes and cannot recurse.
    assert (called, context.payloads[entry["artifact"]]) == ([], wrapper), "limit exhaustion must preserve original evidence"
    assert context.coverage[("d", "attachments")]["status"] == "unavailable", "limit refusal must degrade attachment coverage"


@pytest.mark.parametrize("suffix", sorted((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".xls",
                                          ".ods", ".odt", ".odp", ".html", ".epub", ".eml", ".md", ".txt")))
def test_shared_recognizer_preserves_all_fifteen_supported_names(suffix):
    # GIVEN a filename admitted by the original package recognizer.
    filename = "original" + suffix
    # WHEN the lower helper serves package and XLS consumers.
    result = embedded_document_name(filename, b"exact source", InspectionContext(), {"kind": "compound"})
    # THEN recognition preserves the original name and bounded dispatch set.
    assert result == filename, "supported source names must remain byte-for-byte unchanged"


def test_compound_raw_pdf_stream_recurses_through_existing_pdf_inspector():
    # GIVEN an admitted Package stream containing a structurally valid PDF with a URI action.
    from brewdoc.inspection import _scan
    from test_inspect_pdf import _pdf

    payload = _pdf({1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction 3 0 R >>",
                    2: b"<< /Type /Pages /Count 0 /Kids [] >>",
                    3: b"<< /Type /Action /S /URI /URI (https://native.invalid/pdf) >>"})
    context = InspectionContext()
    root = context.document(b"parent", "parent.xls", parent=None, location={"kind": "source"})
    entry = admitted_entry(context, root, payload, name="Package")
    # WHEN the moved recognizer sees the original PDF header at its existing boundary.
    inspect_compound_payloads((entry,), "parent.xls", context, root,
                              scan_embedded=lambda data, name, **keywords: _scan(data, name, context, **keywords))
    # THEN PDF semantics use the exact raw stream and its original component.
    assert [(item["name"], item["sha256"], item["component"]) for item in context.documents[1:]] == [
        ("Package.pdf", hashlib.sha256(payload).hexdigest(), entry["component"])], "raw Package stream must recurse with exact stream provenance"
    assert [item["target"] for item in context.findings if item["capability"] == "links" and item["target"] == "https://native.invalid/pdf"] == [
        "https://native.invalid/pdf"], "existing PDF action inspection must see the nested original destination"


def test_compound_nested_document_uses_existing_depth_limit():
    # GIVEN a native child while the caller is already at its nesting limit.
    from brewdoc.inspection import _scan

    context = InspectionContext(limits={"nesting": 0})
    root = context.document(b"parent", "parent.xls", parent=None, location={"kind": "source"})
    context.active_hashes.append(hashlib.sha256(b"parent").hexdigest())
    wrapper, _ = package(b"# source", b"nested.md")
    entry = admitted_entry(context, root, wrapper)
    # WHEN the callback attempts to admit the nested source.
    inspect_compound_payloads((entry,), "parent.xls", context, root,
                              scan_embedded=lambda data, name, **keywords: _scan(data, name, context, **keywords))
    # THEN the existing shared limit records a refusal without changing ancestry.
    assert [item["limit"] for item in context.exhausted] == ["nesting"], "native recursion must use the caller's existing depth budget"
    assert len(context.active_hashes) == 1, "refused child must not remove the parent's active hash"


def test_compound_returns_exact_original_artifact_and_component_references():
    # GIVEN an actual parsed mini stream with its admitted artifact and component.
    from brewdoc._compound import read_compound
    from test_compound import compound_fixture

    context = InspectionContext()
    entries = read_compound(compound_fixture(), context, "d")
    # WHEN the returned entry identifies that existing evidence directly.
    entry = entries[1]
    # THEN consumers do not need searches, rereads or duplicate inventories.
    assert (entry["component"], entry["artifact"]) == (
        context.components[1]["id"], context.components[1]["artifact"]), "returned references must identify existing exact CFB stream evidence"
    assert context.payloads[entry["artifact"]] == entry["data"] == b"abc", "direct stream artifact must address the original bytes"
