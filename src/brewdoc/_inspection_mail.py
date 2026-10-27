"""Bounded static MIME traversal over original headers and payload bytes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import quopri
import re
from email import policy
from email.header import decode_header
from email.parser import BytesHeaderParser
from pathlib import PurePath

from brewdoc._inspection_common import InspectionContext
from brewdoc._ole import embedded_document_name
from brewdoc.common import BrewdocError

_MAIL_CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                      "attachments", "images", "external_references", "hidden_structures")
_SUPPORTED = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".xls",
                        ".ods", ".odt", ".odp", ".html", ".epub", ".eml", ".md", ".txt"))
_ENCODED_WORD = re.compile(r"=\?([^?]+)\?([bBqQ])\?([^?]*)\?=")
_MIME_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12": ".xlsb",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/epub+zip": ".epub", "text/html": ".html",
    "message/rfc822": ".eml", "message/global": ".eml",
    "text/markdown": ".md", "text/plain": ".txt",
}


def _embedded_name(data, filename, media_type, charset, location, context):
    """Choose a supported attachment format from bounded source structure and hints."""
    original_suffix = PurePath(filename or "").suffix.lower()
    hinted = _MIME_SUFFIXES.get(media_type) or (original_suffix if original_suffix in _SUPPORTED else None)
    base = filename or "mime-%s" % location["mime_path"]
    detected = None
    if re.search(rb"%PDF-[0-9]\.[0-9]", data[:1024]) and b"%%EOF" in data[-1024:]:
        detected = ".pdf"
    elif data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        recognized = embedded_document_name("mime-payload", data, context, location)
        detected = PurePath(recognized).suffix if recognized else None
        if detected == ".xlsx" and hinted == ".xlsm":
            detected = ".xlsm"
    elif hinted == ".xls" and len(data) >= 512 and data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if data[28:30] == b"\xfe\xff" and int.from_bytes(data[30:32], "little") in (9, 12):
            detected = ".xls"
    elif hinted == ".eml" and re.search(rb"\r?\n\r?\n", data) and re.match(rb"[!-9;-~]+:", data):
        detected = ".eml"
    elif hinted in (".html", ".txt", ".md"):
        context.check("decoded_bytes", context.consumed["decoded_bytes"] + len(data) * 4, location)
        text = data.decode(charset)
        context.charge("decoded_bytes", len(text.encode("utf-8")), location)
        detected = hinted
    if detected is None:
        return None, None, "Supported MIME attachment structure is invalid or unavailable" if hinted else None
    name = base if original_suffix == detected else base + detected
    basis = "filename+structure" if original_suffix == detected else "mime+structure" if hinted == detected else "structure"
    return name, basis, None


def _decoded_header(raw: str) -> str:
    """Reject malformed encoded words instead of accepting repaired header text."""
    unfolded = re.sub(r"\r\n|\r|\n", "", raw).encode("ascii", "surrogateescape").decode("utf-8")
    remaining = _ENCODED_WORD.sub("", unfolded)
    if "=?" in remaining:
        raise ValueError("malformed encoded header word")
    for match in _ENCODED_WORD.finditer(unfolded):
        encoded = match[3].encode("ascii")
        if match[2].lower() == "b":
            base64.b64decode(encoded, validate=True)
        elif re.search(rb"=(?![0-9A-Fa-f]{2})", encoded):
            raise ValueError("malformed quoted-printable header")
    fragments = []
    for value, charset in decode_header(unfolded):
        fragments.append(value.decode(charset or "ascii") if isinstance(value, bytes) else value)
    text = "".join(fragments)
    text.encode("utf-8")
    return text


def _decode_payload(raw: bytes, transfer: str, context: InspectionContext,
                    location: dict) -> bytes:
    """Admit decoded size before transfer decoding creates an output buffer."""
    if transfer == "base64":
        context.check("payload_bytes", len(raw), location)
        compact = re.sub(rb"[\t\r\n ]", b"", raw)
        size = len(compact) // 4 * 3 - len(compact) + len(compact.rstrip(b"="))
        size = max(0, size)
    elif transfer == "quoted-printable":
        if re.search(rb"=(?![0-9A-Fa-f]{2}|\r?\n)", raw):
            raise ValueError("malformed quoted-printable payload")
        size = len(raw) - sum(len(match[0]) if match[0].endswith(b"\n") else 2
                              for match in re.finditer(rb"=(?:[0-9A-Fa-f]{2}|\r?\n)", raw))
    elif transfer in ("7bit", "8bit", "binary"):
        if transfer == "7bit" and not raw.isascii():
            raise ValueError("non-ASCII bytes in 7bit payload")
        size = len(raw)
    else:
        raise ValueError("unsupported transfer encoding: %s" % transfer)
    context.check("payload_bytes", size, location)
    context.check("decoded_bytes", context.consumed["decoded_bytes"] + size, location)
    context.check("retained_bytes", context.consumed["retained_bytes"] + size, location)
    decoded = (base64.b64decode(compact, validate=True) if transfer == "base64" else
               quopri.decodestring(raw) if transfer == "quoted-printable" else raw)
    context.charge("decoded_bytes", len(decoded), location)
    return decoded


def _multipart_ranges(body: bytes, boundary: str):
    """Yield original preamble, child and epilogue spans without a message tree."""
    token = boundary.encode("ascii")
    if not token or len(token) > 70 or b"\r" in token or b"\n" in token:
        raise ValueError("invalid MIME boundary")
    pattern = re.compile(rb"(?m)^--" + re.escape(token) + rb"(--)?[ \t]*(?:\r?\n|$)")
    start = None
    closed = False
    for match in pattern.finditer(body):
        if start is None and match.start():
            yield "preamble", 0, match.start()
        if start is not None:
            end = match.start()
            if body[max(start, end - 2):end] == b"\r\n":
                end -= 2
            elif body[max(start, end - 1):end] == b"\n":
                end -= 1
            yield "part", start, end
        if match[1]:
            closed = True
            if match.end() < len(body):
                yield "epilogue", match.end(), len(body)
            break
        start = match.end()
    if not closed or start is None:
        raise ValueError("missing or unclosed MIME boundary")


def inspect_mail(data: bytes, name: str, context: InspectionContext,
                 document: str, *, scan_html, scan_embedded, scan_text=None) -> None:
    """Inspect every MIME occurrence; delegate only admitted static payload bytes."""
    root_location = {"kind": "mime", "mime_path": "1", "content_id": None}
    try:
        context.check("source_bytes", len(data), root_location)
        context.payload(document, "mime-source", root_location, data, "message/rfc822")
        _visit(data, (1,), 0, context, document, scan_html, scan_embedded, scan_text)
        for capability in _MAIL_CAPABILITIES:
            context.cover(document, capability, "complete",
                          "All native MIME and HTML content scanned; opaque attachment internals excluded")
        for capability in ("cells", "formulas", "names", "macros"):
            context.cover(document, capability, "not_applicable", "MIME has no native workbook or macro structures")
    except (BrewdocError, ValueError, LookupError, UnicodeError, binascii.Error,
            RecursionError) as exc:
        context.problem(document, _MAIL_CAPABILITIES, "MIME inspection failed: %s" % exc, root_location)


def _visit(raw: bytes, path: tuple[int, ...], depth: int, context: InspectionContext,
           document: str, scan_html, scan_embedded, scan_text, source_offset=0) -> None:
    """Read one bounded header block and traverse its original body spans."""
    location = {"kind": "mime", "mime_path": ".".join(map(str, path)), "content_id": None}
    component = None
    try:
        context.check("nesting", depth + max(0, len(context.active_hashes) - 1), location)
        context.check("payload_bytes", len(raw), location)
        separator = re.search(rb"\r\n\r\n|\n\n|\r\r", raw)
        header_end = separator.start() if separator else len(raw)
        body_start = separator.end() if separator else len(raw)
        count = sum(1 for _ in re.finditer(rb"(?m)^[^ \t\r\n][^:\r\n]*:", raw[:header_end]))
        context.check("records", context.consumed["records"] + count + 2, location)
        headers = BytesHeaderParser(policy=policy.default).parsebytes(raw[:body_start])
        if headers.defects:
            raise ValueError("malformed MIME headers: " + ", ".join(type(item).__name__ for item in headers.defects))
        location["content_id"] = str(headers.get("Content-ID")) if headers.get("Content-ID") else None
        for ordinal, (header_name, value) in enumerate(headers.raw_items(), 1):
            header_location = dict(location, header_ordinal=ordinal, header_name=header_name)
            original = value.encode("ascii", "surrogateescape")
            try:
                context.check("report_bytes", context.consumed["report_bytes"] + len(original) * 12 + 512,
                              header_location)
                context.check("decoded_bytes", context.consumed["decoded_bytes"] + len(original) * 4, header_location)
                text = _decoded_header(value)
                context.charge("decoded_bytes", len(text.encode("utf-8")), header_location)
                parsed = headers[header_name]
                if parsed.defects:
                    raise ValueError("malformed header: " + ", ".join(type(item).__name__ for item in parsed.defects))
                context.finding(document, "text", "mime-header", header_location,
                                text=text, raw=original.decode("latin1"))
            except (ValueError, LookupError, UnicodeError, binascii.Error) as exc:
                context.finding(document, "text", "mime-header", header_location,
                                raw=original.decode("latin1"), decoding_error=str(exc))
                context.problem(document, _MAIL_CAPABILITIES, "MIME header decoding failed: %s" % exc, header_location)
        media_type = headers.get_content_type()
        if media_type in ("multipart/encrypted", "application/pgp-encrypted") or (
                media_type in ("application/pkcs7-mime", "application/x-pkcs7-mime")
                and headers.get_param("smime-type", "enveloped-data") == "enveloped-data"):
            context.problem(document, _MAIL_CAPABILITIES,
                            "Encrypted MIME content cannot be inspected", location)
        transfer = str(headers.get("Content-Transfer-Encoding", "7bit")).strip().lower()
        if len(headers.get_all("Content-Type", [])) > 1 or len(headers.get_all("Content-Transfer-Encoding", [])) > 1:
            raise ValueError("ambiguous duplicate MIME structural header")
        body = raw[body_start:]
        if media_type.startswith("multipart/"):
            component = context.component(document, "mime-part", location, data=raw,
                                          media_type=media_type)
            if transfer not in ("7bit", "8bit", "binary"):
                raise ValueError("encoded multipart container")
            boundary = headers.get_boundary()
            if boundary is None:
                raise ValueError("missing MIME boundary parameter")
            ordinal = 0
            for region, start, end in _multipart_ranges(body, boundary):
                context.check("records", context.consumed["records"] + 1, location)
                if region == "part":
                    ordinal += 1
                    _visit(body[start:end], path + (ordinal,), depth + 1, context,
                           document, scan_html, scan_embedded, scan_text,
                           source_offset + body_start + start)
                else:
                    region_location = dict(location, mime_region=region,
                                           byte_start=source_offset + body_start + start,
                                           byte_end=source_offset + body_start + end,
                                           span_basis="original_message_bytes")
                    context.check("payload_bytes", end - start, region_location)
                    context.check("retained_bytes", context.consumed["retained_bytes"] + end - start,
                                  region_location)
                    _supplement(body[start:end], region, region_location,
                                headers.get_content_charset() or "ascii", transfer,
                                context, document, scan_text)
            return
        decoded = _decode_payload(body, transfer, context, location)
        artifact = context.payload(document, "mime-payload", location, decoded, media_type)
        filename = headers.get_filename()
        content_disposition = headers.get_content_disposition()
        attachment = (filename is not None or content_disposition in ("attachment", "inline")
                      or location["content_id"] is not None or not media_type.startswith("text/"))
        charset = headers.get_content_charset() or "ascii"
        embedded_name, name_basis, structure_error = (_embedded_name(
            decoded, filename, media_type, charset, location, context)
            if attachment else (None, None, None))
        supported = embedded_name is not None
        if structure_error:
            context.problem(document, _MAIL_CAPABILITIES, structure_error, location)
        text = None
        charset_error = None
        if media_type.startswith("text/"):
            try:
                context.check("decoded_bytes", context.consumed["decoded_bytes"] + len(decoded) * 4, location)
                context.check("report_bytes", context.consumed["report_bytes"] + len(decoded) * 6 + 512,
                              location)
                text = decoded.decode(charset)
                context.charge("decoded_bytes", len(text.encode("utf-8")), location)
            except (LookupError, UnicodeError) as exc:
                charset_error = str(exc)
                context.problem(document, ("text",), "MIME text charset decoding failed: %s" % exc, location)
        disposition = "extracted" if media_type.startswith("text/") or supported else "opaque"
        failure = charset_error or structure_error
        component = context.component(document, "mime-part", location, data=decoded, artifact=artifact,
                                      disposition="unreadable" if failure else disposition,
                                      reason=failure, media_type=media_type, filename=filename,
                                      embedded_name=embedded_name, name_basis=name_basis,
                                      content_disposition=headers.get_content_disposition(), transfer_encoding=transfer)
        if text is not None:
            context.finding(document, "text", "mime-text", location, component=component,
                            text=text, artifact=artifact, charset=charset)
            if media_type in ("text/plain", "text/markdown"):
                _scan_targets(decoded, "mime-%s.txt" % location["mime_path"],
                              dict(location, payload_artifact=artifact,
                                   span_basis="decoded_mime_payload"), charset,
                              context, document, scan_text)
        if attachment:
            context.finding(document, "attachments", "mime-attachment", location,
                            component=component, artifact=artifact, text=filename,
                            filename=filename, content_disposition=content_disposition,
                            disposition="unreadable" if failure else disposition,
                            media_type=media_type, byte_size=len(decoded),
                            sha256=hashlib.sha256(decoded).hexdigest())
        if media_type == "text/html" and not supported:
            scan_html(decoded, filename or "mime-%s.html" % location["mime_path"],
                      context, document, location=dict(location, charset=charset))
        if supported:
            nested = scan_embedded(decoded, embedded_name, parent=document,
                                   location=dict(location, charset=charset) if embedded_name.lower().endswith(".html") else location,
                                   component=component)
            context.finding(document, "attachments", "mime-embedded-document", location,
                            component=component, artifact=artifact, nested_document=nested)
        if media_type.startswith("image/"):
            context.finding(document, "images", "mime-inline-image", location,
                            component=component, artifact=artifact, media_type=media_type)
    except (BrewdocError, ValueError, LookupError, UnicodeError, binascii.Error,
            RecursionError) as exc:
        if component is None:
            try:
                context.component(document, "mime-part", location, data=raw,
                                  disposition="unreadable", reason=str(exc))
            except BrewdocError:
                pass
        context.problem(document, _MAIL_CAPABILITIES, "MIME part inspection failed: %s" % exc, location)


def _scan_targets(data, name, location, charset, context, document, scan_text):
    """Delegate literal text targets without interpreting HTML-looking code."""
    if scan_text is None:
        context.problem(document, ("links", "external_references"),
                        "Plain MIME target scanner is unavailable", location)
        return
    scan_text(data, name, context, document, location=location, charset=charset)


def _supplement(data, region, location, charset, transfer, context, document, scan_text):
    """Retain and strictly decode text outside original multipart boundaries."""
    artifact = context.payload(document, "mime-" + region, location, data, "text/plain")
    try:
        _decode_payload(data, transfer, context, location)
        context.check("decoded_bytes", context.consumed["decoded_bytes"] + len(data) * 4, location)
        context.check("report_bytes", context.consumed["report_bytes"] + len(data) * 6 + 512, location)
        text = data.decode(charset)
        context.charge("decoded_bytes", len(text.encode("utf-8")), location)
        component = context.component(document, "mime-" + region, location,
                                      data=data, artifact=artifact, charset=charset)
        context.finding(document, "text", "mime-" + region, location,
                        component=component, artifact=artifact, text=text, charset=charset)
        target_location = dict(location, payload_artifact=artifact,
                               original_region_byte_start=location["byte_start"],
                               original_region_byte_end=location["byte_end"],
                               span_basis="decoded_mime_payload")
        target_location.pop("byte_start")
        target_location.pop("byte_end")
        _scan_targets(data, "mime-%s-%s.txt" % (location["mime_path"], region),
                      target_location, charset, context, document, scan_text)
    except (BrewdocError, ValueError, LookupError) as exc:
        try:
            context.component(document, "mime-" + region, location, data=data,
                              artifact=artifact, disposition="unreadable", reason=str(exc))
        except BrewdocError:
            pass
        context.problem(document, ("text", "links", "external_references"),
                        "MIME %s decoding failed: %s" % (region, exc), location)
