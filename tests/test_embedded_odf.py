"""Original-member provenance and native caches for embedded ODF workbooks."""

import hashlib
import io
import json
import warnings
import zipfile
from pathlib import Path

import brewdoc
import pytest

from test_inspect_workbook import _ods
from test_odf import members as odf_members


ODS_MEDIA = "application/vnd.oasis.opendocument.spreadsheet"
FIXTURE = Path(__file__).parent / "fixtures/odt/tika-testODFwithOOo3.odt"


def test_public_odt_recovers_embedded_workbook_as_its_own_original_member_scope():
    # GIVEN a licensed ODT containing a manifest-declared spreadsheet directory
    with zipfile.ZipFile(FIXTURE) as archive:
        source = archive.read("Object 1/content.xml")
        ordinal = next(index for index, info in enumerate(archive.infolist(), 1)
                       if info.filename == "Object 1/content.xml")
    assert (ordinal, len(source), b"one embedded" in source) == (7, 4105, True), (
        "public fixture must contain the original spreadsheet XML and expected source occurrence")
    # WHEN complete inspection walks the original package and embedded document scopes
    report = brewdoc.inspect_document(FIXTURE)
    children = [item for item in report["documents"] if item.get("media_type") == ODS_MEDIA]
    # THEN a logical child retains original member provenance and all eight native cells
    assert len(children) == 1, "the manifest-declared embedded spreadsheet must become a separate inspected child"
    child = children[0]
    assert (child["name"], child["representation"], child["byte_size"], child["sha256"]) == (
        "Object 1/", "odf-directory", None, None,
    ), "a directory source must never be relabelled as a fabricated contiguous workbook file"
    assert [(item["location"]["cell"], item["details"]["value"],
             item["location"]["part"], item["location"]["member_ordinal"])
            for item in report["findings"] if item["document"] == child["id"] and item["kind"] == "cell"] == [
        ("A1", "Number", "Object 1/content.xml", 7), ("B1", "Name", "Object 1/content.xml", 7),
        ("A2", 1.0, "Object 1/content.xml", 7), ("B2", "one embedded", "Object 1/content.xml", 7),
        ("A3", 2.0, "Object 1/content.xml", 7), ("B3", "two embedded", "Object 1/content.xml", 7),
        ("A4", 3.0, "Object 1/content.xml", 7), ("B4", "three embedded", "Object 1/content.xml", 7),
    ], "embedded addressed values must retain original physical member names and ordinals"
    assert {item["capability"]: item["status"] for item in report["coverage"]
            if item["document"] == report["documents"][0]["id"]
            and item["capability"] in ("cells", "formulas", "names")} == {
        "cells": "not_applicable", "formulas": "not_applicable", "names": "not_applicable",
    }, "embedded workbook evidence must not change the native capabilities of its ODT parent"
    descriptors = [item for item in child["source_members"] if item["original_part"] == "Object 1/content.xml"]
    assert [(item["member_ordinal"], item["byte_size"], item["sha256"]) for item in descriptors] == [
        (7, len(source), hashlib.sha256(source).hexdigest()),
    ], "logical child sources must identify original admitted bytes rather than a rebuilt ZIP"
    assert brewdoc.read_document_artifact(FIXTURE, descriptors[0]["artifact"]) == source, (
        "retrieval must return exact original embedded member bytes without rebuilding a workbook ZIP")


def _container(kind, children):
    """Declare supported ODF directories while keeping physical source members unchanged."""
    blocks = ('<text:p>Parent body.</text:p>' if kind == "odt" else
              '<draw:page draw:name="Parent"><draw:text-box><text:p>Parent body.</text:p>'
              '</draw:text-box></draw:page>')
    root_members = odf_members(kind, blocks)
    declarations = "".join('<manifest:file-entry manifest:full-path="%s" manifest:media-type="%s"/>'
                           % (prefix, ODS_MEDIA) for prefix, _source in children)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for part, payload in root_members:
            archive.writestr(part, payload.replace("</manifest:manifest>", declarations + "</manifest:manifest>"))
        for prefix, source in children:
            archive.writestr(prefix + "content.xml", source)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["odt", "odp"])
def test_identical_embedded_workbook_siblings_remain_separate_and_deterministic(tmp_path, kind, monkeypatch):
    # GIVEN two manifest directories with identical native spreadsheet bytes and sheet names
    source = _ods('<table:table-row><table:table-cell office:value-type="float" office:value="42"/>'
                  '</table:table-row>')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        content = archive.read("content.xml")
    path = tmp_path / ("parent." + kind)
    path.write_bytes(_container(kind, (("Object A/", content), ("Object B/", content))))
    with zipfile.ZipFile(path) as archive:
        admitted = [(index, info.filename, archive.read(info)) for index, info in enumerate(archive.infolist(), 1)
                    if info.filename.startswith("Object ")]
    assert [(ordinal, part, len(payload)) for ordinal, part, payload in admitted] == [
        (4, "Object A/content.xml", len(content)), (5, "Object B/content.xml", len(content)),
    ], "identical siblings must occupy distinct original source occurrences"
    monkeypatch.setattr("brewdoc._inspection_workbook._cached_values",
                        lambda *args: pytest.fail("embedded ODF inspection must not call calamine"))
    # WHEN the same parent is inspected repeatedly and converted normally
    first, second = brewdoc.inspect_document(path), brewdoc.inspect_document(path)
    code, receipt, markdown = brewdoc.run(path)
    children = [item for item in first["documents"] if item.get("representation") == "odf-directory"]
    # THEN each logical child owns its cells and original source scope without false cycles
    assert [(item["name"], item["parent"], item["byte_size"], item["sha256"]) for item in children] == [
        ("Object A/", first["documents"][0]["id"], None, None),
        ("Object B/", first["documents"][0]["id"], None, None),
    ], "identical payload bytes in sibling directories must not be treated as ancestry cycles"
    assert [(item["document"], item["location"]["qualified_cell"], item["location"]["part"],
             item["details"]["value"]) for item in first["findings"] if item["kind"] == "cell"] == [
        (children[0]["id"], "'Inputs'!A1", "Object A/content.xml", 42.0),
        (children[1]["id"], "'Inputs'!A1", "Object B/content.xml", 42.0),
    ], "same-named cells from different embedded workbooks must never share a document identity"
    assert (first, code, receipt["file_ok"], "Object A/content.xml" in markdown,
            "Object B/content.xml" in markdown, "42.0" in markdown) == (
        second, 0, True, True, True, True,
    ), "normal Markdown must expose independently inspected child values and deterministic source locations"
    assert [json.loads(brewdoc.read_document_artifact(path, "inspection/report"))] == [first], (
        "the retrievable report must exactly match repeated independent original-member inspection")


@pytest.mark.parametrize("scenario,count,encryption,source_kind,status,cell_count", [
    ("missing", 0, "", "valid", "unavailable", 0),
    ("duplicate", 2, "", "valid", "partial", 2),
    ("encrypted", 1, '<manifest:encryption-data manifest:checksum="original"/>', "valid", "unavailable", 0),
    ("malformed", 1, "", "malformed", "unavailable", 0),
])
def test_unreadable_embedded_workbook_sources_preserve_payloads_without_complete_cells(
        tmp_path, scenario, count, encryption, source_kind, status, cell_count):
    # GIVEN an explicitly declared child with missing, duplicated, encrypted or malformed content
    source = _ods('<table:table-row><table:table-cell office:value-type="float" office:value="42"/>'
                  '</table:table-row>')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        content = archive.read("content.xml")
    payload = {"valid": content, "malformed": b"<broken>"}[source_kind]
    original = _container("odt", (("Object A/", content),))
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()
                   if info.filename != "Object A/content.xml"]
    declarations = ('<manifest:file-entry manifest:full-path="Object A/" manifest:media-type="%s"/>' % ODS_MEDIA).encode()
    replacement = ('<manifest:file-entry manifest:full-path="Object A/" manifest:media-type="%s">%s'
                   '</manifest:file-entry>' % (ODS_MEDIA, encryption)).encode()
    members = [(name, data.replace(declarations, replacement)) for name, data in members]
    members.extend([("Object A/content.xml", payload)] * count)
    path = tmp_path / (scenario + ".odt")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        actual = [archive.read(info) for info in archive.infolist() if info.filename == "Object A/content.xml"]
    assert actual == [payload] * count, "the exact defective member occurrences must exist before inspection"
    # WHEN original-member inspection reaches the malformed child boundary
    report = brewdoc.inspect_document(path)
    children = [item for item in report["documents"] if item.get("representation") == "odf-directory"]
    assert len(children) == 1, "a declared supported child must remain visible even when unreadable"
    child = children[0]
    descriptors = [item for item in child["source_members"] if item["original_part"] == "Object A/content.xml"]
    # THEN failed native coverage remains explicit while every original payload remains retrievable
    assert ([item["status"] for item in report["coverage"]
             if item["document"] == child["id"] and item["capability"] == "cells"],
            len([item for item in report["findings"] if item["document"] == child["id"] and item["kind"] == "cell"]),
            [brewdoc.read_document_artifact(path, item["artifact"]) for item in descriptors]) == (
        [status], cell_count, [payload] * count,
    ), "unreadable or ambiguous original content must never become complete empty workbook evidence"
