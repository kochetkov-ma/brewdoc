"""Direct admitted-source checks for the PDF image occurrence pass."""

import zlib

import pytest
from pdfminer.pdftypes import PDFStream

from brewdoc._inspection_common import InspectionContext
from brewdoc._inspection_pdf import _decode_stream, _image_occurrences, _pages
from brewdoc._pdf_xref import PDFSourceError, read_pdf_source
from test_inspect_pdf import _pdf, _stream
from test_inspect_pdf import _table_revision


def _source(objects, *, context=None, shared_cache=None, revision="revision/1", latest=True):
    data = _pdf(objects)
    context = context or InspectionContext()
    document = context.document(data, "source.pdf", parent=None, location={"kind": "file"})
    physical = read_pdf_source(data, context, document, _decode_stream)
    native = physical.view(0)
    native._inspection_id = document
    values, sources = {}, {}
    for object_id in objects:
        value = native.getobj(object_id)
        location = {"kind": "pdf", "object_id": object_id, "revision": revision}
        raw = value.rawdata if isinstance(value, PDFStream) else objects[object_id]
        artifact = context.payload(document, "pdf-stream" if isinstance(value, PDFStream) else "pdf-object",
                                   location, raw, "application/octet-stream")
        component = context.component(document, "pdf-object", location, data=raw, artifact=artifact)
        decoded = artifact
        if isinstance(value, PDFStream) and value.attrs.get("Subtype") is None:
            _decode_stream(value, context, location)
        values[object_id] = value
        sources[object_id] = {"location": location, "component": component,
                              "encoded_artifact": artifact, "decoded_artifact": decoded}
    page_rows = [(object_id, attrs, {"page" if latest else "revision_page": ordinal})
                 for ordinal, (object_id, attrs) in enumerate(_pages(native, context), 1)]
    cache = shared_cache if shared_cache is not None else {}
    return data, native, values, sources, context, document, page_rows, cache


def _run(objects, **options):
    source = _source(objects, **options)
    _data, native, values, sources, context, document, pages, cache = source
    with native:
        _image_occurrences(native, pages, values, sources, context, document,
                           version=(1, 7), decode_stream=_decode_stream, cache=cache)
    return context, document, sources, cache


def _base(content, resources=b"<< >>"):
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources " + resources + b" /Contents 4 0 R >>",
        4: _stream(content),
    }


def _images(context, kind):
    return [finding for finding in context.findings if finding["kind"] == kind]


def test_inline_source_inventory_keeps_lf_and_an_explicit_page_occurrence():
    # GIVEN a genuine native PDF content stream with a sample-ending LF
    payload = b"\xff\x00\n"
    content = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID " + payload + b"EI Q"
    assert content.index(payload) == 32, "the original inline sample begins at measured decoded offset 32"
    # WHEN the separate direct pass consumes admitted source objects
    context, _document, sources, _cache = _run(_base(content))
    inventory = _images(context, "pdf-inline-image")
    occurrences = _images(context, "pdf-inline-image-occurrence")
    # THEN source inventory and its page use share one exact encoded artifact
    assert len(inventory) == 1, "one physical inline source requires one inventory finding"
    assert len(occurrences) == 1, "the page has one actual inline operator occurrence"
    assert context.payloads[inventory[0]["artifact"]] == payload, "inline artifact retrieval must preserve the final LF"
    assert occurrences[0]["artifact"] == inventory[0]["artifact"], "the occurrence must reference its original payload"
    assert (occurrences[0]["location"]["page"], occurrences[0]["location"]["object_id"],
            occurrences[0]["location"]["payload_span"]) == (1, 4, [32, 35]), "page and original content offsets must be explicit"
    assert occurrences[0]["details"]["encoded_container_artifacts"] == [sources[4]["encoded_artifact"]], "decoded ranges link back to the original stream artifact"


def test_two_pages_aliases_and_repeated_draws_share_original_image_inventory():
    # GIVEN one image shared through two aliases, repeated operators and two native pages
    objects = _base(b"/A Do /B Do /A Do", b"<< /XObject << /A 5 0 R /B 5 0 R >> >>")
    objects[2] = b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>"
    objects[5] = _stream(b"\xff\x00\n", b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8")
    objects[6] = objects[3]
    source = _source(objects)
    _data, native, values, sources, context, document, pages, cache = source
    retained_before = len(context.payloads)
    assert pages[0][0:1] == (3,), "the first source page retains its original object number"
    # WHEN each page's declarations and operator occurrences replay independently
    _image_occurrences(native, pages, values, sources, context, document,
                       version=(1, 7), decode_stream=_decode_stream, cache=cache)
    records = _images(context, "pdf-image-occurrence")
    # THEN source payload identity is separate from declaration and drawing identity
    assert [(record["location"]["page"], record["location"]["occurrence_kind"],
             record["location"]["resource_name"], record["location"].get("operator_ordinal"))
            for record in records] == [
        (1, "resource", "A", None), (1, "resource", "B", None),
        (1, "draw", "A", 1), (1, "draw", "B", 2), (1, "draw", "A", 3),
        (2, "resource", "A", None), (2, "resource", "B", None),
        (2, "draw", "A", 1), (2, "draw", "B", 2), (2, "draw", "A", 3),
    ], "aliases and sibling/page reuse must never be erased by a global visited set"
    assert {record["artifact"] for record in records} == {sources[5]["encoded_artifact"]}, "all uses point to existing encoded image inventory"
    assert {record["component"] for record in records} == {sources[5]["component"]}, "the image source component is never recreated"
    assert len(context.payloads) == retained_before, "resource/draw evidence must not retain another XObject payload"


def test_nested_forms_and_every_appearance_state_replay_inline_sources():
    # GIVEN sibling Form uses and selected/unselected named annotation appearance states
    inline = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\nEI"
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << >>"
    objects = _base(b"/F Do /F Do", b"<< /XObject << /F 7 0 R >> >>")
    objects[5] = b"null"
    objects[3] = objects[3][:-2] + b" /Annots [6 0 R] >>"
    objects[6] = b"<< /Type /Annot /Subtype /Widget /AS /Hot /AP << /N 9 0 R /R << /Hot 10 0 R /Cold 11 0 R >> /D 9 0 R >> >>"
    objects[7] = _stream(b"/Inner Do", b"/Type /XObject /Subtype /Form /Resources << /XObject << /Inner 8 0 R >> >>")
    objects[8] = _stream(inline, form)
    objects[9] = _stream(inline, form)
    objects[10] = _stream(inline, form)
    objects[11] = _stream(inline, form)
    assert len({objects[index] for index in (8, 9, 10, 11)}) == 1, "equal bytes remain four distinct physical native streams"
    # WHEN declarations, invocation chains and every AP state are traversed
    context, _document, sources, cache = _run(objects)
    records = _images(context, "pdf-inline-image-occurrence")
    # THEN equal source bytes stay physically distinct while each repeated use is retained
    assert len(_images(context, "pdf-inline-image")) == 4, "physical sources must not collapse merely because their payload bytes match"
    assert len(cache) == 4, "the persistent cache retains one small identity entry per physical inline source"
    assert [(record["location"]["object_id"], len(record["location"]["invocation_path"]),
             record["location"].get("appearance_key"), record["location"].get("appearance_state"))
            for record in records] == [
        (8, 2, None, None), (8, 2, None, None),
        (9, 0, "N", None), (10, 0, "R", "Hot"), (11, 0, "R", "Cold"), (9, 0, "D", None),
    ], "all sibling Form and AP occurrences must carry the actual invocation/state identity"
    assert [(record["location"]["annotation_ordinal"], record["location"]["annotation_object_id"])
            for record in records[2:]] == [(1, 6)] * 4, "unselected appearances retain exact annotation identity"
    assert records[0]["details"]["encoded_container_artifacts"] == [sources[8]["encoded_artifact"]], "nested inline spans bind to the actual Form source"


def test_orphan_form_pattern_and_type3_charproc_inventory_has_no_invented_page():
    # GIVEN three source content containers not referenced by any page resources
    inline = b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI"
    objects = _base(b"")
    objects[5] = _stream(inline, b"/Type /XObject /Subtype /Form /Resources << >>")
    objects[6] = _stream(inline, b"/Type /Pattern /PatternType 1 /Resources << >>")
    objects[7] = b"<< /Type /Font /Subtype /Type3 /CharProcs << /A 8 0 R >> /Resources << >> >>"
    objects[8] = _stream(inline)
    assert b"/PatternType 1" in objects[6], "the orphan pattern is a genuine tiling-pattern stream"
    # WHEN independent inventory visits all admitted content sources
    context, _document, _sources, _cache = _run(objects)
    inventory = _images(context, "pdf-inline-image")
    # THEN all sources survive without inventing page draws or visibility
    assert [item["location"]["object_id"] for item in inventory] == [5, 6, 8], "orphan Forms, patterns and glyph programs each contain real inline source bytes"
    assert [item["location"].get("page") for item in inventory] == [None, None, None], "orphan source inventory has no invented page"
    assert _images(context, "pdf-inline-image-occurrence") == [], "source discovery alone cannot manufacture a drawing occurrence"


def test_split_contents_preserves_payload_member_spans_and_container_artifacts():
    # GIVEN one logical inline image split in the middle of its binary samples
    content = b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID\r\n\x00\n EI"
    start = content.index(b"ID") + 3
    objects = _base(content[:start + 1])
    objects[3] = objects[3].replace(b"/Contents 4 0 R", b"/Contents [4 0 R 5 0 R]")
    objects[5] = _stream(content[start + 1:])
    assert content[start:start + 3] == b"\n\x00\n", "CR separator and both genuine LF samples remain distinct"
    # WHEN the logical content cursor spans both physical admitted source members
    context, _document, sources, _cache = _run(objects)
    record = _images(context, "pdf-inline-image-occurrence")[0]
    # THEN both source ranges and original/decoded artifact links stay exact
    assert context.payloads[record["artifact"]] == b"\n\x00\n", "split payload retrieval must preserve all original samples"
    assert [(part["source"]["object_id"], part["span"]) for part in record["location"]["source_segments"]] == [
        (4, [start, start + 1]), (5, [0, 2]),
    ], "a logical payload split must never be reported as one physical stream span"
    assert record["details"]["encoded_container_artifacts"] == [sources[4]["encoded_artifact"], sources[5]["encoded_artifact"]], "both original content containers are source evidence"


@pytest.mark.parametrize(("objects", "reason"), [
    (_base(b"/Missing Do"), "missing XObject"),
    (_base(b"BI /W 1 /H 1 /CS /Unknown /BPC 8 ID x EI"), "ColorSpace resource"),
    (_base(b"BI /W 1e3 /H 1 /CS /G /BPC 8 ID x EI"), "native tokens"),
])
def test_missing_or_malformed_interpretation_leaves_images_and_inventory_open(objects, reason):
    # GIVEN source that cannot prove a valid image occurrence interpretation
    assert type(objects[4]) is bytes, "the malformed source remains the original encoded content stream"
    # WHEN direct inspection records its measured source refusal
    context, document, _sources, _cache = _run(objects)
    # THEN incomplete source coverage cannot become falsely complete-empty
    assert [(context.coverage[(document, capability)]["status"], reason in context.coverage[(document, capability)]["reason"])
            for capability in ("images", "inventory")] == [("unavailable", True), ("unavailable", True)], "both affected capabilities must retain their explicit source failure"
    assert context.coverage[(document, "images")]["locations"][0]["object_id"] == 4, "failure provenance identifies the actual content stream"


def test_active_form_cycles_leave_sibling_reuse_distinct_and_coverage_open():
    # GIVEN a declared Form that invokes its own resource alias
    objects = _base(b"/F Do", b"<< /XObject << /F 5 0 R >> >>")
    objects[5] = _stream(b"/Self Do", b"/Type /XObject /Subtype /Form /Resources << /XObject << /Self 5 0 R >> >>")
    assert objects[5].count(b"5 0 R") == 1, "the cycle is a genuine native indirect self-reference"
    # WHEN traversal tracks only the currently active ancestry
    context, document, _sources, _cache = _run(objects)
    # THEN traversal terminates with explicit source failure and bounded real occurrences
    assert len(_images(context, "pdf-form-occurrence")) == 5, "each admitted declaration and actual invocation remains distinct before the cycle refusal"
    assert context.coverage[(document, "images")]["status"] == "partial", "a real resource cycle must remain incomplete"
    assert "cycle" in context.coverage[(document, "images")]["reason"], "coverage must explain the cycle rather than silently skip it"


def test_existing_context_limit_refuses_before_occurrence_growth():
    # GIVEN admitted source with only two further record admissions available
    source = _source(_base(b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI"))
    _data, native, values, sources, context, document, pages, cache = source
    maximum = context.consumed["records"] + 2
    context.limits["records"] = maximum
    assert context.remaining("records") == 2, "the operation has exactly the preconsumed remaining allowance"
    # WHEN orchestration reaches a guarded metadata or source growth boundary
    _image_occurrences(native, pages, values, sources, context, document,
                       version=(1, 7), decode_stream=_decode_stream, cache=cache)
    # THEN no fresh budget or payload cache can hide the refusal
    assert context.exhausted[0]["maximum"] == maximum, "the caller's original cumulative record limit governs the pass"
    assert cache == {}, "an unadmitted inline source cannot enter the persistent payload identity map"
    assert context.coverage[(document, "images")]["status"] == "unavailable", "resource exhaustion leaves image coverage open"


def test_historical_form_uses_checked_resources_version_and_reuses_physical_payload():
    # GIVEN retained old/new native views replacing one colour resource of a shared Form
    objects = _base(b"/F Do", b"<< /XObject << /F 5 0 R >> >>")
    objects[5] = _stream(b"BI /W 1 /H 1 /CS /Named /BPC 8 ID abc EI",
                         b"/Type /XObject /Subtype /Form /Resources << /ColorSpace << /Named 6 0 R >> >>")
    objects[6] = b"/DeviceRGB"
    original, old, old_values, old_sources, context, document, old_pages, cache = _source(objects, latest=False)
    revised, _metadata = _table_revision(original, ((6, 0, b"[/Lab << >>]"),))
    new = read_pdf_source(revised, context, document, _decode_stream).view(0)
    new._inspection_id = document
    new_values = {object_id: new.getobj(object_id) for object_id in objects}
    new_sources = {object_id: source | {"location": source["location"] | {"revision": "revision/2"}}
                   for object_id, source in old_sources.items()}
    location = {"kind": "pdf", "object_id": 6, "revision": "revision/2"}
    artifact = context.payload(document, "pdf-object", location, b"[/Lab << >>]", "application/octet-stream")
    new_sources[6] = {"location": location, "component": context.component(document, "pdf-object", location, artifact=artifact),
                      "encoded_artifact": artifact, "decoded_artifact": artifact}
    row_location = new_sources[5]["location"]
    decoded = _decode_stream(new_values[5], context, row_location)
    new_sources[5]["decoded_artifact"] = context.payload(document, "decoded-stream", row_location, decoded, "application/octet-stream")
    new_sources[5]["component"] = context.component(document, "pdf-object-row", row_location, artifact=new_sources[5]["encoded_artifact"])
    new_pages = [(object_id, attrs, {"page": index}) for index, (object_id, attrs) in enumerate(_pages(new, context), 1)]
    assert str(old_values[6].name) == "DeviceRGB", "the historical checked view retains its original resource"
    # WHEN each pass consumes only the matching view and releases working caches afterward
    _image_occurrences(old, old_pages, old_values, old_sources, context, document,
                       version=(1, 7), decode_stream=_decode_stream, cache=cache)
    _image_occurrences(new, new_pages, new_values, new_sources, context, document,
                       version=(2, 0), decode_stream=_decode_stream, cache=cache)
    records = _images(context, "pdf-inline-image-occurrence")
    # THEN revision/resource interpretations differ while one physical encoded payload survives
    assert [(record["location"]["revision"], record["location"].get("page"), record["location"].get("revision_page"))
            for record in records] == [("revision/1", None, 1), ("revision/2", 1, None)], "historical source cannot acquire the latest page provenance"
    assert len(cache) == 1, "repeated physical stream rows reuse one original inline payload identity"
    assert len(_images(context, "pdf-inline-image")) == 1, "revision replay must not duplicate source inventory"
    assert [(record["details"]["effective_version"]) for record in _images(context, "pdf-inline-image-interpretation")] == [[1, 7], [2, 0]], "both checked effective versions must be recorded independently"
    assert context.payloads[records[0]["artifact"]] == b"abc", "historical and current resource interpretations retain the original encoded source bytes"
    assert records[1]["artifact"] == records[0]["artifact"], "shared physical source payload is independent from row component/version identity"
    assert records[1]["location"]["source_segments"][0]["source"]["component"] == new_sources[5]["component"], "reused physical payload still carries the current source row's own component"


def test_inherited_resources_keep_the_declaring_parent_as_owner():
    # GIVEN page Resources declared on the native Pages parent rather than the page
    objects = _base(b"/A Do")
    objects[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /XObject << /A 5 0 R >> >> >>"
    objects[3] = objects[3].replace(b"/Resources << >> ", b"")
    objects[5] = _stream(b"x", b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8")
    assert b"/Resources" not in objects[3], "the page inherits rather than declares its resource dictionary"
    # WHEN the occurrence pass consumes inherited page attributes and original admitted nodes
    context, _document, _sources, _cache = _run(objects)
    # THEN resource provenance names the actual declaring source owner
    assert [item["location"]["resource_owner"]["object_id"] for item in _images(context, "pdf-image-occurrence")] == [2, 2], "both declaration and drawing must retain parent-owned Resources provenance"


def test_valid_inline_before_a_malformed_inline_remains_recoverable():
    # GIVEN one exact source image followed by an independently malformed dictionary
    content = (b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI "
               b"BI /W 1garbage /H 1 /CS /G /BPC 8 ID y EI")
    assert content.count(b"BI") == 2, "the first source image precedes the failing second candidate"
    # WHEN parsing reports an incomplete source extent later in the same stream
    context, document, _sources, _cache = _run(_base(content))
    records = _images(context, "pdf-inline-image-occurrence")
    # THEN admitted earlier source evidence survives without closing affected coverage
    assert [context.payloads[record["artifact"]] for record in records] == [b"x"], "a later malformed image cannot erase a prior proved payload"
    assert context.coverage[(document, "images")]["status"] == "partial", "the malformed remainder must keep image coverage incomplete"


def test_a_bad_page_resource_does_not_erase_a_later_valid_page():
    # GIVEN a malformed first-page Resources and valid second-page inline source
    objects = _base(b"q")
    objects[2] = b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>"
    objects[3] = objects[3].replace(b"/Resources << >>", b"/Resources 17")
    objects[5] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << >> /Contents 6 0 R >>"
    objects[6] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID z EI")
    assert objects[3].count(b"/Resources 17") == 1, "only the first page has the malformed resource declaration"
    # WHEN separate page roots are inspected despite a domain error in one root
    context, document, _sources, _cache = _run(objects)
    # THEN recoverable later source evidence remains present with sticky incomplete coverage
    assert [record["location"]["page"] for record in _images(context, "pdf-inline-image-occurrence")] == [2], "failure in one page must not suppress independent later page content"
    assert context.coverage[(document, "images")]["status"] == "partial", "partial recovery cannot erase the earlier source failure"


def test_form_without_own_resources_uses_page_fallback_without_false_cycles():
    # GIVEN a valid Form using its invocation page's Resources, which declare that Form
    objects = _base(b"/F Do /F Do", b"<< /XObject << /F 5 0 R >> >>")
    objects[5] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI", b"/Type /XObject /Subtype /Form")
    assert b"/Resources" not in objects[5], "the Form deliberately relies on its actual invocation resource context"
    # WHEN fallback resource inventory encounters the already active Form declaration
    context, document, _sources, _cache = _run(objects)
    # THEN a declaration reference is not mistaken for an executing Form cycle
    assert len(_images(context, "pdf-inline-image-occurrence")) == 2, "both real Form invocations retain the inline source"
    assert context.coverage[(document, "images")]["reason"] == "inspection pending", "valid fallback must not create a false incomplete-coverage reason"
    assert [(item["location"]["occurrence_kind"], item["location"].get("resource_name"))
            for item in _images(context, "pdf-form-occurrence")] == [
        ("resource", "F"), ("draw", "F"), ("draw", "F"),
    ], "an inherited declaration is inventoried once while actual invocations remain distinct"


def test_bad_drawing_does_not_erase_a_later_valid_drawing_in_the_same_stream():
    # GIVEN one missing resource followed by an independently valid image invocation
    objects = _base(b"/Missing Do /A Do", b"<< /XObject << /A 5 0 R >> >>")
    objects[5] = _stream(b"x", b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8")
    assert objects[4].count(b"Do") == 2, "the second native drawing is independently recoverable"
    # WHEN a resource lookup fails at the first drawing operator
    context, document, _sources, _cache = _run(objects)
    # THEN later proved occurrences remain visible with the exact source operator ordinal
    assert [(item["location"]["resource_name"], item["location"].get("operator_ordinal"))
            for item in _images(context, "pdf-image-occurrence")] == [("A", None), ("A", 2)], "a malformed earlier operator must not suppress a later admitted drawing"
    assert context.coverage[(document, "images")]["status"] == "partial", "the missing resource must remain a sticky source failure"


def test_real_bounded_inline_filter_and_predictor_keep_original_encoded_payload():
    # GIVEN two grayscale rows encoded by Flate and the PNG-Up predictor
    encoded = zlib.compress(b"\x02ab\x02\x02\x02")
    content = (b"BI /W 2 /H 2 /CS /G /BPC 8 /F /Fl /DP << /Predictor 12 /Columns 2 >> ID "
               + encoded + b" EI Q")
    assert zlib.decompress(encoded) == b"\x02ab\x02\x02\x02", "the source retains its actual predictor row bytes"
    # WHEN the injected existing decoder validates every transformation on the same context
    context, _document, _sources, cache = _run(_base(content))
    records = _images(context, "pdf-inline-image-occurrence")
    # THEN encoded image identity survives instead of being replaced by ab/cd samples
    assert [context.payloads[item["artifact"]] for item in records] == [encoded], "the recoverable image remains its original outer encoded representation"
    assert [sorted(identity) for identity in cache.values()] == [["artifact", "component"]], "persistent cache stores no native/decoded/resource/parsed graph"


def test_malformed_appearance_key_does_not_suppress_an_unselected_valid_key():
    # GIVEN a missing normal appearance and a genuine rollover appearance
    objects = _base(b"")
    objects[3] = objects[3][:-2] + b" /Annots [5 0 R] >>"
    objects[5] = b"<< /Type /Annot /Subtype /Widget /AS /Unused /AP << /N 99 0 R /R 6 0 R >> >>"
    objects[6] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI", b"/Type /XObject /Subtype /Form /Resources << >>")
    assert b"/N 99 0 R /R 6 0 R" in objects[5], "the malformed key precedes the valid unselected AP key"
    # WHEN each AP key is inspected independently of selected visibility or earlier failure
    context, document, _sources, _cache = _run(objects)
    # THEN the valid rollover source remains available and coverage stays explicitly partial
    assert [item["location"]["appearance_key"] for item in _images(context, "pdf-inline-image-occurrence")] == ["R"], "unselected valid keys must survive malformed earlier appearance keys"
    assert context.coverage[(document, "images")]["status"] == "partial", "recovered appearance evidence cannot erase a missing key"


def test_repeated_passes_preserve_exact_records_cache_and_cumulative_cost():
    # GIVEN equal source documents inspected with independent operation contexts
    objects = _base(b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI")
    assert len(objects) == 4, "the deterministic fixture has one native page and one content stream"
    # WHEN both direct passes consume the same admitted source shape
    first, first_document, _sources, first_cache = _run(objects)
    second, second_document, _sources, second_cache = _run(objects)
    # THEN complete finding records, artifact identities and accounting repeat exactly
    assert (first_document, first.findings, first.components, first.artifacts, first.consumed, first_cache) == (
        second_document, second.findings, second.components, second.artifacts, second.consumed, second_cache,
    ), "the image pass must preserve deterministic evidence and cumulative receipt counters"


def test_checked_generation_failure_cannot_be_bypassed_by_an_admitted_image_map():
    # GIVEN a genuine checked reference requesting generation 1 of physical generation 0
    objects = _base(b"/A Do BI /W 1 /H 1 /CS /G /BPC 8 ID x EI", b"<< /XObject << /A 5 1 R >> >>")
    objects[5] = _stream(b"z", b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8")
    data, context = _pdf(objects), InspectionContext()
    document = context.document(data, "checked.pdf", parent=None, location={"kind": "file"})
    physical = read_pdf_source(data, context, document, _decode_stream)
    with physical.view(0) as checked:
        checked._inspection_id = document
        values, sources = {}, {}
        for row in [item for item in physical.rows if item["type"] == 1]:
            occurrence = checked.read_occurrence(row)
            value, location = occurrence["value"], occurrence["location"]
            raw = value.rawdata if isinstance(value, PDFStream) else occurrence["data"]
            artifact = context.payload(document, "pdf-object", location, raw, "application/octet-stream")
            component = context.component(document, "pdf-object", location, data=raw, artifact=artifact)
            values[row["object_id"]] = value
            sources[row["object_id"]] = {"location": location, "component": component,
                                         "encoded_artifact": artifact, "decoded_artifact": artifact}
        reference = values[3]["Resources"]["XObject"]["A"]
        assert (reference.generation, sources[5]["location"]["generation"]) == (1, 0), "source bytes retain the stale requested generation and actual physical generation"
        with pytest.raises(PDFSourceError, match="generation does not match"):
            reference.resolve()
        pages = [(object_id, attrs, {"page": ordinal})
                 for ordinal, (object_id, attrs) in enumerate(_pages(checked, context), 1)]
        # WHEN the pass resolves the real checked references before any admission-map fast path
        _image_occurrences(checked, pages, values, sources, context, document,
                           version=(1, 4), decode_stream=_decode_stream, cache={})
    # THEN stale image uses are refused while unrelated inline source remains recoverable
    assert _images(context, "pdf-image-occurrence") == [], "an object-ID map must not turn a stale generation into a valid resource or draw"
    assert [(item["location"]["page"], item["location"]["object_id"], item["location"]["operator_ordinal"], context.payloads[item["artifact"]])
            for item in _images(context, "pdf-inline-image-occurrence")] == [(1, 4, 2, b"x")], "independent valid content must survive the checked-reference refusal"
    assert context.coverage[(document, "images")]["status"] == "partial", "stale reference evidence must keep image coverage incomplete"
    assert {(item["source_error_location"]["object_id"], item["source_error_location"]["generation"])
            for item in context.coverage[(document, "images")]["locations"]} == {(5, 1)}, "every stale use must retain the checked resolver's exact requested target generation"


def test_missing_first_annotation_preserves_later_page_and_original_ordinal():
    # GIVEN a missing first annotation followed by a real native appearance at ordinal two
    objects = _base(b"")
    objects[3] = objects[3][:-2] + b" /Annots [99 0 R 5 0 R] >>"
    objects[5] = b"<< /Type /Annot /Subtype /Widget /AP << /N 6 0 R >> >>"
    objects[6] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID x EI", b"/Type /XObject /Subtype /Form /Resources << >>")
    assert b"/Annots [99 0 R 5 0 R]" in objects[3], "the genuine later annotation retains source ordinal two"
    # WHEN each annotation root has its own recovery boundary
    context, document, _sources, _cache = _run(objects)
    # THEN later AP evidence retains all available original page/annotation provenance
    assert [(item["location"].get("page"), item["location"].get("annotation_ordinal"),
             item["location"]["annotation_object_id"], item["location"]["appearance_key"],
             item["location"]["dictionary_path"], context.payloads[item["artifact"]])
            for item in _images(context, "pdf-inline-image-occurrence")] == [
        (1, 2, 5, "N", "Page/Annots[2]/AP/N", b"x"),
    ], "orphan recovery cannot substitute for known page and ordinal provenance"
    assert context.coverage[(document, "images")]["status"] == "partial", "the missing earlier annotation remains explicit after later recovery"
    failure = context.coverage[(document, "images")]["locations"][0]
    assert (failure["page"], failure["annotation_ordinal"], failure["annotation_object_id"], failure["dictionary_path"]) == (
        1, 1, 99, "Page/Annots[1]",
    ), "the refused first annotation must keep its own exact page, ordinal and target location"


@pytest.mark.parametrize("space", [b"/G", b"/Good"])
def test_unused_malformed_colour_resource_preserves_unrelated_valid_inline_source(space):
    # GIVEN one native PSKeyword colour object beside a valid built-in or named grayscale space
    objects = _base(b"BI /W 1 /H 1 /CS " + space + b" /BPC 8 ID x EI",
                    b"<< /ColorSpace << /Bad 5 0 R /Good /DeviceGray >> >>")
    objects[5] = b"garbage"
    assert objects[5] == b"garbage", "the malformed source remains a native keyword rather than an invented colour interpretation"
    # WHEN colour interpretation fingerprinting isolates each admitted resource value
    context, document, _sources, _cache = _run(objects)
    # THEN unrelated exact source survives and both affected capabilities remain incomplete
    assert [(item["location"]["page"], item["location"]["object_id"], context.payloads[item["artifact"]])
            for item in _images(context, "pdf-inline-image-occurrence")] == [(1, 4, b"x")], "a malformed unused colour cannot hide a valid independent inline image"
    assert [(context.coverage[(document, capability)]["status"], context.coverage[(document, capability)]["locations"][0]["object_id"])
            for capability in ("images", "inventory")] == [("partial", 5), ("unavailable", 5)], "malformed colour diagnostics must identify original object 5 and follow shared recovered-finding coverage semantics"
    assert context.coverage[(document, "images")]["locations"][0]["dictionary_path"] == "Object/Resources/ColorSpace/Bad", "the diagnostic must retain the exact malformed declaration path"
    assert "invalid native value: PSKeyword" in context.coverage[(document, "images")]["reason"], "native keyword data must be diagnosed rather than interpreted as a colour space"


def test_selected_malformed_colour_is_not_replaced_by_an_unrelated_valid_space():
    # GIVEN a selected malformed named colour and a separate valid Gray declaration
    objects = _base(b"BI /W 1 /H 1 /CS /Bad /BPC 8 ID x EI",
                    b"<< /ColorSpace << /Bad 5 0 R /Good /DeviceGray >> >>")
    objects[5] = b"garbage"
    assert b"/CS /Bad" in objects[4], "the source explicitly selects the malformed declaration"
    # WHEN the bad native value is refused independently of the valid sibling resource
    context, document, _sources, cache = _run(objects)
    # THEN no artifact or interpretation may be invented for the selected bad space
    assert _images(context, "pdf-inline-image-occurrence") == [], "an unrelated valid colour resource cannot justify this image's extent"
    assert cache == {}, "an unproved selected colour cannot retain an exact payload identity"
    assert (context.coverage[(document, "images")]["status"], "ColorSpace resource" in context.coverage[(document, "images")]["reason"]) == (
        "unavailable", True,
    ), "selected malformed colour evidence remains explicitly incomplete"


@pytest.mark.parametrize("glyph_count", [2, 18])
def test_type3_sibling_declarations_keep_aliases_pages_and_inherited_resources_without_recursion(glyph_count):
    # GIVEN a Type3 font whose sibling glyphs inherit resources declaring that same font twice
    inline = b"BI /W 1 /H 1 /CS /G /BPC 8 ID p EI"
    objects = _base(inline, b"<< /Font << /F 5 0 R /Alias 5 0 R >> >>")
    procedures = b" ".join(("/G%d %d 0 R" % (index, index + 6)).encode() for index in range(glyph_count))
    objects[5] = b"<< /Type /Font /Subtype /Type3 /CharProcs << " + procedures + b" >> >>"
    for index in range(glyph_count):
        objects[index + 6] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID " + bytes((65 + index,)) + b" EI")
    last_page = glyph_count + 6
    objects[last_page] = objects[3]
    objects[2] = ("<< /Type /Pages /Kids [3 0 R %d 0 R] /Count 2 >>" % last_page).encode()
    assert objects[5].count(b"/Type3") == 1, "one actual font declaration must contain all distinct sibling glyph sources"
    # WHEN declaration recursion distinguishes the same active inherited font from actual content invocations
    context, document, _sources, _cache = _run(objects)
    declarations = _images(context, "pdf-form-occurrence")
    # THEN each alias/page/glyph declaration survives once and later actual page inline uses remain visible
    assert ([(item["location"]["page"], item["location"]["resource_name"], item["location"]["object_id"],
              item["location"]["dictionary_path"]) for item in declarations],
            [(item["location"]["page"], context.payloads[item["artifact"]])
             for item in _images(context, "pdf-inline-image-occurrence")],
            len(_images(context, "pdf-inline-image")), context.exhausted,
            context.coverage[(document, "images")]["reason"]) == (
        [(page, "G%d" % index, index + 6, "Object/Resources/Font/%s/CharProcs/G%d" % (alias, index))
         for page in (1, 2) for alias in ("F", "Alias") for index in range(glyph_count)],
        [(1, b"p"), (2, b"p")], glyph_count + 1, [], "inspection pending",
    ), "revisiting inherited font declarations must not multiply sibling source occurrences or exhaust nesting"


def test_distinct_type3_fonts_share_inherited_resource_declarations_without_cross_enumeration():
    # GIVEN two genuine sibling fonts whose glyphs inherit the same page resource dictionary
    objects = _base(b"", b"<< /Font << /F 5 0 R /G 6 0 R >> >>")
    objects[5] = b"<< /Type /Font /Subtype /Type3 /CharProcs << /A 7 0 R >> >>"
    objects[6] = b"<< /Type /Font /Subtype /Type3 /CharProcs << /B 8 0 R >> >>"
    objects[7] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID a EI")
    objects[8] = _stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID b EI")
    assert objects[5] != objects[6], "the source must declare two distinct fonts and glyph streams"
    # WHEN each inherited declaration bundle is enumerated through its original outer loop
    context, _document, _sources, _cache = _run(objects)
    # THEN one font cannot re-enumerate its distinct sibling's already-active resource bundle
    assert [(item["location"]["page"], item["location"]["object_id"], item["location"]["dictionary_path"])
            for item in _images(context, "pdf-form-occurrence")] == [
        (1, 7, "Object/Resources/Font/F/CharProcs/A"), (1, 8, "Object/Resources/Font/G/CharProcs/B"),
    ], "source declarations must stay independent from artificial nesting between sibling fonts"
