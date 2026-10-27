"""Bounded OLE native and Package unwrapping without file or command access."""

from __future__ import annotations

import io
from pathlib import PurePosixPath
import zipfile

from brewdoc._inspection_common import InspectionLimit
from brewdoc.common import BrewdocError, _zip_member_bytes

DOCUMENT_SUFFIXES = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb",
                               ".xls", ".ods", ".odt", ".odp", ".html", ".epub",
                               ".eml", ".md", ".txt"))


class _NativeCursor:
    """Walk declared wrapper fields without searching for document signatures."""

    def __init__(self, data, context, location):
        self.data, self.context, self.location = data, context, location
        self.position = 4
        self.fields = {}

    def integer(self, size):
        """Read a bounded little-endian wrapper field."""
        self.context.charge("records", 1, self.location)
        end = self.position + size
        if end > len(self.data):
            raise ValueError("truncated OLE native integer")
        value = int.from_bytes(memoryview(self.data)[self.position:end], "little")
        self.position = end
        return value

    def string(self, field, *, length=None, wide=False):
        """Preserve metadata text with its measured original byte span."""
        start = self.position
        self.context.charge("records", 1, self.location)
        if wide:
            size = self.integer(4) * 2
            start = self.position
            end = start + size
            encoding = "utf-16le"
        elif length is not None:
            if length < 1:
                raise ValueError("OLE Package path length excludes terminator")
            size = length
            end = start + size
            encoding = "latin-1"
        else:
            end = self.data.find(b"\0", start, min(len(self.data), start + 65_537))
            if end < 0:
                raise ValueError("unterminated or oversized OLE Package metadata")
            end += 1
            size = end - start
            encoding = "latin-1"
        if size > 65_536 or end > len(self.data):
            raise ValueError("truncated or oversized OLE Package metadata")
        if not wide and self.data[end - 1] != 0:
            raise ValueError("OLE Package metadata terminator missing")
        self.context.charge("decoded_bytes", size, self.location)
        value = self.data[start:end].decode(encoding)
        if not wide:
            value = value[:-1]
            if "\0" in value:
                raise ValueError("OLE Package path length includes trailing fields")
        self.position = end
        self.fields[field] = {"offset": start, "length": size, "encoding": encoding}
        return value


def unwrap_ole_native(data, context, *, location=None):
    """Return exact native stream offsets and bytes; the caller owns retention and recursion."""
    where = dict(location or {"kind": "ole_native"})
    result = {"status": "invalid", "variant": None, "filename": None, "label": None,
              "source_path": None, "temp_path": None, "payload": None,
              "payload_offset": None, "payload_length": None, "reason": None,
              "metadata_spans": {}, "metadata_encoding": None,
              "unicode_label": None, "unicode_source_path": None, "unicode_temp_path": None}
    context.check("source_bytes", len(data), where)
    context.charge("records", 1, where)
    if len(data) < 4:
        result["reason"] = "truncated OLE NativeDataSize"
        return result
    declared = int.from_bytes(memoryview(data)[:4], "little")
    if declared != len(data) - 4:
        result["reason"] = "OLE NativeDataSize does not match stream length"
        return result
    cursor = _NativeCursor(data, context, where)
    try:
        if data[4:6] == b"\x02\x00":
            result["variant"] = "package"
            cursor.integer(2)
            result["label"] = cursor.string("label")
            result["filename"] = result["label"]
            result["source_path"] = cursor.string("source_path")
            result["icon_index"] = cursor.integer(2)
            mode = cursor.integer(2)
            result["object_type"] = mode
            result["metadata_encoding"] = "latin-1 byte mapping; package codepage unspecified"
            if mode not in (1, 3):
                result.update(status="unsupported", reason="unsupported OLE Package object type %d" % mode)
                return result
            if cursor.position < len(data):
                result["temp_path"] = cursor.string("temp_path", length=cursor.integer(4))
            if mode == 1:
                result.update(status="linked", reason="OLE Package link retained as metadata; target not opened")
                return result
            payload_length = cursor.integer(4)
            payload_offset = cursor.position
            end = payload_offset + payload_length
            if end > len(data):
                raise ValueError("truncated OLE Package embedded payload")
            cursor.position = end
            if cursor.position < len(data):
                if len(data) - cursor.position == 2 and data[cursor.position:cursor.position + 2] == b"\0\0":
                    cursor.position += 2
                else:
                    result["unicode_temp_path"] = cursor.string("unicode_temp_path", wide=True)
                    if cursor.position < len(data):
                        result["unicode_label"] = cursor.string("unicode_label", wide=True)
                        result["unicode_source_path"] = cursor.string("unicode_source_path", wide=True)
                        result["filename"] = result["unicode_label"].rstrip("\0")
                    result["variant"] = "package_unicode"
            if cursor.position != len(data):
                raise ValueError("unrecognized OLE Package trailing bytes")
        else:
            result["variant"] = "native_data"
            payload_offset, payload_length = 4, declared
        context.check("payload_bytes", payload_length, where)
        context.charge("decoded_bytes", payload_length, where)
        result.update(status="embedded", reason=None, payload_offset=payload_offset,
                      payload_length=payload_length,
                      payload=data[payload_offset:payload_offset + payload_length])
    except (ValueError, UnicodeError) as error:
        result.update(status="invalid", reason=str(error))
    finally:
        result["metadata_spans"] = cursor.fields
    return result


def embedded_document_name(name, data, context, location):
    """Recognize supported document names or validated container entry structures."""
    if PurePosixPath(name).suffix.lower() in DOCUMENT_SUFFIXES:
        return name
    if b"%PDF-" in data[:1024]:
        return name + ".pdf"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                context.zip_budget.check("members", len(infos), name)
                names = {info.filename for info in infos}
                for part, suffix in (("word/document.xml", ".docx"), ("ppt/presentation.xml", ".pptx"),
                                     ("xl/workbook.bin", ".xlsb"), ("xl/workbook.xml", ".xlsx"),
                                     ("META-INF/container.xml", ".epub")):
                    if part in names:
                        return name + suffix
                mimetypes = [info for info in infos if info.filename == "mimetype"]
                if len(mimetypes) == 1:
                    mime = _zip_member_bytes(archive, mimetypes[0], context.zip_budget,
                                             max_bytes=256, limit_error="embedded mimetype is too large")
                    context.charge("decoded_bytes", len(mime), location)
                    suffix = {b"application/vnd.oasis.opendocument.text": ".odt",
                              b"application/vnd.oasis.opendocument.presentation": ".odp",
                              b"application/vnd.oasis.opendocument.spreadsheet": ".ods"}.get(mime)
                    if suffix is not None:
                        return name + suffix
        except (zipfile.BadZipFile, OSError, ValueError):
            return None
    return None


def inspect_compound_payloads(entries, name, context, document, *, scan_embedded, location=None):
    """Inspect supported native stream occurrences using their already admitted exact bytes."""
    for entry in entries:
        payload = entry["data"]
        if entry["kind"] != "stream" or payload is None:
            continue
        where = dict(entry["location"])
        if location:
            where = {**location, **where}
        source_name = entry["name"]
        component = entry.get("component")
        try:
            if source_name == "\x01Ole10Native":
                result = unwrap_ole_native(payload, context, location=where)
                metadata = {key: value for key, value in result.items() if key != "payload"}
                metadata.update(wrapper_component=component, wrapper_artifact=entry.get("artifact"))
                if result["status"] == "linked":
                    context.finding(document, "external_references", "ole-native-link", where,
                                    target=result["source_path"], component=component, **metadata)
                    continue
                if result["status"] != "embedded":
                    context.finding(document, "attachments", "ole-native-unreadable", where,
                                    target=result["filename"], component=component, **metadata)
                    context.problem(document, ("attachments",), result["reason"] or "OLE native payload unavailable", where)
                    continue
                payload = result["payload"]
                where.update(native_data_offset=result["payload_offset"], native_data_length=result["payload_length"])
                source_name = result["filename"] or source_name
                artifact = context.payload(document, "ole-native-payload", where, payload, "application/octet-stream")
                component = context.component(document, "ole-native-payload", where, data=payload,
                                              artifact=artifact, disposition="opaque",
                                              parent_component=entry.get("component"),
                                              wrapper_artifact=entry.get("artifact"),
                                              reason="native document bytes retained independently of inspection")
                context.finding(document, "attachments", "ole-native-payload", where,
                                target=result["filename"], artifact=artifact, component=component, **metadata)
            child_name = embedded_document_name(source_name, payload, context, where)
            if child_name is None:
                continue
            if scan_embedded is None:
                context.problem(document, ("attachments",), "supported CFB payload retained without recursive inspection callback", where)
                continue
            scan_embedded(payload, child_name, parent=document, location=where, component=component)
        except (InspectionLimit, BrewdocError) as error:
            context.problem(document, ("attachments",), str(error), where)
