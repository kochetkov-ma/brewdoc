"""Physical PDF source rows retain revision, generation and decoded-container identity."""

import re
import io
from pathlib import Path

import pytest

from brewdoc._inspection_common import InspectionContext
from brewdoc._inspection_pdf import _decode_stream
from brewdoc._pdf_xref import PDFReference, PDFRevision, PDFSourceError, read_pdf_source
from test_inspect_pdf import (_compressed_revisions, _last_xref, _pdf, _revision_base,
                              _revision_catalog, _stream, _table_revision, _xref_stream_entry)


def _source(data, *, limits=None):
    context = InspectionContext(limits=limits)
    document = context.document(data, "source.pdf", parent=None, location={"kind": "source"})
    return read_pdf_source(data, context, document, _decode_stream), context


def _program(view):
    value = view.getobj(5)["JS"]
    return value.resolve().get_data() if isinstance(value, PDFReference) else value


@pytest.mark.parametrize("indirect", [False, True])
def test_table_views_resolve_literal_and_indirect_programs_in_original_revision(indirect):
    # GIVEN a retained original action or a retained action referencing an overwritten stream
    original = _revision_base(b"oldProof();", indirect=indirect)
    updates = {False: ((5, 0, rb"<< /S /JavaScript /JS (newProof\(\);) >>"),),
               True: ((6, 0, _stream(b"newProof();")),)}[indirect]
    revised, metadata = _table_revision(original, updates)
    assert revised[:len(original)] == original, "incremental source must preserve every original byte"
    # WHEN independent checked views resolve the same object numbers
    source, _context = _source(revised)
    with source.view(0) as current:
        actual_new = _program(current)
    with source.view(1) as historical:
        actual_old = _program(historical)
    # THEN resolution and physical revision identity cannot share newest-object caches
    assert (actual_new, actual_old, [(item["ordinal"], item["section"]) for item in source.revisions],
            source.data is revised) == (
        b"newProof();", b"oldProof();", [(0, metadata["section"]), (1, _last_xref(original))], True,
    ), "source views must retain exact historical bytes, section order and the single admitted source buffer"


@pytest.mark.parametrize("mode", ["stream", "multi-index", "hybrid"])
def test_compressed_views_bind_original_container_offsets_and_body_spans(mode):
    # GIVEN valid old/new compressed objects that reuse the same ObjStm and action IDs
    original, revised, metadata = _compressed_revisions(mode)
    assert revised[:len(original)] == original, "the compressed source must retain its original physical revision"
    # WHEN each revision resolves its action through a fresh view-local object-stream parser
    source, _context = _source(revised)
    with source.view(0) as current:
        row = source.lookup(current.revision["section"], 5)
        newest = current.read_occurrence(row)
    with source.view(1) as older:
        row = source.lookup(older.revision["section"], 5)
        oldest = older.read_occurrence(row)
    # THEN exact bodies and measured decoded-container spans preserve both source occurrences
    assert (newest["value"]["JS"], oldest["value"]["JS"], newest["data"], oldest["data"],
            newest["location"]["object_stream_offset"], oldest["location"]["object_stream_offset"],
            newest["location"]["byte_start"], newest["location"]["byte_end"],
            newest["location"]["byte_basis"]) == (
        b"newCompressed();", b"oldCompressed();", metadata["new_body"], metadata["old_body"],
        metadata["new_container"], metadata["old_container"], metadata["first"],
        metadata["first"] + len(metadata["new_body"]), "decoded_object_stream",
    ), "compressed body identity must use the selected revision's physical container and exact decoded span"


def test_free_tombstone_and_generation_reuse_never_resolve_stale_references():
    # GIVEN a deleted action followed by a valid new generation of the same object number
    original = _revision_base()
    deleted, _metadata = _table_revision(original, ((1, 0, _revision_catalog(action=False)),), free=((5, 1),))
    revised, _metadata = _table_revision(deleted, ((1, 0, _revision_catalog(generation=1)),
                                                 (5, 1, rb"<< /S /JavaScript /JS (reusedProof\(\);) >>")))
    assert len(re.findall(rb"startxref", revised)) == 3, "the source must retain allocation, deletion and generation reuse"
    # WHEN each checked source view resolves the object with its original generation
    source, _context = _source(revised)
    with source.view(0) as newest:
        actual = newest.catalog["OpenAction"].resolve()["JS"]
        with pytest.raises(PDFSourceError, match="generation"):
            newest.getobj(5, 0)
    with source.view(1) as middle:
        with pytest.raises(PDFSourceError, match="free"):
            middle.getobj(5, 0)
    with source.view(2) as oldest:
        original_value = oldest.getobj(5, 0)["JS"]
    # THEN a present free row terminates lookup and a valid reused generation resolves only its own bytes
    assert (actual, original_value, [(row["revision"], row["type"], row["generation"])
                                    for row in source.rows if row["object_id"] == 5]) == (
        b"reusedProof();", b"oldProof();", [(0, 1, 1), (1, 0, 1), (2, 1, 0)],
    ), "free metadata and generation-aware references must preserve every original source occurrence without resurrection"


def test_version_resolution_uses_the_selected_catalog_revision():
    # GIVEN a newer catalog Version declaration without rewriting the original PDF header
    original = _revision_base(b"\xef\xbb\xbfproof();")
    revised, _metadata = _table_revision(original, ((1, 0, _revision_catalog(version="2.0")),))
    assert revised[:8] == b"%PDF-1.7", "the document header must remain the original measured version"
    # WHEN each view resolves its own catalog and version references
    source, _context = _source(revised)
    with source.view(0) as current:
        latest = current.version()
    with source.view(1) as original_view:
        prior = original_view.version()
    # THEN the newer catalog cannot select the historical source's script encoding
    assert (latest["effective"], latest["source"], prior["effective"], prior["source"]) == (
        "2.0", "catalog", "1.7", "header",
    ), "catalog version and inherited source header must remain scoped to their actual revision"


@pytest.mark.parametrize("edge", ["self", 2 ** 40])
def test_guarded_bootstrap_rejects_cyclic_and_out_of_range_prev_without_native_fallback(edge, monkeypatch):
    # GIVEN a malformed Prev edge that native initialization must never recursively follow
    original = _revision_base()
    revised, metadata = _table_revision(original, ((5, 0, rb"<< /S /JavaScript /JS (newProof\(\);) >>"),), previous=edge)
    assert metadata["previous"] == {"self": metadata["section"], 2 ** 40: 2 ** 40}[edge], "the fixture must contain the exact malformed Prev target"

    def forbidden(*_args, **_kwargs):
        pytest.fail("native fallback scanning must not run after a guarded source failure")

    monkeypatch.setattr("pdfminer.pdfdocument.PDFXRefFallback.load", forbidden)
    # WHEN source indexing validates offsets and active section edges before native initialization
    source, _context = _source(revised)
    # THEN the known source boundary retains an explicit cycle/range failure instead of entering fallback
    assert [(item["location"]["xref_section_offset"], bool(re.search("cycle|range", item["reason"])))
            for item in source.problems] == [(metadata["section"], True)], (
        "guarded bootstrap must report the exact refused edge and keep native fallback disabled"
    )


def test_wrong_compressed_header_identity_cannot_become_a_valid_action():
    # GIVEN a compressed row claiming object 5 while the container header declares object 99
    _original, revised, _metadata = _compressed_revisions(header_id=99)
    assert b"/OpenAction 5 0 R" in revised, "the source must reference the distinct claimed object number"
    # WHEN a checked view compares the selected compressed header ID with its source row
    source, _context = _source(revised)
    with source.view(0) as current:
        with pytest.raises(PDFSourceError, match="header ID/index mismatch"):
            current.getobj(5)
    # THEN the xref rows themselves remain inventoried even though their object identity is malformed
    assert [(row["revision"], row["object_id"], row["target"], row["index"])
            for row in source.rows if row["type"] == 2] == [(0, 5, 6, 0), (1, 5, 6, 0)], (
        "malformed compressed identity must not erase either retained source row"
    )


def test_low_record_budget_stops_before_excess_xref_rows_are_stored():
    # GIVEN a valid PDF whose declared rows exceed an explicitly small shared record allowance
    data = _revision_base()
    assert b"xref\n0 6\n" in data, "the input must contain six source xref rows"
    # WHEN guarded bootstrap consumes the operation-wide budget before row/container growth
    source, context = _source(data, limits={"records": 8})
    # THEN the exhausted counter and original source boundary remain explicit without a fresh per-view allowance
    assert (context.exhausted[0]["limit"], len(source.rows), len(source.problems)) == (
        "records", 0, 1,
    ), "source indexing must not admit rows after the shared record allowance is exhausted"


def _hybrid(original, *, table_action=None):
    body = rb"5 0 << /S /JavaScript /JS (supplementProof\(\);) >>" + b"\n"
    container = _stream(body, b"/Type /ObjStm /N 1 /First 4")
    supplement = _stream(_xref_stream_entry(2, 6, 0),
                          b"/Type /XRef /Size 9 /Root 1 0 R /W [1 4 2] /Index [5 1]")
    updates = [(1, 0, _revision_catalog()), (6, 0, container), (8, 0, supplement)]
    if table_action is not None:
        updates.append((5, 0, table_action))
    return _table_revision(original, updates, supplement=8)[0]


def test_current_table_precedes_own_supplement_without_discarding_either_row():
    # GIVEN competing current table and supplement definitions of the same object
    revised = _hybrid(_revision_base(), table_action=rb"<< /S /JavaScript /JS (tableProof\(\);) >>")
    assert b"/XRefStm" in revised, "the current table must declare its own supplemental stream"
    # WHEN current source lookup follows table, own supplement and only then older revisions
    source, _context = _source(revised)
    with source.view(0) as current:
        chosen = current.getobj(5)["JS"]
    rows = [row for row in source.rows if row["object_id"] == 5 and row["revision"] == 0]
    # THEN table precedence is explicit and both physical row occurrences remain available
    assert (chosen, [row["type"] for row in rows], source.conflicts[0]["rule"],
            source.conflicts[0]["selected_row"]) == (
        b"tableProof();", [1, 2], "current-table-first", rows[0]["id"],
    ), "hybrid lookup must preserve its deterministic precedence and retain the competing source evidence"


def test_current_supplement_precedes_an_older_free_slot():
    # GIVEN an older free slot that is allocated only by the current hybrid supplement
    objects = {1: _revision_catalog(action=False), 2: b"<< /Type /Pages /Count 0 /Kids [] >>"}
    original = _pdf(objects)
    original = original.replace(b"trailer\n", b"5 1\n0000000000 00000 f \ntrailer\n").replace(b"/Size 3", b"/Size 6")
    revised = _hybrid(original)
    assert b"5 1\n0000000000 00000 f" in original, "the prior revision must contain the actual free source row"
    # WHEN the current supplement is searched before that older table
    source, _context = _source(revised)
    with source.view(0) as current:
        chosen = current.getobj(5, 0)["JS"]
    with source.view(1) as prior:
        with pytest.raises(PDFSourceError, match="free"):
            prior.getobj(5, 0)
    # THEN the valid compressed allocation wins without resurrecting a definition from an older view
    assert chosen == b"supplementProof();", "a current supplemental definition must precede an older free tombstone"


def test_source_region_accounting_keeps_unindexed_objects_separate_from_stream_payload_decoys():
    # GIVEN an unindexed script plus header-like bytes inside an admitted stream body
    original = _revision_base()
    unindexed = rb"9 0 obj << /S /JavaScript /JS (orphanProof\(\);) >> endobj" + b"\n"
    decoy = b"99 0 obj << /S /JavaScript /JS (stream-data-only) >> endobj\nxref\n"
    revised, _metadata = _table_revision(original + unindexed, ((6, 0, _stream(decoy)),))
    assert revised.count(decoy) == 1, "the stream must contain the exact decoy bytes as its actual payload"
    # WHEN admitted object/stream spans are excluded from native gap tokenization
    source, _context = _source(revised)
    regions = source.account_regions()
    # THEN the real orphan remains recoverable while stream bytes never become a fake object or section
    assert ([(item["object_id"], item["value"]["JS"]) for item in source.orphans],
            len(source.sections), [item["kind"] for item in regions if item["kind"] == "pdf-unclaimed-source"],
            [item["reason"] for item in source.problems]) == (
        [(9, b"orphanProof();")], 2, [], ["PDF unindexed object has no established revision context"],
    ), "source-gap recovery must distinguish original orphan syntax from opaque bytes inside known streams"


def test_disconnected_source_sections_keep_physical_identity_without_becoming_revision_zero():
    # GIVEN a current xref that deliberately omits the earlier Prev link
    original = _revision_base()
    revised, metadata = _table_revision(original, ((5, 0, rb"<< /S /JavaScript /JS (newProof\(\);) >>"),))
    inherited_rows = b"1 4\n" + b"".join(("%010d 00000 n \n" % original.index(("%d 0 obj\n" % number).encode())).encode()
                                          for number in range(1, 5))
    revised = revised[:metadata["section"]] + revised[metadata["section"]:].replace(b"xref\n", b"xref\n" + inherited_rows, 1)
    revised = revised.replace((" /Prev %d" % metadata["previous"]).encode(), b"", 1)
    assert revised[:len(original)] == original, "the disconnected original xref and its source objects must remain physically present"
    # WHEN the source-region pass finds the unlinked native section
    source, _context = _source(revised)
    source.account_regions()
    unlinked = next(item for item in source.revisions if item["status"] == "unlinked")
    with source.view(unlinked["id"]) as older:
        old = older.getobj(5)["JS"]
    # THEN its physical revision ID remains explicit and its original program resolves through that local source view
    assert (unlinked["ordinal"], unlinked["id"], old,
            {(row["revision"], row["revision_id"]) for row in source.rows if row["section"] == metadata["previous"]},
            source.problems) == (
        None, "xref@%d" % metadata["previous"], b"oldProof();",
        {(None, "xref@%d" % metadata["previous"])}, [],
    ), "a disconnected section must never borrow newest-revision identity or newest object resolution"


def test_duplicate_dictionary_keys_preserve_opaque_source_instead_of_native_overwrite():
    # GIVEN two conflicting JS keys that the pinned native dictionary parser would collapse
    source = _revision_base()
    source, _metadata = _table_revision(source, ((5, 0, rb"<< /S /JavaScript /JS (firstProof\(\);) /JS (secondProof\(\);) >>"),))
    assert source.count(b"/JS (firstProof") == 1, "the first conflicting source value must remain in the physical bytes"
    # WHEN the narrow dictionary hook checks original keys before native dict construction
    parsed, _context = _source(source)
    with parsed.view(0) as current:
        with pytest.raises(PDFSourceError, match="duplicated"):
            current.getobj(5)
    regions = parsed.account_regions()
    opaque = b"".join(item["data"] for item in regions if item["kind"] == "pdf-unclaimed-source")
    # THEN both values survive as explicit ambiguous source rather than a falsely complete last-key result
    assert (b"firstProof" in opaque, b"secondProof" in opaque,
            any("duplicated" in item["reason"] for item in parsed.problems)) == (
        True, True, True,
    ), "duplicate keys must retain original bytes and a located ambiguity failure before semantic inspection"


def test_duplicate_xref_rows_preserve_raw_ambiguity_without_repeated_inventory():
    # GIVEN a repeated object row in distinct subsections of the same original table
    original = _revision_base()
    row = ("%010d 00000 n \n" % original.index(b"5 0 obj\n")).encode()
    data = original.replace(b"trailer\n", b"5 1\n" + row + b"trailer\n", 1)
    assert data.count(row) == 2, "both exact original row occurrences must remain in source"
    # WHEN the guarded reader refuses ambiguity before an index can silently overwrite it
    source, context = _source(data)
    regions = source.account_regions()
    opaque = [item for item in regions if item["kind"] == "pdf-unclaimed-source"]
    # THEN one partial row inventory and exact opaque table bytes retain the ambiguity
    assert (len(source.rows), len({item["id"] for item in source.rows}),
            sum(item["data"].count(row) for item in opaque),
            any("repeats an object ID" in item["reason"] for item in source.problems),
            [context.payloads[item["artifact"]] == item["data"] for item in opaque]) == (
        6, 6, 2, True, [True],
    ), "duplicate xref source must remain retrievable without overwriting or duplicating admitted row identities"


def test_xref_stream_decoding_uses_shared_limits_before_row_admission():
    # GIVEN a valid compressed xref whose decoded rows exceed the operation allowance
    original, _revised, _metadata = _compressed_revisions()
    assert b"/W [1 4 2]" in original, "the fixture must retain its declared seven-byte xref rows"
    # WHEN bootstrap invokes the existing bounded decoder before native initialization
    source, context = _source(original, limits={"decoded_bytes": 32})
    # THEN the source cannot create a fresh decoder budget or silently enter fallback scanning
    assert (len(source.rows), context.exhausted[0]["limit"],
            context.exhausted[0]["location"]["xref_section_offset"],
            source.problems[0]["location"]["xref_section_offset"]) == (
        0, "decoded_bytes", _last_xref(original), _last_xref(original),
    ), "xref streams must fail at their exact shared decoded-byte boundary before source rows are admitted"


@pytest.mark.corpus
def test_valid_linearized_sections_share_one_base_revision_and_catalog():
    # GIVEN a public linearized PDF with three updates and a rootless main base section
    data = Path("tests/fixtures/pdf/nist-sp800-145.pdf").read_bytes()
    assert data[16:44] == b"187 0 obj\r<</Linearized 1/L ", "the source must retain its actual linearization dictionary"
    # WHEN both base sections are admitted through their declared linearization relationship
    source, _context = _source(data)
    source.account_regions()
    # THEN physical section rows remain inventoried without inventing an invalid historical document
    assert ([(item["section"], item["ordinal"]) for item in source.revisions], source.problems) == (
        [(85522, 0), (79781, 1), (116, 2)], [],
    ), "the first-page and main linearized xrefs belong to the same base revision"


def _linearized_base():
    """Build a small table-linearized base with checked fixed-width parameter offsets."""
    output, offsets = io.BytesIO(), {}
    output.write(b"%PDF-1.7\n")
    offsets[7] = output.tell()
    output.write(b"7 0 obj\n<< /Linearized 1 /L 0000000000 /O 5 /E 0000000000 /N 1 /T 0000000000 /H [0000000000 0000000000] >>\nendobj\n")
    first = output.tell()
    output.write(b"xref\n4 5\n" + b"0000000000 00000 n \n" * 5)
    output.write(b"trailer\n<< /Size 9 /Root 4 0 R /Prev 0000000000 >>\n")
    objects = {4: b"<< /Type /Catalog /Pages 1 0 R /OpenAction 6 0 R >>",
               5: b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 100 100] /Contents 3 0 R >>",
               6: b"<< /S /JavaScript /JS (linearOld) >>", 8: _stream(b"")}
    for number, payload in objects.items():
        offsets[number] = output.tell()
        output.write(("%d 0 obj\n" % number).encode() + payload + b"\nendobj\n")
    page_end = output.tell()
    for number, payload in {1: b"<< /Type /Pages /Count 1 /Kids [5 0 R] >>", 2: b"null", 3: _stream(b"")}.items():
        offsets[number] = output.tell()
        output.write(("%d 0 obj\n" % number).encode() + payload + b"\nendobj\n")
    main = output.tell()
    output.write(b"xref\n0 4\n0000000000 65535 f \n")
    for number in range(1, 4):
        output.write(("%010d 00000 n \n" % offsets[number]).encode())
    output.write(("trailer\n<< /Size 4 >>\nstartxref\n%d\n%%%%EOF\n" % first).encode())
    data = output.getvalue()
    rows = b"".join(("%010d 00000 n \n" % offsets[number]).encode() for number in range(4, 9))
    data = data.replace(b"0000000000 00000 n \n" * 5, rows, 1)
    for key, value in ((b"L", len(data)), (b"E", page_end), (b"T", main + len(b"xref\n0 4\n") - 1), (b"Prev", main)):
        data = data.replace(b"/" + key + b" 0000000000", b"/" + key + (" %010d" % value).encode(), 1)
    data = data.replace(b"/H [0000000000 0000000000]", ("/H [%010d %010d]" % (offsets[8], page_end - offsets[8])).encode(), 1)
    return data, first, main


@pytest.mark.parametrize("deceptive", [False, True])
def test_linearized_base_with_incremental_update_requires_exact_parameter_evidence(deceptive):
    # GIVEN a linearized base and one retained action replacement, or a deceptive T declaration
    base, first, main = _linearized_base()
    updated, metadata = _table_revision(base, ((6, 0, b"<< /S /JavaScript /JS (linearNew) >>"),))
    updated = updated.replace(b"/Root 1 0 R", b"/Root 4 0 R")
    original_t = ("/T %010d" % (main + len(b"xref\n0 4\n") - 1)).encode()
    changed = {False: updated, True: updated.replace(original_t, b"/T 0000000001", 1)}[deceptive]
    assert changed[:9] == b"%PDF-1.7\n", "the synthetic base must retain its original PDF version and source prefix"
    # WHEN the lower reader verifies grouping before constructing historical native views
    source, _context = _source(changed)
    with source.view(0) as current:
        new = current.getobj(6)["JS"]
    with source.view(1) as older:
        old = older.getobj(6)["JS"]
    expected = {False: ([(metadata["section"], 0), (first, 1)], []),
                True: ([(metadata["section"], 0), (first, 1), (main, 2)],
                       ["PDF linearized main table does not match T or consecutive rows"])}[deceptive]
    # THEN only measured matching linearization may group sections, with old/new resolution kept separate
    assert (new, old, [(item["section"], item["ordinal"]) for item in source.revisions],
            [item["reason"] for item in source.problems]) == (
        b"linearNew", b"linearOld", *expected,
    ), "deceptive linearization metadata must never merge arbitrary revision sections or replace historical source"


@pytest.mark.corpus
def test_malformed_public_negative_reference_retains_exact_opaque_source():
    # GIVEN an actual public outline object with a negative indirect reference
    data = Path("tests/fixtures/pdf/us-patent-223898-scan.pdf").read_bytes()
    assert data[226682:226712] == b"6 0 obj\r<<\r\n\t/Dest [ -1 0 R /X", "the negative reference must reproduce in original source bytes"
    # WHEN source accounting refuses that malformed object while retaining its original interval
    source, context = _source(data)
    regions = source.account_regions()
    opaque = [item for item in regions if item["kind"] == "pdf-unclaimed-source"]
    # THEN the malformed source has located failures and exact retrievable bytes
    assert ([item["reason"] for item in source.problems],
            [(item["location"]["byte_start"], item["location"]["byte_end"], context.payloads[item["artifact"]])
             for item in opaque]) == (
        ["PDF indirect reference has invalid object ID or generation", "PDF source region remains opaque and unparsed"],
        [(226680, 226796, data[226680:226796])],
    ), "a malformed real object must retain original evidence and cannot be reported complete"


def test_closed_revision_views_do_not_rebuild_released_native_caches():
    # GIVEN a checked source view that has resolved an action and has been released
    source, context = _source(_revision_base())
    with source.view(0) as view:
        program = view.getobj(5)["JS"]
    assert program == b"oldProof();", "the live view must first resolve its admitted source"
    before = dict(context.consumed)
    # WHEN a retained reference attempts to use the released view
    with pytest.raises(PDFSourceError, match="view is closed"):
        view.getobj(5)
    # THEN closing prevents new work without refunding consumed operation counters
    assert (view.occurrences, view.containers, context.consumed) == (
        {}, {}, before,
    ), "closed historical views must release mutable caches and retain cumulative charges"


@pytest.mark.parametrize("rows,bootstrap,section", [(13, 77, 313), (103, 347, 2206)])
def test_source_accounting_stops_at_first_shared_record_exhaustion(rows, bootstrap, section, monkeypatch):
    # GIVEN small and large valid sources with the same 100-record post-bootstrap allowance
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [] /Count 0 >>"}
    objects.update({number: b"null" for number in range(3, rows)})
    source, context = _source(_pdf(objects), limits={"records": bootstrap + 100})
    assert (len(source.rows), context.consumed["records"], source.rows[-1]["section"]) == (
        rows, bootstrap, section,
    ), "source indexing must reproduce the exact reviewed bootstrap and physical section"
    visits, gaps, decodes, views = [], [], [], []
    original_read = PDFRevision.read_occurrence

    def record_row(view, row):
        visits.append(row["object_id"])
        views.append(view)
        return original_read(view, row)

    def record_gap(*interval):
        gaps.append(interval)
        raise AssertionError("gap work must not run after shared exhaustion")

    def record_decode(*arguments):
        decodes.append(arguments)
        raise AssertionError("later stream decoding must not run after shared exhaustion")

    monkeypatch.setattr(PDFRevision, "read_occurrence", record_row)
    monkeypatch.setattr(source, "_region", record_gap)
    monkeypatch.setattr(source, "decoder", record_decode)
    # WHEN source accounting reaches the first checked object whose parse exceeds the allowance
    result = source.account_regions()
    consumed = dict(context.consumed)
    repeated = source.account_regions()
    # THEN one located diagnostic ends accounting and repeated calls cannot resume charged work
    assert (result, repeated, visits, gaps, decodes, len(source.rows), source.regions,
            source.problems, context.exhausted, context.consumed, all(view.closed for view in views)) == (
        (), (), list(range(1, 13)), [], [], rows, [],
        [{"reason": "inspection records limit exceeded: %d" % (bootstrap + 100),
          "location": {"kind": "pdf", "object_id": 12, "generation": 0, "xref_offset": 292}}],
        [{"limit": "records", "maximum": bootstrap + 100,
          "location": {"kind": "pdf", "object_id": 12, "generation": 0, "xref_offset": 292}}],
        consumed, True,
    ), "exhaustion must preserve admitted rows, close views and stop before later rows, gaps or decoder work"
