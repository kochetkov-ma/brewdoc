"""Source-derived ODT/ODP behavior through the existing public service."""

import hashlib
import re
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from brewdoc import run
from inspection_contract import rendering_bytes, rendering_result
from brewdoc import odf
from test_contract import NOT_APPLICABLE, SCHEMA_TWO, zero_tally


OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MIMES = {"odt": "application/vnd.oasis.opendocument.text",
         "odp": "application/vnd.oasis.opendocument.presentation"}
KINDS = {"odt": "chapter", "odp": "slide"}
BODY_TAGS = {"odt": "text", "odp": "presentation"}
ODF_OMISSIONS = ["images, charts, equations, embedded objects and macros",
                 "notes, comments and tracked-change history",
                 "page layout, styles, list numbering and formula evaluation"]
OMISSIONS = {"odt": ODF_OMISSIONS, "odp": ODF_OMISSIONS + ["masters and automatic headers and footers"]}
FIXTURES = Path(__file__).parent / "fixtures"


def content(kind, blocks):
    """Wrap synthetic body blocks in exact ODF namespace boundaries."""
    return (f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
            f'xmlns:table="{TABLE}" xmlns:draw="{DRAW}" xmlns:presentation="{PRESENTATION}" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">'
            f'<office:body><office:{BODY_TAGS[kind]}>{blocks}'
            f'</office:{BODY_TAGS[kind]}></office:body></office:document-content>')


def manifest(kind, encryption=""):
    """Declare the package root and the consumed content member."""
    return (f'<manifest:manifest xmlns:manifest="{MANIFEST}" manifest:version="1.2">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{MIMES[kind]}"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml">'
            f'{encryption}</manifest:file-entry></manifest:manifest>')


def members(kind, blocks):
    """Return the three required parts without optional package metadata."""
    return [("mimetype", MIMES[kind]), ("META-INF/manifest.xml", manifest(kind)),
            ("content.xml", content(kind, blocks))]


def package(path, parts):
    """Write literal parts, including deliberately duplicated required names."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in parts:
                archive.writestr(name, payload)
    return path


def receipt(path, kind, reason, *, units=0, tables=0, text_regions=0, out=None):
    """Pin the receipt envelope independently of the route implementation."""
    return {"file_ok": False, "route": kind, "reason": reason, "source": path.name,
            "out": out, "receipt_schema": "brewdoc.receipt/1", "unit_kind": KINDS[kind],
            "units": units, "tables": tables, "text_regions": text_regions,
            "columns_split": 0, "broken_ligature_words": 0,
            "dropped": zero_tally()["dropped"]}


def expected_document(path, kind, body, labels, omissions, tables, text_regions):
    """Build exact schema 2 bytes from explicitly expected units and counters."""
    unit = KINDS[kind]
    keys = [f"{unit}/{ordinal:06d}" for ordinal in range(1, len(labels) + 1)]
    titles = [f'{unit.title()} {ordinal}: "{label}"' for ordinal, label in enumerate(labels, 1)]
    contents = "\n".join(f'- [{title}](#brewdoc-{key.replace("/", "-")})'
                         for title, key in zip(titles, keys))
    source = path.read_bytes()
    rendered = body.encode("utf-8")
    return SCHEMA_TWO.format(
        **NOT_APPLICABLE, name=path.name, suffix=path.suffix,
        route=kind, unit=unit, count=len(labels), keys=", ".join(keys) or "none",
        source_bytes=len(source), source_sha256=hashlib.sha256(source).hexdigest(),
        body_bytes=len(rendered), body_sha256=hashlib.sha256(rendered).hexdigest(),
        pages=0, sheets=0, chapters={"odt": len(labels), "odp": 0}[kind],
        tables=tables, text_regions=text_regions,
        omissions="\n".join("- " + line for line in omissions),
        contents=contents, body=body,
    )


def success(path, kind, labels, omissions, tables, text_regions, out):
    """Pin every successful receipt field, including UTF-8 file output identity."""
    unit = KINDS[kind]
    reason = (f"{kind} rendered: {len(labels)} {unit}s, {tables} tables, "
              f"{text_regions} text regions, 0 column splits")
    return receipt(path, kind, reason, units=len(labels), tables=tables,
                   text_regions=text_regions, out=str(out)) | {
        "file_ok": True, "not_carried": omissions, "artifacts": [],
        "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": [f"{unit}/{ordinal:06d}" for ordinal in range(1, len(labels) + 1)],
    }


ODT_CASES = [
    pytest.param(
        '<text:p>before</text:p><table:table><table:table-header-rows><table:table-row>'
        '<table:table-cell table:number-columns-spanned="2"><text:p>Joined</text:p>'
        '</table:table-cell><table:covered-table-cell/><table:table-cell><text:p>Right</text:p>'
        '</table:table-cell></table:table-row></table:table-header-rows><table:table-row-group>'
        '<table:table-row table:number-rows-repeated="2"><table:table-cell '
        'table:number-columns-repeated="2"><text:p>é</text:p></table:table-cell>'
        '<table:table-cell><text:p>Z</text:p></table:table-cell></table:table-row>'
        '</table:table-row-group></table:table><text:p>after</text:p>',
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nbefore\n\n'
        '| Joined |  | Right |\n| --- | --- | --- |\n| é | é | Z |\n| é | é | Z |\n\nafter\n',
        ["Body"], 1, 2, id="merged-covered-repeated-table-order"),
    pytest.param(
        '<text:p>preface</text:p><text:section><text:h text:outline-level="1">Café 東京</text:h>'
        '<text:list><text:list-item><text:p>First</text:p><text:list><text:list-item>'
        '<text:p>Nested</text:p></text:list-item></text:list></text:list-item><text:list-item>'
        '<text:p>Second</text:p></text:list-item></text:list><text:section><text:h '
        'text:outline-level="2">Next</text:h><text:p>last</text:p></text:section></text:section>',
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\npreface\n\n'
        '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Café 東京"\n\nFirst\n\nNested\n\nSecond\n\n'
        '<a id="brewdoc-chapter-000003"></a>\n## Chapter 3: "Next"\n\nlast\n',
        ["Body", "Café 東京", "Next"], 0, 5, id="headings-lists-sections-once"),
    pytest.param(
        '<text:tracked-changes><text:changed-region><text:deletion><text:p>history</text:p>'
        '</text:deletion></text:changed-region></text:tracked-changes>'
        '<text:p>one<text:s text:c="3"/>two<text:tab/>three<text:line-break/>'
        '<text:span>café</text:span><text:s/>東京<text:note><text:note-citation>1</text:note-citation>'
        '<text:note-body><text:p>note</text:p></text:note-body></text:note> tail'
        '<office:annotation><text:p>comment</text:p></office:annotation> end '
        '<text:a xlink:href="https://invalid.example/">visible</text:a></text:p>',
        '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'
        'one   two three\ncafé 東京 tail end visible\n',
        ["Body"], 0, 1, id="controls-unicode-and-skipped-subtree-tails"),
    pytest.param("", '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n',
                 ["Body"], 0, 0, id="empty-body"),
]


@pytest.mark.parametrize("blocks,body,labels,tables,text_regions", ODT_CASES)
def test_odt_public_run_preserves_exact_blocks_receipt_and_utf8_output(
        tmp_path, blocks, body, labels, tables, text_regions):
    # GIVEN qualified synthetic ODT content and an existing destination
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    out.write_bytes(b"old output")
    assert (zipfile.is_zipfile(path), out.read_bytes()) == (True, b"old output"), (
        "the regression must start with a readable package and a protected old output")

    # WHEN the existing public API renders the document
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN whole Markdown, counters, receipt and UTF-8 bytes match the source-derived contract
    assert code == 0, "ODT must render rather than return an unsupported-suffix receipt"
    omissions = OMISSIONS["odt"]
    expected = expected_document(path, "odt", body, labels, omissions, tables, text_regions)
    assert (line, markdown, rendering_bytes(out.read_bytes()), sorted(item.name for item in tmp_path.iterdir())) == (
        success(path, "odt", labels, omissions, tables, text_regions, out), expected,
        expected.encode("utf-8"), ["output.md", "source.odt"],
    ), "paragraphs, headings, table slots and omission tails must survive exactly once"


def test_odp_public_run_keeps_hidden_empty_and_grouped_pages_in_source_order(tmp_path):
    # GIVEN a hidden titled page, an unnamed empty page and named grouped text around a table
    blocks = (
        '<draw:page draw:name="third" presentation:visibility="hidden"><draw:frame '
        'presentation:class="title"><draw:text-box><text:p/></draw:text-box>'
        '</draw:frame><draw:frame '
        'presentation:class="title"><draw:text-box><text:p>Étage</text:p></draw:text-box>'
        '</draw:frame><draw:frame><draw:text-box><text:p>hidden body</text:p></draw:text-box>'
        '</draw:frame><draw:frame presentation:class="footer"><draw:text-box><text:p>footer</text:p>'
        '</draw:text-box></draw:frame><presentation:notes><text:p>private note</text:p></presentation:notes>'
        '</draw:page><draw:page/><draw:page draw:name="first"><draw:g><draw:frame><draw:text-box>'
        '<text:p>before</text:p></draw:text-box></draw:frame><draw:frame><table:table>'
        '<table:table-row><table:table-cell><text:p>Left</text:p></table:table-cell>'
        '<table:table-cell><text:p>Right</text:p></table:table-cell></table:table-row>'
        '</table:table></draw:frame><draw:g><draw:frame><draw:text-box><text:p>after</text:p>'
        '</draw:text-box></draw:frame></draw:g></draw:g></draw:page>')
    path = package(tmp_path / "source.odp", members("odp", blocks))
    out = tmp_path / "output.md"
    assert out.exists() is False, "presentation output must start absent"

    # WHEN public run renders direct pages in XML order
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN hidden and empty slides retain keys and grouped blocks retain table order
    assert code == 0, "ODP must render rather than return an unsupported-suffix receipt"
    body = ('<a id="brewdoc-slide-000001"></a>\n## Slide 1: "Étage"\n\nhidden body\n\n'
            '<a id="brewdoc-slide-000002"></a>\n## Slide 2: "Untitled"\n\n'
            '<a id="brewdoc-slide-000003"></a>\n## Slide 3: "first"\n\nbefore\n\n'
            '| Left | Right |\n| --- | --- |\n\nafter\n')
    labels = ["Étage", "Untitled", "first"]
    omissions = OMISSIONS["odp"]
    expected = expected_document(path, "odp", body, labels, omissions, 1, 3)
    assert (line, markdown, rendering_bytes(out.read_bytes())) == (
        success(path, "odp", labels, omissions, 1, 3, out), expected, expected.encode("utf-8"),
    ), "slide title must label once, notes stay omitted and draw:name must not reorder pages"


@pytest.mark.parametrize("kind", ["odt", "odp"])
@pytest.mark.parametrize("part_index", [0, 1, 2], ids=["mimetype", "manifest", "content"])
def test_odf_missing_required_member_refuses_without_replacing_output(tmp_path, kind, part_index):
    # GIVEN an ODF package missing exactly one consumed member
    parts = members(kind, "")
    missing, _payload = parts.pop(part_index)
    path = package(tmp_path / ("source." + kind), parts)
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert names.count(missing) == 0, "the required member must actually be absent"

    # WHEN the public service attempts conversion
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN a named ODF missing-part refusal preserves all old destination bytes
    assert re.search(r"(?i)ODF.*missing.*" + re.escape(missing), line["reason"]) is not None, (
        "the refusal must identify the missing ODF member rather than unsupported suffix")
    expected = receipt(path, kind, line["reason"]) | {"not_carried": OMISSIONS[kind]}
    assert (code, line, markdown, out.read_bytes(), sorted(item.name for item in tmp_path.iterdir())) == (
        1, expected, "", b"protected destination", ["output.md", path.name],
    ), "missing-part failure must retain zero counters and leave no output staging files"


@pytest.mark.parametrize("kind", ["odt", "odp"])
@pytest.mark.parametrize("part_index", [0, 1, 2], ids=["mimetype", "manifest", "content"])
def test_odf_duplicate_required_member_refuses_without_replacing_output(tmp_path, kind, part_index):
    # GIVEN a duplicate consumed member with otherwise valid package bytes
    parts = members(kind, "")
    duplicate, _payload = parts[part_index]
    parts.append(parts[part_index])
    path = package(tmp_path / ("source." + kind), parts)
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert names.count(duplicate) == 2, "the fixture must contain two identical required names"

    # WHEN the public service encounters the ambiguous member
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN ambiguity is refused and the existing output survives
    assert re.search(r"(?i)ODF.*(duplicate|multiple|unique|more than once).*"
                     + re.escape(duplicate), line["reason"]) is not None, (
        "the refusal must identify the duplicate required member")
    expected = receipt(path, kind, line["reason"]) | {"not_carried": OMISSIONS[kind]}
    assert (code, line, markdown, out.read_bytes()) == (
        1, expected, "", b"protected destination",
    ), "duplicate consumed parts must not produce a successful receipt or replace output"


INVALID_PARTS = [
    pytest.param(0, "application/vnd.oasis.opendocument.spreadsheet", r"(?i)ODF.*(MIME|mimetype)",
                 id="wrong-mimetype"),
    pytest.param(1, '<manifest:manifest xmlns:manifest="%s"><manifest:file-entry '
                 'manifest:full-path="/" manifest:media-type="wrong"/></manifest:manifest>'
                 % MANIFEST, r"(?i)ODF.*(MIME|media.type)", id="wrong-manifest-root-mime"),
    pytest.param(1, "<manifest><file-entry full-path=\"/\"/></manifest>",
                 r"(?i)ODF.*manifest", id="unqualified-manifest-root"),
    pytest.param(1, "<", r"cannot parse ODF part META-INF/manifest\.xml:", id="malformed-manifest"),
    pytest.param(2, "<", r"cannot parse ODF part content\.xml:", id="malformed-content"),
    pytest.param(2, "<document-content><body><text/></body></document-content>",
                 r"(?i)ODF.*(root|document.content)", id="unqualified-content-root"),
    pytest.param(2, '<office:document-content xmlns:office="%s"/>' % OFFICE,
                 r"(?i)ODF.*body", id="missing-body"),
    pytest.param(2, '<office:document-content xmlns:office="%s"><body><text/></body>'
                 '</office:document-content>' % OFFICE, r"(?i)ODF.*body", id="unqualified-body"),
    pytest.param(2, '<office:document-content xmlns:office="%s"><office:body>'
                 '<office:spreadsheet/></office:body></office:document-content>' % OFFICE,
                 r"(?i)ODF.*(body|text|presentation)", id="wrong-body-kind"),
]


@pytest.mark.parametrize("kind", ["odt", "odp"])
@pytest.mark.parametrize("part_index,payload,reason_pattern", INVALID_PARTS)
def test_odf_invalid_required_boundaries_preserve_existing_destination(
        tmp_path, kind, part_index, payload, reason_pattern):
    # GIVEN one malformed or semantically wrong required part
    parts = members(kind, "")
    part_name, _original = parts[part_index]
    parts[part_index] = (part_name, payload)
    path = package(tmp_path / ("source." + kind), parts)
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    with zipfile.ZipFile(path) as archive:
        actual = archive.read(part_name)
    assert actual == payload.encode("utf-8"), "the invalid part must retain its exact test payload"

    # WHEN conversion validates the package before output staging
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN the boundary failure is named and has the exact zero-unit refusal envelope
    assert re.search(reason_pattern, line["reason"]) is not None, (
        "the reason must report the selected package/XML boundary rather than unsupported suffix")
    expected = receipt(path, kind, line["reason"]) | {"not_carried": OMISSIONS[kind]}
    assert (code, line, markdown, out.read_bytes(), sorted(item.name for item in tmp_path.iterdir())) == (
        1, expected, "", b"protected destination", ["output.md", path.name],
    ), "invalid required content must leave the complete previous destination and no staging files"


@pytest.mark.parametrize("kind", ["odt", "odp"])
def test_odf_manifest_encryption_is_refused_before_content_is_opened(tmp_path, monkeypatch, kind):
    # GIVEN manifest-declared encrypted content and a spy on the consumed ZIP member
    parts = members(kind, "")
    parts[1] = ("META-INF/manifest.xml", manifest(kind, "<manifest:encryption-data/>"))
    path = package(tmp_path / ("source." + kind), parts)
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    opened = []
    original_open = zipfile.ZipFile.open

    def tracked_open(archive, name, *args, **kwargs):
        """Observe selected reads without changing ordinary ZIP behavior."""
        opened.append((archive.filename, getattr(name, "filename", name)))
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", tracked_open)
    assert opened == [], "the read spy must begin without consumed-part observations"

    # WHEN public conversion checks manifest encryption
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN only inspection retains raw ciphertext; readable conversion never opens its content
    assert re.search(r"(?i)ODF.*encrypt.*content\.xml", line["reason"]) is not None, (
        "the refusal must identify encryption on the consumed member")
    expected = receipt(path, kind, line["reason"]) | {"not_carried": OMISSIONS[kind]}
    assert (code, line, markdown, opened.count((str(path), "content.xml")),
            opened.count((None, "content.xml")), out.read_bytes()) == (
        1, expected, "", 0, 1, b"protected destination",
    ), "encryption must precede readable content reads while inspection preserves the opaque payload"


@pytest.mark.parametrize("optional_name", ["styles.xml", "meta.xml", "settings.xml", "../ignored.xml"])
def test_odt_ignores_malformed_unconsumed_optional_members(tmp_path, optional_name):
    # GIVEN valid ODT body text and an irrelevant malformed optional ZIP member
    parts = members("odt", "<text:p>kept</text:p>") + [(optional_name, "<")]
    path = package(tmp_path / "source.odt", parts)
    out = tmp_path / "output.md"
    assert out.exists() is False, "optional-part coverage must begin without an output file"

    # WHEN only fixed consumed members feed public conversion
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN optional malformed bytes and ignored member names create no admission requirement
    assert code == 0, "unconsumed optional members must not prevent valid body rendering"
    omissions = OMISSIONS["odt"]
    body = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nkept\n'
    expected = expected_document(path, "odt", body, ["Body"], omissions, 0, 1)
    assert (line, markdown, rendering_bytes(out.read_bytes())) == (
        success(path, "odt", ["Body"], omissions, 0, 1, out), expected, expected.encode("utf-8"),
    ), "only consumed package boundaries should govern body extraction"


@pytest.mark.parametrize("attribute,value", [
    ("number-columns-repeated", "0"), ("number-columns-repeated", "-1"),
    ("number-columns-repeated", "bad"), ("number-columns-spanned", "0"),
    ("number-columns-spanned", "-1"), ("number-columns-spanned", "bad"),
])
def test_odt_invalid_table_counts_refuse_without_fabricating_cells(tmp_path, attribute, value):
    # GIVEN a cell whose explicit repeat or span is not a positive integer
    blocks = ('<table:table><table:table-row><table:table-cell table:%s="%s">'
              '<text:p>owner</text:p></table:table-cell></table:table-row></table:table>'
              % (attribute, value))
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    assert f'table:{attribute}="{value}"' in blocks, "the invalid explicit count must be in the source"

    # WHEN conversion attempts to expand the table grid
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN invalid counts are refused instead of silently becoming one fabricated cell
    assert re.search(r"(?i)ODF.*(repeat|span|integer|positive|count|number)", line["reason"]) is not None, (
        "invalid table counts must produce a named ODF expansion refusal")
    expected = receipt(path, "odt", line["reason"]) | {"not_carried": OMISSIONS["odt"]}
    assert (code, line, markdown, out.read_bytes()) == (
        1, expected, "", b"protected destination",
    ), "invalid table counts must not publish fabricated columns"


@pytest.mark.parametrize("value", ["0", "-1", "bad", "9" * 5000],
                         ids=["zero", "negative", "nonnumeric", "decimal-overflow"])
def test_odt_invalid_row_repeat_refuses_before_materializing_rows(tmp_path, value):
    # GIVEN a tiny table declaring an invalid or unbounded decimal row repeat
    blocks = ('<table:table><table:table-row table:number-rows-repeated="%s">'
              '<table:table-cell><text:p>cell</text:p></table:table-cell>'
              '</table:table-row></table:table>' % value)
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    assert path.stat().st_size < 8192, "even decimal-overflow coverage must use a small source"

    # WHEN conversion admits explicit repeat counts
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN invalid counts and decimal overflow produce a bounded refusal without output
    assert re.search(r"(?i)ODF.*(repeat|integer|positive|count|number|limit)", line["reason"]) is not None, (
        "row-repeat admission must identify an ODF integer or expansion boundary")
    expected = receipt(path, "odt", line["reason"]) | {"not_carried": OMISSIONS["odt"]}
    assert (code, line, markdown, out.read_bytes()) == (
        1, expected, "", b"protected destination",
    ), "row-repeat failure must retain output and never fall through to an unreadable traceback"


@pytest.mark.parametrize("cells", [
    pytest.param('<table:covered-table-cell/><table:table-cell><text:p>owner</text:p>'
                 '</table:table-cell>', id="covered-slot-without-owner"),
    pytest.param('<table:table-cell table:number-columns-spanned="2"><text:p>owner</text:p>'
                 '</table:table-cell><table:table-cell><text:p>overlap</text:p></table:table-cell>',
                 id="owner-span-without-covered-slot"),
])
def test_odt_contradictory_merged_grid_refuses_instead_of_shifting_columns(tmp_path, cells):
    # GIVEN a covered slot without an owner or an owner span overlapping a real next cell
    blocks = '<table:table><table:table-row>' + cells + '</table:table-row></table:table>'
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    assert cells in blocks, "the contradictory source grid must be present unchanged"

    # WHEN conversion checks merged-cell occupancy
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN unsupported occupancy is refused rather than rewritten into fabricated columns
    assert re.search(r"(?i)ODF.*(covered|span|grid|merge|overlap)", line["reason"]) is not None, (
        "contradictory merged occupancy must name the ODF grid boundary")
    expected = receipt(path, "odt", line["reason"]) | {"not_carried": OMISSIONS["odt"]}
    assert (code, line, markdown, out.read_bytes()) == (
        1, expected, "", b"protected destination",
    ), "a contradictory source grid must not silently move logical cells"


def test_odt_exact_namespaces_ignore_lookalike_tags_and_accept_alias_prefixes(tmp_path):
    # GIVEN valid aliased namespaces alongside similar URIs and unqualified paragraph tags
    blocks = ('<n:p xmlns:n="%s/"><n:span>wrong paragraph</n:span></n:p><p>unqualified</p>'
              '<text:P>wrong case</text:P><text:p>kept<text:span> span</text:span>'
              '<n:span xmlns:n="%s/">wrong inline</n:span> tail</text:p>' % (TEXT, TEXT))
    xml = (content("odt", blocks).replace("<text:", "<t:").replace("</text:", "</t:")
           .replace('xmlns:text=', 'xmlns:t='))
    parts = members("odt", "")
    parts[2] = ("content.xml", xml)
    path = package(tmp_path / "source.odt", parts)
    out = tmp_path / "output.md"
    root = ET.fromstring(xml)
    assert (root.find("{%s}body" % OFFICE).tag,
            len(list(root.iter("{%s}p" % TEXT)))) == ("{%s}body" % OFFICE, 1), (
        "aliases must preserve both exact office and text namespace URIs")

    # WHEN public conversion follows expanded namespace names
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN only exactly qualified text nodes contribute while skipped tails survive
    assert code == 0, "prefix aliases must be accepted without including lookalike text"
    body = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nkept span tail\n'
    expected = expected_document(path, "odt", body, ["Body"], OMISSIONS["odt"], 0, 1)
    assert (line, markdown, rendering_bytes(out.read_bytes())) == (
        success(path, "odt", ["Body"], OMISSIONS["odt"], 0, 1, out), expected, expected.encode("utf-8"),
    ), "namespace suffixes, unqualified tags and different local-name case must stay excluded"


CORPUS_CASES = [
    pytest.param("odt/tika-testOpenOffice2.odt",
                 "daee7b62602780f2599c19b3593a1e455a21e041959416304c112bdb136640bb",
                 (2, 0, 0, 0, 0), "odt", 1, 0,
                 ["This is a sample Open Office document, written in NeoOffice 2.2.1 for the Mac."],
                 id="odf-1.0-text-space"),
    pytest.param("odt/tika-testODFwithOOo3.odt",
                 "b6cd74b570a98fcb6d838dd85ab869604e0711812d9533d5ecde0000b24ba7ef",
                 (24, 1, 1, 0, 0), "odt", 1, 0,
                 ["This is a rather difficult test document, with interesting features.",
                  "First item", "Second item", "Third item", "Lucene website"],
                 id="odf-1.1-heading-list-no-table"),
    pytest.param("odt/tika-testODTStyles2.odt",
                 "eaa2f7d3fa686751699227a06b24e9e2b587da9db15d838ff93af8c5b14fabb8",
                 (42, 0, 0, 1, 0), "odt", 1, 1,
                 ["| Publicatie Onbekwaamverklaring tov Belgisch Staatsblad |  |",
                  "| --- | --- |", "| Identificatie vredegerecht | Vredegerecht Name |"],
                 id="odf-1.2-two-column-covered-merge"),
    pytest.param("odp/tika-testMasterFooter.odp",
                 "c786aecfc72a2ff55ede5a21c4dfda14af298cebf1e788da685262da7025c833",
                 (0, 0, 0, 0, 1), "odp", 1, 0, [], id="empty-master-footer-slide"),
    pytest.param("odp/tika-testODPMacro.odp",
                 "9132f1a2a5193fe415a9542f096ed705d008d69577f0ba1fab5ba83cd4e3e3f0",
                 (1, 0, 0, 0, 1), "odp", 1, 0, [], id="empty-macro-slide"),
]


@pytest.mark.corpus
@pytest.mark.parametrize("relative,digest,shape,kind,units,tables,fragments", CORPUS_CASES)
def test_odf_licensed_original_bytes_render_only_source_backed_features(
        tmp_path, relative, digest, shape, kind, units, tables, fragments):
    # GIVEN an unchanged Apache-2.0 original with independently inspected source features
    path = FIXTURES / relative
    out = tmp_path / "output.md"
    raw = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
        mime = archive.read("mimetype").decode("ascii")
        counts = tuple(len(list(root.iter("{%s}%s" % pair))) for pair in
                       [(TEXT, "p"), (TEXT, "h"), (TEXT, "list"), (TABLE, "table"), (DRAW, "page")])
    assert (hashlib.sha256(raw).hexdigest(), counts, mime) == (digest, shape, MIMES[kind]), (
        "fixture bytes and source structure must match the licensed provenance and audit correction")

    # WHEN the public service renders the existing corpus document
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN route units and only features present in these original sources are claimed
    assert (code, line["route"], line["unit_kind"], line["units"], line["tables"]) == (
        0, kind, KINDS[kind], units, tables,
    ), "all five licensed ODF originals must render without invented nonempty ODP corpus coverage"
    expression = r"(?!)" + "".join("|" + re.escape(fragment) for fragment in fragments)
    anchor = '<a id="brewdoc-%s-000001"></a>' % KINDS[kind]
    matched = re.findall(expression, markdown[markdown.index(anchor):])
    assert (matched, rendering_bytes(out.read_bytes()), path.read_bytes()) == (
        fragments, markdown.encode("utf-8"), raw,
    ), "source-backed text/table fragments retain order and conversion preserves original bytes"


@pytest.mark.corpus
@pytest.mark.parametrize("relative,digest", [
    ("odp/tika-testMasterFooter.odp", "c786aecfc72a2ff55ede5a21c4dfda14af298cebf1e788da685262da7025c833"),
    ("odp/tika-testODPMacro.odp", "9132f1a2a5193fe415a9542f096ed705d008d69577f0ba1fab5ba83cd4e3e3f0"),
])
def test_odp_licensed_empty_slides_have_exact_body_receipt_and_zero_text_regions(tmp_path, relative, digest):
    # GIVEN an original empty slide whose notes, footer or macro provide no slide text
    path = FIXTURES / relative
    out = tmp_path / "output.md"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, "licensed source bytes must stay original"

    # WHEN public run renders the empty declared slide
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN the named slide remains navigable without fabricated notes or image text
    assert code == 0, "the existing empty ODP fixture must be accepted"
    body = '<a id="brewdoc-slide-000001"></a>\n## Slide 1: "page1"\n'
    omissions = OMISSIONS["odp"]
    expected = expected_document(path, "odp", body, ["page1"], omissions, 0, 0)
    assert (line, markdown, rendering_bytes(out.read_bytes())) == (
        success(path, "odp", ["page1"], omissions, 0, 0, out), expected, expected.encode("utf-8"),
    ), "master/footer and macro corpus fixtures prove one empty slide and no body text regions"


def repeated_table(*, columns=1, rows=1, text=""):
    """Return a tiny source table whose explicit repeat expansion remains observable."""
    return (f'<table:table><table:table-row table:number-rows-repeated="{rows}">'
            f'<table:table-cell table:number-columns-repeated="{columns}"><text:p>{text}</text:p>'
            '</table:table-cell></table:table-row></table:table>')


BODY_START = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'
BOUNDARY_ACCEPTANCE = [
    pytest.param("TABLE_MAX_COLUMNS", 2, repeated_table(columns=2),
                 BODY_START + '|  |  |\n| --- | --- |\n', ["Body"], 1, 0, id="exact-columns"),
    pytest.param("TABLE_MAX_SLOTS", 4, repeated_table(rows=4),
                 BODY_START + '|  |\n| --- |\n|  |\n|  |\n|  |\n', ["Body"], 1, 0, id="exact-table-slots"),
    pytest.param("TOTAL_TABLE_MAX_SLOTS", 4, repeated_table(columns=2) * 2,
                 BODY_START + '|  |  |\n| --- | --- |\n\n|  |  |\n| --- | --- |\n',
                 ["Body"], 2, 0, id="exact-cumulative-table-slots"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 5, '<text:p>ABCDE</text:p>',
                 BODY_START + 'ABCDE\n', ["Body"], 0, 1, id="exact-literal-characters"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 5, '<text:p>a<text:s text:c="3"/>b</text:p>',
                 BODY_START + 'a   b\n', ["Body"], 0, 1, id="exact-expanded-spaces"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, repeated_table(columns=2, text="ab"),
                 BODY_START + '| ab | ab |\n| --- | --- |\n', ["Body"], 1, 0, id="exact-cell-copies"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, repeated_table(rows=4, text="a"),
                 BODY_START + '| a |\n| --- |\n| a |\n| a |\n| a |\n', ["Body"], 1, 0,
                 id="exact-row-copies"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4,
                 '<text:h text:outline-level="1">AB</text:h><text:p>CD</text:p>',
                 '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "AB"\n\nCD\n',
                 ["AB"], 0, 1, id="exact-heading-plus-body"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, '<text:p>AB</text:p><text:p>CD</text:p>',
                 BODY_START + 'AB\n\nCD\n', ["Body"], 0, 2, id="exact-cumulative-paragraphs"),
]


@pytest.mark.parametrize("constant,limit,blocks,body,labels,tables,text_regions", BOUNDARY_ACCEPTANCE)
def test_odf_exact_injected_expansion_boundaries_accept_complete_output(
        tmp_path, monkeypatch, constant, limit, blocks, body, labels, tables, text_regions):
    # GIVEN a small source whose expansion reaches an injected limit exactly
    monkeypatch.setattr(odf, constant, limit)
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    assert getattr(odf, constant) == limit, "the test must use the assigned injected expansion limit"

    # WHEN public conversion admits the exact boundary
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN equality is accepted and generated framing is not charged as extracted source text
    assert code == 0, "the exact admitted boundary must remain readable"
    expected = expected_document(path, "odt", body, labels, OMISSIONS["odt"], tables, text_regions)
    assert (line, markdown, rendering_bytes(out.read_bytes())) == (
        success(path, "odt", labels, OMISSIONS["odt"], tables, text_regions, out),
        expected, expected.encode("utf-8"),
    ), "exact repeat, character and cumulative limits must preserve complete output semantics"


BOUNDARY_REFUSALS = [
    pytest.param("TABLE_MAX_COLUMNS", 2, repeated_table(columns=3),
                 "ODF table columns limit exceeded: 2", id="one-over-columns"),
    pytest.param("TABLE_MAX_SLOTS", 4, repeated_table(rows=5),
                 "ODF table slots limit exceeded: 4", id="one-over-table-slots"),
    pytest.param("TOTAL_TABLE_MAX_SLOTS", 4, repeated_table(columns=2) + repeated_table(columns=3),
                 "ODF total table slots limit exceeded: 4", id="one-over-cumulative-table-slots"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 5, '<text:p>ABCDEF</text:p>',
                 "ODF expanded characters limit exceeded: 5", id="one-over-literal-characters"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 5, '<text:p><text:s text:c="6"/></text:p>',
                 "ODF expanded characters limit exceeded: 5", id="one-over-expanded-spaces"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, repeated_table(columns=5, text="a"),
                 "ODF expanded characters limit exceeded: 4", id="one-over-cell-copies"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, repeated_table(rows=5, text="a"),
                 "ODF expanded characters limit exceeded: 4", id="one-over-row-copies"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4,
                 '<text:h text:outline-level="1">AB</text:h><text:p>CDE</text:p>',
                 "ODF expanded characters limit exceeded: 4", id="one-over-heading-plus-body"),
    pytest.param("EXPANDED_MAX_CHARACTERS", 4, '<text:p>AB</text:p><text:p>CDE</text:p>',
                 "ODF expanded characters limit exceeded: 4", id="one-over-cumulative-paragraphs"),
]


@pytest.mark.parametrize("constant,limit,blocks,reason", BOUNDARY_REFUSALS)
def test_odf_one_over_injected_expansion_boundaries_preserve_destination(
        tmp_path, monkeypatch, constant, limit, blocks, reason):
    # GIVEN one more expanded slot or character than the injected policy admits
    monkeypatch.setattr(odf, constant, limit)
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    out.write_bytes(b"protected destination")
    assert (getattr(odf, constant), out.read_bytes()) == (limit, b"protected destination"), (
        "the one-over check must use the injected policy and a protected destination")

    # WHEN public conversion checks prospective operation growth
    result = rendering_result(run(path, out), out)

    # THEN the exact named refusal retains zero-unit receipt semantics and old output bytes
    expected = receipt(path, "odt", reason) | {"not_carried": OMISSIONS["odt"]}
    assert (result, out.read_bytes(), sorted(item.name for item in tmp_path.iterdir())) == (
        (1, expected, ""), b"protected destination", ["output.md", "source.odt"],
    ), "one-over repeat and cumulative budgets must refuse before output publication"


def test_odf_character_refusal_precedes_space_materialization(tmp_path, monkeypatch):
    # GIVEN more requested spaces than the operation's remaining character allowance
    monkeypatch.setattr(odf, "EXPANDED_MAX_CHARACTERS", 5)
    path = package(tmp_path / "source.odt", members("odt", '<text:p>x<text:s text:c="5"/></text:p>'))
    allocations = []
    original_spaces = odf._spaces

    def tracked_spaces(count):
        """Observe admitted space allocation without replacing its behavior."""
        allocations.append(count)
        return original_spaces(count)

    monkeypatch.setattr(odf, "_spaces", tracked_spaces)
    assert allocations == [], "space allocation observations must begin empty"

    # WHEN text:s would grow extracted characters from one to six
    result = rendering_result(run(path))

    # THEN the character refusal happens before multiplying the spaces string
    expected = receipt(path, "odt", "ODF expanded characters limit exceeded: 5") | {
        "not_carried": OMISSIONS["odt"]}
    assert (result, allocations) == ((1, expected, ""), []), (
        "remaining operation capacity must be checked before _spaces is entered")


def test_odf_slot_refusal_precedes_repeated_row_materialization(tmp_path, monkeypatch):
    # GIVEN a five-row declaration whose four-slot limit forbids entering the repeat loop
    monkeypatch.setattr(odf, "TABLE_MAX_SLOTS", 4)
    path = package(tmp_path / "source.odt", members("odt", repeated_table(rows=5)))
    allocations = []

    def tracked_range(*arguments):
        """Observe ODF row/grid loops without changing their Python iteration."""
        allocations.append(arguments)
        return range(*arguments)

    monkeypatch.setattr(odf, "range", tracked_range, raising=False)
    assert allocations == [], "row materialization observations must begin empty"

    # WHEN the source row count exceeds the admitted slot count by one
    result = rendering_result(run(path))

    # THEN no ODF repeat or grid loop begins before the named refusal
    expected = receipt(path, "odt", "ODF table slots limit exceeded: 4") | {
        "not_carried": OMISSIONS["odt"]}
    assert (result, allocations) == ((1, expected, ""), []), (
        "slot admission must happen before any repeated row or cell grid is materialized")


def test_odf_zero_width_huge_row_repeat_skips_decimal_conversion_and_iteration(tmp_path, monkeypatch):
    # GIVEN an empty source row declaring a positive 5,000-digit repeat and later visible text
    blocks = ('<table:table><table:table-row table:number-rows-repeated="%s"/></table:table>'
              '<text:p>kept</text:p>' % ("9" * 5000))
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    counts, loops = [], []
    original_count = odf._count

    def tracked_count(*arguments):
        """Observe numeric admission calls rather than permitting a huge integer conversion."""
        counts.append(arguments[1])
        return original_count(*arguments)

    def tracked_range(*arguments):
        """Observe whether a zero-width repeated source starts a proportional loop."""
        loops.append(arguments)
        return range(*arguments)

    monkeypatch.setattr(odf, "_count", tracked_count)
    monkeypatch.setattr(odf, "range", tracked_range, raising=False)
    assert (path.stat().st_size < 8192, counts, loops) == (True, [], []), (
        "a huge repeat must still be represented by a small source with fresh observations")

    # WHEN public conversion reaches a repeated row with no explicit cell slots
    code, line, markdown = rendering_result(run(path, out), out)

    # THEN the unrenderable row is skipped and later prose remains exact without numeric work
    assert code == 0, "a positive zero-width repeat must not be converted into a huge integer"
    body = BODY_START + "kept\n"
    expected = expected_document(path, "odt", body, ["Body"], OMISSIONS["odt"], 0, 1)
    assert (line, markdown, rendering_bytes(out.read_bytes()), counts, loops) == (
        success(path, "odt", ["Body"], OMISSIONS["odt"], 0, 1, out),
        expected, expected.encode("utf-8"), [], [],
    ), "zero-width repeats must do no decimal conversion or work proportional to the repeat"


@pytest.mark.parametrize("constant,blocks,reason,state", [
    ("TOTAL_TABLE_MAX_SLOTS", repeated_table(columns=2) + repeated_table(columns=3),
     "ODF total table slots limit exceeded: 4", (0, 2)),
    ("EXPANDED_MAX_CHARACTERS", '<text:p>AB</text:p><text:p>CDE</text:p>',
     "ODF expanded characters limit exceeded: 4", (2, 0)),
], ids=["slots", "characters"])
def test_odf_rejected_growth_does_not_retain_over_budget_counter_state(
        tmp_path, monkeypatch, constant, blocks, reason, state):
    # GIVEN an operation with admitted growth followed by a cumulative one-over request
    monkeypatch.setattr(odf, constant, 4)
    path = package(tmp_path / "source.odt", members("odt", blocks))
    counters = []
    original_expansion = odf._Expansion

    def tracked_expansion():
        """Observe retained budget state after ordinary operation admission checks."""
        counter = original_expansion()
        counters.append(counter)
        return counter

    monkeypatch.setattr(odf, "_Expansion", tracked_expansion)
    assert counters == [], "each operation must create its own fresh expansion counter"

    # WHEN cumulative slot or character admission refuses the later source block
    result = rendering_result(run(path))

    # THEN the counter retains only previously admitted state
    expected = receipt(path, "odt", reason) | {"not_carried": OMISSIONS["odt"]}
    assert (result, [(counter.characters, counter.slots) for counter in counters]) == (
        (1, expected, ""), [state],
    ), "charge_slots and charge_characters must reject prospective growth before retaining it"


def test_odf_consumed_members_share_one_zip_xml_budget_per_operation(tmp_path, monkeypatch):
    # GIVEN three selected members and an observer for the operation-owned ZIP/XML budget
    parts = members("odt", '<text:p>kept</text:p>')
    path = package(tmp_path / "source.odt", parts)
    out = tmp_path / "output.md"
    budgets = []
    original_budget = odf._ZipBudget
    selected_bytes = sum(len(payload.encode("utf-8")) for _name, payload in parts)

    def tracked_budget(**arguments):
        """Observe concrete budget reuse without changing shared ZIP/XML policy."""
        budget = original_budget(**arguments)
        budgets.append(budget)
        return budget

    monkeypatch.setattr(odf, "_ZipBudget", tracked_budget)
    assert budgets == [], "there must be no inherited budget before public conversion starts"

    # WHEN two independent public operations consume mimetype, manifest and content
    first = rendering_result(run(path, out), out)
    second = rendering_result(run(path, out), out)

    # THEN selected member bytes accumulate in one fresh ODF-labelled budget per operation
    body = BODY_START + "kept\n"
    expected = expected_document(path, "odt", body, ["Body"], OMISSIONS["odt"], 0, 1)
    line = success(path, "odt", ["Body"], OMISSIONS["odt"], 0, 1, out)
    assert (first, second, [(budget.format_name, budget.consumed_bytes) for budget in budgets],
            rendering_bytes(out.read_bytes())) == (
        (0, line, expected), (0, line, expected), [("ODF", selected_bytes), ("ODF", selected_bytes)],
        expected.encode("utf-8"),
    ), "manifest and content XML must share the same operation budget without cross-run leakage"


@pytest.mark.parametrize("xml,expected_code", [
    (content("odt", '<text:p>kept</text:p>'), 0), ("<", 1),
], ids=["success", "malformed-content"])
def test_odf_archive_is_closed_after_success_or_required_xml_refusal(tmp_path, monkeypatch, xml, expected_code):
    # GIVEN consumed content and an observer for the concrete opened archive
    parts = members("odt", "")
    parts[2] = ("content.xml", xml)
    path = package(tmp_path / "source.odt", parts)
    archives = []
    original_zip = zipfile.ZipFile

    def tracked_zip(*arguments, **options):
        """Keep ordinary archive objects visible after their context exits."""
        archive = original_zip(*arguments, **options)
        archives.append(archive)
        return archive

    monkeypatch.setattr(odf.zipfile, "ZipFile", tracked_zip)
    assert archives == [], "archive lifetime observations must begin empty"

    # WHEN public conversion succeeds or required content fails XML parsing
    code, _line, _markdown = run(path)

    # THEN the operation releases its archive on both ordinary exit paths
    assert (code, [(archive.filename, archive.fp is None) for archive in archives]) == (
        expected_code, [(None, True), (str(path), True)],
    ), "inspection and readable ODF archives must close after rendering and XML refusal"


@pytest.mark.parametrize("blocks,body,labels,text_regions", [
    pytest.param('<text:h text:outline-level="1">Body</text:h>'
                 '<text:h text:outline-level="1">Next</text:h><text:p>value</text:p>',
                 '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\n'
                 '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Next"\n\nvalue\n',
                 ["Body", "Next"], 1, id="consecutive-explicit-body-heading"),
    pytest.param('<text:h text:outline-level="1">First</text:h>'
                 '<text:h text:outline-level="1">Next</text:h><text:p>value</text:p>',
                 '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "First"\n\n'
                 '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Next"\n\nvalue\n',
                 ["First", "Next"], 1, id="consecutive-other-heading-control"),
    pytest.param('<text:h text:outline-level="1">Next</text:h>'
                 '<text:h text:outline-level="1">Body</text:h>',
                 '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Next"\n\n'
                 '<a id="brewdoc-chapter-000002"></a>\n## Chapter 2: "Body"\n',
                 ["Next", "Body"], 0, id="trailing-explicit-body-heading"),
])
def test_odt_explicit_body_heading_retains_empty_chapter_identity(
        tmp_path, blocks, body, labels, text_regions):
    # GIVEN explicit consecutive or trailing headings, including the literal label Body
    path = package(tmp_path / "source.odt", members("odt", blocks))
    out = tmp_path / "output.md"
    parsed = ET.fromstring(content("odt", blocks))
    assert [node.text for node in parsed.iter("{%s}h" % TEXT)] == labels, (
        "both expected chapters must be explicitly declared headings in the source")

    # WHEN public conversion creates chapters from the explicit heading sequence
    result = rendering_result(run(path, out), out)

    # THEN an explicit Body label has the same empty-chapter identity as every other heading
    expected = expected_document(path, "odt", body, labels, OMISSIONS["odt"], 0, text_regions)
    assert (result, rendering_bytes(out.read_bytes())) == (
        (0, success(path, "odt", labels, OMISSIONS["odt"], 0, text_regions, out), expected),
        expected.encode("utf-8"),
    ), "literal Body headings must preserve their declared units, including empty trailing chapters"
