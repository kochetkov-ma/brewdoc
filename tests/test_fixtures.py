import hashlib
import json
import platform
import re
import sys
from pathlib import Path

import pytest

from brewdoc import common, service

# Every test here opens a real document, so the whole module stays out of the fast gate.
pytestmark = pytest.mark.corpus

FIXTURES = Path(__file__).parent / "fixtures"
RECEIPTS = json.loads((FIXTURES / "receipts.json").read_text(encoding="ascii"))
# Derived from the live route table, never a literal: a new route must bring its corpus with it.
SUPPORTED = tuple(sorted(service.ROUTES))
PLANNED = (".csv", ".eml", ".epub", ".md", ".odp", ".odt", ".tsv", ".txt")
HTML_SUPPORTED = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
RECEIPT_KEYS = ("route", "receipt_schema", "unit_kind", "units", "tables", "text_regions")
SNAPSHOT_KEYS = {
    False: RECEIPT_KEYS,
    True: RECEIPT_KEYS + ("markdown_schema", "unit_keys", "artifacts"),
}
SCAN = "pdf/us-patent-223898-scan.pdf"
CORPUS_FILES = 67
CORPUS_BYTES = 5_976_093
PROVENANCE = ("SOURCES.md", "receipts.json")
ROW = re.compile(r"^\| `(?P<path>[^`]+)` \| (?P<url>\S+) \| \[(?P<license>[^\]]+)\]\((?P<link>[^)]+)\)"
                 r" \| `(?P<sha256>[0-9a-f]{64})` \| (?P<size>\d+) \| (?P<shape>.+) \|$")
LICENSES = [
    "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC-BY-4.0", "CC0-1.0", "MIT", "MPL-2.0",
    "US Government work, public domain (17 U.S.C. 105)",
    "US patent text and drawings, not subject to copyright",
]


def fixture_files(suffixes=None) -> list[str]:
    return sorted(path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*")
                  if path.is_file() and path.name not in PROVENANCE
                  and (suffixes is None or path.suffix.lower() in suffixes))


RENDERABLE = [rel for rel in fixture_files(SUPPORTED) if rel != SCAN and Path(rel).suffix.lower() != ".html"]
HTML_FIXTURES = fixture_files((".html",))


def snapshot(rc: int, line: dict) -> dict:
    keys = SNAPSHOT_KEYS[line["file_ok"]]
    return {"rc": rc, "file_ok": line["file_ok"], **{key: line[key] for key in keys}}


def refusal(rel: str) -> tuple[int, str, str, str]:
    """Reduce a refused read to the fields the refusal contract pins."""
    rc, line, markdown = service.run(FIXTURES / rel)
    return rc, line["route"], line["reason"], markdown


@pytest.mark.parametrize("rel", RENDERABLE)
def test_a_supported_fixture_renders_deterministically_to_its_snapshot(rel, tmp_path):
    # GIVEN a real document of a supported format and its recorded receipt
    path = FIXTURES / rel
    out = tmp_path / (path.name + ".md")
    # WHEN it is rendered twice
    rc, line, first = service.run(path, out)
    second = service.run(path)[2]
    # THEN it exits 0 with exactly the recorded receipt, and both renders share one sha256
    assert snapshot(rc, line) == RECEIPTS[rel], "receipt drifted for %s: %s" % (rel, line["reason"])
    assert common.sha256(first) == common.sha256(second), "the render of %s is not deterministic" % rel
    assert out.read_text(encoding="ascii") == first, "--out must hold the same bytes run() returns"


@pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
@pytest.mark.parametrize("rel", HTML_FIXTURES)
def test_html_fixture_renders_deterministically_to_its_utf8_snapshot(rel, tmp_path):
    # GIVEN a licensed HTML source and its successful receipt on a supported runtime
    path = FIXTURES / rel
    out = tmp_path / (path.name + ".md")
    assert out.exists() is False, "the HTML destination must start absent"
    # WHEN the source is rendered twice through the public service
    rc, line, first = service.run(path, out)
    second = service.run(path)[2]
    # THEN the snapshot is exact and API/file output retains identical UTF-8 bytes
    assert snapshot(rc, line) == RECEIPTS[rel], "HTML receipt drifted for %s: %s" % (rel, line["reason"])
    assert (second.encode("utf-8"), out.read_bytes()) == (first.encode("utf-8"), first.encode("utf-8")), (
        "HTML must render deterministic UTF-8 bytes without a BOM")


@pytest.mark.skipif(HTML_SUPPORTED, reason="this expectation covers declined HTML runtimes")
@pytest.mark.parametrize("rel", HTML_FIXTURES)
def test_html_fixture_is_explicitly_refused_on_an_unsupported_runtime(rel, tmp_path):
    # GIVEN a licensed HTML source and a preexisting destination on an unsupported runtime
    out = tmp_path / "preserved.md"
    out.write_bytes(b"existing output\n")
    assert out.read_bytes() == b"existing output\n", "the refusal sentinel must start intact"
    # WHEN the static HTML route is called despite its declared runtime prerequisite
    rc, line, markdown = service.run(FIXTURES / rel, out)
    # THEN the named zero-unit refusal preserves the destination instead of skipping the source
    assert (snapshot(rc, line), line["reason"], markdown, out.read_bytes()) == (
        {"rc": 1, "file_ok": False, "receipt_schema": "brewdoc.receipt/1", "route": "html",
         "unit_kind": "chapter", "units": 0, "tables": 0, "text_regions": 0},
        "HTML requires CPython 3.12-3.14", "", b"existing output\n",
    ), "unsupported runtimes must explicitly refuse every HTML fixture with an unchanged output"


def test_the_scanned_patent_is_refused_by_name():
    # GIVEN a real five-page scan with no text layer
    path = FIXTURES / SCAN
    # WHEN it is read
    rc, line, markdown = service.run(path)
    # THEN the refusal names the page count, the path and the absence of OCR
    assert (rc, line["file_ok"], line["route"], line["reason"], markdown) == (
        service.EXIT_FAIL, False, "pdf",
        "no text layer: 5 of 5 pages carry zero characters in %s - this reader does no OCR" % path,
        "",
    ), "a scan must be refused, never rendered empty"


def test_every_planned_format_fixture_is_refused_by_suffix_today():
    # GIVEN every real document of a format the reader plans but does not read yet
    planned = fixture_files(PLANNED)
    # WHEN each one is read
    refused = {rel: refusal(rel) for rel in planned}
    # THEN one table holds every refusal, so a new supported suffix cannot churn per-file cases
    assert refused == {
        rel: (service.EXIT_FAIL, "none",
              "unsupported suffix '%s' in %s: brewdoc reads %s"
              % (Path(rel).suffix, FIXTURES / rel, service.SUFFIXES), "")
        for rel in planned
    }, "each planned format stays refused until its reader exists; SUFFIXES names the live routes"


def sources_rows() -> list[dict]:
    lines = (FIXTURES / "SOURCES.md").read_text(encoding="ascii").splitlines()
    return [match.groupdict() for match in map(ROW.match, lines) if match]


def test_every_fixture_has_a_sources_row_with_its_sha256_and_size():
    # GIVEN the files on disk and the provenance table
    on_disk = {rel: (hashlib.sha256((FIXTURES / rel).read_bytes()).hexdigest(),
                     (FIXTURES / rel).stat().st_size) for rel in fixture_files()}
    # WHEN the table is read back
    recorded = {row["path"]: (row["sha256"], int(row["size"])) for row in sources_rows()}
    # THEN both sides hold the same paths, hashes and sizes
    assert recorded == on_disk, "SOURCES.md must name every fixture with its current sha256 and size"


def test_every_sources_row_carries_an_allowed_license_and_a_source_url():
    # GIVEN the provenance table
    rows = sources_rows()
    # WHEN its license names and source URLs are collected
    licenses = sorted({row["license"] for row in rows})
    unlinked = [row["path"] for row in rows
                if not (row["url"].startswith("https://") and row["link"].startswith("https://"))]
    # THEN only redistributable licenses appear and every row links its source and its license
    assert licenses == LICENSES, "a license outside the allow-list entered SOURCES.md"
    assert unlinked == [], "every row needs an https source URL and an https license link"


def test_the_corpus_stays_small():
    # GIVEN every fixture's size
    sizes = {rel: (FIXTURES / rel).stat().st_size for rel in fixture_files()}
    # WHEN each file is measured against the 3 MB cap
    oversized = sorted(rel for rel, size in sizes.items() if size > 3_000_000)
    # THEN no file crosses it and the corpus keeps its recorded total, far below the 20 MB cap
    assert (oversized, len(sizes), sum(sizes.values())) == ([], CORPUS_FILES, CORPUS_BYTES), (
        "fixtures must stay small enough for a public CI checkout: 3 MB per file, 20 MB in total;"
        " change CORPUS_FILES and CORPUS_BYTES with SOURCES.md when the corpus changes"
    )
