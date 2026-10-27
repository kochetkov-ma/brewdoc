"""Shared static-inspection records and operation-wide resource accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from brewdoc.common import ArtifactRef, BrewdocError, _ZipBudget

INSPECTION_SCHEMA = "brewdoc.inspection/1"
REPORT_KEY = "inspection/report"
CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                "attachments", "images", "cells", "formulas", "names",
                "external_references", "hidden_structures", "macros")
INSPECTION_LIMITS = {
    "source_bytes": 33_554_432,
    "payload_bytes": 33_554_432,
    "decoded_bytes": 268_435_456,
    "retained_bytes": 268_435_456,
    "records": 1_000_000,
    "documents": 128,
    "nesting": 16,
    "report_bytes": 67_108_864,
}
COUNTERS = ("decoded_bytes", "retained_bytes", "records", "documents", "report_bytes")
ODF_MEDIA_SUFFIXES = {
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}


def json_bytes(value: object) -> bytes:
    """Serialize inspection evidence without platform-dependent framing."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
            + "\n").encode("utf-8")


class InspectionLimit(BrewdocError):
    """A bounded inspection stopped before admitting more data."""


class _InspectionZipBudget(_ZipBudget):
    """Keep existing ZIP/XML refusals while exposing their shared inspection boundary."""

    __slots__ = ("context",)

    def __init__(self, context):
        super().__init__()
        self.context = context

    def check(self, key: str, value: int, part: str) -> None:
        """Record the shared source boundary before preserving the existing refusal."""
        if value > self.limits[key]:
            self.context._exhaust("zip." + key, self.limits[key], {"kind": "zip_xml", "part": part})
        super().check(key, value, part)


class InspectionContext:
    """Own one report, retained payloads and shared limits across nested documents."""

    def __init__(self, *, limits: dict[str, int] | None = None):
        self.limits = INSPECTION_LIMITS.copy()
        if limits:
            unknown = set(limits) - self.limits.keys()
            if unknown:
                raise ValueError("unknown inspection limits: %s" % ", ".join(sorted(unknown)))
            self.limits.update(limits)
        if any(type(value) is not int or value < 0 for value in self.limits.values()):
            raise ValueError("inspection limits must be nonnegative integers")
        if self.limits["report_bytes"] < 8192:
            raise ValueError("inspection report_bytes needs at least 8192 bytes for failure evidence")
        self.consumed = dict.fromkeys(COUNTERS, 0)
        self.zip_budget = _InspectionZipBudget(self)
        self.payloads: dict[str, bytes] = {}
        self.documents: list[dict] = []
        self.components: list[dict] = []
        self.findings: list[dict] = []
        self.artifacts: list[dict] = []
        self.coverage: dict[tuple[str, str], dict] = {}
        self.exhausted: list[dict] = []
        self.active_hashes: list[str] = []
        self._failed: set[tuple[str, str]] = set()
        self._recovered: set[tuple[str, str]] = set()
        self._reserve = min(65_536, self.limits["report_bytes"] // 8)
        self.consumed["report_bytes"] = 2048

    def check(self, limit: str, value: int, location: dict) -> None:
        """Refuse before an item or cumulative counter exceeds its allowance."""
        maximum = self.limits[limit]
        if limit == "report_bytes":
            maximum -= self._reserve
        if value > maximum:
            self._exhaust(limit, maximum, location)
            raise InspectionLimit("inspection %s limit exceeded: %d" % (limit, maximum))

    def _exhaust(self, limit: str, maximum: int, location: dict) -> None:
        failure = {"limit": limit, "maximum": maximum,
                   "location": json.loads(json_bytes(location))}
        if failure not in self.exhausted and len(self.exhausted) < 32:
            self.exhausted.append(failure)

    def charge(self, counter: str, amount: int, location: dict) -> None:
        """Charge admitted work once, checking the operation-wide allowance first."""
        if counter not in self.consumed or type(amount) is not int or amount < 0:
            raise ValueError("invalid inspection charge: %s=%r" % (counter, amount))
        value = self.consumed[counter] + amount
        self.check(counter, value, location)
        self.consumed[counter] = value

    def remaining(self, counter: str) -> int:
        """Return remaining cumulative capacity for bounded decoder reads."""
        maximum = self.limits[counter] - (self._reserve if counter == "report_bytes" else 0)
        return max(0, maximum - self.consumed[counter])

    def _record(self, record: dict, location: dict) -> dict:
        self.charge("records", 1, location)
        encoded = json_bytes(record)
        self.charge("report_bytes", len(encoded) + 64, location)
        return json.loads(encoded)

    def component(self, document: str, kind: str, location: dict, *, data: bytes | None = None,
                  disposition: str = "extracted", artifact: str | None = None,
                  reason: str | None = None, **metadata) -> str:
        """Inventory one occurrence; hashing bytes does not retain or inspect them."""
        if disposition not in ("rendered", "extracted", "opaque", "unreadable"):
            raise ValueError("invalid component disposition: %s" % disposition)
        key = "component/%06d" % (len(self.components) + 1)
        record = {"id": key, "document": document, "kind": kind, "location": dict(location),
                  "disposition": disposition, "byte_size": len(data) if data is not None else None,
                  "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                  "artifact": artifact, "reason": reason, "details": metadata}
        self.components.append(self._record(record, location))
        return key

    def finding(self, document: str, capability: str, kind: str, location: dict, *,
                component: str | None = None, target: str | None = None,
                text: str | None = None, artifact: str | None = None, **details) -> str:
        """Record original decoded evidence at one stable source location."""
        if capability not in CAPABILITIES:
            raise ValueError("unknown inspection capability: %s" % capability)
        key = "finding/%06d" % (len(self.findings) + 1)
        record = {"id": key, "document": document, "capability": capability, "kind": kind,
                  "location": dict(location), "component": component, "target": target,
                  "text": text, "artifact": artifact, "details": details}
        self.findings.append(self._record(record, location))
        self._recovered.add((document, capability))
        coverage = self.coverage.get((document, capability))
        if (document, capability) in self._failed and coverage is not None:
            coverage["status"] = "partial"
        return key

    def cover(self, document: str, capability: str, status: str, reason: str,
              locations: Iterable[dict] = ()) -> None:
        """Update coverage without letting later success erase a recorded failure."""
        if capability not in CAPABILITIES or status not in (
                "complete", "partial", "unavailable", "not_applicable"):
            raise ValueError("invalid inspection coverage: %s=%s" % (capability, status))
        key = (document, capability)
        original = self.coverage.get(key)
        if key in self._failed and status in ("complete", "not_applicable"):
            return
        if status in ("partial", "unavailable"):
            self._failed.add(key)
        record = {"document": document, "capability": capability, "status": status,
                  "reason": reason, "locations": [dict(item) for item in locations]}
        if (original is not None and original["reason"] != "inspection pending"
                and key in self._failed and original["status"] in ("partial", "unavailable")):
            record["locations"] = original["locations"] + [
                item for item in record["locations"] if item not in original["locations"]]
            if original["reason"] != reason:
                record["reason"] = original["reason"] + "; " + reason
            if original["status"] == "partial":
                record["status"] = "partial"
        encoded = json_bytes(record)
        self.charge("report_bytes", max(0, len(encoded)
                    - (len(json_bytes(original)) if original is not None else 0)) + 32,
                    next(iter(record["locations"]), {"kind": "coverage"}))
        self.coverage[key] = json.loads(encoded)

    def payload(self, document: str, kind: str, location: dict, data: bytes,
                media_type: str) -> str:
        """Retain exact admitted bytes and publish a deterministic artifact reference."""
        self.check("payload_bytes", len(data), location)
        self.charge("retained_bytes", len(data), location)
        key = "inspection/payload/%06d" % (len(self.payloads) + 1)
        reference = ArtifactRef(key, kind, "available", 1, json_bytes(location).decode().rstrip(),
                                media_type, len(data), hashlib.sha256(data).hexdigest()).to_dict()
        reference = self._record(reference, location)
        self.payloads[key] = data
        self.artifacts.append(reference)
        return key

    def problem(self, document: str, capabilities: Iterable[str], reason: str,
                location: dict) -> None:
        """Record bounded sticky failure diagnostics even after normal report capacity ends."""
        if isinstance(capabilities, str):
            capabilities = (capabilities,)
        for capability in capabilities:
            if capability not in CAPABILITIES:
                raise ValueError("unknown inspection capability: %s" % capability)
            key = (document, capability)
            self._failed.add(key)
            original = self.coverage.get(key)
            locations = original["locations"][:] if original else []
            if location not in locations and len(locations) < 16:
                locations.append(json.loads(json_bytes(location)))
            text = reason[:1024]
            if (original and original["reason"] != "inspection pending"
                    and original["status"] in ("partial", "unavailable")):
                previous = original["reason"]
                text = previous if text in previous else (previous + "; " + text)[:2048]
            self.coverage[key] = {"document": document, "capability": capability,
                                  "status": "partial" if key in self._recovered else "unavailable",
                                  "reason": text, "locations": locations}

    def document(self, data: bytes, name: str, *, parent: str | None,
                 location: dict, component: str | None = None) -> str:
        """Admit one source occurrence and initialize every capability as pending."""
        record = {"parent": parent, "component": component, "name": name,
                  "suffix": _suffix(name), "byte_size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "location": dict(location)}
        return self._document(record, location)

    def document_parts(self, name: str, media_type: str, *, parent: str, location: dict,
                       component: str | None, source_members) -> str:
        """Admit an ODF directory occurrence without inventing contiguous source bytes."""
        if media_type not in ODF_MEDIA_SUFFIXES:
            raise BrewdocError("unsupported ODF directory media type: %s" % media_type)
        record = {"parent": parent, "component": component, "name": name,
                  "suffix": ODF_MEDIA_SUFFIXES[media_type], "byte_size": None, "sha256": None,
                  "location": dict(location), "representation": "odf-directory",
                  "media_type": media_type, "source_members": list(source_members)}
        return self._document(record, location)

    def _document(self, record: dict, location: dict) -> str:
        """Share source admission and coverage allowances for file and member-scope sources."""
        self.charge("documents", 1, location)
        key = "document/%06d" % (len(self.documents) + 1)
        record["id"] = key
        record = self._record(record, location)
        rows = [{"document": key, "capability": capability, "status": "unavailable",
                 "reason": "inspection pending", "locations": []} for capability in CAPABILITIES]
        self.charge("report_bytes", sum(len(json_bytes(row)) + 32 for row in rows), location)
        self.documents.append(record)
        for capability, coverage in zip(CAPABILITIES, rows):
            self.coverage[(key, capability)] = coverage
        return key

    def report(self, source: dict) -> dict:
        """Return JSON-ready evidence without retained payload bytes or runtime state."""
        return {"schema": INSPECTION_SCHEMA, "source": json.loads(json_bytes(source)),
                "documents": self.documents, "components": self.components,
                "findings": self.findings, "coverage": list(self.coverage.values()),
                "artifacts": self.artifacts,
                "limits": {"configured": {**self.limits, "zip_xml": self.zip_budget.limits.copy()},
                           "consumed": {**self.consumed, "zip_xml": {
                               "consumed_bytes": self.zip_budget.consumed_bytes,
                               "total_elements": self.zip_budget.total_elements,
                               "total_characters": self.zip_budget.total_characters}},
                           "exhausted": self.exhausted}}


def _suffix(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
