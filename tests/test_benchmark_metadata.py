import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = ROOT / "benchmarks" / "corpus.json"
UNIT_FIELDS = {"pages", "sheets", "chapters", "slides"}
UNIT_FIELD_BY_FORMAT = {
    "pdf": "pages",
    "docx": "chapters",
    "pptx": "slides",
    "ods": "sheets",
    "xls": "sheets",
    "xlsb": "sheets",
    "xlsm": "sheets",
    "xlsx": "sheets",
}


def load_manifest() -> dict:
    """Read the benchmark manifest without opening corpus binaries."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_structure_is_unique_and_covers_all_container_fixtures():
    # GIVEN the benchmark manifest and every DOCX or PPTX fixture path
    manifest = load_manifest()
    documents = manifest["documents"]
    fixture_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in FIXTURES.rglob("*")
        if path.suffix.lower() in {".docx", ".pptx"}
    )
    # WHEN structural identities and paths are collected without reading the binaries
    ids = [document["id"] for document in documents]
    paths = [document["path"] for document in documents]
    duplicate_ids = sorted({document_id for document_id in ids if ids.count(document_id) > 1})
    duplicate_paths = sorted({path for path in paths if paths.count(path) > 1})
    missing_paths = sorted(path for path in paths if not (ROOT / path).is_file())
    container_paths = sorted(
        document["path"] for document in documents if document["format"] in {"docx", "pptx"}
    )
    # THEN the index is complete, unique and bound to every container fixture
    assert (manifest["schema_version"], len(documents), sum(doc["size_bytes"] for doc in documents)) == (
        2,
        41,
        4_658_579,
    ), "the manifest must retain schema 2 and the accepted 41-input byte total"
    assert duplicate_ids == [], "benchmark document IDs must be unique"
    assert duplicate_paths == [], "each benchmark input path must identify one document"
    assert missing_paths == [], "every benchmark input path must exist in the checkout"
    assert container_paths == fixture_paths, "the manifest must cover all six DOCX and four PPTX fixtures"


def test_annotations_bind_to_manifest_with_one_format_unit_collection():
    # GIVEN every manifest record and its declared source annotation
    documents = load_manifest()["documents"]
    annotation_paths = [document["annotation_path"] for document in documents]
    duplicate_paths = sorted(
        {path for path in annotation_paths if annotation_paths.count(path) > 1}
    )
    missing_paths = sorted(path for path in annotation_paths if not (ROOT / path).is_file())
    # WHEN annotations are parsed after their paths have been checked
    assert duplicate_paths == [], "each benchmark document must have its own annotation file"
    assert missing_paths == [], "every declared benchmark annotation must exist"
    annotations = {
        document["id"]: json.loads((ROOT / document["annotation_path"]).read_text(encoding="utf-8"))
        for document in documents
    }
    bindings = {
        document_id: (annotation["document_id"], annotation["source_sha256"])
        for document_id, annotation in annotations.items()
    }
    expected_bindings = {
        document["id"]: (document["id"], document["sha256"])
        for document in documents
    }
    unit_fields = {
        document_id: tuple(sorted(UNIT_FIELDS.intersection(annotation)))
        for document_id, annotation in annotations.items()
    }
    expected_unit_fields = {
        document["id"]: (UNIT_FIELD_BY_FORMAT.get(document["format"]),)
        for document in documents
    }
    schema_versions = {document_id: annotation["schema_version"]
                       for document_id, annotation in annotations.items()}
    # THEN identity, schema and the single format-specific unit collection agree
    assert bindings == expected_bindings, "each annotation must match its manifest ID and source hash"
    assert schema_versions == dict.fromkeys(schema_versions, 1), "all source annotations use schema 1"
    assert unit_fields == expected_unit_fields, (
        "each annotation must have exactly one pages, sheets, chapters or slides collection for its format"
    )


@pytest.mark.corpus
def test_every_manifest_binary_matches_its_recorded_identity():
    # GIVEN the accepted benchmark input paths, hashes and byte sizes
    documents = load_manifest()["documents"]
    expected = {
        document["path"]: (document["sha256"], document["size_bytes"])
        for document in documents
    }
    missing_paths = sorted(path for path in expected if not (ROOT / path).is_file())
    # WHEN every corpus binary is read and measured
    assert missing_paths == [], "every benchmark binary must exist before its identity is checked"
    actual = {}
    for path in expected:
        data = (ROOT / path).read_bytes()
        actual[path] = (hashlib.sha256(data).hexdigest(), len(data))
    # THEN no input has changed since the manifest was reviewed
    assert actual == expected, "each benchmark binary must match its recorded SHA-256 and byte size"
