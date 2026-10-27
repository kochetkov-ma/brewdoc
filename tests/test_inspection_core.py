"""Operation-wide inspection accounting and deterministic artifact contracts."""

import hashlib
import io
import json
import zipfile

import pytest

from brewdoc import inspection
from brewdoc._inspection_common import CAPABILITIES, InspectionContext, InspectionLimit
from brewdoc.common import BrewdocError, _zip_member_bytes
from test_spreadsheets import formula_book


def _evidence_scan(data, name, context, document):
    """Supply a format-independent source fixture for orchestration tests."""
    location = {"kind": "source", "byte_start": 0, "byte_end": len(data)}
    artifact = context.payload(document, "script-source", location, data, "text/plain")
    component = context.component(document, "source", location, data=data, artifact=artifact)
    context.finding(document, "scripts", "inline-script", location, component=component,
                    text=data.decode("utf-8"), artifact=artifact)
    for capability in CAPABILITIES:
        context.cover(document, capability, "complete", "synthetic source exhaustively scanned")


def test_inspection_report_and_payload_artifacts_round_trip_deterministically(tmp_path, monkeypatch):
    # GIVEN literal source bytes and one deterministic scanner boundary
    source = tmp_path / "source.txt"
    payload = "literal('café');\r\n```\n".encode("utf-8")
    source.write_bytes(payload)
    monkeypatch.setattr(inspection, "_dispatch", _evidence_scan)
    assert source.read_bytes() == payload, "source precondition must preserve every input byte"
    # WHEN independent inspection and retrieval repeat through public entry points
    first = inspection.inspect_document(source)
    second = inspection.inspect_document(source)
    references = inspection.list_document_artifacts(source)
    report_bytes = inspection.read_document_artifact(source, "inspection/report")
    extracted = inspection.read_document_artifact(source, "inspection/payload/000001")
    # THEN reports, identities and exact payloads are stable without self-referential hashes
    assert (first, json.loads(report_bytes), extracted, [item.key for item in references],
            references[0].byte_size, references[0].sha256) == (
        second, first, payload, ["inspection/report", "inspection/payload/000001"],
        len(report_bytes), hashlib.sha256(report_bytes).hexdigest(),
    ), "report serialization and payload retrieval must describe the same immutable evidence"
    assert first["source"] == {
        "name": "source.txt", "suffix": ".txt", "kind": "local", "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }, "source identity must not contain temporary directory names or runtime state"


def test_nested_inspection_detects_ancestry_cycles_and_preserves_sibling_occurrences(tmp_path, monkeypatch):
    # GIVEN two identical sibling documents, each containing a reference to its ancestor
    source = tmp_path / "root.txt"
    source.write_bytes(b"root")

    def nested(data, name, context, document):
        children = ((b"child", "child.txt"), (b"child", "child.txt")) if data == b"root" else (
            (b"root", "cycle.txt"),)
        for ordinal, (child, label) in enumerate(children, 1):
            inspection._scan(child, label, context, parent=document,
                             location={"kind": "embedded", "ordinal": ordinal})
        for capability in CAPABILITIES:
            context.cover(document, capability, "complete", "synthetic container scanned")

    monkeypatch.setattr(inspection, "_dispatch", nested)
    assert source.read_bytes() == b"root", "the root identity must match the recursive fixture"
    # WHEN traversal detects active ancestry without globally deduplicating hashes
    report = inspection.inspect_document(source)
    # THEN both sibling occurrences and both explicitly incomplete cycle boundaries survive
    assert [(item["id"], item["parent"], item["name"]) for item in report["documents"]] == [
        ("document/000001", None, "root.txt"),
        ("document/000002", "document/000001", "child.txt"),
        ("document/000003", "document/000002", "cycle.txt"),
        ("document/000004", "document/000001", "child.txt"),
        ("document/000005", "document/000004", "cycle.txt"),
    ], "sibling equality must not be mistaken for an ancestry cycle"
    assert [(item["document"], item["status"], item["reason"]) for item in report["coverage"]
            if item["capability"] == "inventory"] == [
        ("document/000001", "complete", "synthetic container scanned"),
        ("document/000002", "complete", "synthetic container scanned"),
        ("document/000003", "unavailable", "embedded document ancestry cycle"),
        ("document/000004", "complete", "synthetic container scanned"),
        ("document/000005", "unavailable", "embedded document ancestry cycle"),
    ], "cycle boundaries must never become complete empty inspections"


@pytest.mark.parametrize("counter,maximum", [("records", 4), ("retained_bytes", 3),
                                               ("decoded_bytes", 3)])
def test_cumulative_limits_refuse_before_growth_and_keep_failures_sticky(counter, maximum):
    # GIVEN one operation with a smaller shared cumulative allowance
    context = InspectionContext(limits={counter: maximum})
    document = context.document(b"source", "source.txt", parent=None, location={"kind": "source"})
    amount = context.remaining(counter)
    assert amount <= maximum, "document admission must use the same cumulative allowance"
    context.charge(counter, amount, {"kind": "first-child"})
    # WHEN a second nested consumer attempts to exceed the exhausted allowance
    with pytest.raises(InspectionLimit, match="inspection " + counter + " limit exceeded"):
        context.charge(counter, 1, {"kind": "second-child"})
    context.problem(document, ("inventory",), "second child exceeded " + counter,
                    {"kind": "second-child"})
    context.cover(document, "inventory", "complete", "later child scanned")
    # THEN failure coverage and exact boundary survive later successful updates
    assert (context.consumed[counter], context.coverage[(document, "inventory")],
            context.exhausted) == (
        maximum,
        {"document": document, "capability": "inventory", "status": "unavailable",
         "reason": "second child exceeded " + counter, "locations": [{"kind": "second-child"}]},
        [{"limit": counter, "maximum": maximum, "location": {"kind": "second-child"}}],
    ), "an exhausted nested budget must remain explicit without charging the refused byte or record"


def test_snapshot_provenance_is_serialized_inside_the_saved_report(tmp_path, monkeypatch):
    # GIVEN an acquired snapshot identity before any evidence serialization
    source = tmp_path / "snapshot.txt"
    source.write_bytes(b"snapshot")
    provenance = {"kind": "static_html", "sha256": hashlib.sha256(b"snapshot").hexdigest(),
                  "bytes": 8, "url": "https://snapshot.example.invalid/original"}
    monkeypatch.setattr(inspection, "_dispatch", _evidence_scan)
    assert source.stat().st_size == provenance["bytes"], "provenance must describe the inspected bytes"
    # WHEN the internal conversion boundary saves the report artifact
    report, payloads = inspection._inspect_document(source, source_provenance=provenance)
    # THEN both the in-memory report and saved JSON identify the inspected snapshot
    assert (report["source"], json.loads(payloads["inspection/report"])["source"]) == (
        {"name": "snapshot.txt", "suffix": ".txt", "kind": "snapshot", "byte_size": 8,
         "sha256": provenance["sha256"], "provenance": provenance},
        report["source"],
    ), "receipt-only provenance cannot substitute for source identity inside the saved artifact"


def test_report_budget_exhaustion_retains_bounded_diagnostics(tmp_path, monkeypatch):
    # GIVEN a scanner attempting a finding larger than the remaining report allowance
    source = tmp_path / "bounded.txt"
    source.write_bytes(b"bounded")

    def oversized(data, name, context, document):
        context.finding(document, "scripts", "oversized", {"kind": "script"}, text="x" * 20_000)

    monkeypatch.setattr(inspection, "_dispatch", oversized)
    assert source.read_bytes() == b"bounded", "the refusal must arise from report growth, not source input"
    # WHEN admission checks reject the finding before appending it
    report, payloads = inspection._inspect_document(source, _limits={"report_bytes": 16_384})
    # THEN a usable report describes the exact failure instead of returning complete empty output
    assert (len(payloads["inspection/report"]) <= 16_384, report["findings"],
            report["limits"]["exhausted"], {item["status"] for item in report["coverage"]}) == (
        True, [], [{"limit": "report_bytes", "maximum": 14_336,
                    "location": {"kind": "script"}}], {"unavailable"},
    ), "reserved diagnostics must survive an exhausted report admission budget"


def test_diagnostic_fallback_keeps_payload_inventory_and_retrieval_consistent(tmp_path, monkeypatch):
    # GIVEN retained payloads followed by failure diagnostics that need the reserved report space
    source = tmp_path / "fallback.txt"
    source.write_bytes(b"retained")

    def diagnostics(data, name, context, document):
        _evidence_scan(data, name, context, document)
        for capability in CAPABILITIES:
            context.problem(document, (capability,), "failure " + "x" * 1800,
                            {"kind": "boundary", "detail": "y" * 500})

    monkeypatch.setattr(inspection, "_dispatch", diagnostics)
    assert source.read_bytes() == b"retained", "source must permit payload extraction before the failure"
    # WHEN fallback emits a compact explicit failure report
    report, payloads = inspection._inspect_document(source, _limits={"report_bytes": 8192})
    # THEN no retained payload can be retrieved without a matching listed artifact
    assert (len(payloads["inspection/report"]) <= 8192, sorted(payloads), report["artifacts"],
            report["limits"]["exhausted"], {row["status"] for row in report["coverage"]}) == (
        True, ["inspection/report"], [],
        [{"limit": "report_bytes", "maximum": 8192, "location": {"kind": "report"},
          "omitted_records": {"documents": 0, "findings": 1, "components": 1, "artifacts": 1}}],
        {"unavailable"},
    ), "truncation must remain explicit and must not create unlisted retrievable artifacts"


def test_root_admission_failure_still_identifies_the_uninspected_source(tmp_path, monkeypatch):
    # GIVEN a root source whose operation permits no record admission
    source = tmp_path / "zero-records.txt"
    source.write_bytes(b"uninspected")
    monkeypatch.setattr(inspection, "_dispatch", _evidence_scan)
    assert source.read_bytes() == b"uninspected", "the source must exist before resource refusal"
    # WHEN source inventory cannot enter the normal record allowance
    report, payloads = inspection._inspect_document(source, _limits={"records": 0})
    # THEN coherent emergency identity and unavailable capabilities survive without a fake success
    assert (report["documents"], {row["document"] for row in report["coverage"]},
            {row["status"] for row in report["coverage"]}, sorted(payloads)) == (
        [{"id": "document/000001", "parent": None, "component": None,
          "name": "zero-records.txt", "suffix": ".txt", "byte_size": 11,
          "sha256": hashlib.sha256(b"uninspected").hexdigest(), "location": {"kind": "source"}}],
        {"document/000001"}, {"unavailable"}, ["inspection/report"],
    ), "failure diagnostics must refer to an actual source inventory entry"


@pytest.mark.parametrize("suffix,reason", [
    (".pdf", "PDF signature is missing"),
    (".xls", "CFB signature is missing"),
    (".xlsx", "package signature does not match .xlsx"),
])
def test_malformed_supported_sources_cannot_produce_complete_empty_reports(tmp_path, suffix, reason):
    # GIVEN supported suffixes carrying bytes that cannot be their declared source format
    source = tmp_path / ("malformed" + suffix)
    source.write_bytes(b"invalid source")
    assert source.stat().st_size == 14, "the malformed source must still have a stable byte identity"
    # WHEN static inspection validates source structure before invoking a format reader
    report = inspection.inspect_document(source)
    # THEN every capability remains explicitly uninspected at the original source boundary
    assert (report["source"]["sha256"], report["components"], report["findings"],
            {(row["status"], row["reason"]) for row in report["coverage"]}) == (
        hashlib.sha256(b"invalid source").hexdigest(), [], [],
        {("unavailable", "malformed%s inspection stopped: BrewdocError: %s" % (suffix, reason))},
    ), "a parser refusal must never be represented as complete empty extraction"


def test_public_report_matches_saved_json_for_html_attribute_occurrences(tmp_path):
    # GIVEN original duplicate attributes and form state represented by ordered token pairs
    source = tmp_path / "attributes.html"
    source.write_bytes(b'<form action="javascript:send()"><input name="original" value="hidden-value"></form>')
    assert source.read_bytes().count(b"hidden-value") == 1, "the control default must exist in source bytes"
    # WHEN the public dict and saved report independently describe native attribute occurrences
    report = inspection.inspect_document(source)
    saved = json.loads(inspection.read_document_artifact(source, "inspection/report"))
    # THEN the returned report already contains only JSON types, including nested attribute lists
    assert report == saved, "ordered attribute tuples must be normalized before returning the public report"


def test_sheet_selection_keeps_unselected_hidden_values_and_formulas_in_normal_supplement(tmp_path):
    # GIVEN a hidden source sheet with numeric data and a formula outside readable selection
    source = formula_book(tmp_path / "hidden.xlsx")
    with zipfile.ZipFile(source) as archive:
        members = [(item.filename, archive.read(item)) for item in archive.infolist()]
    rewritten = io.BytesIO()
    with zipfile.ZipFile(rewritten, "w") as archive:
        for part, data in members:
            archive.writestr(part, data.replace(b'<sheet name="Calc"', b'<sheet state="hidden" name="Calc"'))
    source.write_bytes(rewritten.getvalue())
    report = inspection.inspect_document(source)
    cells = [item for item in report["findings"] if item["kind"] == "cell"
             and item["location"]["qualified_cell"] == "'Calc'!B1"]
    assert [(item["details"]["value"], item["details"]["formula"]) for item in cells] == [
        (2.0, "Inputs!A1*2")], "the hidden cell must have genuine source value and formula evidence"
    # WHEN normal service conversion selects only the first readable sheet
    from brewdoc import run

    code, _receipt, markdown = run(source, sheets=["Inputs"])
    # THEN structured independent findings expose the hidden source value beside its original address
    assert (code, '"qualified_cell":"\'Calc\'!B1"' in markdown,
            '"value":2.0' in markdown, '"formula":"Inputs!A1*2"' in markdown) == (
        0, True, True, True,
    ), "sheet selection must not hide independently extracted structured values or formulas"


def test_normal_supplement_exposes_form_defaults_and_named_expression_metadata():
    # GIVEN structured source findings whose useful values live below the common finding envelope
    context = InspectionContext()
    document = context.document(b"source", "source.html", parent=None, location={"kind": "source"})
    context.finding(document, "forms", "form-control", {"kind": "html"},
                    attributes=[("name", "original-control"), ("value", "<script>literal()</script>")])
    context.finding(document, "names", "defined-name", {"kind": "workbook"},
                    name="OriginalName", expression="'Hidden Sheet'!AA100")
    report = context.report({"name": "source.html"})
    assert len(report["findings"]) == 2, "both complete structured findings must reach the supplement"
    # WHEN original source findings are projected as normal inert Markdown
    markdown = inspection.inspection_markdown(report)
    # THEN control defaults and named expressions are visible inside literal JSON blocks
    assert ('"name":"OriginalName"' in markdown, '"expression":"\'Hidden Sheet\'!AA100"' in markdown,
            '["name","original-control"]' in markdown,
            '["value","<script>literal()</script>"]' in markdown) == (True, True, True, True), (
        "normal supplementary output must include useful structured evidence without evaluating its values")


def test_nested_zip_limits_are_reported_with_shared_consumption_and_exhaustion():
    # GIVEN separate nested archives sharing one physical-read allowance
    context = InspectionContext()
    document = context.document(b"source", "source.docx", parent=None, location={"kind": "source"})
    context.zip_budget.limits["consumed_bytes"] = 3
    first, second = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(first, "w") as archive:
        archive.writestr("first.txt", b"ab")
    with zipfile.ZipFile(second, "w") as archive:
        archive.writestr("second.txt", b"cd")
    with zipfile.ZipFile(first) as archive:
        admitted = _zip_member_bytes(archive, "first.txt", context.zip_budget)
    assert admitted == b"ab", "the first nested archive must consume the shared allowance"
    # WHEN another archive would cross the same cumulative bound
    with zipfile.ZipFile(second) as archive:
        with pytest.raises(BrewdocError, match="consumed_bytes limit exceeded"):
            _zip_member_bytes(archive, "second.txt", context.zip_budget)
    report = context.report({"name": "source.docx"})
    # THEN public resource evidence includes the exact ZIP allowance, consumed count and boundary
    assert (report["limits"]["configured"].get("zip_xml", {}).get("consumed_bytes"),
            report["limits"]["consumed"].get("zip_xml", {}).get("consumed_bytes"),
            report["limits"]["exhausted"]) == (
        3, 2, [{"limit": "zip.consumed_bytes", "maximum": 3,
                "location": {"kind": "zip_xml", "part": "second.txt"}}],
    ), "an inherited ZIP/XML refusal must be visible in standardized cumulative inspection evidence"


def _odf_member(context, parent, prefix="Object 1/", ordinal=7):
    """Retain one physical package member before admitting any logical child source."""
    data = b'<office:spreadsheet xmlns:office="urn:synthetic"><cell>42</cell></office:spreadsheet>'
    part = prefix + "content.xml"
    location = {"kind": "package", "part": part, "member_ordinal": ordinal}
    artifact = context.payload(parent, "package-member", location, data, "application/xml")
    component = context.component(parent, "package-member", location, data=data, artifact=artifact)
    descriptor = {"original_part": part, "member_ordinal": ordinal, "component": component,
                  "artifact": artifact, "byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                  "reason": None, "manifest_locations": [{"part": "META-INF/manifest.xml", "xpath": "/manifest/file-entry[2]"}]}
    return ((ordinal, part, data),), (descriptor,), component


@pytest.mark.parametrize("media_type,suffix,overlays,formula_status", [
    ("application/vnd.oasis.opendocument.spreadsheet", ".ods", 1, "unavailable"),
    ("application/vnd.oasis.opendocument.text", ".odt", 0, "not_applicable"),
    ("application/vnd.oasis.opendocument.presentation", ".odp", 0, "not_applicable"),
])
def test_logical_odf_source_uses_original_members_and_declared_type_without_new_bytes(
        monkeypatch, media_type, suffix, overlays, formula_status):
    # GIVEN admitted original members with actual identities but no contiguous child file
    from brewdoc import _inspection_package, _inspection_workbook

    context = InspectionContext()
    parent = context.document(b"parent bytes", "parent.odt", parent=None, location={"kind": "source"})
    members, source_members, component = _odf_member(context, parent)
    retained = context.consumed["retained_bytes"]
    calls = []
    workbook_calls = []
    embedded_callbacks = []

    def scoped(original_members, name, current, document, **options):
        calls.append(("package", original_members, name, current, document, options["prefix"]))
        embedded_callbacks.append(options["scan_embedded"])
        return original_members

    def workbook(data, name, current, document, **options):
        calls.append(("workbook", data, name, current, document, options))
        workbook_calls.append((data, name, current, document, options))
        current.finding(document, "cells", "cell", {
            "kind": "workbook", "part": members[0][1], "member_ordinal": 7,
            "qualified_cell": "'Sheet1'!C5"}, value=42.0)
        current.cover(document, "cells", "complete", "native source metadata recovered")

    monkeypatch.setattr(_inspection_package, "inspect_package_members", scoped, raising=False)
    monkeypatch.setattr(_inspection_workbook, "inspect_workbook", workbook)
    assert context.payloads[source_members[0]["artifact"]] == members[0][2], (
        "physical source identity must exist before logical document admission")
    # WHEN the logical child routes through generic members and the native-type overlay
    location = {"kind": "package", "part": "META-INF/manifest.xml", "member_ordinal": 38,
                "xpath": "/manifest/file-entry[2]"}
    child = inspection._scan_parts("Object 1/", media_type, members, context, parent=parent,
                                    location=location, component=component,
                                    source_members=source_members, prefix="Object 1/")
    # THEN source hashes stay attached to original occurrences, with no reconstructed ZIP identity
    assert context.documents[-1] == {
        "id": child, "parent": parent, "component": component, "name": "Object 1/",
        "suffix": suffix, "representation": "odf-directory", "media_type": media_type,
        "byte_size": None, "sha256": None, "location": location,
        "source_members": list(source_members),
    }, "logical ODF identity must preserve declared type and exact original member descriptors"
    assert (context.consumed["documents"], context.consumed["retained_bytes"],
            context.zip_budget.consumed_bytes, [item[0] for item in calls],
            context.active_hashes) == (
        2, retained, 0, ["package"] + ["workbook"] * overlays, [],
    ), "logical routing must share allowances without retaining, decoding or hashing invented bytes"
    assert workbook_calls == [(None, "Object 1/", context, child, {
        "members": members, "prefix": "Object 1/", "contiguous_source": False,
        "media_type": media_type, "scan_embedded": embedded_callbacks[0]})] * overlays, (
        "native workbook routing must explicitly disable a nonexistent contiguous source cache")
    assert [item[-1]["scan_embedded"] is embedded_callbacks[0] for item in workbook_calls] == [True] * overlays, (
        "package and workbook consumers must receive the identical operation callback")
    assert context.coverage[(child, "formulas")]["status"] == formula_status, (
        "a directory name without an extension must not erase declared native workbook applicability")
    assert callable(embedded_callbacks[0]) is True, "the shared recursion handoff must be callable"
    forwarded = []

    def byte_child(data, name, current, **options):
        forwarded.append((data, name, current, options))
        return "document/callback-probe"

    monkeypatch.setattr(inspection, "_scan", byte_child)
    returned = embedded_callbacks[0](b"child bytes", "child.txt", context, parent=child,
                                      location={"kind": "callback-probe"}, component=component)
    assert (returned, forwarded) == ("document/callback-probe", [
        (b"child bytes", "child.txt", context, {"parent": child,
         "location": {"kind": "callback-probe"}, "component": component})]), (
        "the callback must forward child bytes and provenance inside the original shared context")


def test_logical_odf_scopes_keep_siblings_distinct_and_stop_shared_ancestry_cycles(monkeypatch):
    # GIVEN equal physical payload bytes stored at two different original member occurrences
    context = InspectionContext()
    parent = context.document(b"parent", "parent.odt", parent=None, location={"kind": "source"})
    first = _odf_member(context, parent, "Object 1/", 7)
    second = _odf_member(context, parent, "Object 2/", 12)
    media_type = "application/vnd.oasis.opendocument.spreadsheet"

    def recurse(data, name, current, document, **options):
        inspection._scan_parts(name, media_type, options["members"], current, parent=document,
                               location={"kind": "nested-scope"}, component=None,
                               source_members=options["source_members"], prefix=options["prefix"])

    monkeypatch.setattr(inspection, "_dispatch", recurse)
    assert first[1][0]["sha256"] == second[1][0]["sha256"], "the sibling source bytes must be identical"
    # WHEN each sibling independently attempts to revisit its own active physical member scope
    for prefix, (members, descriptors, component) in (("Object 1/", first), ("Object 2/", second)):
        inspection._scan_parts(prefix, media_type, members, context, parent=parent,
                               location={"kind": "manifest", "prefix": prefix}, component=component,
                               source_members=descriptors, prefix=prefix)
    # THEN both sibling occurrences survive, while only their active descendant revisits are cycles
    assert [(item["name"], item["parent"]) for item in context.documents] == [
        ("parent.odt", None), ("Object 1/", parent), ("Object 1/", "document/000002"),
        ("Object 2/", parent), ("Object 2/", "document/000004"),
    ], "logical sibling occurrences must never be deduplicated by matching payload bytes"
    assert [(row["document"], row["status"], row["reason"]) for row in context.coverage.values()
            if row["capability"] == "inventory" and "cycle" in row["reason"]] == [
        ("document/000003", "unavailable", "embedded ODF member-scope ancestry cycle"),
        ("document/000005", "unavailable", "embedded ODF member-scope ancestry cycle"),
    ], "scope cycle failures must remain separate from valid sibling admission"


def test_logical_odf_document_admission_exhausts_the_parent_operation_budget(monkeypatch):
    # GIVEN a parent operation allowing only itself and one logical child
    context = InspectionContext(limits={"documents": 2})
    parent = context.document(b"parent", "parent.odt", parent=None, location={"kind": "source"})
    members, descriptors, component = _odf_member(context, parent)
    monkeypatch.setattr(inspection, "_dispatch", lambda *_args, **_kwargs: None)
    media_type = "application/vnd.oasis.opendocument.spreadsheet"
    first = inspection._scan_parts("Object 1/", media_type, members, context, parent=parent,
                                   location={"kind": "first-scope"}, component=component,
                                   source_members=descriptors, prefix="Object 1/")
    assert first == "document/000002", "the first logical child must use the existing parent allowance"
    # WHEN another sibling tries to obtain a fresh document allowance
    with pytest.raises(InspectionLimit, match="inspection documents limit exceeded: 2"):
        inspection._scan_parts("Object 2/", media_type, members, context, parent=parent,
                               location={"kind": "second-scope"}, component=component,
                               source_members=descriptors, prefix="Object 2/")
    # THEN no fabricated source or independent counter can bypass the shared limit
    assert (context.consumed["documents"], len(context.documents), context.exhausted) == (
        2, 2, [{"limit": "documents", "maximum": 2, "location": {"kind": "second-scope"}}],
    ), "logical ODF children must share the exact root document budget"


def test_logical_odf_admission_does_not_leave_partially_initialized_coverage(monkeypatch):
    # GIVEN enough remaining report capacity for a child record but not its capability inventory
    context = InspectionContext()
    parent = context.document(b"parent", "parent.odt", parent=None, location={"kind": "source"})
    members, descriptors, component = _odf_member(context, parent)
    before = (list(context.documents), list(context.coverage))
    context.limits["report_bytes"] = context.consumed["report_bytes"] + context._reserve + 2048
    monkeypatch.setattr(inspection, "_dispatch", lambda *_args, **_kwargs: pytest.fail("unadmitted child scanned"))
    assert len(context.documents) == 1, "only the admitted root must exist before the resource boundary"
    # WHEN logical source admission cannot reserve all its required coverage rows
    with pytest.raises(InspectionLimit, match="inspection report_bytes limit exceeded"):
        inspection._scan_parts("Object 1/", "application/vnd.oasis.opendocument.spreadsheet",
                               members, context, parent=parent, location={"kind": "child-admission"},
                               component=component, source_members=descriptors, prefix="Object 1/")
    # THEN the refused logical source leaves no dangling record or pending capability placeholders
    assert (context.documents, list(context.coverage)) == before, (
        "source record and full capability inventory must enter the operation atomically")


def test_supplementary_markdown_keeps_scripts_and_destinations_inert():
    # GIVEN a source script containing fences and an executable-looking destination
    context = InspectionContext()
    document = context.document(b"literal", "literal.html", parent=None, location={"kind": "source"})
    script = "alert('literal');\n```\n<script>original()</script>"
    context.finding(document, "scripts", "inline-script", {"kind": "html"}, text=script)
    context.finding(document, "links", "href", {"kind": "html"}, target="javascript:original()")
    report = context.report({"name": "literal.html"})
    assert len(report["findings"]) == 2, "both source findings must reach the passive projection"
    # WHEN findings become normal supplementary Markdown
    markdown = inspection.inspection_markdown(report)
    # THEN variable fences preserve script bytes and destinations stay code literals
    assert markdown == (
        'Source inspection findings. Omission notices describe the readable projection.\n'
        '\n## Links\n\n- ` href ` at ` {"kind":"html"} `\n'
        '  Target: ` javascript:original() `\n\n## Active content\n\n'
        '- ` inline-script ` at ` {"kind":"html"} `\n\n````text\n'
        + script + '\n````\n'
    ), "a source fence must not close its literal block or create an executable destination"
