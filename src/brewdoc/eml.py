"""EML adapter: one selected MIME body and literal message metadata, stdlib only."""

from __future__ import annotations

import base64
import binascii
import re
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from brewdoc.common import (BrewdocError, Rendered, Route, _assemble,
                            _escape_markdown_text, new_tally, reading)

MAX_MESSAGE_NODES = 1_024
MAX_MIME_DEPTH = 64
HEADERS = ("From", "To", "Cc", "Date", "Subject", "Message-ID")
TRANSFER_ENCODINGS = frozenset(("7bit", "8bit", "binary", "base64", "quoted-printable"))


def _parse_message(path: Path) -> EmailMessage:
    """Count retained MIME nodes before construction; bound depth before body selection."""
    nodes = 0

    def bounded_factory(*, policy):
        """Refuse before creating a message beyond this operation's allowance."""
        nonlocal nodes
        if nodes >= MAX_MESSAGE_NODES:
            raise BrewdocError("EML message nodes limit exceeded: %d" % MAX_MESSAGE_NODES)
        nodes += 1
        return EmailMessage(policy=policy)

    try:
        with path.open("rb") as source:
            message = BytesParser(policy=policy.default.clone(
                message_factory=bounded_factory)).parse(source)
    except RecursionError as exc:
        raise BrewdocError("EML MIME nesting exceeds parser capacity") from exc
    stack = [(message, 1)]
    while stack:
        part, depth = stack.pop()
        if depth > MAX_MIME_DEPTH:
            raise BrewdocError("EML MIME depth limit exceeded: %d" % MAX_MIME_DEPTH)
        stack.extend((child, depth + 1) for child in part.iter_parts())
    return message


def _check_defects(part: EmailMessage, context: str) -> None:
    """Refuse defects in consumed structure or body, leaving opaque attachments alone."""
    if part.defects:
        names = ", ".join(type(defect).__name__ for defect in part.defects)
        detail = "boundary" if "Boundary" in names or "MultipartInvariant" in names else context
        raise BrewdocError("EML %s malformed: %s" % (detail, names))
    for name in ("Content-Type", "Content-Transfer-Encoding"):
        for header in part.get_all(name, ()):
            if header.defects:
                names = ", ".join(type(defect).__name__ for defect in header.defects)
                raise BrewdocError("EML %s malformed: %s" % (name, names))


def _message_parts(message: EmailMessage):
    """Visit metadata and ancestor paths in source order, leaving attached mail opaque."""
    stack = [(message, ())]
    while stack:
        part, ancestors = stack.pop()
        yield part, ancestors
        if part.get_content_type() != "message/rfc822":
            stack.extend((child, ancestors + (part,))
                         for child in reversed(list(part.iter_parts())))


def _header_text(raw: str) -> str:
    """Decode encoded words strictly and unfold without address or date normalization."""
    unfolded = re.sub(r"\r\n|\r|\n", "", raw)
    for encoded in re.finditer(r"=\?[^?]+\?[bB]\?([^?]*)\?=", unfolded):
        try:
            base64.b64decode(encoded[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BrewdocError("EML header base64 unreadable: %s" % exc) from exc
    fragments = []
    for value, charset in decode_header(unfolded):
        if isinstance(value, bytes):
            encoding = charset or "ascii"
            try:
                value = value.decode(encoding)
            except (LookupError, UnicodeError) as exc:
                raise BrewdocError("EML header charset %s unreadable: %s" % (encoding, exc)) from exc
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise BrewdocError("EML header text unreadable: %s" % exc) from exc
        fragments.append(value)
    return "".join(fragments)


def _body_text(body: EmailMessage) -> str:
    """Decode only the selected payload with strict transfer and charset checks."""
    _check_defects(body, "body")
    transfer = str(body.get("Content-Transfer-Encoding", "7bit")).strip().lower()
    if transfer not in TRANSFER_ENCODINGS:
        raise BrewdocError("EML transfer encoding unsupported: %s" % transfer)
    payload = body.get_payload(decode=True)
    _check_defects(body, "base64" if transfer == "base64" else "body")
    charset = body.get_content_charset() or "ascii"
    try:
        return payload.decode(charset).replace("\r\n", "\n").replace("\r", "\n")
    except (LookupError, UnicodeError) as exc:
        raise BrewdocError("EML body charset %s unreadable: %s" % (charset, exc)) from exc


def _render_eml(path: Path, _sheets=None, *, _html_content=None) -> Rendered:
    """Render selected headers, one body and attachment names in one Unicode chapter."""
    tally = new_tally()
    blocks = []
    with reading(path, "EML"):
        message = _parse_message(path)
        _check_defects(message, "message")
        headers = {name: [] for name in HEADERS}
        for name, raw in message.raw_items():
            canonical = next((key for key in HEADERS if key.lower() == name.lower()), None)
            if canonical is not None:
                headers[canonical].append(_header_text(raw))
        label = (headers["Subject"][0] if headers["Subject"] else "") or "Untitled"
        lines = [_escape_markdown_text(name + ": " + value, preserve_unicode=True)
                 for name in HEADERS for value in headers[name]]
        if lines:
            blocks.append(("text", lines))
            tally["text_regions"] += 1
        body = message.get_body(preferencelist=("plain", "html"))
        attachments = []
        for part, ancestors in _message_parts(message):
            if part is body:
                for container in ancestors:
                    _check_defects(container, "MIME structure")
            filename = part.get_filename()
            if filename is not None or part.is_attachment():
                attachments.append(filename if filename is not None else "[unnamed attachment]")
        if body is None:
            blocks.append(("text", ["No supported message body."]))
        else:
            text = _body_text(body)
            if body.get_content_type() == "text/html":
                if _html_content is None:
                    raise BrewdocError("EML HTML content renderer unavailable")
                _title, body_blocks = _html_content(text, tally, article=False)
                blocks.extend(body_blocks)
            elif text:
                blocks.append(("block", ["```", *text.split("\n"), "```"]))
                tally["text_regions"] += 1
        if attachments:
            lines = ["Attachments:", *(
                "- " + _escape_markdown_text(name, preserve_unicode=True) for name in attachments)]
            blocks.append(("text", lines))
            tally["text_regions"] += 1
    return _assemble(path, ROUTE.name, ROUTE.unit_kind, 1, [(1, label, blocks)], tally,
                     not_carried=ROUTE.not_carried, preserve_unicode=True)


ROUTE = Route("eml", "chapter", (".eml",), _render_eml,
              ("unlisted headers, including Bcc, routing and signature fields",
               "attachment payloads and nested message bodies",
               "unselected body alternatives and non-body inline resources"))
