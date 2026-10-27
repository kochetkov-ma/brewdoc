"""Passive PDF object inspection, independent of page rendering and text layers."""

from __future__ import annotations

import base64
import hashlib
from itertools import chain
import re
import xml.etree.ElementTree as ET
import zlib

from pdfminer.pdfdevice import PDFTextDevice
from pdfminer.pdffont import PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import PDFObjRef, PDFStream
from pdfminer.psparser import PSKeyword, PSLiteral
from pdfminer.utils import decode_text

from brewdoc._inspection_common import InspectionLimit, json_bytes
from brewdoc._pdf_content import ContentError, iter_content
from brewdoc._pdf_fonts import PDF_FONT_GLYPHS, type1_encoding
from brewdoc._pdf_xref import PDFReference, read_pdf_source
from brewdoc.common import BrewdocError, _local_name, _xml_preflight


CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                "attachments", "images", "external_references", "hidden_structures")
ACTION_TYPES = frozenset(("GoTo", "GoToR", "GoToE", "Launch", "Thread", "URI", "Sound",
                          "Movie", "Hide", "Named", "SubmitForm", "ResetForm", "ImportData",
                          "JavaScript", "SetOCGState", "Rendition", "Trans", "GoTo3DView"))
TEXT_KEYS = frozenset(("Contents", "ActualText", "Alt", "TU", "TM", "Title", "Subject",
                      "Author", "Keywords", "Creator", "Producer", "RC"))
IMAGE_FILTERS = frozenset(("DCTDecode", "DCT", "JPXDecode", "JBIG2Decode", "CCITTFaxDecode", "CCF"))


class _StreamLimit(BrewdocError):
    """A pure stream decoder refused growth before its byte allowance was exceeded."""


def _name(value) -> str:
    return str(value.name) if isinstance(value, (PSLiteral, PSKeyword)) else ""


def _action_position(location):
    """Separate action keys from identically named resource and glyph entries."""
    path = location["dictionary_path"]
    return ("/Resources/" not in path and "/CharProcs/" not in path and bool(re.search(
        r"/(?:A|OpenAction|Next)(?:\[\d+\])?$|/AA/[^/\[\]]+$", path)))


def _resolve(value):
    """Resolve indirect scalar chains without hiding missing or cyclic references."""
    seen = set()
    while isinstance(value, PDFObjRef):
        identity = id(value.doc), value.objid, getattr(value, "generation", None)
        if identity in seen:
            raise BrewdocError("PDF indirect reference cycle: object %d" % value.objid)
        seen.add(identity)
        value = value.resolve()
    return value


def _text(value) -> str:
    value = _resolve(value)
    if isinstance(value, bytes):
        return decode_text(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (PSLiteral, PSKeyword)):
        return _name(value)
    return str(value) if isinstance(value, (int, float, bool)) else ""


def _plain(value, depth=0):
    """Keep dictionary values JSON-safe without following arbitrary graph edges."""
    if depth > 8:
        return {"truncated": True}
    if isinstance(value, PDFObjRef):
        result = {"object_id": value.objid}
        if isinstance(value, PDFReference):
            result.update(generation=value.generation, reference_location=dict(value.location))
            revision = getattr(value.doc, "revision", {})
            result.update(reference_revision=revision.get("ordinal"), reference_revision_id=revision.get("id"))
        return result
    if isinstance(value, PDFStream):
        return {"stream_object_id": value.objid}
    if isinstance(value, dict):
        return {str(key): _plain(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, (bytes, PSLiteral, PSKeyword)):
        return _text(value)
    return value if value is None or isinstance(value, (str, int, float, bool)) else repr(value)


def _bounded_runlength(data: bytes, limit: int) -> bytes:
    output, offset = bytearray(), 0
    while offset < len(data):
        count = data[offset]
        offset += 1
        if count == 128:
            return bytes(output)
        length = count + 1 if count < 128 else 257 - count
        if len(output) + length > limit:
            raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
        needed = length if count < 128 else 1
        if offset + needed > len(data):
            raise BrewdocError("PDF RunLength stream is truncated")
        output.extend(data[offset:offset + length] if count < 128 else data[offset:offset + 1] * length)
        offset += needed
    raise BrewdocError("PDF RunLength stream has no end marker")


def _bounded_lzw(data: bytes, limit: int, early_change: int) -> bytes:
    """Decode PDF LZW with a bounded table and output, requiring its end marker."""
    if early_change not in (0, 1):
        raise BrewdocError("PDF LZW EarlyChange must be zero or one")
    table, output, previous = [bytes((item,)) for item in range(256)] + [None, None], bytearray(), b""
    width, bit = 9, 0
    while bit + width <= len(data) * 8:
        code = 0
        for _ in range(width):
            code = (code << 1) | ((data[bit // 8] >> (7 - bit % 8)) & 1)
            bit += 1
        if code == 256:
            table, width, previous = [bytes((item,)) for item in range(256)] + [None, None], 9, b""
            continue
        if code == 257:
            return bytes(output)
        if code < len(table) and table[code] is not None:
            item = table[code]
        elif code == len(table) and previous:
            item = previous + previous[:1]
        else:
            raise BrewdocError("PDF LZW stream has an invalid code")
        if len(output) + len(item) > limit:
            raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
        output.extend(item)
        if previous and len(table) < 4096:
            table.append(previous + item[:1])
            if width < 12 and len(table) == (1 << width) - early_change:
                width += 1
        previous = item
    raise BrewdocError("PDF LZW stream has no end marker")


def _bounded_ascii85(data: bytes, limit: int) -> bytes:
    encoded = re.sub(rb"\s+", b"", data).removeprefix(b"<~")
    if not encoded.endswith(b"~>"):
        raise BrewdocError("PDF ASCII85 stream has no end marker")
    output, group = bytearray(), bytearray()
    for value in encoded[:-2]:
        if value == ord("z"):
            if group:
                raise BrewdocError("PDF ASCII85 zero group occurs inside another group")
            chunk = b"\x00" * 4
        else:
            group.append(value)
            if len(group) < 5:
                continue
            chunk = base64.a85decode(group, adobe=False)
            group.clear()
        if len(output) + len(chunk) > limit:
            raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
        output.extend(chunk)
    if len(group) == 1:
        raise BrewdocError("PDF ASCII85 trailing group is incomplete")
    chunk = base64.a85decode(group, adobe=False) if group else b""
    if len(output) + len(chunk) > limit:
        raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
    return bytes(output) + chunk


def _filtered(data, filter_name, parameter, remaining):
    """Decode one admitted filter; check expansion before materializing its result."""
    if filter_name in ("FlateDecode", "Fl"):
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(data, remaining + 1)
        if len(decoded) > remaining or decoder.unconsumed_tail:
            raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
        if not decoder.eof:
            raise BrewdocError("PDF Flate stream is truncated")
        return decoded
    if filter_name in ("ASCIIHexDecode", "AHx"):
        encoded = re.sub(rb"\s+", b"", data)
        if not encoded.endswith(b">"):
            raise BrewdocError("PDF ASCIIHex stream has no end marker")
        encoded = encoded[:-1]
        if (len(encoded) + 1) // 2 > remaining:
            raise _StreamLimit("PDF decoded stream exceeds its remaining byte allowance")
        return bytes.fromhex((encoded + (b"0" if len(encoded) % 2 else b"")).decode("ascii"))
    if filter_name in ("ASCII85Decode", "A85"):
        return _bounded_ascii85(data, remaining)
    if filter_name in ("RunLengthDecode", "RL"):
        return _bounded_runlength(data, remaining)
    if filter_name in ("LZWDecode", "LZW"):
        return _bounded_lzw(data, remaining, int(parameter.get("EarlyChange", 1)))
    if filter_name in IMAGE_FILTERS:
        raise BrewdocError("PDF image codec retained without pixel decoding: %s" % filter_name)
    raise BrewdocError("PDF stream filter is unsupported: %s" % filter_name)


def _sample(data, offset, bits):
    """Read one high-order-first component without expanding the packed row."""
    if bits == 16:
        return (data[offset // 8] << 8) | data[offset // 8 + 1]
    return (data[offset // 8] >> (8 - bits - offset % 8)) & ((1 << bits) - 1)


def _set_sample(data, offset, bits, value):
    """Replace one packed component while preserving neighbors and padding bits."""
    if bits == 16:
        data[offset // 8:offset // 8 + 2] = value.to_bytes(2, "big")
    else:
        shift, mask = 8 - bits - offset % 8, (1 << bits) - 1
        data[offset // 8] = (data[offset // 8] & ~(mask << shift)) | (value << shift)


def _predicted(data: bytes, parameter: dict, remaining: int) -> bytes:
    """Reverse declared TIFF/PNG byte transforms within checked row and output bounds."""
    predictor = parameter.get("Predictor", 1)
    if type(predictor) is not int:
        raise BrewdocError("PDF Predictor is not an integer")
    if predictor == 1:
        return data
    if predictor != 2 and not 10 <= predictor <= 15:
        raise BrewdocError("PDF predictor is unsupported: %d" % predictor)
    colors, columns, bits = (parameter.get("Colors", 1), parameter.get("Columns", 1),
                             parameter.get("BitsPerComponent", 8))
    if (type(colors) is not int or colors < 1 or type(columns) is not int or columns < 1
            or type(bits) is not int or bits not in (1, 2, 4, 8, 16)):
        raise BrewdocError("PDF predictor dimensions or BitsPerComponent are invalid")
    row_bytes = (colors * columns * bits + 7) // 8
    if row_bytes > remaining:
        raise _StreamLimit("PDF predictor row exceeds its remaining byte allowance")
    stride = row_bytes + (predictor >= 10)
    if len(data) % stride:
        raise BrewdocError("PDF predictor row is truncated")
    output_size = len(data) // stride * row_bytes
    if output_size > remaining:
        raise _StreamLimit("PDF predictor output exceeds its remaining byte allowance")
    if predictor == 2:
        output = bytearray(data)
        mask = (1 << bits) - 1
        for row in range(0, len(output), row_bytes):
            for sample in range(colors, colors * columns):
                offset = row * 8 + sample * bits
                value = (_sample(output, offset, bits) + _sample(output, offset - colors * bits, bits)) & mask
                _set_sample(output, offset, bits, value)
        return bytes(output)
    output, previous = bytearray(output_size), bytearray(row_bytes)
    pixel_bytes = (colors * bits + 7) // 8
    for row, start in enumerate(range(0, len(data), stride)):
        tag = data[start]
        if tag > 4:
            raise BrewdocError("PDF PNG predictor row tag is invalid: %d" % tag)
        current = bytearray(row_bytes)
        for index in range(row_bytes):
            left = current[index - pixel_bytes] if index >= pixel_bytes else 0
            above = previous[index]
            upper_left = previous[index - pixel_bytes] if index >= pixel_bytes else 0
            if tag == 0:
                prediction = 0
            elif tag == 1:
                prediction = left
            elif tag == 2:
                prediction = above
            elif tag == 3:
                prediction = (left + above) // 2
            else:
                value = left + above - upper_left
                distances = abs(value - left), abs(value - above), abs(value - upper_left)
                prediction = (left if distances[0] <= distances[1] and distances[0] <= distances[2]
                              else above if distances[1] <= distances[2] else upper_left)
            current[index] = (data[start + 1 + index] + prediction) & 255
        output[row * row_bytes:(row + 1) * row_bytes] = current
        previous = current
    return bytes(output)


def _decode_stream(stream: PDFStream, context, location: dict) -> bytes:
    """Decode textual or embedded streams before growth; image codecs stay encoded."""
    failure = getattr(stream, "_inspection_decode_error", None)
    if failure is not None:
        raise BrewdocError(failure)
    if stream.data is not None:
        return stream.data
    try:
        return _admit_stream(stream, context, location)
    except (BrewdocError, ValueError, zlib.error) as exc:
        stream._inspection_decode_error = str(exc)
        raise


def _admit_stream(stream, context, location):
    """Admit every filter/predictor result to the same cumulative decoder allowance."""
    data = stream.rawdata or b""
    context.check("payload_bytes", len(data), location)
    if stream.decipher is not None:
        data = stream.decipher(stream.objid, stream.genno, data, stream.attrs)
    filters = _resolve(stream.attrs.get("Filter", stream.attrs.get("F", [])))
    filters = filters if isinstance(filters, list) else [filters]
    parameters = _resolve(stream.attrs.get("DecodeParms", stream.attrs.get("DP", {})))
    parameters = parameters if isinstance(parameters, list) else [parameters] * len(filters)
    if len(parameters) != len(filters):
        raise BrewdocError("PDF stream filter and parameter counts differ")
    for filter_value, parameter in zip(filters, parameters):
        filter_name = _name(_resolve(filter_value))
        parameter = _resolve(parameter) or {}
        if not isinstance(parameter, dict):
            raise BrewdocError("PDF stream decoder parameters are not a dictionary")
        remaining = min(context.remaining("decoded_bytes"), context.limits["payload_bytes"])
        try:
            data = _filtered(data, filter_name, parameter, remaining)
        except _StreamLimit:
            context.check("payload_bytes", remaining + 1, location)
            context.charge("decoded_bytes", remaining + 1, location)
            raise
        context.check("payload_bytes", len(data), location)
        context.charge("decoded_bytes", len(data), location)
        prediction = {key: _resolve(parameter.get(key, default)) for key, default in (
            ("Predictor", 1), ("Colors", 1), ("Columns", 1), ("BitsPerComponent", 8))}
        if type(prediction["Predictor"]) is not int or prediction["Predictor"] != 1:
            if filter_name not in ("FlateDecode", "Fl", "LZWDecode", "LZW"):
                raise BrewdocError("PDF predictors require a Flate or LZW filter")
            remaining = min(context.remaining("decoded_bytes"), context.limits["payload_bytes"])
            try:
                data = _predicted(data, prediction, remaining)
            except _StreamLimit:
                context.check("payload_bytes", remaining + 1, location)
                context.charge("decoded_bytes", remaining + 1, location)
                raise
            context.charge("decoded_bytes", len(data), location)
    if not filters:
        context.charge("decoded_bytes", len(data), location)
    stream.data = data
    return data


def _pages(document, context):
    """Map source page objects iteratively; report malformed page-tree cycles."""
    result, seen, stack = [], set(), [(document.catalog.get("Pages"), (), {})]
    while stack:
        value, ancestry, inherited = stack.pop()
        object_id = value.objid if isinstance(value, PDFObjRef) else None
        location = getattr(document, "_inspection_sources", {}).get(object_id, {}).get("location", {}) | {
            "kind": "pdf", "dictionary_path": "Catalog/Pages", "object_id": object_id}
        context.charge("records", 1, location)
        if object_id is not None and object_id in ancestry:
            context.problem(document._inspection_id, CAPABILITIES, "PDF page-tree cycle", location)
            continue
        try:
            value = _resolve(value)
        except InspectionLimit:
            raise
        except BrewdocError as exc:
            context.problem(document._inspection_id, CAPABILITIES, str(exc), location)
            continue
        if not isinstance(value, dict):
            context.problem(document._inspection_id, CAPABILITIES, "PDF page-tree node is not a dictionary", location)
            continue
        value = inherited | value
        if _name(value.get("Type")) == "Page":
            if object_id not in seen:
                result.append((object_id, value))
                seen.add(object_id)
            else:
                context.problem(document._inspection_id, CAPABILITIES,
                                "PDF page object occurs more than once in the page tree", location)
        else:
            kids = _resolve(value.get("Kids", []))
            if not isinstance(kids, list):
                raise BrewdocError("PDF page-tree Kids is not an array")
            ancestors = ancestry + ((object_id,) if object_id is not None else ())
            parent = {key: item for key, item in value.items() if key in PDFPage.INHERITABLE_ATTRS}
            stack.extend((child, ancestors, parent) for child in reversed(kids))
    return result


def _xml_packet(data, context, document, location, artifact, prefix):
    """Inspect preserved XFA or metadata XML without evaluating document code."""
    _xml_preflight(data, "PDF " + prefix, context.zip_budget)
    root = ET.fromstring(data)
    stack = [(root, "/" + _local_name(root.tag) + "[1]")]
    while stack:
        node, path = stack.pop()
        here = location | {"xpath": path}
        context.charge("records", 1, here)
        tag = _local_name(node.tag)
        text = "".join(node.itertext()) if tag in ("script", "text", "exData") else node.text
        if tag == "script":
            size = sum(1 if ord(character) < 128 else 2 if ord(character) < 2048
                       else 3 if ord(character) < 65536 else 4 for character in text or "")
            context.check("payload_bytes", size, here)
            context.check("retained_bytes", context.consumed["retained_bytes"] + size, here)
            context.charge("decoded_bytes", size, here)
            source = (text or "").encode("utf-8")
            script_artifact = context.payload(document, "script", here, source, "text/plain; charset=utf-8")
            script_component = context.component(document, "xml-script", here, data=source, artifact=script_artifact,
                                                  container_artifact=artifact, encoding="UTF-8")
            context.finding(document, "scripts", prefix + "-script", here, text=text or "", artifact=script_artifact,
                            component=script_component, content_type=node.get("contentType"), encoding="UTF-8",
                            encoding_source="decoded-xml-character-data", container_artifact=artifact)
        elif tag in ("field", "subform", "submit", "event"):
            target = node.get("target") if tag == "submit" else None
            context.finding(document, "forms", prefix + "-" + tag, here, target=target,
                            attributes=dict(node.attrib), artifact=artifact)
            if target is not None:
                context.finding(document, "links", prefix + "-submit", here, target=target, artifact=artifact)
        elif text and text.strip():
            context.finding(document, "text", prefix + "-text", here, text=text, artifact=artifact)
        counts = {}
        children = []
        for child in node:
            name = _local_name(child.tag)
            counts[name] = counts.get(name, 0) + 1
            children.append((child, "%s/%s[%d]" % (path, name, counts[name])))
        stack.extend(reversed(children))


def _script_text(data, version):
    """Decode PDF text strings/streams from their normative BOM and document version."""
    if not version or not version["supported"]:
        raise BrewdocError("PDF JavaScript document version is unsupported or unavailable")
    if tuple(map(int, version["effective"].split("."))) < (1, 3):
        raise BrewdocError("PDF JavaScript requires document version 1.3 or newer")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16be"), "UTF-16BE", "bom"
    if version["effective"] == "2.0" and data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "UTF-8", "bom"
    return decode_text(data), "PDFDocEncoding", "pdf-text-default"


def _action(value, context, document, location, component, streams, *, version=None, retain=None,
            source_artifact=None, source_member_path=None):
    """Recover original action source with checked version and physical payload identity."""
    action = _name(_resolve(value.get("S")))
    if action not in ACTION_TYPES:
        if action and (_name(value.get("Type")) == "Action" or _action_position(location)):
            context.finding(document, "actions", "pdf-unsupported-action", location,
                            component=component, action=action, entries=_plain(value))
            context.problem(document, ("actions",), "PDF action type is unsupported: " + action, location)
        return
    required = {"URI": "URI", "JavaScript": "JS", "SubmitForm": "F", "ImportData": "F",
                "GoTo": "D", "GoToR": "F", "GoToE": "D"}.get(action)
    if required is not None and required not in value:
        context.problem(document, ("actions", "scripts" if action == "JavaScript" else "links"),
                        "PDF %s action is missing /%s" % (action, required), location)
    target = None
    if action == "URI":
        target = _text(value.get("URI"))
    elif action in ("Launch", "SubmitForm", "ImportData", "GoToR", "GoToE", "Thread"):
        file_spec = _resolve(value.get("F"))
        if isinstance(file_spec, dict):
            target = _text(file_spec.get("UF", file_spec.get("F")))
        else:
            target = _text(file_spec)
        if not target and action == "Launch":
            windows = _resolve(value.get("Win"))
            if isinstance(windows, dict):
                target = _text(windows.get("F"))
    context.finding(document, "actions", "pdf-action", location, component=component,
                    target=target, action=action, entries=_plain(value))
    if action in ("GoTo", "GoToR", "GoToE") and "D" in value:
        destination = _resolve(value["D"])
        context.finding(document, "links", "pdf-destination", location, component=component,
                        target=_text(destination) or None,
                        text=json_bytes(_plain(destination)).decode().rstrip(), destination=_plain(destination))
    if target is not None:
        context.finding(document, "links", "pdf-" + action.lower(), location,
                        component=component, target=target)
        context.finding(document, "external_references", "pdf-" + action.lower(), location,
                        component=component, target=target)
    if action == "SubmitForm":
        context.finding(document, "forms", "pdf-submit", location, component=component,
                        target=target, fields=_plain(value.get("Fields")), flags=_plain(value.get("Flags")))
    if action == "Launch":
        for platform in ("Win", "Mac", "Unix"):
            specific = _resolve(value.get(platform))
            if isinstance(specific, dict) and "F" in specific:
                here = location | {"dictionary_path": location["dictionary_path"] + "/" + platform + "/F"}
                context.finding(document, "links", "pdf-launch", here, component=component,
                                target=_text(specific["F"]), platform=platform, entries=_plain(specific))
    if action == "JavaScript":
        if "JS" not in value:
            return
        script = _resolve(value.get("JS"))
        if isinstance(script, PDFStream):
            artifact = streams.get(script.objid)
            try:
                script_data = _decode_stream(script, context, location | {"dictionary_path": location["dictionary_path"] + "/JS"})
            except InspectionLimit:
                raise
            except BrewdocError as exc:
                context.problem(document, ("scripts",), str(exc), location)
                context.finding(document, "scripts", "pdf-javascript-unavailable", location,
                                component=component, artifact=artifact, source_type="text-stream",
                                encoding="unavailable", reason=str(exc))
                return
            source_type = "text-stream"
        else:
            if not isinstance(script, bytes):
                context.problem(document, ("scripts",), "PDF JavaScript source is not a text string or stream", location)
                return
            script_data = script
            artifact = (retain(("script", source_artifact or component, source_member_path,
                                hashlib.sha256(script_data).hexdigest()), "script", location,
                               script_data, "application/javascript") if retain is not None and source_member_path is not None else
                        context.payload(document, "script", location, script_data, "application/javascript"))
            source_type = "text-string"
        physical = {"source_member_path": [*source_member_path, "JS"]} if (
            source_type == "text-string" and source_member_path is not None) else {}
        try:
            source, encoding, encoding_source = _script_text(script_data, version)
        except (BrewdocError, UnicodeError) as exc:
            context.problem(document, ("scripts",), str(exc), location)
            context.finding(document, "scripts", "pdf-javascript-unavailable", location,
                            component=component, artifact=artifact, source_type=source_type,
                            encoding="unavailable", reason=str(exc), version=version, **physical)
            return
        context.finding(document, "scripts", "pdf-javascript", location, component=component,
                        text=source, artifact=artifact, source_type=source_type, encoding=encoding,
                        encoding_source=encoding_source, pdf_version=version["effective"],
                        version_source=version["source"], header_version=version["header_version"],
                        catalog_version=version["catalog_version"], **physical)


def _inspect_graph(document, objects, components, streams, sources, context, document_id, scan_embedded,
                   *, roots=None, pages=None):
    """Inspect one checked view with original source identity on every graph occurrence."""
    pages = _pages(document, context) if pages is None else pages
    page_key = getattr(document, "_inspection_page_key", "page")
    page_numbers = {object_id: index for index, (object_id, _value) in enumerate(pages, 1)}
    visited, reached = set(), set()
    if roots is None:
        catalog_id = next((key for key, value in objects.items() if value is document.catalog), None)
        roots = [(document.catalog, sources.get(catalog_id, {}).get("location", {}) | {
            "kind": "pdf", "dictionary_path": "Catalog"}, ())]
        roots.extend((value, sources[object_id]["location"] | {"dictionary_path": "Object"}, ())
                     for object_id, value in objects.items())
    for root in roots:
        if root[1].get("object_id") in reached:
            continue
        stack = [root]
        while stack:
            value, location, ancestry = stack.pop()
            context.charge("records", 1, location)
            if isinstance(value, PDFObjRef):
                object_id = value.objid
                reached.add(object_id)
                path = location["dictionary_path"]
                referring = dict(value.location) if isinstance(value, PDFReference) else {}
                referring.update({key: location[key] for key in (
                    "revision", "revision_id", "source_revision", "source_revision_id") if key in location})
                referring["dictionary_path"] = path
                target = sources.get(object_id, {})
                if not target and isinstance(value, PDFReference) and hasattr(value.doc, "revision"):
                    row = value.doc.source.lookup(value.doc.revision["section"], object_id)
                    if row is not None:
                        target = {"location": value.doc.source.row_location(row) | {
                            "source_revision": row["revision"], "source_revision_id": row["revision_id"],
                            "revision": value.doc.revision["ordinal"], "revision_id": value.doc.revision["id"]}}
                scope = {key: location[key] for key in (
                    "kind", "dictionary_path", "page", "revision_page", "annotation_ordinal", "field_path",
                    "revision", "revision_id", "reference_revision", "reference_revision_id", "source_selection") if key in location}
                location = scope | target.get("location", {}) | {
                    "object_id": object_id, "dictionary_path": path, "referring_source": referring,
                    "requested_generation": getattr(value, "generation", None)}
                location["source_component"] = target.get("component")
                if object_id in page_numbers:
                    location[page_key] = page_numbers[object_id]
                identity = id(value.doc), object_id, getattr(value, "generation", None)
                if identity in ancestry:
                    context.problem(document_id, ("actions", "links", "scripts", "forms"),
                                    "PDF graph reference cycle", location)
                    continue
                try:
                    value = _resolve(value)
                except InspectionLimit:
                    raise
                except BrewdocError as exc:
                    context.problem(document_id, CAPABILITIES, str(exc), location)
                    continue
                ancestry += (identity,)
            action_occurrence = (isinstance(value, dict) and _name(value.get("S")) in ACTION_TYPES
                                 or _action_position(location) or location["dictionary_path"].endswith("/AA"))
            marker = (id(value), location.get("reference_revision_id"), location.get(page_key), location.get("annotation_ordinal"),
                      location.get("field_path"), location["dictionary_path"] if action_occurrence else None)
            if isinstance(value, (dict, list, PDFStream)):
                if marker in visited:
                    continue
                visited.add(marker)
            if isinstance(value, PDFStream):
                if _action_position(location):
                    context.problem(document_id, ("actions",), "PDF action is a stream instead of a dictionary", location)
                prefix = ("xfa" if "/XFA" in location["dictionary_path"] else
                          "metadata" if _name(value.attrs.get("Type")) == "Metadata" else None)
                if prefix is not None:
                    try:
                        packet = _decode_stream(value, context, location)
                        _xml_packet(packet, context, document_id, location, streams.get(value.objid), prefix)
                    except InspectionLimit:
                        raise
                    except (BrewdocError, ET.ParseError, UnicodeError, ValueError) as exc:
                        context.problem(document_id, ("forms", "scripts", "links", "text"), str(exc), location)
                value = value.attrs
            if isinstance(value, dict):
                component = location.get("source_component", components.get(location.get("object_id")))
                try:
                    _action(value, context, document_id, location, component, streams, version=document.inspection_version,
                            retain=getattr(document, "_inspection_retain", None),
                            source_artifact=getattr(document, "_inspection_payloads", {}).get(component),
                            source_member_path=getattr(document, "_inspection_member_paths", {}).get(id(value)))
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError) as exc:
                    context.problem(document_id, ("actions", "scripts", "links", "forms"), str(exc), location)
                if "FT" in value or ("T" in value and "field_path" in location):
                    field_name = _text(value.get("T"))
                    parent_name = location.get("field_path", "")
                    field_path = ".".join(item for item in (parent_name, field_name) if item)
                    location = location | {"field_path": field_path}
                    context.finding(document_id, "forms", "pdf-field", location, component=component,
                                    text=_text(value.get("V")), name=field_name,
                                    field_type=_name(_resolve(value.get("FT"))),
                                    value=_plain(value.get("V")), default=_plain(value.get("DV")),
                                    flags=_plain(value.get("Ff")))
                if "EF" in value:
                    filename = _text(value.get("UF", value.get("F"))) or "attachment.bin"
                    files = _resolve(value["EF"])
                    if isinstance(files, dict):
                        for key, payload in files.items():
                            here = location | {"dictionary_path": location["dictionary_path"] + "/EF/" + key}
                            payload = _resolve(payload)
                            if not isinstance(payload, PDFStream):
                                context.problem(document_id, ("attachments",), "PDF embedded file is not a stream", here)
                                continue
                            try:
                                embedded = _decode_stream(payload, context, here)
                            except InspectionLimit:
                                raise
                            except (BrewdocError, ValueError, zlib.error) as exc:
                                context.problem(document_id, ("attachments",), str(exc), here)
                                continue
                            artifact = streams.get(payload.objid)
                            context.finding(document_id, "attachments", "pdf-embedded-file", here,
                                            component=components.get(payload.objid), text=filename, artifact=artifact)
                            if scan_embedded is not None:
                                scan_embedded(embedded, filename, context, parent=document_id,
                                              location=here, component=components.get(payload.objid))
                children = []
                for key, item in value.items():
                    here = location | {"dictionary_path": location["dictionary_path"] + "/" + key}
                    if key in TEXT_KEYS:
                        try:
                            if isinstance(_resolve(item), (bytes, str)):
                                context.finding(document_id, "text", "pdf-string", here, component=component, text=_text(item))
                        except InspectionLimit:
                            raise
                        except BrewdocError as exc:
                            context.problem(document_id, ("text",), str(exc), here)
                    if key in ("Parent", "P", "Prev"):
                        continue
                    if (key == "Data" and _name(value.get("Type")) == "SigRef"
                            and _resolve(item) is document.catalog):
                        context.finding(document_id, "inventory", "pdf-signature-data-reference", here,
                                        component=component, text=json_bytes(_plain(item)).decode().rstrip(),
                                        reference=_plain(item))
                        continue
                    if key == "Dest":
                        context.finding(document_id, "links", "pdf-destination", here,
                                        component=component, target=_text(item) or None, destination=_plain(item))
                        continue
                    if key == "OpenAction":
                        try:
                            destination = _resolve(item)
                        except InspectionLimit:
                            raise
                        except BrewdocError:
                            children.append((item, here, ancestry))
                            continue
                        if not isinstance(destination, (dict, PDFStream)):
                            context.finding(document_id, "links", "pdf-open-destination", here,
                                            component=component, target=_text(destination) or None,
                                            text=json_bytes(_plain(destination)).decode().rstrip(),
                                            destination=_plain(destination))
                            continue
                    if (key == "Fields" and _name(value.get("S")) in ("SubmitForm", "ResetForm")) or (
                            key == "D" and _name(value.get("S")) in ("GoTo", "GoToR", "GoToE")):
                        continue
                    if key == "Base" and location["dictionary_path"].endswith("/URI"):
                        context.finding(document_id, "links", "pdf-base-uri", here,
                                        component=component, target=_text(item))
                    if key == "OCProperties":
                        context.finding(document_id, "hidden_structures", "pdf-optional-content", here,
                                        component=component, properties=_plain(item))
                    if key == "Fields" or (key == "Kids" and "field_path" in location):
                        here["field_path"] = location.get("field_path", "")
                    children.append((item, here, ancestry))
                stack.extend(reversed(children))
            elif isinstance(value, list):
                children = []
                for index, item in enumerate(value, 1):
                    here = location | {"dictionary_path": location["dictionary_path"] + "[%d]" % index}
                    if location["dictionary_path"].endswith("/Annots"):
                        here["annotation_ordinal"] = index
                    children.append((item, here, ancestry))
                stack.extend(reversed(children))
    return pages


class _ImageOccurrences:
    """Replay admitted source content while keeping payload inventory physically distinct."""

    def __init__(self, pdf, pages, objects, sources, context, document, version, decode_stream, cache, roots):
        self.pdf, self.pages, self.objects, self.sources = pdf, pages, objects, sources
        self.context, self.document, self.version = context, document, version
        self.decode_stream, self.cache = decode_stream, cache
        self.parsed, self.interpretations, self.content_seen, self.annotations_seen = {}, set(), set(), set()
        self.active_fonts, self.active_declarations = set(), set()
        location = {"kind": "pdf", "dictionary_path": "Images/AlternateRoots"}
        context.charge("records", len(roots), location)
        context.charge("retained_bytes", len(roots) * 96, location)
        self.roots = roots
        self.root_sources = {id(value): entry for _key, value, entry in roots if isinstance(value, PDFStream)}

    def location(self, base, **fields):
        """Admit location/path copies before allocating occurrence metadata."""
        self.context.charge("records", 1, base)
        size = sum(len(value) * 4 for value in fields.values() if isinstance(value, str))
        self.context.check("payload_bytes", size, base)
        self.context.charge("retained_bytes", size + (len(base) + len(fields)) * 16, base)
        return base | fields

    def path(self, base, parts, location):
        """Admit the combined resource path before joining any source name."""
        parts = (parts,) if isinstance(parts, str) else parts
        size = (len(base) + sum(len(part) for part in parts)) * 4
        self.context.check("payload_bytes", size, location)
        self.context.charge("retained_bytes", size, location)
        return "".join((base, *parts))

    def value(self, value):
        """Resolve only through the caller's admitted objects in this checked native view."""
        seen = set()
        while isinstance(value, PDFObjRef):
            location = self.sources.get(value.objid, {}).get("location", {"kind": "pdf", "object_id": value.objid})
            self.context.check("nesting", len(seen) + 1, location)
            self.context.charge("records", 1, location)
            if value.doc is not self.pdf or value.objid in seen:
                raise BrewdocError("PDF image reference is foreign, cyclic or not admitted: %d" % value.objid)
            seen.add(value.objid)
            resolved = value.resolve()
            if value.objid not in self.objects:
                raise BrewdocError("PDF image reference is not admitted: %d" % value.objid)
            admitted = self.objects[value.objid]
            if not isinstance(admitted, PDFObjRef) and resolved is not admitted:
                raise BrewdocError("PDF image reference differs from its admitted source: %d" % value.objid)
            value = admitted
        return value

    def source(self, stream):
        """Require caller-inventoried physical stream identity before linking an artifact."""
        if not isinstance(stream, PDFStream):
            raise BrewdocError("PDF image/content stream has no admitted source identity")
        source = self.root_sources.get(id(stream), self.sources.get(stream.objid))
        if source is None:
            raise BrewdocError("PDF image/content stream has no admitted source identity")
        if source.get("encoded_artifact") is None or source.get("component") is None:
            raise BrewdocError("PDF image/content source lacks encoded inventory")
        return source

    def resources(self, raw, owner, path, fallback=None):
        """Keep the actual resource owner or an explicit invocation-bound fallback."""
        self.context.charge("records", 1, owner)
        self.context.charge("retained_bytes", 128, owner)
        if raw is None:
            return fallback if fallback is not None else {"values": {}, "owner": owner, "path": path, "missing": True}
        if isinstance(raw, PDFObjRef):
            owner = self.sources.get(raw.objid, {}).get("location", owner)
        values = self.value(raw)
        if not isinstance(values, dict):
            raise BrewdocError("PDF image Resources is not a dictionary")
        return {"values": values, "owner": owner, "path": path, "missing": False}

    def page_resources(self, object_id, page, location):
        """Find inherited Resources on its declaring admitted page-tree node."""
        current, seen = object_id, set()
        while current is not None:
            self.context.check("nesting", len(seen) + 1, location)
            self.context.charge("records", 1, location)
            if current in seen:
                raise BrewdocError("PDF image page resource inheritance cycle")
            seen.add(current)
            node = self.objects.get(current)
            if not isinstance(node, dict):
                raise BrewdocError("PDF image page resource owner is not admitted")
            if "Resources" in node:
                return self.resources(node["Resources"], self.sources[current]["location"], "Object/Resources")
            parent = node.get("Parent")
            if parent is None:
                break
            self.value(parent)
            if not isinstance(parent, PDFObjRef):
                raise BrewdocError("PDF page resource Parent is not an indirect reference")
            current = parent.objid
        if "Resources" in page:
            raise BrewdocError("PDF inherited Resources has no admitted declaring owner")
        return self.resources(None, location, "Page/Resources")

    def fingerprint(self, value, location, ancestry=()):
        """Bound the colour interpretation key without retaining native resource graphs."""
        self.context.check("nesting", len(ancestry) + 1, location)
        self.context.charge("records", 1, location)
        self.context.charge("retained_bytes", 256, location)
        if isinstance(value, PDFObjRef):
            if value.objid in ancestry:
                raise BrewdocError("PDF image colour resource cycle")
            identity = self.sources.get(value.objid)
            if identity is None:
                raise BrewdocError("PDF image colour resource is not admitted")
            return {"source": identity.get("encoded_artifact") or identity["component"],
                    "value": self.fingerprint(self.value(value), location, ancestry + (value.objid,))}
        if isinstance(value, PDFStream):
            return {"source": self.source(value)["encoded_artifact"],
                    "N": self.fingerprint(value.attrs.get("N"), location, ancestry)}
        if isinstance(value, dict):
            self.context.charge("retained_bytes", sum(len(key) * 6 + 32 for key in value), location)
            return {key: self.fingerprint(item, location, ancestry + (None,)) for key, item in value.items()}
        if isinstance(value, list):
            self.context.charge("retained_bytes", len(value) * 16, location)
            return [self.fingerprint(item, location, ancestry + (None,)) for item in value]
        if isinstance(value, bytes):
            self.context.charge("decoded_bytes", len(value), location)
            return {"sha256": hashlib.sha256(value).hexdigest(), "byte_size": len(value)}
        if isinstance(value, PSLiteral):
            self.context.charge("retained_bytes", (len(_name(value)) + 1) * 6, location)
            return "/" + _name(value)
        if isinstance(value, str):
            self.context.charge("retained_bytes", len(value) * 6, location)
            return value
        if value is None or type(value) in (bool, int, float):
            return value
        raise BrewdocError("PDF colour resource has an invalid native value: " + type(value).__name__)

    def colors(self, bundle, location):
        """Separate parsed content by its checked colour-resource interpretation."""
        raw = bundle["values"].get("ColorSpace", {})
        owner = self.sources.get(raw.objid, {}).get("location", bundle["owner"]) if isinstance(raw, PDFObjRef) else bundle["owner"]
        path = self.path(bundle["path"], "/ColorSpace", owner)
        origin = self.location(owner, dictionary_path=path, resource_owner=bundle["owner"])
        valid, descriptions = {}, {}
        try:
            values = self.value(raw)
            if not isinstance(values, dict):
                raise BrewdocError("PDF image ColorSpace resources is not a dictionary")
        except InspectionLimit:
            raise
        except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
            self.problem(exc, origin)
            values = {}
            descriptions["unavailable_resources"] = True
        for name, value in values.items():
            target = self.sources.get(value.objid, {}).get("location", owner) if isinstance(value, PDFObjRef) else owner
            here = self.location(target, dictionary_path=self.path(path, ("/", name), target), resource_owner=bundle["owner"])
            self.context.charge("records", 1, here)
            self.context.charge("retained_bytes", len(name) * 6 + 64, here)
            try:
                descriptions[name] = self.fingerprint(value, here)
                valid[name] = value
            except InspectionLimit:
                raise
            except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                self.problem(exc, here)
                descriptions[name] = {"unavailable": True}
        fingerprint = json_bytes(descriptions)
        self.context.charge("retained_bytes", len(fingerprint), location)
        return valid, hashlib.sha256(fingerprint).hexdigest()

    def color(self, value):
        """Resolve colour declarations and ICC component evidence inside this view."""
        value = self.value(value)
        if isinstance(value, list):
            self.context.charge("records", len(value), {"kind": "pdf", "dictionary_path": "ColorSpace"})
            self.context.charge("retained_bytes", len(value) * 16, {"kind": "pdf", "dictionary_path": "ColorSpace"})
            result = [self.value(item) for item in value]
            if result and _name(result[0]) == "ICCBased" and len(result) == 2:
                profile = result[1].attrs if isinstance(result[1], PDFStream) else result[1]
                if isinstance(profile, dict):
                    result[1] = {"N": self.value(profile.get("N"))}
            return result
        return value

    def inline_decode(self, attrs, encoded, context, location):
        """Apply the existing bounded decoder up to a terminal image representation."""
        count = len(attrs["Filter"]) if isinstance(attrs["Filter"], list) else 1
        context.charge("records", count + 1, location)
        context.charge("retained_bytes", count * 32 + 128, location)
        filters = attrs["Filter"] if isinstance(attrs["Filter"], list) else [attrs["Filter"]]
        parameters = attrs.get("DecodeParms", {})
        parameters = parameters if isinstance(parameters, list) else [parameters] * len(filters)
        if _name(filters[-1]) in ("DCTDecode", "DCT", "CCITTFaxDecode", "CCF"):
            filters, parameters = filters[:-1], parameters[:-1]
        return self.decode_stream(PDFStream({"Filter": filters, "DecodeParms": parameters}, encoded), context, location)

    def parse(self, streams, bundle, location):
        """Cache one small source interpretation inside this view only."""
        colors, interpretation = self.colors(bundle, location)
        segments, identities = [], []
        for stream in streams:
            source = self.source(stream)
            self.context.charge("records", 1, location)
            identity = self.location(source["location"], component=source["component"],
                                     encoded_artifact=source["encoded_artifact"],
                                     decoded_artifact=source.get("decoded_artifact") or source["encoded_artifact"])
            segments.append({"source": identity, "data": self.decode_stream(stream, self.context, source["location"])})
            identities.append((identity["encoded_artifact"], identity["decoded_artifact"]))
        key = tuple(identities), interpretation, self.version
        if key not in self.parsed:
            records = []
            try:
                for record in iter_content(segments, self.context, location,
                                           resolve_color=lambda name: self.color(colors.get(name)),
                                           version=self.version, decode=self.inline_decode):
                    self.context.charge("records", 1, location)
                    records.append(record)
            except ContentError as exc:
                origin = exc.segments[0]["source"] if exc.segments else location
                self.problem(str(exc), self.location(origin, decoded_span=exc.span, source_segments=exc.segments))
            self.context.charge("records", 1, location)
            self.parsed[key] = records
        return self.parsed[key], interpretation

    def inline(self, record, interpretation, bundle):
        """Retain one exact physical payload while recording each resource interpretation."""
        self.context.charge("records", len(record["payload_segments"]), record["segments"][0]["source"])
        self.context.charge("retained_bytes", len(record["payload_segments"]) * 64, record["segments"][0]["source"])
        key = tuple((part["source"]["encoded_artifact"], tuple(part["span"]))
                    for part in record["payload_segments"])
        location = self.location(record["segments"][0]["source"], span_basis="decoded-stream",
                                 operator_span=record["span"], payload_span=record["payload_span"],
                                 source_segments=record["payload_segments"])
        if key not in self.cache:
            self.context.charge("records", 1, location)
            artifact = self.context.payload(self.document, "image", location, record["payload"], "application/octet-stream")
            component = self.context.component(self.document, "pdf-inline-image", location,
                                               data=record["payload"], artifact=artifact)
            self.context.finding(self.document, "images", "pdf-inline-image", location,
                                 component=component, artifact=artifact)
            self.cache[key] = {"artifact": artifact, "component": component}
        inventory = self.cache[key]
        owner = json_bytes(bundle["owner"])
        self.context.charge("retained_bytes", len(owner), location)
        marker = key, interpretation, self.version, owner, bundle["path"]
        if marker not in self.interpretations:
            self.context.charge("records", 1, location)
            self.interpretations.add(marker)
            self.context.finding(self.document, "images", "pdf-inline-image-interpretation", location,
                                 **inventory, effective_version=list(self.version), resource_interpretation=interpretation,
                                 resource_owner=bundle["owner"], resource_path=bundle["path"])
        return inventory

    def occurrence(self, stream, scope, path, kind, bundle, invocation, *, record=None, resource_name=None):
        """Link declarations and invocations to existing encoded source inventory."""
        source = self.source(stream)
        base = record["segments"][0]["source"] if record is not None else source["location"]
        location = self.location(base, **scope, occurrence_kind=kind, dictionary_path=path,
                                 resource_owner=bundle["owner"], invocation_path=invocation)
        if record is not None:
            location = self.location(location, operator=record["operator"], operator_ordinal=record["operator_ordinal"],
                                     operator_span=record["span"], source_segments=record["segments"], span_basis="decoded-stream")
        if resource_name is not None:
            location = self.location(location, resource_name=resource_name)
        subtype = _name(stream.attrs.get("Subtype"))
        finding = "pdf-image-occurrence" if subtype == "Image" else "pdf-form-occurrence"
        self.context.finding(self.document, "images", finding, location, component=source["component"],
                             artifact=source["encoded_artifact"], target_source=source["location"],
                             source_subtype=subtype or "content")

    def invocation(self, current, stream, record, name):
        """Bound invocation depth and storage before copying its ancestry path."""
        source = self.source(stream)
        location = source["location"]
        self.context.check("nesting", len(current) + 1, location)
        self.context.charge("records", len(current) + 1, location)
        self.context.charge("retained_bytes", (len(current) + 1) * 96 + len(name) * 4, location)
        return current + [{"encoded_artifact": source["encoded_artifact"], "object_id": stream.objid,
                           "resource_name": name, "operator_ordinal": record["operator_ordinal"],
                           "operator_span": record["span"]}]

    def declarations(self, bundle, scope, invocation, ancestry):
        """Enumerate each active resource bundle once while preserving page and alias replay."""
        identity = id(bundle["values"])
        self.context.charge("records", 1, bundle["owner"])
        if identity in self.active_declarations:
            return
        self.context.charge("retained_bytes", 64, bundle["owner"])
        self.active_declarations.add(identity)
        try:
            self._declarations(bundle, scope, invocation, ancestry)
        finally:
            self.active_declarations.remove(identity)

    def _declarations(self, bundle, scope, invocation, ancestry):
        """Visit original image-bearing members, including unselected resource names."""
        for category in ("XObject", "Pattern", "Font"):
            values = self.value(bundle["values"].get(category, {}))
            if not isinstance(values, dict):
                raise BrewdocError("PDF image resource category is not a dictionary: " + category)
            for name, raw in values.items():
                path = self.path(bundle["path"], ("/", category, "/", name), bundle["owner"])
                try:
                    value = self.value(raw)
                    if category == "Font":
                        if isinstance(value, dict) and _name(value.get("Subtype")) == "Type3":
                            owner = self.sources[raw.objid]["location"] if isinstance(raw, PDFObjRef) else bundle["owner"]
                            self.charprocs(value, scope, path, bundle, invocation, ancestry, owner=owner)
                        continue
                    if not isinstance(value, PDFStream):
                        raise BrewdocError("PDF image resource is not a stream: " + name)
                    subtype = _name(value.attrs.get("Subtype"))
                    if category == "XObject" and subtype not in ("Image", "Form"):
                        continue
                    if category == "Pattern" and self.value(value.attrs.get("PatternType")) != 1:
                        continue
                    self.occurrence(value, scope, path, "resource", bundle, invocation, resource_name=name)
                    if subtype != "Image":
                        self.visit([value], scope, path, bundle, invocation, ancestry, replay=False)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, self.location(bundle["owner"], **scope, dictionary_path=path))

    def charprocs(self, font, scope, path, fallback, invocation, ancestry, *, orphan=False, owner=None):
        """Inventory every Type3 glyph program without assuming a glyph was painted."""
        procedures = self.value(font.get("CharProcs", {}))
        if not isinstance(procedures, dict):
            raise BrewdocError("PDF Type3 CharProcs is not a dictionary")
        owner = fallback["owner"] if owner is None else owner
        bundle = self.resources(font.get("Resources"), owner, self.path(path, "/Resources", owner), fallback)
        identity = id(font), id(bundle["values"])
        if identity in self.active_fonts:
            return
        self.context.charge("records", 1, owner)
        self.context.charge("retained_bytes", 64, owner)
        self.active_fonts.add(identity)
        try:
            for glyph, raw in procedures.items():
                glyph_path = self.path(path, ("/CharProcs/", glyph), bundle["owner"])
                try:
                    stream = self.value(raw)
                    if orphan and self.source(stream)["encoded_artifact"] in self.content_seen:
                        continue
                    self.occurrence(stream, scope, glyph_path, "resource", bundle, invocation, resource_name=glyph)
                    self.visit([stream], scope, glyph_path, bundle, invocation, ancestry, replay=False)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, self.location(owner, **scope, dictionary_path=glyph_path))
        finally:
            self.active_fonts.remove(identity)

    def drawing(self, record, scope, path, bundle, invocation, active, xobjects, replay, location):
        """Keep one malformed Do independent from later admitted operator occurrences."""
        try:
            name = record["resource_name"]
            if name not in xobjects:
                raise BrewdocError("PDF drawing names a missing XObject resource: " + name)
            stream = self.value(xobjects[name])
            if not isinstance(stream, PDFStream) or _name(stream.attrs.get("Subtype")) not in ("Image", "Form"):
                raise BrewdocError("PDF drawing resource is not an image or Form: " + name)
            if replay:
                self.occurrence(stream, scope, path, "draw", bundle, invocation, record=record, resource_name=name)
                if _name(stream.attrs.get("Subtype")) == "Form":
                    next_path = self.invocation(invocation, stream, record, name)
                    self.visit([stream], scope, path, bundle, next_path, active, replay=True)
        except InspectionLimit:
            raise
        except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
            self.problem(exc, self.location(location, **scope, dictionary_path=path))

    def problem(self, reason, location):
        """Keep image and inventory coverage incomplete after a source refusal."""
        source_error = getattr(reason, "location", None)
        if isinstance(source_error, dict):
            location = self.location(location, source_error_location=source_error)
        self.context.problem(self.document, ("images", "inventory"), str(reason), location)

    def visit(self, streams, scope, path, fallback, invocation, ancestry, *, replay):
        """Parse source once, then replay every invocation with active-ancestry cycles."""
        location = fallback["owner"]
        try:
            if streams:
                location = self.source(streams[0])["location"]
            self.context.charge("records", len(streams), location)
            self.context.charge("retained_bytes", len(streams) * 32 + (len(ancestry) + 1) * 8, location)
            identities = tuple(self.source(stream)["encoded_artifact"] for stream in streams)
            self.context.check("nesting", len(ancestry) + 1, location)
            self.context.charge("records", len(identities), location)
            if any(identity in group for group in ancestry for identity in identities):
                if replay:
                    raise BrewdocError("PDF image content/resource cycle")
                return
            for identity in identities:
                self.content_seen.add(identity)
            active = ancestry + (identities,)
            bundle = fallback
            if len(streams) == 1 and ("Resources" in streams[0].attrs or _name(streams[0].attrs.get("Subtype")) == "Form"):
                source = self.source(streams[0])
                bundle = self.resources(streams[0].attrs.get("Resources"), source["location"],
                                        "Object/Resources", fallback)
            inherited = (len(streams) == 1 and _name(streams[0].attrs.get("Subtype")) == "Form"
                         and "Resources" not in streams[0].attrs and ancestry)
            if not inherited:
                self.declarations(bundle, scope, invocation, active)
            records, interpretation = self.parse(streams, bundle, location)
            xobjects = self.value(bundle["values"].get("XObject", {}))
            if not isinstance(xobjects, dict):
                raise BrewdocError("PDF XObject resources is not a dictionary")
            for record in records:
                location = self.location(record["segments"][0]["source"], operator=record["operator"],
                                         operator_ordinal=record["operator_ordinal"], operator_span=record["span"],
                                         source_segments=record["segments"])
                if record["operator"] == "BI":
                    inventory = self.inline(record, interpretation, bundle)
                    if replay:
                        here = self.location(record["segments"][0]["source"], **scope, occurrence_kind="inline",
                                             dictionary_path=path, resource_owner=bundle["owner"], invocation_path=invocation,
                                             operator_ordinal=record["operator_ordinal"], operator_span=record["span"],
                                             payload_span=record["payload_span"], source_segments=record["payload_segments"],
                                             span_basis="decoded-stream")
                        encoded = list(dict.fromkeys(part["source"]["encoded_artifact"] for part in record["segments"]))
                        decoded = list(dict.fromkeys(part["source"]["decoded_artifact"] for part in record["segments"]))
                        self.context.finding(self.document, "images", "pdf-inline-image-occurrence", here, **inventory,
                                             encoded_container_artifacts=encoded, decoded_container_artifacts=decoded)
                elif record["operator"] == "Do":
                    self.drawing(record, scope, path, bundle, invocation, active, xobjects, replay, location)
        except InspectionLimit:
            raise
        except ContentError as exc:
            origin = exc.segments[0]["source"] if exc.segments else location
            self.problem(str(exc), self.location(origin, **scope, dictionary_path=path,
                                                decoded_span=exc.span, source_segments=exc.segments))
        except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
            self.problem(exc, self.location(location, **scope, dictionary_path=path))

    def appearances(self, annotation, scope, path, fallback):
        """Visit every appearance key and named state, regardless of selected AS."""
        appearances = self.value(annotation.get("AP", {}))
        if not isinstance(appearances, dict):
            raise BrewdocError("PDF annotation AP is not a dictionary")
        for key, raw in appearances.items():
            try:
                value = self.value(raw)
            except InspectionLimit:
                raise
            except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                self.problem(exc, self.location(fallback["owner"], **scope, appearance_key=key))
                continue
            states = value.items() if isinstance(value, dict) else [(None, value)]
            for state, raw_stream in states:
                here = self.location(scope, appearance_key=key)
                if state is not None:
                    here = self.location(here, appearance_state=state)
                parts = ("/AP/", key) if state is None else ("/AP/", key, "/", state)
                ap_path = self.path(path, parts, fallback["owner"])
                try:
                    stream = self.value(raw_stream)
                    self.occurrence(stream, here, ap_path, "appearance", fallback, [])
                    self.visit([stream], here, ap_path, fallback, [], (), replay=True)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, self.location(fallback["owner"], **here, dictionary_path=ap_path))

    def page(self, object_id, page, scope):
        """Keep one page's resource, content and annotation occurrence scope explicit."""
        location = self.location(self.sources[object_id]["location"], **scope, page_object_id=object_id)
        bundle = self.page_resources(object_id, page, location)
        contents = self.value(page.get("Contents", []))
        contents = contents if isinstance(contents, list) else [contents]
        self.context.charge("records", len(contents), location)
        self.context.charge("retained_bytes", len(contents) * 16, location)
        streams = [self.value(value) for value in contents]
        if streams:
            self.visit(streams, scope, "Page/Contents", bundle, [], (), replay=True)
        else:
            self.declarations(bundle, scope, [], ())
        annotations = self.value(page.get("Annots", []))
        if not isinstance(annotations, list):
            raise BrewdocError("PDF page Annots is not an array")
        for ordinal, raw in enumerate(annotations, 1):
            annotation_id = raw.objid if isinstance(raw, PDFObjRef) else None
            here = self.location(scope, annotation_ordinal=ordinal, annotation_object_id=annotation_id)
            path = "Page/Annots[%d]" % ordinal
            try:
                annotation = self.value(raw)
                if not isinstance(annotation, dict):
                    raise BrewdocError("PDF annotation is not a dictionary")
                self.context.charge("records", 1, location)
                self.annotations_seen.add(annotation_id)
                self.appearances(annotation, here, path, bundle)
            except InspectionLimit:
                raise
            except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                self.problem(exc, self.location(location, **here, dictionary_path=path))

    def orphan(self, object_id, value, *, source=None):
        """Inventory admitted orphan content without assigning a page or a draw."""
        location = (source or self.sources[object_id])["location"]
        bundle = self.resources(None, location, "Object/Resources")
        if isinstance(value, PDFStream):
            source = self.source(value)
            if source["encoded_artifact"] in self.content_seen:
                return
            if (_name(value.attrs.get("Subtype")) == "Form"
                    or self.value(value.attrs.get("PatternType")) == 1):
                bundle = self.resources(value.attrs.get("Resources"), location, "Object/Resources")
                if bundle["missing"]:
                    self.problem("Orphan PDF content has no applicable Resources interpretation", location)
                self.visit([value], {}, "Object", bundle, [], (), replay=False)
        elif isinstance(value, dict):
            if _name(value.get("Subtype")) == "Type3":
                self.charprocs(value, {}, "Object", bundle, [], (), orphan=True)
            if "AP" in value and (source is not None or object_id not in self.annotations_seen):
                self.appearances(value, {"annotation_object_id": object_id}, "Object", bundle)

    def run(self):
        """Isolate page/object failures while stopping globally on shared exhaustion."""
        location = {"kind": "pdf", "dictionary_path": "Images"}
        try:
            for object_id, page, scope in self.pages:
                location = self.sources[object_id]["location"]
                try:
                    self.page(object_id, page, scope)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, self.location(location, **scope))
            for object_id, value in self.objects.items():
                location = self.sources[object_id]["location"]
                try:
                    self.orphan(object_id, value)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, location)
            for object_id, value, source in self.roots:
                location = source["location"]
                try:
                    self.orphan(object_id, value, source=source)
                except InspectionLimit:
                    raise
                except (BrewdocError, ValueError, UnicodeError, zlib.error) as exc:
                    self.problem(exc, location)
        except InspectionLimit as exc:
            self.problem(str(exc), location)


def _image_occurrences(pdf, pages, objects, sources, context, document, *, version, decode_stream, cache, roots=()):
    """Inspect one admitted revision view; only physical inline identity survives in cache."""
    _ImageOccurrences(pdf, pages, objects, sources, context, document, version, decode_stream, cache, roots).run()


def _difference_names(encoding):
    """Retain only explicit PDF Differences assignments, including duplicate ambiguity."""
    if not isinstance(encoding, dict):
        return {}
    differences = _resolve(encoding.get("Differences", []))
    if not isinstance(differences, list):
        return {}
    names, number = {}, None
    for item in differences:
        item = _resolve(item)
        if type(item) is int:
            number = item
        elif isinstance(item, PSLiteral) and number is not None and 0 <= number <= 255:
            names[number] = _name(item) if number not in names else None
            number += 1
        else:
            return {}
    return names


class _FontManager(PDFResourceManager):
    """Bind recovered source encodings to actual font instances instead of BaseFont names."""

    def __init__(self, objects, components, streams, context, document):
        super().__init__()
        self.objects, self.components, self.streams = objects, components, streams
        self.context, self.document = context, document
        self.font_sources, self.named_sources, self.headers = {}, {}, {}
        self.named_glyphs = 0

    def get_font(self, objid, spec):
        """Keep the actual source object/specification beside the engine's cached font instance."""
        font = super().get_font(objid, spec)
        self.font_sources[font] = objid, spec
        return font

    def _inline_location(self, spec):
        """Locate an inline font by container identity without following indirect references."""
        for object_id, value in self.objects.items():
            stack = [(value, "Object")]
            while stack:
                item, path = stack.pop()
                location = {"kind": "pdf", "object_id": object_id, "dictionary_path": path}
                self.context.charge("records", 1, location)
                if item is spec:
                    return object_id, path
                if isinstance(item, PDFStream):
                    item = item.attrs
                if isinstance(item, dict):
                    stack.extend((child, path + "/" + key) for key, child in reversed(tuple(item.items())))
                elif isinstance(item, list):
                    stack.extend((child, "%s[%d]" % (path, index))
                                 for index, child in reversed(tuple(enumerate(item, 1))))
        return None, None

    def named_source(self, font):
        """Read admitted source declarations once, preserving explicit PDF Encoding precedence."""
        if font in self.named_sources:
            return self.named_sources[font]
        object_id, spec = self.font_sources.get(font, (None, {}))
        owner, path = (object_id, "Object") if object_id is not None else self._inline_location(spec)
        source = {"mapping": {}, "font_object_id": object_id, "font_resource_object_id": owner,
                  "font_resource_path": path, "font_component": self.components.get(owner),
                  "component": self.components.get(owner), "artifact": None,
                  "encoding_origin": "pdf-encoding" if "Encoding" in spec else "type1-header"}
        self.named_sources[font] = source
        if "Encoding" in spec:
            source["mapping"] = _difference_names(_resolve(spec["Encoding"]))
            return source
        if _name(spec.get("Subtype")) not in ("Type1", "MMType1"):
            return source
        descriptor_reference = spec.get("FontDescriptor")
        descriptor = _resolve(descriptor_reference)
        if not isinstance(descriptor, dict):
            return source
        stream = _resolve(descriptor.get("FontFile"))
        if not isinstance(stream, PDFStream) or stream.data is None:
            return source
        length = _resolve(stream.attrs.get("Length1"))
        if type(length) is not int or not 0 < length <= len(stream.data):
            return source
        source["font_descriptor_object_id"] = (descriptor_reference.objid
                                                if isinstance(descriptor_reference, PDFObjRef) else None)
        source["font_file_object_id"] = stream.objid
        source["font_file_component"] = self.components.get(stream.objid)
        source["font_file_artifact"] = self.streams.get(stream.objid)
        key = id(stream), length
        if key not in self.headers:
            header = stream.data[:length]
            location = {"kind": "pdf", "object_id": stream.objid,
                        "dictionary_path": "Object/Type1Header", "byte_start": 0, "byte_end": length,
                        "byte_basis": "decoded_fontfile"}
            artifact = self.context.payload(self.document, "font-header", location, header,
                                            "application/octet-stream")
            component = self.context.component(self.document, "pdf-font-header", location,
                                                data=header, artifact=artifact,
                                                reason="Literal Encoding header only; font programs remain opaque")
            self.headers[key] = {"mapping": type1_encoding(header), "artifact": artifact,
                                 "component": component, "header_start": 0, "header_end": length}
        source.update(self.headers[key])
        return source


class _TextDevice(PDFTextDevice):
    """Use the installed font decoder without retaining a second page layout."""

    def __init__(self, manager, context, document, location):
        super().__init__(manager)
        self.context, self.document, self.location = context, document, location
        self.fragments, self.byte_size = [], 0
        self.text_show_ordinal = 0

    def _append(self, text):
        size = len(text.encode("utf-8"))
        self.context.check("payload_bytes", self.byte_size + size, self.location)
        self.context.charge("decoded_bytes", size, self.location)
        self.fragments.append(text)
        self.byte_size += size

    def render_string(self, textstate, seq, ncs, graphicstate):
        """Decode glyphs in source-operator order, including invisible text."""
        font = textstate.font
        if font is None:
            self.context.problem(self.document, ("text",), "PDF text has no font", self.location)
            return
        self.text_show_ordinal += 1
        glyph_ordinal = 0
        for token in seq:
            if not isinstance(token, bytes):
                continue
            for cid in font.decode(token):
                glyph_ordinal += 1
                self.context.charge("records", 1, self.location)
                try:
                    text = font.to_unichr(cid)
                except PDFUnicodeNotDefined:
                    source = self.rsrcmgr.named_source(font)
                    glyph = source["mapping"].get(cid)
                    if glyph is None:
                        self.context.problem(self.document, ("text",),
                                             "PDF font has no unambiguous source mapping for character %d" % cid,
                                             self.location)
                        continue
                    identity = source["font_object_id"] if source["font_object_id"] is not None else source["font_resource_object_id"]
                    text = "[font-glyph:%s:%d:%s]" % (identity, cid, glyph)
                    location = self.location | {"font_object_id": source["font_object_id"],
                                                "text_show_ordinal": self.text_show_ordinal,
                                                "glyph_ordinal": glyph_ordinal}
                    if "font_file_object_id" in source:
                        location["font_file_object_id"] = source["font_file_object_id"]
                    details = {key: value for key, value in source.items()
                               if key not in ("mapping", "component", "artifact", "font_object_id", "font_file_object_id")}
                    self.context.finding(self.document, "text", "pdf-named-glyph", location,
                                         component=source["component"], artifact=source["artifact"], text=text,
                                         representation="glyph-name", unicode_mapping="unavailable", cid=cid,
                                         glyph_name=glyph, font_name=_text(font.fontname),
                                         readable_token=PDF_FONT_GLYPHS.get(glyph), **details)
                    self.rsrcmgr.named_glyphs += 1
                self._append(text)
        self._append("\n")


def _page_text(document, pages, objects, components, streams, context, document_id, sources):
    """Recover page and standalone Form XObject text after bounded stream admission."""
    manager = _FontManager(objects, components, streams, context, document_id)
    text_sources = [(attributes, sources[object_id]["location"] | scope | {
        "dictionary_path": "Page/Contents"}, "pdf-page-text") for object_id, attributes, scope in pages]
    text_sources.extend((value, sources[object_id]["location"] | {"dictionary_path": "Object"}, "pdf-xobject-text")
        for object_id, value in objects.items() if isinstance(value, PDFStream)
        and _name(value.attrs.get("Subtype")) == "Form")
    for attributes, location, kind in text_sources:
        try:
            if isinstance(attributes, PDFStream):
                attributes = {"MediaBox": _resolve(attributes.attrs.get("BBox", [0, 0, 1, 1])),
                              "Resources": _resolve(attributes.attrs.get("Resources", {})), "Contents": attributes}
            with _TextDevice(manager, context, document_id, location) as device:
                PDFPageInterpreter(manager, device).process_page(
                    PDFPage(document, location["object_id"], attributes, None))
            text = "".join(device.fragments)
            if text.strip("\x0c\n\r\t "):
                context.finding(document_id, "text", kind, location, text=text)
        except InspectionLimit:
            raise
        except Exception as exc:
            context.problem(document_id, ("text",), "PDF text unreadable: %s" % exc, location)
    return manager.named_glyphs


def _pdf_member_paths(roots, context):
    """Map direct containers to stable member paths without following indirect references."""
    paths = {}
    for value, location in roots:
        pending = [(value, ())]
        while pending:
            value, path = pending.pop()
            value = value.attrs if isinstance(value, PDFStream) else value
            if not isinstance(value, (dict, list)) or id(value) in paths:
                continue
            context.charge("records", 1, location)
            context.charge("retained_bytes", 64 + len(path) * 16, location)
            paths[id(value)] = path
            children = value.items() if isinstance(value, dict) else enumerate(value, 1)
            for key, child in children:
                if isinstance(child, (dict, list, PDFStream)):
                    context.charge("retained_bytes", 64 + (len(path) + 1) * 16 + (len(key) * 6 if isinstance(key, str) else 8), location)
                    pending.append((child, path + (key,)))
    return paths


def _admit_pdf_object(source, pdf, row, context, document, inventory):
    """Link one physical source row to a view-local value without retaining its native graph."""
    occurrence = pdf.read_occurrence(row)
    value, original = occurrence["value"], occurrence["data"]
    location = occurrence["location"] | {"dictionary_path": "Object"}
    stream = isinstance(value, PDFStream)
    image = stream and _name(value.attrs.get("Subtype")) == "Image"
    kind = "image" if image else "pdf-stream" if stream else "pdf-object"
    raw = value.rawdata if stream else original
    identity = ("raw-stream", value._source_span) if stream else (
        "object-source", location["byte_basis"], location.get("object_stream_offset"),
        location["byte_start"], location["byte_end"])
    artifact = source.retain(identity, kind, location, raw)
    if row["id"] not in inventory:
        component = context.component(document, kind, location, data=raw, artifact=artifact,
                                      disposition="opaque" if stream else "extracted",
                                      entries=_plain(value.attrs if stream else value),
                                      xref_entry=row["id"],
                                      reason="Encoded image pixels" if image else "Original stream bytes" if stream else None)
        inventory[row["id"]] = component
        if image:
            context.finding(document, "images", "pdf-image", location, component=component,
                            artifact=artifact, width=_plain(value.attrs.get("Width")),
                            height=_plain(value.attrs.get("Height")), filters=_plain(value.attrs.get("Filter")))
        elif stream and _name(value.attrs.get("Type")) == "XObject":
            context.finding(document, "images", "pdf-xobject", location, component=component,
                            artifact=artifact, subtype=_name(value.attrs.get("Subtype")))
    view_location = location | {"source_revision": row["revision"], "source_revision_id": row["revision_id"],
                                "revision": pdf.revision["ordinal"], "revision_id": pdf.revision["id"]}
    result = {"location": view_location, "component": inventory[row["id"]], "encoded_artifact": artifact,
              "decoded_artifact": artifact}
    if stream:
        try:
            decoded = source.decode(value, pdf, location)
            if decoded != raw:
                decoded_key = "decoded-payload", value._source_span, hashlib.sha256(decoded).hexdigest()
                if decoded_key not in inventory:
                    context.charge("records", len(source.decoded), location)
                    inventory[decoded_key] = next(artifact for key, artifact in source.decoded.items()
                                                  if key[1] == value._source_span and context.payloads[artifact] is decoded)
                result["decoded_artifact"] = inventory[decoded_key]
            if decoded != raw and ("decoded", row["id"]) not in inventory:
                inventory["decoded", row["id"]] = context.component(
                    document, "decoded-stream", location | {"encoding": "decoded"}, data=decoded,
                    artifact=result["decoded_artifact"], encoded_artifact=artifact)
        except InspectionLimit:
            raise
        except (BrewdocError, ValueError, zlib.error) as exc:
            if not image:
                context.problem(document, CAPABILITIES, str(exc), location)
    return value, result


def _pdf_view(source, pdf, context, document, inventory, image_cache, scan_embedded):
    """Inspect effective rows through one checked resolver and discard all native maps afterward."""
    objects, sources, alternate = {}, {}, []
    pdf._inspection_id = document
    pdf._inspection_retain = source.retain
    pdf.inspection_version = pdf.version()
    pdf._inspection_page_key = "page" if pdf.revision["ordinal"] == 0 else "revision_page"
    for section in source.chain(pdf.revision["section"]):
        for row in section["rows"]:
            object_id = row["object_id"]
            if object_id == 0 or object_id in objects or source.lookup(pdf.revision["section"], object_id) is not row:
                continue
            if row["type"] not in (1, 2):
                continue
            try:
                value, entry = _admit_pdf_object(source, pdf, row, context, document, inventory)
                objects[object_id], sources[object_id] = value, entry
            except InspectionLimit:
                raise
            except Exception as exc:
                location = source.row_location(row) | {"dictionary_path": "Object"}
                if row["id"] not in inventory:
                    inventory[row["id"]] = context.component(document, "pdf-object", location,
                                                              disposition="unreadable", reason=str(exc))
                context.problem(document, CAPABILITIES, "PDF object unreadable: %s" % exc, location)
    for row in source.rows:
        if row["revision_id"] != pdf.revision["id"] or row["type"] not in (1, 2) or row["object_id"] == 0:
            continue
        if source.lookup(pdf.revision["section"], row["object_id"]) is row:
            continue
        try:
            value, entry = _admit_pdf_object(source, pdf, row, context, document, inventory)
            alternate.append((row["object_id"], value, entry))
        except InspectionLimit:
            raise
        except Exception as exc:
            context.problem(document, CAPABILITIES, "PDF alternate source unreadable: %s" % exc, source.row_location(row))
    pdf._inspection_sources = sources
    pdf._inspection_payloads = {entry["component"]: entry["encoded_artifact"] for entry in sources.values()}
    pdf._inspection_payloads.update({entry["component"]: entry["encoded_artifact"] for _key, _value, entry in alternate})
    pdf._inspection_member_paths = _pdf_member_paths(chain(
        ((value, sources[key]["location"]) for key, value in objects.items()),
        ((value, entry["location"]) for _key, value, entry in alternate)), context)
    components = {key: entry["component"] for key, entry in sources.items()}
    streams = {key: entry["decoded_artifact"] for key, entry in sources.items() if isinstance(objects[key], PDFStream)}
    pages = _inspect_graph(pdf, objects, components, streams, sources, context, document, scan_embedded)
    page_rows = [(object_id, attributes, {pdf._inspection_page_key: ordinal})
                 for ordinal, (object_id, attributes) in enumerate(pages, 1) if object_id in sources]
    version = tuple(map(int, pdf.inspection_version["effective"].split("."))) if pdf.inspection_version["supported"] else (0, 0)

    def decode(stream, _context, location):
        """Keep temporary inline streams outside native physical-stream caches."""
        return source.decode(stream, pdf, location) if hasattr(stream, "_source_span") else _decode_stream(stream, context, location)

    _image_occurrences(pdf, page_rows, objects, sources, context, document,
                       version=version, decode_stream=decode, cache=image_cache, roots=alternate)
    if context.exhausted:
        exhausted = context.exhausted[-1]
        raise InspectionLimit("inspection %s limit exceeded: %d" % (exhausted["limit"], exhausted["maximum"]))
    named_glyphs = _page_text(pdf, page_rows, objects, components, streams, context, document, sources)
    roots = [(value, entry["location"] | {"source_component": entry["component"],
               "source_selection": "nonselected-xref-row"}, ()) for _object_id, value, entry in alternate]
    _inspect_graph(pdf, objects, components, streams, sources, context, document, scan_embedded, roots=roots, pages=[])
    return named_glyphs


def _pdf_source_evidence(source, context, document, inventory):
    """Publish every physical row, uncovered source region and ambiguity before closing coverage."""
    for section in source.sections:
        location = {"kind": "pdf", "xref_section_offset": section["offset"], "xref_section_role": section["role"]}
        claim = next((item for item in source.claims if item[0] == section["offset"]
                      and item[2] in ("xref-table", "xref-stream")), None)
        raw = source.data[claim[0]:claim[1]] if claim is not None else None
        artifact = source.retain(("xref-section", section["offset"]), "pdf-xref-section", location, raw) if raw is not None else None
        context.component(document, "pdf-xref-section", location, data=raw, artifact=artifact,
                          disposition="extracted" if section["complete"] else "unreadable",
                          trailer=_plain(section.get("trailer")), previous=section["previous"],
                          supplement=section["supplement"])
    for row in source.rows:
        location = source.row_location(row) | {"dictionary_path": "Object"}
        artifact = source.retain(("xref-row", row["id"]), "pdf-xref-entry", location, row["raw"])
        if row["id"] in inventory:
            context.component(document, "pdf-xref-entry", location, data=row["raw"],
                              xref_entry_artifact=artifact, object_component=inventory[row["id"]], xref_entry=row["id"])
        else:
            reserved = row["object_id"] == 0 and row["target"] == 0
            allocated = row["type"] in (1, 2) and not reserved
            component = context.component(document, "pdf-object" if allocated else "pdf-xref-entry", location,
                                          data=row["raw"], artifact=artifact,
                                          disposition="unreadable" if allocated else "extracted",
                                          reason="Source row has no readable object" if allocated else "Original cross-reference metadata",
                                          xref_entry=row["id"], reserved=reserved, in_use=row["type"] in (1, 2))
            inventory[row["id"]] = component
            if allocated:
                context.problem(document, CAPABILITIES, "PDF source object was not semantically inspected", location)
    for region in source.regions:
        context.component(document, region["kind"], region["location"], data=region["data"],
                          artifact=region["artifact"], disposition="opaque" if region["kind"] == "pdf-unclaimed-source" else "extracted")
    for conflict in source.conflicts:
        context.finding(document, "inventory", "pdf-xref-conflict", {"kind": "pdf", "revision_id": conflict["revision_id"]},
                        **conflict)
    for problem in source.problems:
        context.component(document, "pdf-source-problem", problem["location"],
                          disposition="unreadable", reason=problem["reason"])
    source.inspection_version = {"supported": False, "effective": None}
    source._inspection_retain = source.retain
    for orphan in source.orphans:
        location = orphan["location"] | {"object_id": orphan["object_id"], "generation": orphan["generation"],
                                         "dictionary_path": "Object"}
        artifact = source.retain(("orphan", location["byte_start"], location["byte_end"]), "pdf-object", location, orphan["data"])
        component = context.component(document, "pdf-unindexed-object", location, data=orphan["data"], artifact=artifact,
                                      disposition="opaque", reason="Source revision context is unavailable")
        source._inspection_payloads = {component: artifact}
        source._inspection_member_paths = _pdf_member_paths(((orphan["value"], location),), context)
        try:
            _inspect_graph(source, {orphan["object_id"]: orphan["value"]}, {orphan["object_id"]: component}, {}, {},
                           context, document, None, roots=[(orphan["value"], location, ())], pages=[])
        except InspectionLimit:
            raise
        except Exception as exc:
            context.problem(document, CAPABILITIES, "PDF unindexed source unreadable: %s" % exc, location)


def inspect_pdf(data: bytes, name: str, context, document: str, *, scan_embedded=None) -> None:
    """Inspect guarded source occurrences through disposable generation-aware revision views."""
    location = {"kind": "pdf", "byte_basis": "file", "byte_start": 0, "byte_end": len(data)}
    artifact = context.payload(document, "pdf-source", location, data, "application/pdf")
    context.component(document, "pdf-source", location, data=data, artifact=artifact,
                      disposition="opaque", reason="Original admitted PDF container")
    source = read_pdf_source(data, context, document, _decode_stream)
    source.account_regions()
    if context.exhausted:
        return
    inventory, image_cache, embedded_seen = {}, {}, set()
    named_glyphs = 0

    def embedded(payload, filename, _context, **metadata):
        """Inspect each physical attachment once while retaining every trigger finding."""
        identity = metadata.get("component"), filename
        context.charge("records", 1, metadata["location"])
        if identity not in embedded_seen:
            embedded_seen.add(identity)
            scan_embedded(payload, filename, context, **metadata)

    for revision in source.revisions:
        try:
            with source.view(revision["id"]) as pdf:
                named_glyphs += _pdf_view(source, pdf, context, document, inventory, image_cache,
                                           embedded if scan_embedded is not None else None)
        except InspectionLimit as exc:
            location = context.exhausted[-1]["location"] if context.exhausted else {"kind": "pdf"}
            context.problem(document, CAPABILITIES, str(exc), location)
            return
        except Exception as exc:
            context.problem(document, CAPABILITIES, "PDF revision unreadable: %s: %s" % (type(exc).__name__, exc),
                            getattr(exc, "location", {"kind": "pdf", "revision_id": revision["id"]}))
    _pdf_source_evidence(source, context, document, inventory)
    for capability in CAPABILITIES:
        reason = ("All source text recovered as Unicode or named glyph identities; Unicode unavailable for %d occurrences"
                  % named_glyphs if capability == "text" and named_glyphs else
                  "All admitted PDF objects and occurrences inspected")
        context.cover(document, capability, "complete", reason)
    for capability in ("cells", "formulas", "names", "macros"):
        context.cover(document, capability, "not_applicable", "PDF has no workbook or VBA structures")
