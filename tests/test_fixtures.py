import hashlib
import json
import platform
import re
import sys
from pathlib import Path

import pytest

from brewdoc import service

# Every test here opens a real document, so the whole module stays out of the fast gate.
pytestmark = pytest.mark.corpus

FIXTURES = Path(__file__).parent / "fixtures"
RECEIPT_RECORDS = json.loads((FIXTURES / "receipts.json").read_text(encoding="ascii"))
RECEIPTS = {
    rel: {key: value for key, value in row.items() if key != "accepted_output"}
    for rel, row in RECEIPT_RECORDS.items()
}
ACCEPTED_OUTPUTS = {
    rel: row["accepted_output"] for rel, row in RECEIPT_RECORDS.items()
    if "accepted_output" in row
}
# Derived from the live route table, never a literal: a new route must bring its corpus with it.
SUPPORTED = tuple(sorted(service.ROUTES))
UNSUPPORTED = (".csv", ".eml", ".md", ".odp", ".odt", ".tsv", ".txt")
HTML_SUPPORTED = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
RECEIPT_KEYS = ("route", "receipt_schema", "unit_kind", "units", "tables", "text_regions")
SNAPSHOT_KEYS = {
    False: RECEIPT_KEYS,
    True: RECEIPT_KEYS + ("markdown_schema", "unit_keys", "artifacts"),
}
SCAN = "pdf/us-patent-223898-scan.pdf"
CORPUS_FILES = 68
CORPUS_BYTES = 5_978_461
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


RENDERABLE = [rel for rel in fixture_files(SUPPORTED)
              if rel != SCAN and Path(rel).suffix.lower() not in {".html", ".epub"}]
HTML_FIXTURES = fixture_files((".html",))
EPUB_FIXTURES = fixture_files((".epub",))


def snapshot(rc: int, line: dict) -> dict:
    """Keep selected receipt fields; successful snapshots add navigation and artifacts."""
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
    assert out.exists() is False, "the legacy destination must start absent"
    # WHEN it is rendered twice
    rc, line, first = service.run(path, out)
    second = service.run(path)[2]
    # THEN the selected receipt fields match and both renders preserve exact ASCII bytes
    assert snapshot(rc, line) == RECEIPTS[rel], "receipt drifted for %s: %s" % (rel, line["reason"])
    assert (second.encode("ascii"), out.read_bytes()) == (first.encode("ascii"), first.encode("ascii")), (
        "both renders and --out must preserve identical ASCII bytes for %s" % rel)


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


@pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
@pytest.mark.parametrize("rel,sha256,reason", [
    pytest.param("html/mdn-first-form-styled.html",
                 "cd7c192dc6e230737064e053f92947a5159e82e72cd8740d2529c4f1a8abad06",
                 "HTML article extraction refused: no eligible article or suitable main", id="form"),
    pytest.param("html/apache-httpd-allowmethods.html",
                 "3f12805108439e0008b6e1918416da2da7381ae04ec9c68f19a56e4b55ebcb56",
                 "HTML article extraction refused: no eligible article or suitable main", id="unmarked-reference"),
    pytest.param("html/mdn-flexbox0.html",
                 "ad4e1248e97f6c51431ce2c9e9d93b708aee91887eda4c26965f5ceeed3bdfd5",
                 "HTML article extraction refused: ambiguous articles", id="three-peer-articles"),
])
def test_original_html_article_refusals_preserve_the_complete_receipt_and_destination(
        tmp_path, rel, sha256, reason):
    from test_html_article import refused_receipt

    # GIVEN unchanged licensed original HTML and a preserved output destination
    path, out = FIXTURES / rel, tmp_path / "preserved.md"
    out.write_bytes(b"preserved")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256, "article corpus input must match provenance"
    # WHEN article mode encounters an unsuitable original document
    result = service.run(path, out, article=True)
    # THEN fixed refusal semantics and transaction safety match the complete expected object
    assert (result, out.read_bytes()) == ((1, refused_receipt(path, reason), ""), b"preserved"), (
        "original forms, unmarked references and peer articles must refuse without fallback")


@pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
def test_original_mdn_structure_article_keeps_sections_and_excludes_page_chrome(tmp_path):
    from test_html_article import article_document, article_receipt

    # GIVEN the original licensed structure example with article, navigation, aside and footer
    path, out = FIXTURES / "html/mdn-document-structure.html", tmp_path / "article.md"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "8623d0ff8d0696128844a2315d8c23840cf839d52fab09fcd06eabb3e5e966ec"
    ), "positive article corpus input must remain byte-identical to the licensed source"
    content = (
        "#### Article heading\n\nLorem ipsum dolor sit amet, consectetur adipisicing elit\\. "
        "Donec a diam lectus\\. Set sit amet ipsum mauris\\. Maecenas congue ligula as quam "
        "viverra nec consectetur ant hendrerit\\. Donec et mollis dolor\\. Praesent et diam eget "
        "libero egestas mattis sit amet vitae augue\\. Nam tincidunt congue enim, ut porta "
        "lorem lacinia consectetur\\.\n\n##### Subsection\n\nDonec ut librero sed accu vehicula "
        "ultricies a non tortor\\. Lorem ipsum dolor sit amet, consectetur adipisicing elit\\. "
        "Aenean ut gravida lorem\\. Ut turpis felis, pulvinar a semper sed, adipiscing id dolor\\.\n\n"
        "Pelientesque auctor nisi id magna consequat sagittis\\. Curabitur dapibus, enim sit amet "
        "elit pharetra tincidunt feugiat nist imperdiet\\. Ut convallis libero in urna ultrices "
        "accumsan\\. Donec sed odio eros\\.\n\n##### Another subsection\n\nDonec viverra mi quis "
        "quam pulvinar at malesuada arcu rhoncus\\. Cum soclis natoque penatibus et manis dis "
        "parturient montes, nascetur ridiculus mus\\. In rutrum accumsan ultricies\\. Mauris vitae "
        "nisi at sem facilisis semper ac in est\\.\n\nVivamus fermentum semper porta\\. Nunc diam "
        "velit, adipscing ut tristique vitae sagittis vel odio\\. Maecenas convallis ullamcorper "
        "ultricied\\. Curabitur ornare, ligula semper consectetur sagittis, nisi diam iaculis "
        "velit, is fringille sem nunc vet mi\\.\n"
    )
    expected = article_document(path, content, 8, title="My page title").replace(
        "sample\\.html", "mdn\\-document\\-structure\\.html")
    # WHEN article mode selects the original article subtree
    result = service.run(path, out, article=True)
    # THEN exact source prose and headings survive with no navigation, search, aside or footer
    assert (result, out.read_bytes()) == (
        (0, article_receipt(path, out, 8), expected), expected.encode("utf-8")
    ), "licensed positive article evidence must preserve every selected paragraph in source order"


@pytest.mark.parametrize("rel", EPUB_FIXTURES)
def test_epub_fixture_preserves_reviewed_utf8_bytes_and_full_receipt(rel, tmp_path):
    # GIVEN a licensed EPUB source with independently reviewed capture evidence
    path = FIXTURES / rel
    assert tuple(sorted(ACCEPTED_OUTPUTS)) == tuple(EPUB_FIXTURES), (
        "exactly both licensed EPUB fixtures must have independent accepted-output evidence")
    accepted = ACCEPTED_OUTPUTS[rel]
    out = tmp_path / (path.name + ".md")
    assert (out.exists(), hashlib.sha256(path.read_bytes()).hexdigest()) == (
        False, accepted["source_sha256"],
    ), "the output must start absent and the source must match its reviewed identity"
    # WHEN the offline service renders the same source twice, including --out
    rc, first_receipt, first = service.run(path, out)
    second_rc, second_receipt, second = service.run(path)
    # THEN complete receipts and Markdown match accepted evidence with only out normalized
    assert (rc, second_rc, snapshot(rc, first_receipt)) == (0, 0, RECEIPTS[rel]), (
        "both EPUB conversions must succeed with the reviewed counters and ordered unit keys")
    assert ({**first_receipt, "out": None}, second_receipt) == (
        accepted["receipt"], accepted["receipt"],
    ), "both complete public receipts must match after only out normalization"
    first_bytes = first.encode("utf-8")
    assert (hashlib.sha256(first_bytes).hexdigest(), len(first_bytes)) == (
        accepted["markdown_sha256"], accepted["markdown_utf8_bytes"],
    ), "complete EPUB Markdown must retain its independently accepted UTF-8 hash and byte length"
    assert (second.encode("utf-8"), out.read_bytes()) == (first_bytes, first_bytes), (
        "repeated API conversion and --out must preserve exact reviewed UTF-8 bytes")


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


def test_every_unsupported_format_fixture_is_refused_by_suffix():
    # GIVEN every real document of a format the reader does not support
    unsupported = fixture_files(UNSUPPORTED)
    assert {Path(rel).suffix.lower() for rel in unsupported} == set(UNSUPPORTED), (
        "the unsupported corpus must cover all seven refused suffixes")
    # WHEN each one is read
    refused = {rel: refusal(rel) for rel in unsupported}
    # THEN one table holds every refusal, so a new supported suffix cannot churn per-file cases
    assert refused == {
        rel: (service.EXIT_FAIL, "none",
              "unsupported suffix '%s' in %s: brewdoc reads %s"
              % (Path(rel).suffix, FIXTURES / rel, service.SUFFIXES), "")
        for rel in unsupported
    }, "each unsupported format must be refused; SUFFIXES names the live routes"


def sources_rows() -> list[dict]:
    lines = (FIXTURES / "SOURCES.md").read_text(encoding="ascii").splitlines()
    return [match.groupdict() for match in map(ROW.match, lines) if match]


def test_every_fixture_has_a_sources_row_with_its_sha256_and_size():
    # GIVEN the files on disk and the provenance table
    on_disk = {rel: (hashlib.sha256((FIXTURES / rel).read_bytes()).hexdigest(),
                     (FIXTURES / rel).stat().st_size) for rel in fixture_files()}
    rows = sources_rows()
    paths = [row["path"] for row in rows]
    assert len(paths) == len(set(paths)), "each fixture path must have exactly one provenance row"
    # WHEN the table is read back
    recorded = {row["path"]: (row["sha256"], int(row["size"])) for row in rows}
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
