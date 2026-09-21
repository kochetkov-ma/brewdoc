import hashlib
import json
import re
from pathlib import Path

import pytest

from brewdoc import common, service

pytestmark = pytest.mark.corpus

FIXTURES = Path(__file__).parent / "fixtures"
RECEIPTS = json.loads((FIXTURES / "receipts.json").read_text(encoding="ascii"))
SUPPORTED = (".docx", ".ods", ".pdf", ".xls", ".xlsb", ".xlsm", ".xlsx")
PLANNED = (".csv", ".eml", ".epub", ".html", ".md", ".odp", ".odt", ".pptx", ".tsv", ".txt")
RECEIPT_KEYS = ("route", "pages", "tables", "text_regions", "sheets")
SNAPSHOT_KEYS = {
    False: RECEIPT_KEYS,
    True: RECEIPT_KEYS + ("markdown_schema", "unit_keys", "artifacts"),
}
SCAN = "pdf/us-patent-223898-scan.pdf"
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


RENDERABLE = [rel for rel in fixture_files(SUPPORTED) if rel != SCAN]


def snapshot(rc: int, line: dict) -> dict:
    keys = SNAPSHOT_KEYS[line["file_ok"]]
    return {"rc": rc, "file_ok": line["file_ok"], **{key: line[key] for key in keys}}


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


def test_the_receipt_snapshot_covers_every_supported_fixture_and_nothing_else():
    # GIVEN supported files, WHEN compared with snapshot keys, THEN both sets match.
    assert sorted(RECEIPTS) == fixture_files(SUPPORTED), (
        "receipts.json must hold exactly one row per supported-format fixture"
    )


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


@pytest.mark.parametrize("rel", fixture_files(PLANNED))
def test_a_planned_format_fixture_is_refused_today(rel):
    # GIVEN a real document of a format the reader plans but does not read yet
    path = FIXTURES / rel
    # WHEN it is read
    rc, line, markdown = service.run(path)
    # THEN it is refused with the exact suffix message - this assertion flips when support lands
    assert (rc, line["route"], line["reason"], markdown) == (
        service.EXIT_FAIL, "none",
        "unsupported suffix '%s' in %s: brewdoc reads .docx .ods .pdf .xls .xlsb .xlsm .xlsx"
        % (path.suffix, path),
        "",
    ), "%s is refused until its reader exists; update SUFFIXES and this list together" % rel


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
    # WHEN measured against the caps (3 MB per file, 20 MB in total)
    oversized = sorted(rel for rel, size in sizes.items() if size > 3_000_000)
    # THEN no file and no total crosses them
    assert (oversized, sum(sizes.values()) <= 20_000_000) == ([], True), (
        "fixtures must stay small enough for a public CI checkout"
    )
