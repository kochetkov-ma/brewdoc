"""Independent static inspection, deterministic evidence and passive Markdown."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from brewdoc._inspection_common import (CAPABILITIES, INSPECTION_SCHEMA, ODF_MEDIA_SUFFIXES, REPORT_KEY,
                                         InspectionContext, InspectionLimit, json_bytes)
from brewdoc.common import ArtifactRef, BrewdocError

SUPPORTED_SUFFIXES = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".xls",
                                ".ods", ".odt", ".odp", ".html", ".epub", ".eml", ".md", ".txt"))
PACKAGE_SUFFIXES = frozenset((".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt",
                              ".odp", ".epub"))
WORKBOOK_SUFFIXES = frozenset((".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"))
SUPPLEMENT_SCOPE = "Source inspection findings. Omission notices describe the readable projection."


def _dispatch(data: bytes | None, name: str, context: InspectionContext, document: str, *,
              members: tuple | None = None, prefix: str = "", source_members=(),
              media_type: str | None = None) -> None:
    """Route original bytes; callbacks retain the same operation and ancestry."""
    from brewdoc._inspection_html import inspect_html, inspect_plain_targets, inspect_text

    def embedded(child_data: bytes, child_name: str, child_context=None, *,
                 parent: str, location: dict, component: str | None = None) -> str:
        """Admit only supported child documents under this operation's existing limits."""
        if child_context is not None and child_context is not context:
            raise ValueError("embedded inspection must share its parent context")
        if Path(child_name).suffix.lower() not in SUPPORTED_SUFFIXES:
            return ""
        return _scan(child_data, child_name, context, parent=parent,
                     location=location, component=component)

    def html(source: bytes, source_name: str, child_context, child_document: str, *,
             location: dict | None = None) -> None:
        """Keep nested payload provenance and callbacks inside the same operation."""
        if child_context is not context:
            raise ValueError("HTML inspection must share its parent context")
        if location is None:
            location = next(item["location"] for item in context.documents if item["id"] == child_document)
        inspect_html(source, source_name, context, child_document,
                     location=location, scan_embedded=embedded)

    def embedded_members(child_name: str, child_media_type: str, child_members: tuple, *,
                         parent: str, location: dict, component: str | None,
                         source_members, prefix: str) -> str:
        """Inspect original admitted member scopes without assembling a replacement file."""
        return _scan_parts(child_name, child_media_type, child_members, context, parent=parent,
                           location=location, component=component,
                           source_members=source_members, prefix=prefix)

    suffix = ODF_MEDIA_SUFFIXES[media_type] if media_type is not None else Path(name).suffix.lower()
    if members is not None:
        from brewdoc._inspection_package import inspect_package_members

        scoped = inspect_package_members(
            members, name, context, document, scan_html=html, scan_embedded=embedded,
            scan_embedded_members=embedded_members, prefix=prefix,
            source_members=source_members, media_type=media_type)
        if suffix == ".ods":
            from brewdoc._inspection_workbook import inspect_workbook

            inspect_workbook(None, name, context, document, members=scoped, prefix=prefix,
                             contiguous_source=False, media_type=media_type, scan_embedded=embedded)
        return
    if suffix == ".pdf":
        from brewdoc._inspection_pdf import inspect_pdf

        inspect_pdf(data, name, context, document, scan_embedded=embedded)
    elif suffix in PACKAGE_SUFFIXES:
        from brewdoc._inspection_package import inspect_package

        members = inspect_package(data, name, context, document,
                                  scan_html=html, scan_embedded=embedded,
                                  scan_embedded_members=embedded_members)
        if suffix in WORKBOOK_SUFFIXES:
            from brewdoc._inspection_workbook import inspect_workbook

            inspect_workbook(data, name, context, document, members=members, scan_embedded=embedded)
    elif suffix == ".xls":
        from brewdoc._inspection_workbook import inspect_workbook

        inspect_workbook(data, name, context, document, scan_embedded=embedded)
    elif suffix == ".html":
        html(data, name, context, document)
    elif suffix == ".eml":
        from brewdoc._inspection_mail import inspect_mail

        inspect_mail(data, name, context, document,
                     scan_html=html, scan_embedded=embedded,
                     scan_text=inspect_plain_targets)
    else:
        inspect_text(data, name, context, document, scan_embedded=embedded)


def _scan_parts(name: str, media_type: str, members: tuple, context: InspectionContext, *,
                parent: str, location: dict, component: str | None,
                source_members, prefix: str) -> str:
    """Inspect one logical ODF source under the existing physical-byte operation."""
    source_members = tuple(source_members)
    document = context.document_parts(name, media_type, parent=parent, location=location,
                                      component=component, source_members=source_members)
    scope = "odf-directory:" + hashlib.sha256(json_bytes({
        "media_type": media_type, "source_members": source_members})).hexdigest()
    try:
        context.check("nesting", len(context.active_hashes), location)
        if scope in context.active_hashes:
            context.problem(document, CAPABILITIES, "embedded ODF member-scope ancestry cycle", location)
            return document
        context.active_hashes.append(scope)
        try:
            _dispatch(None, name, context, document, members=members, prefix=prefix,
                      source_members=source_members, media_type=media_type)
        finally:
            context.active_hashes.pop()
    except Exception as exc:
        context.problem(document, CAPABILITIES,
                        "%s inspection stopped: %s: %s" % (name, type(exc).__name__, exc), location)
    _finalize_coverage(context, document, name, inspection_suffix=ODF_MEDIA_SUFFIXES[media_type])
    return document


def _scan(data: bytes, name: str, context: InspectionContext, *, parent: str | None = None,
          location: dict | None = None, component: str | None = None) -> str:
    """Inspect one occurrence, detecting ancestry cycles without deduplicating siblings."""
    location = {"kind": "source"} if location is None else location
    document = context.document(data, name, parent=parent, location=location, component=component)
    digest = hashlib.sha256(data).hexdigest()
    try:
        context.check("source_bytes", len(data), location)
        context.check("nesting", len(context.active_hashes), location)
        if digest in context.active_hashes:
            context.problem(document, CAPABILITIES, "embedded document ancestry cycle", location)
            return document
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            context.problem(document, CAPABILITIES, "unsupported embedded suffix: " + suffix, location)
            return document
        if suffix in PACKAGE_SUFFIXES and not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise BrewdocError("package signature does not match " + suffix)
        if suffix == ".pdf" and b"%PDF-" not in data[:1024]:
            raise BrewdocError("PDF signature is missing")
        if suffix == ".xls" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise BrewdocError("CFB signature is missing")
        context.active_hashes.append(digest)
        try:
            _dispatch(data, name, context, document)
        finally:
            context.active_hashes.pop()
    except Exception as exc:
        context.problem(document, CAPABILITIES,
                        "%s inspection stopped: %s: %s" % (name, type(exc).__name__, exc), location)
    _finalize_coverage(context, document, name)
    return document


def _finalize_coverage(context: InspectionContext, document: str, name: str, *,
                       inspection_suffix: str | None = None) -> None:
    """Finalize true format exclusions without promoting unfinished reader work to success."""
    suffix = inspection_suffix if inspection_suffix is not None else Path(name).suffix.lower()
    excluded = set() if suffix in WORKBOOK_SUFFIXES else {"cells", "formulas", "names"}
    if suffix in (".html", ".md", ".txt"):
        excluded.add("macros")
    for capability in CAPABILITIES:
        row = context.coverage.get((document, capability))
        if row is None or row["reason"] != "inspection pending":
            continue
        if capability in excluded:
            context.cover(document, capability, "not_applicable",
                          "%s has no native %s structures" % (suffix, capability))
        else:
            context.cover(document, capability, "unavailable",
                          "%s reader did not finalize %s inspection" % (suffix, capability))


def _final_report(context: InspectionContext, source: dict) -> tuple[dict, bytes]:
    """Keep failure evidence when report growth exhausts its reserved allowance."""
    report = context.report(source)
    payload = json_bytes(report)
    if len(payload) > context.limits["report_bytes"]:
        location = {"kind": "report"}
        omitted = {"documents": max(0, len(context.documents) - 1),
                   "findings": len(context.findings), "components": len(context.components),
                   "artifacts": len(context.artifacts)}
        root = context.documents[0]
        report["documents"] = [root]
        report["findings"], report["components"], report["artifacts"] = [], [], []
        report["coverage"] = [{
            "document": root["id"], "capability": capability, "status": "unavailable",
            "reason": "report byte limit exceeded; evidence inventory omitted", "locations": [location],
        } for capability in CAPABILITIES]
        report["limits"]["exhausted"] = [{
            "limit": "report_bytes", "maximum": context.limits["report_bytes"],
            "location": location, "omitted_records": omitted}]
        provenance = report["source"].get("provenance")
        if provenance is not None and len(json_bytes(provenance)) > 1024:
            original = json_bytes(provenance)
            report["source"]["provenance"] = {
                "omitted": "report byte limit exceeded", "byte_size": len(original),
                "sha256": hashlib.sha256(original).hexdigest()}
        payload = json_bytes(report)
    return report, payload


def _inspect_document(path: Path, *, source_provenance: dict | None = None,
                      _limits: dict[str, int] | None = None) -> tuple[dict, dict[str, bytes]]:
    """Read one bounded source and return evidence plus operation-owned artifact bytes."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise BrewdocError("inspection unsupported suffix: %s" % path.suffix.lower())
    context = InspectionContext(limits=_limits)
    try:
        with path.open("rb") as stream:
            data = stream.read(context.limits["source_bytes"] + 1)
            oversized = len(data) > context.limits["source_bytes"]
            actual_size = path.stat().st_size
    except OSError as exc:
        raise BrewdocError("inspection source unreadable: %s: %s" % (path, exc)) from exc
    source = {"name": path.name, "suffix": path.suffix.lower(), "byte_size": actual_size,
              "sha256": None if oversized else hashlib.sha256(data).hexdigest(), "kind": "local"}
    if source_provenance is not None:
        source["kind"] = "snapshot"
        source["provenance"] = source_provenance
    try:
        _scan(data, path.name, context)
    except InspectionLimit as exc:
        if not context.documents:
            context.documents.append({
                "id": "document/000001", "parent": None, "component": None,
                "name": path.name, "suffix": path.suffix.lower(), "byte_size": actual_size,
                "sha256": source["sha256"], "location": {"kind": "source"}})
        document = context.documents[0]["id"]
        context.problem(document, CAPABILITIES, str(exc), {"kind": "source"})
    if oversized:
        for document in context.documents:
            if document["parent"] is None:
                document["byte_size"] = actual_size
                document["sha256"] = None
    report, serialized = _final_report(context, source)
    listed = {item["key"] for item in report["artifacts"]}
    return report, {REPORT_KEY: serialized,
                    **{key: value for key, value in context.payloads.items() if key in listed}}


def inspect_document(path) -> dict:
    """Inspect original supported input independently of readable conversion."""
    return _inspect_document(Path(path))[0]


def inspection_artifacts(report: dict, payloads: dict[str, bytes]) -> tuple[ArtifactRef, ...]:
    """Build retrieval references, keeping the report digest outside its own body."""
    report_bytes = payloads[REPORT_KEY]
    references = [ArtifactRef(REPORT_KEY, "inspection-report", "available", 1, "source",
                              "application/json", len(report_bytes),
                              hashlib.sha256(report_bytes).hexdigest())]
    references.extend(ArtifactRef(**{key: value for key, value in item.items() if key != "out"})
                      for item in report["artifacts"])
    return tuple(references)


def list_document_artifacts(path) -> tuple[ArtifactRef, ...]:
    """List exact inspection report, source and embedded-payload artifacts."""
    report, payloads = _inspect_document(Path(path))
    return inspection_artifacts(report, payloads)


def read_document_artifact(path, key: str) -> bytes:
    """Retrieve one listed inspection artifact without evaluating or fetching content."""
    _report, payloads = _inspect_document(Path(path))
    try:
        return payloads[key]
    except KeyError as exc:
        raise BrewdocError("unknown artifact key: %s" % key) from exc


def inspection_summary(report: dict) -> dict:
    """Summarize evidence without equating readable success with coverage."""
    statuses = dict.fromkeys(("complete", "partial", "unavailable", "not_applicable"), 0)
    for row in report["coverage"]:
        statuses[row["status"]] += 1
    return {"schema": INSPECTION_SCHEMA, "artifact": REPORT_KEY,
            "source_sha256": report["source"]["sha256"], "source": report["source"],
            "documents": len(report["documents"]), "components": len(report["components"]),
            "findings": len(report["findings"]), "coverage": statuses,
            "not_carried_scope": "readable_projection", "supplement_scope": "original_source_findings"}


def _literal(text: object) -> str:
    value = str(text).replace("\r", "\\r").replace("\n", "\\n")
    fence = "`" * max(1, max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    return fence + " " + value + " " + fence


def _code(text: str, language: str = "text") -> list[str]:
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return [fence + language, text, fence]


def readable_projection(markdown: str) -> str:
    """Return only a byte-bound readable prefix; source lookalikes stay unchanged."""
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|\Z)", markdown)
    fence = None
    offset = 0
    candidates = []
    for index, line in enumerate(lines):
        line = line.rstrip("\r\n")
        opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)\Z", line)
        if opening:
            delimiter = opening[1]
            if fence is None and (delimiter[0] != "`" or "`" not in opening[2]):
                fence = delimiter
            elif (fence is not None and delimiter[0] == fence[0]
                  and len(delimiter) >= len(fence) and not opening[2].strip(" \t")):
                fence = None
        marker = re.fullmatch(r"<!-- brewdoc-inspection/1 readable-bytes=(0|[1-9][0-9]*) sha256=([0-9a-f]{64}) -->", line)
        if (fence is None and marker and index + 3 < len(lines)
                and lines[index + 1].rstrip("\r\n") == SUPPLEMENT_SCOPE
                and not lines[index + 2].strip(" \t\r\n")
                and lines[index + 3].rstrip("\r\n") in ("## Links", "## Active content", "## Extracted content")):
            candidates.append((offset, marker[1], marker[2]))
        offset += len(lines[index])
    encoded = markdown.encode("utf-8")
    for position, size, digest in reversed(candidates):
        if len(size) > len(str(len(encoded))) or int(size) > len(encoded):
            continue
        prefix = encoded[:int(size)]
        try:
            readable = prefix.decode("utf-8")
        except UnicodeError:
            continue
        separator = ("" if not readable or readable.endswith(("\n\n", "\r\n\r\n"))
                     else "\n" if readable.endswith("\n") else "\n\n")
        if (markdown[:position] == readable + separator
                and hashlib.sha256(prefix).hexdigest() == digest):
            return readable
    return markdown


def inspection_markdown(report: dict, *, preserve_unicode: bool = True) -> str:
    """Project findings as inert literals with fences source text cannot close."""
    groups: dict[str, list[dict]] = {"Links": [], "Active content": [], "Extracted content": []}
    for finding in report["findings"]:
        capability = finding["capability"]
        group = ("Links" if capability in ("links", "external_references") else "Active content"
                 if capability in ("scripts", "actions", "forms", "macros") else "Extracted content")
        groups[group].append(finding)
    lines: list[str] = []
    for title, findings in groups.items():
        if not findings:
            continue
        lines.extend(("", "## " + title, ""))
        for finding in findings:
            location = json_bytes(finding["location"]).decode("utf-8").rstrip()
            lines.append("- " + _literal(finding["kind"]) + " at " + _literal(location))
            if finding["target"] is not None:
                lines.append("  Target: " + _literal(finding["target"]))
            if finding["text"] is not None:
                lines.extend(("", *_code(finding["text"]), ""))
            if finding["details"]:
                details = json_bytes(finding["details"]).decode("utf-8").rstrip("\n")
                lines.extend(("", *_code(details, "json"), ""))
            if finding["artifact"] is not None:
                lines.append("  Artifact: " + _literal(finding["artifact"]))
    text = SUPPLEMENT_SCOPE + "\n" + "\n".join(lines).rstrip() + "\n" if lines else ""
    return text if preserve_unicode else text.encode("ascii", "xmlcharrefreplace").decode("ascii")
