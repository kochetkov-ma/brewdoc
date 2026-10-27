"""Bounded PDF source rows and generation-aware views over one immutable buffer."""

from __future__ import annotations

import re
from bisect import bisect_right

from pdfminer.pdfdocument import PDFBaseXRef, PDFDocument
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFObjRef, PDFStream, decipher_all
from pdfminer.psparser import KWD, PSEOF, PSLiteral

from brewdoc.common import BrewdocError
from brewdoc._inspection_common import InspectionLimit


CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                "attachments", "images", "external_references", "hidden_structures")


class PDFSourceError(BrewdocError):
    """A source occurrence could not be resolved within its measured PDF interval."""

    def __init__(self, reason, location):
        super().__init__(reason)
        self.location = dict(location)


def _name(value):
    return value.name if isinstance(value, PSLiteral) else None


class _Cursor:
    """Expose independent bounded cursors without copying the admitted source buffer."""

    def __init__(self, data, context, location, start=0, end=None):
        self.data, self.context, self.location = data, context, location
        self.start, self.end, self.position = start, len(data) if end is None else end, 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        value = offset + (self.position if whence == 1 else self.end - self.start if whence == 2 else 0)
        if not 0 <= value <= self.end - self.start:
            raise PDFSourceError("PDF seek exceeds admitted source interval", self.location)
        self.position = value
        return value

    def read(self, size=-1):
        self.context.charge("records", 1, self.location)
        size = self.end - self.start - self.position if size < 0 else size
        length = min(size, self.end - self.start - self.position)
        start = self.start + self.position
        self.position += length
        return self.data[start:start + length]


class PDFReference(PDFObjRef):
    """Preserve the source generation and refuse missing, free or cyclic targets."""

    def __init__(self, document, object_id, generation, location):
        super().__init__(document, object_id)
        self.generation, self.location = generation, dict(location)

    def resolve(self, default=None):
        value, seen = self, set()
        while isinstance(value, PDFReference):
            key = id(value.doc), value.objid, value.generation
            if key in seen:
                raise PDFSourceError("PDF indirect reference cycle", value.location)
            seen.add(key)
            value.doc.source.context.charge("records", 1, value.location)
            value = value.doc.getobj(value.objid, value.generation)
        return value


class _Stream(PDFStream):
    """Keep native consumers on the same bounded decoder, including deferred font reads."""

    def __init__(self, attributes, data, source, view, location):
        super().__init__(attributes, data, getattr(view, "decipher", None))
        self.source, self.view, self.location = source, view, location

    def get_data(self):
        return self.source.decode(self, self.view, self.location)


class _Parser(PDFParser):
    """Reuse native object syntax with checked references, streams and token work."""

    def __init__(self, cursor, source, document, location, *, stream_mode=False):
        self.source, self.location, self.stream_mode = source, location, stream_mode
        super().__init__(cursor)
        self.set_document(document)

    def nexttoken(self):
        self.source.context.charge("records", 1, self.location)
        return super().nexttoken()

    def nextline(self):
        self.source.context.charge("records", 1, self.location)
        return super().nextline()

    def flush(self):
        if self.stream_mode:
            self.add_results(*self.popall())
        else:
            super().flush()

    def end_type(self, kind):
        if kind == "d" and self.curtype == "d":
            keys = set()
            for index in range(0, len(self.curstack), 2):
                position, token = self.curstack[index]
                key = _name(token)
                if key is None or key in keys:
                    location = self.location | {"dictionary_key_offset": self.fp.start + position}
                    raise PDFSourceError("PDF dictionary key is invalid or duplicated: %r" % key, location)
                keys.add(key)
        return super().end_type(kind)

    def do_keyword(self, pos, token):
        """Bind checked references and exact-length streams before native dispatch."""
        if token is self.KEYWORD_R:
            (start, object_id), (_position, generation) = self.pop(2)
            if type(object_id) is not int or object_id < 0 or type(generation) is not int or not 0 <= generation <= 65535:
                raise PDFSourceError("PDF indirect reference has invalid object ID or generation", self.location)
            location = self.location | {"reference_start": self.fp.start + start,
                                        "reference_end": self.fp.start + pos + 1}
            self.push((pos, PDFReference(self.doc, object_id, generation, location)))
        elif token is self.KEYWORD_STREAM:
            if self.stream_mode:
                raise PDFSourceError("PDF ObjStm cannot contain a stream object", self.location)
            (_position, attributes), = self.pop(1)
            if not isinstance(attributes, dict):
                raise PDFSourceError("PDF stream extent is not a dictionary", self.location)
            length = attributes.get("Length")
            length = length.resolve() if isinstance(length, PDFReference) else length
            if type(length) is not int or length < 0:
                raise PDFSourceError("PDF stream Length is not a nonnegative integer", self.location)
            self.source.context.check("payload_bytes", length, self.location)
            self.seek(pos)
            _linepos, line = self.nextline()
            if line.strip() != b"stream" or not line.endswith((b"\n", b"\r")):
                raise PDFSourceError("PDF stream requires a line ending before payload bytes", self.location)
            start = pos + len(line)
            if length > self.fp.end - self.fp.start - start:
                raise PDFSourceError("PDF stream Length exceeds admitted object interval", self.location)
            self.fp.seek(start)
            data = self.fp.read(length)
            self.seek(start + length)
            _endpos, end = self.nexttoken()
            if end is not KWD(b"endstream"):
                raise PDFSourceError("PDF stream Length does not end at endstream", self.location)
            value = _Stream(attributes, data, self.source, self.doc, self.location)
            value._source_span = self.fp.start + start, self.fp.start + start + length
            self.source.claim(*value._source_span, "stream-payload")
            self.push((pos, value))
        elif self.stream_mode and token in (self.KEYWORD_OBJ, self.KEYWORD_ENDOBJ):
            raise PDFSourceError("PDF ObjStm body contains indirect-object keywords", self.location)
        else:
            super().do_keyword(pos, token)


def _map_references(value, transform, context, location):
    """Clone only mutable syntax containers while rebinding or resolving references."""
    pending = []

    def create(item):
        if isinstance(item, PDFReference):
            item = transform(item)
        if isinstance(item, dict):
            target = {}
            pending.append((item, target))
            return target
        if isinstance(item, list):
            target = []
            pending.append((item, target))
            return target
        return item

    result = create(value)
    while pending:
        original, target = pending.pop()
        context.charge("records", len(original), location)
        if isinstance(original, dict):
            for key, item in original.items():
                target[key] = create(item)
        else:
            target.extend(create(item) for item in original)
    return result


class _Section(PDFBaseXRef):
    """Adapt already admitted section rows to native initialization without re-reading."""

    def __init__(self, section, view):
        self.section, self.view = section, view
        self.index = {row["object_id"]: row for row in section["rows"]}

    def get_trailer(self):
        return _map_references(self.section["trailer"], self.view.reference,
                               self.view.source.context, self.view.location)

    def get_objids(self):
        return tuple(key for key, row in self.index.items() if row["type"] in (1, 2))

    def get_pos(self, object_id):
        row = self.index[object_id]
        if row["type"] == 1:
            return None, row["target"], row["generation"]
        if row["type"] == 2:
            return row["target"], row["index"], 0
        raise KeyError(object_id)


class PDFRevision(PDFDocument):
    """Use independent native caches and checked resolution for one physical revision."""

    def __init__(self, source, revision):
        self.source, self.revision = source, revision
        self.location = {"kind": "pdf", "revision_id": revision["id"], "revision": revision["ordinal"]}
        self.occurrences, self.active, self.closed = {}, set(), False
        self.containers = {}
        cursor = _Cursor(source.data, source.context, self.location)
        parser = _Parser(cursor, source, self, self.location)
        super().__init__(parser, fallback=False)

    def read_xref_from(self, parser, start, xrefs):
        xrefs.extend(_Section(section, self) for section in self.source.chain(self.revision["section"]))

    def reference(self, original):
        return PDFReference(self, original.objid, original.generation, original.location | self.location)

    def getobj(self, object_id, generation=None):
        """Resolve only the first effective row, checking its original generation."""
        if self.closed:
            raise PDFSourceError("PDF revision view is closed", self.location)
        if type(object_id) is not int or object_id < 1 or (generation is not None and (
                type(generation) is not int or not 0 <= generation <= 65535)):
            raise PDFSourceError("PDF requested object ID or generation is invalid", self.location)
        row = self.source.lookup(self.revision["section"], object_id)
        location = self.location | {"object_id": object_id, "generation": generation}
        if row is None or row["type"] == 0:
            raise PDFSourceError("PDF reference targets a missing or free object", location)
        if row["type"] not in (1, 2):
            raise PDFSourceError("PDF reference targets an unsupported xref entry type", self.source.row_location(row))
        if generation is not None and generation != row["generation"]:
            raise PDFSourceError("PDF reference generation does not match source generation", location)
        return self.read_occurrence(row)["value"]

    def read_occurrence(self, row):
        """Read the exact selected row through this view, retaining physical source spans."""
        if self.closed:
            raise PDFSourceError("PDF revision view is closed", self.location)
        key = row["id"]
        if key in self.occurrences:
            return self.occurrences[key]
        if key in self.active:
            raise PDFSourceError("PDF source resolution cycle", self.source.row_location(row))
        self.active.add(key)
        try:
            if row["type"] == 1:
                value, start, end = self.source.read_object(row["target"], row["object_id"], row["generation"], self)
                location = self.source.row_location(row) | {"byte_start": start, "byte_end": end, "byte_basis": "file"}
                data = self.source.data[start:end]
                if self.decipher:
                    value = decipher_all(self.decipher, row["object_id"], row["generation"], value)
                if isinstance(value, PDFStream):
                    value.set_objid(row["object_id"], row["generation"])
            elif row["type"] == 2:
                value, data, location = self._compressed(row)
            else:
                raise PDFSourceError("PDF source row has no object payload", self.source.row_location(row))
            result = {"row": row, "value": value, "data": data, "location": location | {
                "reference_revision": self.revision["ordinal"], "reference_revision_id": self.revision["id"]}}
            self.occurrences[key] = result
            if row["revision_id"] == self.revision["id"]:
                self.source.validated_rows.add(row["id"])
            return result
        finally:
            self.active.remove(key)

    def _compressed(self, row):
        container_row = self.source.lookup(self.revision["section"], row["target"])
        if container_row is None or container_row["type"] != 1:
            raise PDFSourceError("PDF ObjStm container is missing or not an uncompressed object", self.source.row_location(row))
        stream = self.getobj(row["target"], container_row["generation"])
        if not isinstance(stream, PDFStream) or _name(stream.attrs.get("Type")) != "ObjStm":
            raise PDFSourceError("PDF compressed object container is not /Type /ObjStm", self.source.row_location(row))
        if container_row["id"] not in self.containers:
            decoded = self.source.decode(stream, self, self.source.row_location(container_row))
            count, first = stream.attrs.get("N"), stream.attrs.get("First")
            count = count.resolve() if isinstance(count, PDFReference) else count
            first = first.resolve() if isinstance(first, PDFReference) else first
            if type(count) is not int or count < 1 or type(first) is not int or not 0 <= first <= len(decoded):
                raise PDFSourceError("PDF ObjStm N or First is invalid", self.source.row_location(container_row))
            self.source.context.charge("records", count, self.source.row_location(container_row))
            parser = _Parser(_Cursor(decoded, self.source.context, self.location, 0, first), self.source, self, self.location,
                             stream_mode=True)
            pairs = []
            for _index in range(count):
                _, object_id = parser.nexttoken()
                _, offset = parser.nexttoken()
                if type(object_id) is not int or object_id < 1 or type(offset) is not int or offset < 0:
                    raise PDFSourceError("PDF ObjStm header pair is invalid", self.source.row_location(container_row))
                pairs.append((object_id, offset))
            try:
                parser.nexttoken()
            except PSEOF:
                pass
            else:
                raise PDFSourceError("PDF ObjStm header has extra tokens", self.source.row_location(container_row))
            if (len({item[0] for item in pairs}) != count or pairs[0][1] != 0
                    or any(left[1] >= right[1] for left, right in zip(pairs, pairs[1:]))
                    or first + pairs[-1][1] >= len(decoded)):
                raise PDFSourceError("PDF ObjStm IDs or monotonic body spans are invalid", self.source.row_location(container_row))
            self.containers[container_row["id"]] = decoded, first, pairs
        decoded, first, pairs = self.containers[container_row["id"]]
        index = row["index"]
        if not 0 <= index < len(pairs) or pairs[index][0] != row["object_id"]:
            raise PDFSourceError("PDF ObjStm selected header ID/index mismatch", self.source.row_location(row))
        start = first + pairs[index][1]
        end = first + pairs[index + 1][1] if index + 1 < len(pairs) else len(decoded)
        location = self.source.row_location(row) | {"object_stream_offset": container_row["target"],
                                                   "byte_start": start, "byte_end": end,
                                                   "byte_basis": "decoded_object_stream"}
        parser = _Parser(_Cursor(decoded, self.source.context, location, start, end), self.source, self, location,
                         stream_mode=True)
        _position, value = parser.nextobject()
        try:
            parser.nextobject()
        except PSEOF:
            pass
        else:
            raise PDFSourceError("PDF ObjStm body contains more than one object", location)
        return value, decoded[start:end], location

    def version(self):
        """Resolve a catalog's effective encoding version through this same revision view."""
        if self.closed:
            raise PDFSourceError("PDF revision view is closed", self.location)
        header = re.search(rb"%PDF-(\d+)\.(\d+)", self.source.data[:1024])
        original = tuple(map(int, header.groups())) if header else None
        declared = self.catalog.get("Version")
        declared = declared.resolve() if isinstance(declared, PDFReference) else declared
        declared_name = _name(declared)
        match = re.fullmatch(r"(\d+)\.(\d+)", declared_name) if isinstance(declared_name, str) else None
        catalog = tuple(map(int, match.groups())) if match else None
        known = {(1, minor) for minor in range(8)} | {(2, 0)}
        valid = original in known and (declared is None or catalog in known)
        effective = max(original, catalog) if valid and catalog is not None else original
        return {"header_version": ".".join(map(str, original)) if original else None,
                "catalog_version": declared_name if isinstance(declared_name, str) else repr(declared) if declared is not None else None,
                "effective": ".".join(map(str, effective)) if effective else None,
                "source": "catalog" if valid and catalog is not None and catalog > original else "header",
                "supported": valid}

    def close(self):
        """Release parsed graphs without refunding cumulative work or retained evidence."""
        self.occurrences.clear()
        self.containers.clear()
        self._cached_objs.clear()
        self._parsed_objs.clear()
        self.catalog = {}
        self.info = []
        self.xrefs = []
        self._parser = None
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class PDFSource:
    """Own physical rows, section links and source accounting for one PDF operation."""

    decipher = None

    def __init__(self, data, context, document, decode_stream):
        self.data, self.context, self.document, self.decoder = data, context, document, decode_stream
        self.source = self
        self.sections, self.rows, self.revisions = [], [], []
        self.claims, self.orphans, self.regions, self.problems = [], [], [], []
        self.by_offset, self.payloads, self.decoded = {}, {}, {}
        self.row_indexes = {}
        self._offsets, self._offset_index = set(), None
        self._problem_keys, self._claim_keys = set(), set()
        self._accounting_stopped = False
        self.validated_rows = set()
        self.conflicts = []
        context.check("source_bytes", len(data), {"kind": "pdf"})
        parser = _Parser(_Cursor(data, context, {"kind": "pdf"}), self, self, {"kind": "pdf"})
        try:
            start = PDFDocument.find_xref(self, parser)
            self._read_chain(start)
            self._linearization()
            self._revisions(start)
        except Exception as exc:
            where = context.exhausted[-1]["location"] if isinstance(exc, InspectionLimit) and context.exhausted else {"kind": "pdf"}
            self.problem(str(exc), getattr(exc, "location", where))

    def getobj(self, object_id, generation=None):
        raise PDFSourceError("PDF bootstrap metadata cannot use an unresolved indirect object", {
            "kind": "pdf", "object_id": object_id, "generation": generation})

    def problem(self, reason, location):
        reason = str(reason)[:1024]
        record = {"reason": reason, "location": dict(location)}
        key = reason, repr(sorted(location.items()))
        if key not in self._problem_keys:
            self._problem_keys.add(key)
            self.problems.append(record)
            self.context.problem(self.document, CAPABILITIES, reason, location)

    def claim(self, start, end, kind):
        if not 0 <= start <= end <= len(self.data):
            raise PDFSourceError("PDF source span exceeds original bytes", {"kind": "pdf", "byte_start": start, "byte_end": end})
        record = start, end, kind
        if record not in self._claim_keys:
            self.context.charge("records", 1, {"kind": "pdf", "byte_start": start, "byte_end": end})
            self._claim_keys.add(record)
            self.claims.append(record)

    def _integer(self, value, field, location, *, maximum=None):
        if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
            raise PDFSourceError("PDF %s is outside its valid integer range" % field, location)
        return value

    def _read_chain(self, start):
        active, stack = set(), [(start, "main", False)]
        while stack:
            offset, role, leaving = stack.pop()
            location = {"kind": "pdf", "xref_section_offset": offset}
            if leaving:
                active.remove(offset)
                continue
            self._integer(offset, "xref offset", location, maximum=len(self.data) - 1)
            if offset in active:
                raise PDFSourceError("PDF xref section cycle", location)
            if offset in self.by_offset:
                if not self.by_offset[offset].get("complete"):
                    raise PDFSourceError("PDF xref section remains unreadable", location)
                continue
            self.context.charge("records", 1, location)
            active.add(offset)
            try:
                section = self._read_section(offset, role)
            except BrewdocError:
                raise
            except Exception as exc:
                raise PDFSourceError("PDF xref syntax unreadable: %s" % exc, location) from exc
            stack.append((offset, role, True))
            trailer = section["trailer"]
            if role != "supplement" and "Prev" in trailer:
                edge = location | {"reference_field": "Prev", "target_offset": trailer["Prev"]}
                previous = self._integer(trailer["Prev"], "Prev offset", edge, maximum=len(self.data) - 1)
                section["previous"] = previous
                stack.append((previous, "main", False))
            if "XRefStm" in trailer:
                edge = location | {"reference_field": "XRefStm", "target_offset": trailer["XRefStm"]}
                supplement = self._integer(trailer["XRefStm"], "XRefStm offset", edge, maximum=len(self.data) - 1)
                section["supplement"] = supplement
                stack.append((supplement, "supplement", False))

    def _read_section(self, offset, role):
        location = {"kind": "pdf", "xref_section_offset": offset}
        parser = _Parser(_Cursor(self.data, self.context, location, offset), self, self, location)
        section = {"offset": offset, "role": role, "rows": [], "previous": None, "supplement": None, "complete": False}
        self.by_offset[offset] = section
        self._offsets.add(offset)
        self._offset_index = None
        self.sections.append(section)
        self.row_indexes[offset] = {}
        _position, token = parser.nexttoken()
        if token is KWD(b"xref"):
            if role == "supplement":
                raise PDFSourceError("PDF hybrid supplement is not an xref stream", location)
            parser.nextline()
            while True:
                position, line = parser.nextline()
                fields = line.strip().split()
                if not fields:
                    continue
                if fields[0] == b"trailer":
                    parser.seek(position)
                    _position, keyword = parser.nexttoken()
                    parser.stream_mode = True
                    _position, trailer = parser.nextobject()
                    if keyword is not KWD(b"trailer") or not isinstance(trailer, dict):
                        raise PDFSourceError("PDF xref trailer is not a dictionary", location)
                    section["trailer"] = trailer
                    break
                if len(fields) != 2 or not all(field.isdigit() for field in fields):
                    raise PDFSourceError("PDF xref subsection header is malformed", location)
                first, count = map(int, fields)
                self.context.charge("records", count, location)
                for object_id in range(first, first + count):
                    row_start, raw = parser.nextline()
                    values = raw.strip().split()
                    if len(values) != 3 or not values[0].isdigit() or not values[1].isdigit() or values[2] not in (b"f", b"n"):
                        raise PDFSourceError("PDF xref row is malformed", location | {"object_id": object_id})
                    target, generation = map(int, values[:2])
                    self._row(section, object_id, 0 if values[2] == b"f" else 1, target, generation,
                              raw, offset + row_start, offset + row_start + len(raw), "file")
            end = offset + parser.bufpos + parser.charpos
            self.claim(offset, end, "xref-table")
        elif type(token) is int:
            value, start, end = self.read_object(offset, token, None, self, bound=len(self.data))
            if not isinstance(value, PDFStream) or _name(value.attrs.get("Type")) != "XRef":
                raise PDFSourceError("PDF xref offset does not identify an xref stream", location)
            section["trailer"] = value.attrs
            size = self._integer(value.attrs.get("Size"), "xref Size", location)
            widths = value.attrs.get("W")
            index = value.attrs.get("Index", [0, size])
            if (not isinstance(widths, list) or len(widths) != 3 or any(type(item) is not int or item < 0 for item in widths)
                    or sum(widths) == 0 or not isinstance(index, list) or len(index) % 2):
                raise PDFSourceError("PDF xref W/Index dimensions are malformed", location)
            ranges, seen = [], set()
            for first, count in zip(index[::2], index[1::2]):
                self._integer(first, "xref Index start", location, maximum=size)
                self._integer(count, "xref Index count", location, maximum=size - first)
                self.context.charge("records", count, location)
                for object_id in range(first, first + count):
                    if object_id in seen:
                        raise PDFSourceError("PDF xref Index ranges overlap", location | {"object_id": object_id})
                    seen.add(object_id)
                ranges.append((first, count))
            stride, count = sum(widths), sum(item[1] for item in ranges)
            self.context.check("payload_bytes", stride, location)
            self.context.check("payload_bytes", count * stride, location)
            decoded = self.decode(value, self, location)
            if count * stride != len(decoded):
                raise PDFSourceError("PDF xref stream row length does not match W/Index", location)
            cursor = 0
            for first, count in ranges:
                for object_id in range(first, first + count):
                    raw = decoded[cursor:cursor + stride]
                    one = int.from_bytes(raw[:widths[0]], "big") if widths[0] else 1
                    two = int.from_bytes(raw[widths[0]:widths[0] + widths[1]], "big")
                    three = int.from_bytes(raw[widths[0] + widths[1]:], "big")
                    self._row(section, object_id, one, two, three, raw, cursor, cursor + stride, "decoded_xref_stream")
                    cursor += stride
            self.claim(start, end, "xref-stream")
        else:
            raise PDFSourceError("PDF xref offset does not identify a section", location)
        section["complete"] = True
        return section

    def _row(self, section, object_id, kind, target, third, raw, start, end, basis):
        location = {"kind": "pdf", "xref_section_offset": section["offset"], "object_id": object_id}
        if object_id in self.row_indexes[section["offset"]]:
            raise PDFSourceError("PDF xref section repeats an object ID", location)
        self.context.charge("records", 1, location)
        row = {"id": "xref@%d/row/%d" % (section["offset"], len(section["rows"]) + 1),
               "section": section["offset"], "ordinal": len(section["rows"]) + 1,
               "object_id": object_id, "type": kind, "target": target,
               "generation": 0 if kind == 2 else third if kind in (0, 1) else None,
               "index": third if kind == 2 else None, "field_3": third,
               "row_start": start, "row_end": end, "row_basis": basis,
               "revision": None, "revision_id": None, "revision_status": "unlinked", "role": section["role"]}
        if kind in (0, 1, 2):
            self._integer(row["generation"], "xref generation", location, maximum=65535)
        row["raw"] = raw
        section["rows"].append(row)
        self.row_indexes[section["offset"]][object_id] = row
        self.rows.append(row)
        if kind == 1:
            self._offsets.add(target)
            self._offset_index = None
        if kind not in (0, 1, 2):
            self.problem("PDF xref entry type is unsupported: %d" % kind, location)

    def _linearization(self):
        """Group only a measured Appendix F first-page/main pair in the original base."""
        location = {"kind": "pdf", "linearization": True}
        parser = _Parser(_Cursor(self.data, self.context, location, 0, min(1024, len(self.data))), self, self, location)
        try:
            offset, object_id = parser.nexttoken()
            _, generation = parser.nexttoken()
            _, keyword = parser.nexttoken()
            if type(object_id) is not int or type(generation) is not int or keyword is not KWD(b"obj"):
                return
            value, _, end = self.read_object(offset, object_id, generation, self, bound=min(1024, len(self.data)))
        except InspectionLimit:
            raise
        except Exception:
            return
        if not isinstance(value, dict) or "Linearized" not in value:
            return
        try:
            if type(value["Linearized"]) not in (int, float) or value["Linearized"] != 1:
                raise PDFSourceError("PDF Linearized marker is invalid", location)
            length = self._integer(value.get("L"), "linearized L", location, maximum=len(self.data))
            target = self._integer(value.get("T"), "linearized T", location, maximum=length - 1)
            first_page = self._integer(value.get("O"), "linearized O", location)
            count = self._integer(value.get("N"), "linearized N", location)
            page_end = self._integer(value.get("E"), "linearized E", location, maximum=length)
            hint = value.get("H")
            if (not first_page or not count or not isinstance(hint, list) or len(hint) not in (2, 4)
                    or any(type(item) is not int or item < 0 for item in hint)
                    or any(start + size > length for start, size in zip(hint[::2], hint[1::2]))):
                raise PDFSourceError("PDF linearization page or hint bounds are invalid", location)
            first = next((part for part in self.sections if end <= part["offset"] < 1024
                          and _metadata_only(self.data[end:part["offset"]])), None)
            main = self.by_offset.get(first["previous"]) if first else None
            if (main is None or main["previous"] is not None or not end < first["offset"] < page_end <= main["offset"] < length
                    or not isinstance(first["trailer"].get("Root"), PDFReference)):
                raise PDFSourceError("PDF linearization section relationship is inconsistent", location)
            rows = main["rows"]
            if self.data[main["offset"]:main["offset"] + 4] == b"xref":
                entry_start = rows[0]["row_start"] if rows else 0
                if (not rows or rows[0]["type"] != 0 or target + 1 != entry_start
                        or self.data[target] not in (0, 9, 10, 12, 13, 32)
                        or [row["object_id"] for row in rows] != list(range(len(rows)))
                        or any(row["type"] != 1 for row in rows[1:])):
                    raise PDFSourceError("PDF linearized main table does not match T or consecutive rows", location)
            elif target != main["offset"]:
                raise PDFSourceError("PDF linearized main stream does not match T", location)
            first_rows = self.row_indexes[first["offset"]]
            all_ids = set(first_rows) | set(self.row_indexes[main["offset"]])
            linear = first_rows.get(object_id)
            if (linear is None or linear["type"] != 1 or linear["target"] != offset
                    or first_page not in first_rows or any(row["type"] != 1 for row in first_rows.values())
                    or set(first_rows) & set(self.row_indexes[main["offset"]])
                    or first["trailer"].get("Size") != max(all_ids) + 1):
                raise PDFSourceError("PDF linearized first-page rows do not match the base inventory", location)
            base_parser = _Parser(_Cursor(self.data, self.context, location, 0, length), self, self, location)
            if PDFDocument.find_xref(self, base_parser) != first["offset"]:
                raise PDFSourceError("PDF linearized L does not end at the original base footer", location)
            first["linearized_main"] = main["offset"]
            main["linearized_owner"] = first["offset"]
            main["role"] = "linearized-main"
            for row in main["rows"]:
                row["role"] = "linearized-main"
        except InspectionLimit:
            raise
        except Exception as exc:
            self.problem(str(exc), getattr(exc, "location", location))

    def _revisions(self, first):
        offset, seen = first, set()
        while offset is not None:
            if offset in seen:
                raise PDFSourceError("PDF revision chain cycle", {"kind": "pdf", "xref_section_offset": offset})
            seen.add(offset)
            section = self.by_offset[offset]
            self.context.charge("records", 1, {"kind": "pdf", "xref_section_offset": offset})
            revision = {"id": "xref@%d" % offset, "section": offset, "ordinal": len(self.revisions), "status": "linked"}
            self.revisions.append(revision)
            for part in (section, self.by_offset.get(section["supplement"]), self.by_offset.get(section.get("linearized_main"))):
                if part is not None:
                    for row in part["rows"]:
                        row.update(revision=revision["ordinal"], revision_id=revision["id"], revision_status="linked")
            if section["supplement"] is not None:
                for object_id, table_row in self.row_indexes[offset].items():
                    stream_row = self.row_indexes[section["supplement"]].get(object_id)
                    if stream_row is not None:
                        self.conflicts.append({"object_id": object_id, "revision_id": revision["id"],
                                               "table_row": table_row["id"], "supplement_row": stream_row["id"],
                                               "selected_row": table_row["id"], "rule": "current-table-first"})
            offset = section["previous"]
            if section.get("linearized_main") is not None:
                offset = self.by_offset[section["linearized_main"]]["previous"]

    def chain(self, first):
        result, seen, offset = [], set(), first
        while offset is not None:
            if offset in seen:
                raise PDFSourceError("PDF revision chain cycle", {"kind": "pdf", "xref_section_offset": offset})
            seen.add(offset)
            section = self.by_offset[offset]
            result.append(section)
            if section["supplement"] is not None:
                result.append(self.by_offset[section["supplement"]])
            offset = section["previous"]
        return result

    def lookup(self, section, object_id):
        """Apply current table, own supplement and older-section precedence, including free rows."""
        seen, offset = set(), section
        while offset is not None:
            if offset in seen:
                raise PDFSourceError("PDF lookup section cycle", {"kind": "pdf", "xref_section_offset": offset})
            seen.add(offset)
            location = {"kind": "pdf", "xref_section_offset": offset, "object_id": object_id}
            self.context.charge("records", 1, location)
            row = self.row_indexes[offset].get(object_id)
            if row is not None:
                return row
            part = self.by_offset[offset]
            if part["supplement"] is not None:
                self.context.charge("records", 1, location)
                row = self.row_indexes[part["supplement"]].get(object_id)
                if row is not None:
                    return row
            offset = part["previous"]
        return None

    def view(self, revision=0):
        """Create one disposable resolver for a measured physical revision."""
        selected = next((item for item in self.revisions if item["id"] == revision or item["ordinal"] == revision), None)
        if selected is None:
            raise PDFSourceError("PDF revision does not exist", {"kind": "pdf", "revision": revision})
        return PDFRevision(self, selected)

    def row_location(self, row):
        """Expose original row identity separately from its file or decoded object location."""
        kind = {0: "free", 1: "in_use", 2: "compressed"}.get(row["type"], "unknown")
        result = {"kind": "pdf", "object_id": row["object_id"], "generation": row["generation"],
                  "revision": row["revision"], "revision_id": row["revision_id"], "revision_status": row["revision_status"],
                  "xref_section_offset": row["section"], "xref_section_role": row["role"],
                  "xref_entry_ordinal": row["ordinal"], "xref_entry_kind": kind,
                  "xref_entry_start": row["row_start"], "xref_entry_end": row["row_end"], "xref_entry_basis": row["row_basis"]}
        if row["type"] == 1:
            result["xref_offset"] = row["target"]
        elif row["type"] == 2:
            result.update(object_stream=row["target"], object_stream_index=row["index"])
        elif row["type"] == 0:
            result["next_free"] = row["target"]
        else:
            result.update(xref_unknown_type=row["type"], xref_field_2=row["target"], xref_field_3=row["field_3"])
        return result

    def read_object(self, offset, object_id, generation, document, *, bound=None):
        """Require one exact header and terminated object within its admitted physical interval."""
        location = {"kind": "pdf", "object_id": object_id, "generation": generation, "xref_offset": offset}
        self._integer(offset, "object offset", location, maximum=len(self.data) - 1)
        if bound is None:
            if self._offset_index is None:
                self._offset_index = sorted(self._offsets)
            index = bisect_right(self._offset_index, offset)
            bound = self._offset_index[index] if index < len(self._offset_index) else len(self.data)
            bound = min(bound, len(self.data))
        parser = _Parser(_Cursor(self.data, self.context, location, offset, bound), self, document, location)
        _, actual_id = parser.nexttoken()
        _, actual_generation = parser.nexttoken()
        _, keyword = parser.nexttoken()
        if (type(actual_id) is not int or actual_id != object_id or type(actual_generation) is not int
                or not 0 <= actual_generation <= 65535 or (generation is not None and actual_generation != generation)
                or keyword is not KWD(b"obj")):
            raise PDFSourceError("PDF object header ID/generation does not match its exact xref row", location)
        _, value = parser.nextobject()
        end = offset + parser.bufpos + parser.charpos
        if parser.results or not self.data[offset:end].rstrip().endswith(b"endobj"):
            raise PDFSourceError("PDF indirect object body does not terminate exactly", location)
        if isinstance(value, PDFStream):
            value.set_objid(object_id, actual_generation)
        self.claim(offset, end, "object")
        return value, offset, end

    def retain(self, identity, kind, location, data, media_type="application/octet-stream"):
        """Retain one admitted payload per physical storage and exact interpretation."""
        if identity not in self.payloads:
            self.payloads[identity] = self.context.payload(self.document, kind, location, data, media_type)
        return self.payloads[identity]

    def decode(self, stream, view, location):
        """Reuse admitted stream bytes only for the same physical and resolved decoder identity."""
        original = stream.attrs
        metadata = {key: _map_references(original[key], lambda value: value.resolve(), self.context, location)
                    for key in ("Filter", "F", "DecodeParms", "DP", "FDecodeParms") if key in original}
        span = getattr(stream, "_source_span", (location.get("xref_offset"), stream.objid))
        identity = ("decoded-stream", span, repr(metadata), view.revision["id"] if getattr(view, "decipher", None) else "plain")
        if identity in self.decoded:
            stream.data = self.context.payloads[self.decoded[identity]]
            return stream.data
        self.retain(("raw-stream", span), "pdf-stream", location, stream.rawdata or b"")
        stream.attrs = original | metadata
        try:
            decoded = self.decoder(stream, self.context, location)
        finally:
            stream.attrs = original
        artifact = self.retain(identity, "pdf-decoded-stream", location | {"encoding": "decoded"}, decoded)
        self.decoded[identity] = artifact
        return decoded

    def account_regions(self):
        """Retain unknown source spans and discover disconnected sections without stream-body searches."""
        if self._accounting_stopped:
            return tuple(self.regions)
        try:
            return self._account_regions()
        except InspectionLimit as exc:
            self._accounting_stopped = True
            location = self.context.exhausted[-1]["location"] if self.context.exhausted else {"kind": "pdf"}
            self.problem(str(exc), location)
            return tuple(self.regions)

    def _account_regions(self):
        """Propagate shared exhaustion immediately while continuing ordinary malformed-source recovery."""
        examined = set()
        while True:
            pending = [revision for revision in self.revisions if revision["id"] not in examined]
            for revision in pending:
                examined.add(revision["id"])
                try:
                    with self.view(revision["id"]) as view:
                        owner = self.by_offset[revision["section"]]
                        parts = [owner]
                        if owner["supplement"] is not None:
                            parts.append(self.by_offset[owner["supplement"]])
                        if owner.get("linearized_main") is not None:
                            parts.append(self.by_offset[owner["linearized_main"]])
                        for section in parts:
                            for row in section["rows"]:
                                if row["type"] in (1, 2) and row["object_id"] != 0 and row["id"] not in self.validated_rows:
                                    try:
                                        view.read_occurrence(row)
                                    except InspectionLimit:
                                        raise
                                    except Exception as exc:
                                        self.problem(str(exc), getattr(exc, "location", self.row_location(row)))
                except InspectionLimit:
                    raise
                except Exception as exc:
                    self.problem(str(exc), getattr(exc, "location", {"kind": "pdf", "revision_id": revision["id"]}))
            discovered = False
            intervals = []
            for start, end, _kind in sorted(self.claims):
                if intervals and start <= intervals[-1][1]:
                    intervals[-1] = intervals[-1][0], max(end, intervals[-1][1])
                else:
                    intervals.append((start, end))
            cursor = 0
            gaps = []
            for start, end in intervals:
                if cursor < start:
                    gaps.append((cursor, start))
                cursor = max(cursor, end)
            if cursor < len(self.data):
                gaps.append((cursor, len(self.data)))
            for start, end in gaps:
                if self._region(start, end):
                    discovered = True
            if not discovered:
                break
        indexed = {(row["target"], row["object_id"], row["generation"]) for row in self.rows
                   if row["type"] == 1 and self.by_offset.get(row["section"], {}).get("complete")}
        self.orphans = [item for item in self.orphans if (item["location"]["byte_start"], item["object_id"], item["generation"]) not in indexed]
        for item in self.orphans:
            self.problem("PDF unindexed object has no established revision context", item["location"])
        return tuple(self.regions)

    def _region(self, start, end):
        location = {"kind": "pdf", "byte_start": start, "byte_end": end, "byte_basis": "file"}
        parser = _Parser(_Cursor(self.data, self.context, location, start, end), self, self, location)
        discovered, nontrivial, structural = False, False, []
        while True:
            try:
                position, token = parser.nexttoken()
            except PSEOF:
                break
            except InspectionLimit:
                raise
            except Exception as exc:
                self.problem(str(exc), getattr(exc, "location", location))
                nontrivial = True
                break
            if token is KWD(b"startxref"):
                try:
                    _, _value = parser.nexttoken()
                    if type(_value) is not int:
                        nontrivial = True
                    else:
                        structural.append((position, parser.bufpos + parser.charpos))
                except PSEOF:
                    nontrivial = True
                continue
            if token is KWD(b"xref"):
                try:
                    offset = start + position
                    self._read_chain(offset)
                    for section in self.sections:
                        if section["role"] != "supplement" and not any(item["section"] == section["offset"] for item in self.revisions):
                            self.revisions.append({"id": "xref@%d" % section["offset"], "section": section["offset"], "ordinal": None, "status": "unlinked"})
                            for row in section["rows"]:
                                row["revision_id"] = "xref@%d" % section["offset"]
                    discovered = True
                except InspectionLimit:
                    raise
                except Exception as exc:
                    self.problem(str(exc), getattr(exc, "location", location))
                    nontrivial = True
                break
            if type(token) is int:
                try:
                    _, generation = parser.nexttoken()
                    _, keyword = parser.nexttoken()
                    if type(generation) is not int or keyword is not KWD(b"obj"):
                        raise PDFSourceError("PDF unclaimed source is not indirect-object syntax", location)
                    value, object_start, object_end = self.read_object(start + position, token, generation, self, bound=end)
                    if isinstance(value, PDFStream) and _name(value.attrs.get("Type")) == "XRef":
                        self._read_chain(object_start)
                        self.revisions.append({"id": "xref@%d" % object_start, "section": object_start, "ordinal": None, "status": "unlinked"})
                        for row in self.by_offset[object_start]["rows"]:
                            row["revision_id"] = "xref@%d" % object_start
                        discovered = True
                        break
                    data = self.data[object_start:object_end]
                    where = location | {"byte_start": object_start, "byte_end": object_end, "revision": None, "revision_status": "unindexed"}
                    self.orphans.append({"value": value, "data": data, "object_id": token, "generation": generation, "location": where})
                    discovered = True
                    parser.seek(object_end - start)
                    continue
                except InspectionLimit:
                    raise
                except Exception as exc:
                    self.problem(str(exc), getattr(exc, "location", location))
                    nontrivial = True
                    break
            nontrivial = True
        if not discovered:
            data = self.data[start:end]
            remaining, cursor = [], 0
            for first, last in structural:
                remaining.append(data[cursor:first])
                cursor = last
            remaining.append(data[cursor:])
            nontrivial = nontrivial or not _metadata_only(b"".join(remaining))
            kind = "pdf-unclaimed-source" if nontrivial else "pdf-source-metadata"
            artifact = self.retain(("region", start, end), kind, location, data)
            self.regions.append({"kind": kind, "location": location, "data": data, "artifact": artifact})
            self.claim(start, end, kind)
            if nontrivial:
                self.problem("PDF source region remains opaque and unparsed", location)
        return discovered


def read_pdf_source(data: bytes, context, document: str, decode_stream) -> PDFSource:
    """Read guarded physical source metadata before creating any native revision view."""
    return PDFSource(data, context, document, decode_stream)


def _metadata_only(data):
    """Recognize only whitespace and comments after native footer-token accounting."""
    index = 0
    while index < len(data):
        if data[index] in (0, 9, 10, 12, 13, 32):
            index += 1
        elif data[index] == 37:
            while index < len(data) and data[index] not in (10, 13):
                index += 1
        else:
            return False
    return True
