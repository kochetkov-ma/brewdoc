"""Passive inspection of original HTML tokens and UTF-8 text sources."""

from __future__ import annotations

import codecs
import base64
import binascii
import html
import json
import re
from array import array
from bisect import bisect_right
from collections import Counter
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import unquote_to_bytes

from brewdoc._html_runtime import LexborHTMLParser, _PARSER_ERROR, _SUPPORTED_RUNTIME
from brewdoc._html_encoding import _codec, _decode_html
from brewdoc.common import BrewdocError

if TYPE_CHECKING:
    from brewdoc._inspection_common import InspectionContext

_CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                 "attachments", "images", "external_references", "hidden_structures")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_URL_ATTRIBUTES = frozenset(("href", "src", "action", "formaction", "poster", "data", "cite",
                             "background", "longdesc", "usemap", "manifest", "profile", "codebase",
                             "classid", "icon", "lowsrc", "dynsrc", "xlink:href", "code", "object"))
_CONTROLS = frozenset("form input button select option textarea output fieldset optgroup".split())
_RAW = frozenset("script style textarea title xmp iframe noembed noframes".split())
_REFERENCE = re.compile(r"(?m)^[ \t]{0,3}\[([^]\r\n]+)\]:[ \t]*(?:<([^>\r\n]*)>|([^\s]+))")
_ATTRIBUTE = re.compile(r'''([^\s/>=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?''')
_EMAIL_AUTOLINK = re.compile(r"<([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                             r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)>")
_SCRIPT_MEDIA = frozenset(("application/ecmascript", "application/javascript", "application/x-ecmascript",
                           "application/x-javascript", "text/ecmascript", "text/javascript",
                           "text/javascript1.0", "text/javascript1.1", "text/javascript1.2",
                           "text/javascript1.3", "text/javascript1.4", "text/javascript1.5",
                           "text/jscript", "text/livescript", "text/x-ecmascript", "text/x-javascript"))
_MEDIA_SUFFIXES = {
    "application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html",
    "text/plain": ".txt", "text/markdown": ".md", "message/rfc822": ".eml",
    "application/epub+zip": ".epub", "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12": ".xlsb",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}


def _complete(context, document):
    for capability in _CAPABILITIES:
        context.cover(document, capability, "complete", "all original source tokens inspected")


def _decode(data: bytes, *, plain: bool, encoding: str | None = None) -> tuple[str, str]:
    """Share accepted HTML charset semantics while keeping text pass-through strict UTF-8."""
    if plain:
        return data.decode("utf-8"), "utf-8"
    source, name, _ = _decode_html(data, encoding)
    return source, _codec(name)[1]


def _percent_bytes(value, context, location):
    """Admit percent-decoding growth and reject incomplete or non-hex escapes."""
    position, size = 0, 0
    while position < len(value):
        if value[position] == "%":
            if re.fullmatch(r"[0-9a-fA-F]{2}", value[position + 1:position + 3]) is None:
                raise ValueError("invalid percent escape")
            size += 1
            position += 3
        else:
            size += len(value[position].encode("utf-8"))
            position += 1
    context.check("payload_bytes", size, location)
    context.charge("decoded_bytes", size, location)
    return unquote_to_bytes(value)


def _data_bytes(target, context, location):
    """Decode RFC 2397 content under cumulative budgets without reading any URL."""
    target = target.split("#", 1)[0]
    separator = target.find(",", 5)
    if separator < 0:
        raise ValueError("missing comma separator")
    context.charge("records", target[5:separator].count(";") + 1, location)
    pieces = [_percent_bytes(piece, context, location).decode("ascii")
              for piece in target[5:separator].split(";")]
    media_type = pieces[0].lower() or "text/plain"
    if re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+/[!#$%&'*+.^_`|~0-9a-z-]+", media_type) is None:
        raise ValueError("invalid media type")
    parameters = {} if pieces[0] else {"charset": "US-ASCII"}
    encoding = "percent"
    for ordinal, piece in enumerate(pieces[1:], 1):
        if piece.lower() == "base64":
            if ordinal != len(pieces) - 1:
                raise ValueError("base64 marker must be the final parameter")
            encoding = "base64"
        elif "=" in piece:
            key, value = piece.split("=", 1)
            parameters[key.lower()] = value.strip('"')
        else:
            raise ValueError("invalid media parameter")
    encoded = _percent_bytes(target[separator + 1:], context, location)
    if encoding == "base64":
        context.charge("decoded_bytes", len(encoded), location)
        encoded = encoded.translate(None, b" \t\r\n\f")
        if len(encoded) % 4 == 0:
            encoded = encoded[:-2] if encoded.endswith(b"==") else encoded[:-1] if encoded.endswith(b"=") else encoded
        if len(encoded) % 4 == 1 or re.fullmatch(br"[A-Za-z0-9+/]*", encoded) is None:
            raise ValueError("invalid forgiving-base64 alphabet, length or padding")
        maximum = len(encoded) * 6 // 8
        context.check("payload_bytes", maximum, location)
        context.charge("decoded_bytes", maximum, location)
        context.charge("decoded_bytes", len(encoded) + (-len(encoded) % 4), location)
        payload = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)
    else:
        payload = encoded
    return payload, media_type, parameters, encoding


def _data_suffix(payload, media_type):
    """Require structural signatures for binary documents before injected dispatch."""
    suffix = _MEDIA_SUFFIXES.get(media_type)
    if suffix is None and media_type == "application/octet-stream" and b"%PDF-" in payload[:1024]:
        suffix = ".pdf"
    if suffix is None:
        return None, "media type has no supported document reader"
    if suffix == ".pdf" and b"%PDF-" not in payload[:1024]:
        return None, "declared PDF signature is missing"
    if suffix in {".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt", ".odp", ".epub"}:
        if not payload.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return None, "declared package signature is missing"
    if suffix == ".xls" and not payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return None, "declared compound-document signature is missing"
    if suffix == ".eml" and re.match(br"[^\s:]+:", payload) is None:
        return None, "declared message header signature is missing"
    return suffix, None


class _SourceParser(HTMLParser):
    """Keep source-token paths and measured positions without browser tree rewriting."""

    CDATA_CONTENT_ELEMENTS = tuple(sorted(_RAW))

    def __init__(self, source, data, codec, name, context, document, location, *, srcdoc_depth=0,
                 scan_embedded=None, decode_data=True):
        super().__init__(convert_charrefs=False)
        self.source, self.data, self.codec = source, data, codec
        self.name, self.context, self.document = name, context, document
        self.base = dict(location or {})
        self.srcdoc_depth = srcdoc_depth
        self.scan_embedded = scan_embedded
        self.decode_data = decode_data
        self.data_sources = {}
        self.source_elements = []
        self.stack = []
        self.roots = {}
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", source))
        self.offsets = array("Q", [0])
        self.exact_offsets = False
        try:
            encoded = source.encode(codec)
            prefix = len(data) - len(encoded)
            bom_size = next((len(bom) for bom in (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
                             if data.startswith(bom)), 0)
            if codec != "iso2022_jp" and prefix in (0, bom_size) and data[prefix:] == encoded:
                offsets = array("Q", [prefix])
                for character in source:
                    offsets.append(offsets[-1] + len(character.encode(codec)))
                if offsets[-1] == len(data):
                    self.offsets, self.exact_offsets = offsets, True
        except UnicodeEncodeError:
            pass

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def location(self, start, end, *, xpath=None, **details):
        """Identify original tokens; decoded srcdoc positions never claim original byte spans."""
        result = {**self.base, "kind": "html", "part": self.name,
                  "xpath": xpath or (self.stack[-1][1] if self.stack else "/"),
                  "path_basis": "original_source_tokens", **details}
        if self.base.get("decoded_attribute"):
            for key in ("byte_start", "byte_end", "line", "column"):
                result.pop(key, None)
        if self.exact_offsets and not self.base.get("decoded_attribute"):
            result.update(byte_start=self.offsets[start], byte_end=self.offsets[end])
            line = bisect_right(self.lines, start)
            result.update(line=line, column=start - self.lines[line - 1] + 1)
        else:
            line = bisect_right(self.lines, start)
            result.update(position_basis="decoded_text", line=line,
                          column=start - self.lines[line - 1] + 1)
        return result

    def emit(self, capability, kind, location, **details):
        """Record source evidence and inspect inline bytes without fetching references."""
        self.context.finding(self.document, capability, kind, location, **details)
        target = details.get("target")
        if self.decode_data and isinstance(target, str) and target[:5].lower() == "data:":
            self.data_url(target, location, script_context=kind == "external_script")

    def data_url(self, target, location, *, script_context=False):
        """Retain inline bytes once per source occurrence and inject supported recursion."""
        occurrence = (target, json.dumps(location, sort_keys=True))
        if occurrence in self.data_sources:
            state = self.data_sources[occurrence]
            if state is not None:
                self._data_source(state, script_context=script_context)
            return
        self.context.charge("records", 1, location)
        self.data_sources[occurrence] = None
        decoded_location = {"kind": "data_url", "container": dict(location),
                            "payload_ordinal": len(self.data_sources)}
        affected = ("attachments", "images", "text", "scripts", "actions", "external_references")
        try:
            payload, media_type, parameters, encoding = _data_bytes(target, self.context, location)
            decoded_location.update(media_type=media_type, charset=parameters.get("charset"),
                                    encoding=encoding)
            if "#" in target:
                decoded_location["fragment"] = target.split("#", 1)[1]
            artifact = self.context.payload(self.document, "data_url_payload", decoded_location,
                                            payload, media_type)
            suffix, reason = _data_suffix(payload, media_type)
            component = self.context.component(
                self.document, "data_url_payload", decoded_location, data=payload, artifact=artifact,
                disposition="extracted" if media_type.startswith("image/") or suffix or
                media_type in _SCRIPT_MEDIA or media_type == "text/css" else "opaque",
                reason=None if media_type in _SCRIPT_MEDIA or media_type == "text/css" else reason,
                media_type=media_type, parameters=parameters, empty=not payload)
            state = {"payload": payload, "media_type": media_type, "parameters": parameters,
                     "location": decoded_location, "artifact": artifact, "component": component}
            self.data_sources[occurrence] = state
            self.context.finding(self.document, "attachments", "inline_payload", decoded_location,
                                 component=component, target=target, artifact=artifact,
                                 media_type=media_type, parameters=parameters, empty=not payload)
            if media_type.startswith("image/"):
                self.context.finding(self.document, "images", "inline_image", decoded_location,
                                     component=component, artifact=artifact, target=target)
            self._data_source(state, script_context=script_context)
            if reason and media_type in _MEDIA_SUFFIXES:
                self.context.problem(self.document, affected, "data URL " + reason, location)
            if suffix and self.scan_embedded is not None:
                name = "inline-%06d%s" % (len(self.data_sources), suffix)
                child = self.scan_embedded(payload, name, self.context, parent=self.document,
                                           location=decoded_location, component=component)
                self.context.finding(self.document, "attachments", "embedded_document", decoded_location,
                                     component=component, artifact=artifact, child_document=child)
            elif suffix and self.scan_embedded is None:
                self.context.problem(self.document, ("attachments",),
                                     "data URL supported-document scanner is unavailable", location)
        except (BrewdocError, ValueError, UnicodeError, LookupError, binascii.Error) as exc:
            self.context.component(self.document, "data_url_payload", decoded_location,
                                   disposition="unreadable", reason=str(exc))
            self.context.problem(self.document, affected, "data URL decoding failed: " + str(exc), location)

    def _data_source(self, state, *, script_context):
        """Decode each available static source once and reuse its exact payload artifact."""
        media = state["media_type"]
        scripted = media in _SCRIPT_MEDIA or (script_context and media == "text/plain")
        if state.get("source_error") or not (media.startswith("text/") or scripted):
            return
        try:
            if "text" not in state:
                self.context.charge("decoded_bytes", len(state["payload"]) * 4, state["location"])
                default = "utf-8" if media in _SCRIPT_MEDIA or media == "text/css" else "US-ASCII"
                state["text"] = state["payload"].decode(state["parameters"].get("charset", default))
                self.context.finding(self.document, "text", "inline_text", state["location"],
                                     component=state["component"], artifact=state["artifact"],
                                     text=state["text"], mode="source_only")
            if scripted and not state.get("script_source"):
                self.context.finding(self.document, "scripts", "inline_data_script", state["location"],
                                     component=state["component"], artifact=state["artifact"],
                                     text=state["text"], mode="source_only",
                                     script_resource_candidate=media not in _SCRIPT_MEDIA)
                state["script_source"] = True
            if media == "text/css" and not state.get("css_source"):
                state["css_source"] = True
                self.css(state["text"], state["location"])
        except (BrewdocError, UnicodeError, LookupError) as exc:
            state["source_error"] = str(exc)
            self.context.problem(self.document, ("text", "scripts", "external_references"),
                                 "data URL source decoding failed: " + str(exc), state["location"])

    def css(self, value, location):
        """Extract CSS resource tokens while retaining their literal source spelling."""
        for target, original, start, end in _css_targets(value, self.context, location):
            self.emit("external_references", "css_url", location, target=target,
                      source_target=original, css_character_start=start, css_character_end=end)

    def handle_starttag(self, tag, attrs):
        """Inventory original attributes before filtering or readable projection."""
        start = self.position()
        raw = self.get_starttag_text()
        location = self.location(start, start + len(raw))
        self.context.charge("records", 1 + len(attrs), location)
        self.source_elements.append((tag, dict(reversed(attrs))))
        attrs = [list(attribute) for attribute in attrs]
        counts = self.stack[-1][2] if self.stack else self.roots
        counts[tag] = counts.get(tag, 0) + 1
        parent = self.stack[-1][1] if self.stack else ""
        xpath = "%s/%s[%d]" % (parent, tag, counts[tag])
        location = self.location(start, start + len(raw), xpath=xpath)
        hidden = bool(self.stack and self.stack[-1][3]) or any(
            key == "hidden" or (key == "aria-hidden" and value == "true") or
            (key == "style" and re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", value or "", re.I))
            for key, value in attrs)
        component = self.context.component(self.document, "html_element", location, tag=tag,
                                           attributes=attrs, hidden=hidden)
        if hidden:
            self.emit("hidden_structures", "hidden_element", location, component=component)
        if tag in _CONTROLS:
            self.emit("forms", "form_control", location, component=component, tag=tag,
                      attributes=attrs)
        if tag in {"iframe", "frame", "embed", "object", "applet"}:
            self.emit("actions", "embedded_resource", location, component=component, tag=tag)
        if tag in {"img", "image"}:
            self.emit("images", "image", location, component=component, attributes=attrs)
        occurrences = {}
        tag_end = re.match(r"<\s*[^\s/>]+", raw).end()
        source_attributes = iter(_ATTRIBUTE.finditer(raw, tag_end))
        for key, value in attrs:
            occurrences[key] = occurrences.get(key, 0) + 1
            attribute = {**location, "attribute": key, "attribute_ordinal": occurrences[key]}
            token = next(source_attributes, None)
            value_span = None
            if token is not None and token.group(1).lower() == key:
                group = next((number for number in (2, 3, 4) if token.group(number) is not None), 1)
                value_span = (start + token.start(group), start + token.end(group))
                attribute = self.location(*value_span, xpath=xpath, attribute=key,
                                          attribute_ordinal=occurrences[key], name=token.group(1))
            if value is None:
                continue
            if key in _URL_ATTRIBUTES or key.endswith(":href"):
                self.emit("links", "url_attribute", attribute, target=value)
                self.emit("external_references", "resource_reference", attribute, target=value)
                if tag == "script" and key == "src":
                    self.emit("scripts", "external_script", attribute, target=value)
            elif key in {"ping", "archive"}:
                self.emit("links", "url_attribute", attribute, target=value)
                for target in value.split():
                    self.emit("external_references", "resource_reference", attribute, target=target)
            elif key in {"srcset", "imagesrcset"}:
                self.emit("external_references", "srcset", attribute, text=value)
                for target in _srcset(value):
                    self.emit("external_references", "srcset_target", attribute, target=target)
            elif key.startswith("on"):
                payload = (self.data[self.offsets[value_span[0]]:self.offsets[value_span[1]]]
                           if value_span is not None and self.exact_offsets else value.encode("utf-8"))
                if not self.exact_offsets or self.base.get("decoded_attribute"):
                    attribute = {**attribute, "artifact_encoding": "utf-8", "artifact_basis": "decoded_text"}
                artifact = self.context.payload(self.document, "event_handler", attribute,
                                                payload, "text/javascript")
                self.emit("scripts", "event_handler", attribute, text=value, artifact=artifact)
                self.emit("actions", "event_handler", attribute, text=value, artifact=artifact)
            elif key == "style":
                self.css(value, attribute)
            elif key == "srcdoc":
                self.emit("actions", "inline_frame_source", attribute, text=value)
                depth = self.srcdoc_depth + 1
                self.context.check("nesting", len(getattr(self.context, "active_hashes", ())) + depth,
                                   attribute)
                self.context.charge("decoded_bytes", len(value) * 12, attribute)
                nested = _SourceParser(value, value.encode(), "utf-8", self.name,
                                       self.context, self.document,
                                       {**attribute, "decoded_attribute": True,
                                        "parent_xpath": xpath}, srcdoc_depth=depth,
                                       scan_embedded=self.scan_embedded)
                nested.feed(value)
                nested.close()
                _dom(nested)
            if key != "style" and ("(" in value or "\\" in value):
                self.css(value, attribute)
        values = dict(attrs)
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            content = values.get("content", "") or ""
            match = re.search(r"(?:^|;)\s*url\s*=\s*(.*)", content, re.I | re.S)
            self.emit("actions", "meta_refresh", location, text=content,
                      target=match.group(1).strip().strip("\"'") if match else None)
        if tag not in _VOID:
            budget = getattr(self.context, "zip_budget", None)
            if budget is not None:
                budget.check("depth", len(self.stack) + 1, self.name)
            self.stack.append([tag, xpath, {}, hidden, start + len(raw), location])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self._finish_raw(self.stack[index], self.position())
                del self.stack[index:]
                return

    def _finish_raw(self, entry, end):
        """Retain raw source bytes through the closing tag or original EOF."""
        tag, xpath, _, _, start, _ = entry
        if tag not in _RAW:
            return
        location = self.location(start, end, xpath=xpath)
        value = self.source[start:end]
        if tag not in {"script", "style"}:
            self.emit("text", "source_text", location,
                      text=html.unescape(value) if tag in {"textarea", "title"} else value)
            if tag == "textarea":
                self.emit("forms", "control_default", location, text=html.unescape(value))
            return
        payload = (self.data[self.offsets[start]:self.offsets[end]]
                   if self.exact_offsets else value.encode("utf-8"))
        if not self.exact_offsets or self.base.get("decoded_attribute"):
            location = {**location, "artifact_encoding": "utf-8", "artifact_basis": "decoded_text"}
        artifact = self.context.payload(self.document, tag, location, payload,
                                        "text/javascript" if tag == "script" else "text/css")
        self.emit("scripts" if tag == "script" else "text", "inline_" + tag,
                  location, text=value, artifact=artifact)
        if tag == "style":
            self.css(value, location)

    def handle_data(self, value):
        if not value or (self.stack and self.stack[-1][0] in _RAW):
            return
        start = self.position()
        self.context.charge("records", 1, self.base)
        counts = self.stack[-1][2] if self.stack else self.roots
        counts["text()"] = counts.get("text()", 0) + 1
        parent = self.stack[-1][1] if self.stack else ""
        xpath = "%s/text()[%d]" % (parent, counts["text()"])
        self.emit("text", "source_text", self.location(start, start + len(value), xpath=xpath),
                  text=value, hidden=bool(self.stack and self.stack[-1][3]))

    def _entity(self, spelling):
        start = self.position()
        if not self.source.startswith(spelling, start):
            spelling = spelling.rstrip(";")
        self.emit("text", "source_text", self.location(start, start + len(spelling)),
                  text=html.unescape(spelling), source_text=spelling,
                  hidden=bool(self.stack and self.stack[-1][3]))

    def handle_entityref(self, name):
        self._entity("&" + name + ";")

    def handle_charref(self, name):
        self._entity("&#" + name + ";")

    def handle_comment(self, data):
        start = self.position()
        self.emit("hidden_structures", "html_comment", self.location(start, start + len(data) + 7),
                  text=data)

    def close(self):
        super().close()
        for entry in self.stack:
            self._finish_raw(entry, len(self.source))
        self.stack.clear()


def _srcset(value):
    """Preserve URL tokens, including commas inside data URLs, without fetching."""
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in " \t\r\n\f,":
            position += 1
        start = position
        while position < len(value) and value[position] not in " \t\r\n\f":
            position += 1
        target = value[start:position].rstrip(",")
        if target:
            yield target
        if position and value[position - 1] == ",":
            continue
        while position < len(value) and value[position] != ",":
            position += 1


def _inspect(data, name, context, document, location, *, plain, scan_embedded=None):
    """Retain original evidence before decoding and make interruptions explicit."""
    root = {"kind": "text" if plain else "html", "part": name, **(location or {})}
    try:
        context.check("source_bytes", len(data), root)
        artifact = context.payload(document, "original_source", root, data,
                                   "text/plain" if plain else "text/html")
        context.component(document, "original_source", root, data=data, artifact=artifact)
        context.charge("decoded_bytes", len(data) * 12, root)
        source, codec = _decode(data, plain=plain,
                                encoding=(location or {}).get("original_encoding") or
                                (location or {}).get("charset"))
        parser = _SourceParser(source, data, codec, name, context, document, location,
                               scan_embedded=scan_embedded)
        parser.feed(source)
        parser.close()
        _dom(parser)
        if plain:
            _markdown(source, parser)
        _complete(context, document)
    except (BrewdocError, UnicodeError, LookupError, ValueError, RecursionError) as exc:
        context.problem(document, _CAPABILITIES, str(exc), root)


def inspect_html(data: bytes, name: str, context: InspectionContext, document: str,
                 *, location: dict | None = None, scan_embedded=None) -> None:
    """Inspect original source independently of readable article or hidden filtering."""
    _inspect(data, name, context, document, location, plain=False, scan_embedded=scan_embedded)


def inspect_text(data: bytes, name: str, context: InspectionContext, document: str,
                 *, scan_embedded=None) -> None:
    """Record HTML constructs and Markdown links without modifying pass-through bytes."""
    _inspect(data, name, context, document, None, plain=True, scan_embedded=scan_embedded)


def inspect_plain_targets(data: bytes, name: str, context: InspectionContext, document: str,
                          *, location: dict | None = None, charset: str = "utf-8") -> None:
    """Inspect inert text targets with original encoding and no HTML interpretation."""
    boundary = {"kind": "text", "part": name, **(location or {})}
    try:
        context.check("source_bytes", len(data), boundary)
        context.charge("decoded_bytes", len(data) * 12, boundary)
        bom, codec = next(((bom, codec) for bom, codec in (
            (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be")) if data.startswith(bom)), (b"", charset))
        source = data[len(bom):].decode(codec)
        parser = _SourceParser(source, data, codec, name, context, document, location, decode_data=False)
        _markdown(source, parser)
    except (BrewdocError, UnicodeError, LookupError, ValueError) as exc:
        context.problem(document, ("links", "external_references"), str(exc), boundary)


def _text_location(parser, start, end):
    line = bisect_right(parser.lines, start)
    location = {**parser.base, "kind": "text", "part": parser.name,
                "line": line, "column": start - parser.lines[line - 1] + 1}
    if parser.exact_offsets:
        location.update(byte_start=parser.offsets[start], byte_end=parser.offsets[end])
    else:
        location.pop("byte_start", None)
        location.pop("byte_end", None)
        location["position_basis"] = "decoded_text"
    return location


def _css_escape(value, position):
    """Consume one CSS escape, including optional hex-escape whitespace."""
    position += 1
    match = re.match(r"[0-9a-fA-F]{1,6}", value[position:position + 6])
    if match:
        number = int(match.group(), 16)
        position += len(match.group())
        if position < len(value) and value[position].isspace():
            position += 2 if value[position:position + 2] == "\r\n" else 1
        return chr(number) if 0 < number <= 0x10FFFF and not 0xD800 <= number <= 0xDFFF else "\ufffd", position
    if position >= len(value):
        return "\ufffd", position
    character = value[position]
    if character in "\r\n\f":
        return "", position + (2 if value[position:position + 2] == "\r\n" else 1)
    return character, position + 1


def _css_tokens(value):
    """Tokenize CSS strings, identifiers and delimiters without matching inert text."""
    position = 0
    while position < len(value):
        start, character = position, value[position]
        if value.startswith("/*", position):
            end = value.find("*/", position + 2)
            position = len(value) if end < 0 else end + 2
            continue
        if character.isspace():
            while position < len(value) and value[position].isspace():
                position += 1
            yield "space", " ", start, position
            continue
        if character in "\"'":
            quote, decoded = character, []
            position += 1
            while position < len(value) and value[position] != quote:
                if value[position] == "\\":
                    text, position = _css_escape(value, position)
                    decoded.append(text)
                else:
                    decoded.append(value[position])
                    position += 1
            position += position < len(value)
            yield "string", "".join(decoded), start, position
            continue
        if character.isalnum() or character in "_-\\" or ord(character) >= 128:
            decoded = []
            while position < len(value):
                character = value[position]
                if character == "\\":
                    text, position = _css_escape(value, position)
                    decoded.append(text)
                elif character.isalnum() or character in "_-" or ord(character) >= 128:
                    decoded.append(character)
                    position += 1
                else:
                    break
            yield "identifier", "".join(decoded), start, position
            continue
        position += 1
        yield character, character, start, position


def _css_targets(value, context, location):
    """Recover URL function and import-string targets while retaining source spelling."""
    tokens = []
    for token in _css_tokens(value):
        context.charge("records", 1, location)
        tokens.append(token)
    for index, token in enumerate(tokens):
        kind, decoded, start, end = token
        cursor = index + 1
        if kind == "@":
            if cursor >= len(tokens) or tokens[cursor][1].lower() != "import":
                continue
            cursor += 1
            while cursor < len(tokens) and tokens[cursor][0] == "space":
                cursor += 1
            if cursor < len(tokens) and tokens[cursor][0] == "string":
                selected = tokens[cursor]
                yield selected[1], value[selected[2]:selected[3]], selected[2], selected[3]
        elif kind == "identifier" and decoded.lower() == "url":
            if cursor >= len(tokens) or tokens[cursor][0] != "(" or tokens[cursor][2] != end:
                continue
            cursor += 1
            while cursor < len(tokens) and tokens[cursor][0] == "space":
                cursor += 1
            first, parts = cursor, []
            while cursor < len(tokens) and tokens[cursor][0] != ")":
                parts.append(tokens[cursor][1])
                cursor += 1
            if first < cursor and cursor < len(tokens):
                begin, finish = tokens[first][2], tokens[cursor - 1][3]
                yield "".join(parts).strip(), value[begin:finish], begin, finish


def _dom(parser):
    """Inventory the complete accepted Lexbor DOM before readable cleaning."""
    if LexborHTMLParser is None:
        reason = ("HTML DOM unavailable: unsupported runtime" if not _SUPPORTED_RUNTIME else
                  "HTML DOM unavailable: native parser binding: " + _PARSER_ERROR)
        location = {**parser.base, "kind": "html", "part": parser.name}
        parser.context.component(parser.document, "html_dom", location,
                                 disposition="unreadable", reason=reason)
        parser.context.problem(parser.document, _CAPABILITIES, reason, location)
        return
    tree = LexborHTMLParser(parser.source)
    available = Counter((tag, tuple(sorted(attrs.items()))) for tag, attrs in parser.source_elements)
    pending = [(tree.root, "", False)]
    while pending:
        node, parent, inherited_hidden = pending.pop()
        counts, children = {}, []
        child = node.child
        while child is not None:
            parser.context.charge("records", 1, {"kind": "html", "part": parser.name})
            children.append(child)
            child = child.next
        tag = node.tag
        location = {**parser.base, "kind": "html", "part": parser.name,
                    "xpath": parent or "/html[1]", "path_basis": "original_dom"}
        for key in ("byte_start", "byte_end", "line", "column"):
            location.pop(key, None)
        attrs = node.attributes
        parser.context.charge("records", 1 + len(attrs), location)
        hidden = (inherited_hidden or "hidden" in attrs or attrs.get("aria-hidden") == "true" or
                  bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                                 attrs.get("style", "") or "", re.I)))
        parser.context.component(parser.document, "dom_node", location, tag=tag,
                                 attributes=attrs, hidden=hidden)
        if tag == "-text":
            parser.emit("text", "dom_text", location, text=node.text(), hidden=hidden)
        else:
            signature = (tag, tuple(sorted(attrs.items())))
            if available[signature]:
                available[signature] -= 1
            else:
                for key, value in attrs.items():
                    if value is not None and (key in _URL_ATTRIBUTES or key.endswith(":href")):
                        parser.emit("links", "dom_url_attribute", {**location, "attribute": key}, target=value)
                        parser.emit("external_references", "dom_resource_reference",
                                    {**location, "attribute": key}, target=value)
                        if tag == "script" and key == "src":
                            parser.emit("scripts", "external_script", {**location, "attribute": key}, target=value)
                    if value is not None and key.startswith("on"):
                        artifact = parser.context.payload(parser.document, "event_handler", location,
                                                          value.encode(), "text/javascript")
                        parser.emit("scripts", "dom_event_handler", location, text=value,
                                    artifact=artifact, encoding="utf-8", provenance="decoded_dom_attribute")
                        parser.emit("actions", "dom_event_handler", location, text=value, artifact=artifact)
                    if value is not None and key in {"srcset", "imagesrcset"}:
                        for target in _srcset(value):
                            parser.emit("external_references", "srcset_target", location, target=target)
                    if value is not None and key == "style":
                        parser.css(value, {**location, "attribute": key})
                if tag in _CONTROLS:
                    parser.emit("forms", "dom_form_control", location, attributes=attrs)
                if tag in {"img", "image"}:
                    parser.emit("images", "dom_image", location, attributes=attrs)
                if tag in {"iframe", "frame", "embed", "object", "applet"}:
                    parser.emit("actions", "dom_embedded_resource", location, attributes=attrs)
                if tag == "style":
                    parser.css(node.text(), location)
                if tag == "script":
                    value = node.text()
                    artifact = parser.context.payload(parser.document, "script", location,
                                                      value.encode("utf-8"), "text/javascript")
                    parser.emit("scripts", "inline_script", location, text=value, artifact=artifact,
                                provenance="decoded_dom_text", encoding="utf-8")
            if hidden:
                parser.emit("hidden_structures", "dom_hidden_element", location)
        for child in children:
            child_tag = "text()" if child.tag == "-text" else child.tag
            counts[child_tag] = counts.get(child_tag, 0) + 1
            path = "%s/%s[%d]" % (location["xpath"], child_tag, counts[child_tag])
            pending.append((child, path, hidden))
        if children:
            pending[-len(children):] = reversed(pending[-len(children):])


def _markdown_inert(source, parser):
    """Mark code, escapes and raw HTML before recognizing Markdown destinations."""
    location = {"kind": "text", "part": parser.name}
    parser.context.charge("decoded_bytes", len(source), location)
    mask = bytearray(len(source))
    def mark(start, end):
        parser.context.charge("decoded_bytes", end - start, location)
        mask[start:end] = b"\x01" * (end - start)
    fence, html_block, previous_blank = None, False, True
    raw_tag = r'''</?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])(?:[^>"']|"[^"]*"|'[^']*')*>'''
    block_tags = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|"
                  "dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
                  "header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|"
                  "p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul")
    block_start = re.compile(r" {0,3}</?(?:" + block_tags + r")(?=[\s/>])", re.I)
    for line in re.finditer(r"[^\n]+\n?|\n", source):
        text = line.group()
        stripped = text.lstrip(" ")
        marker = re.match(r" {0,3}(`{3,}|~{3,})", text)
        if fence is not None:
            mark(*line.span())
            if (marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]
                    and not text[marker.end():].strip()):
                fence = None
        elif marker:
            fence = marker.group(1)[0], len(marker.group(1))
            mark(*line.span())
        elif html_block:
            if not stripped.strip():
                html_block = False
            else:
                mark(*line.span())
        elif block_start.match(text):
            html_block = True
            mark(*line.span())
        elif previous_blank and re.fullmatch(r" {0,3}" + raw_tag + r"\s*", text):
            html_block = True
            mark(*line.span())
        elif text.startswith(("    ", "\t")):
            mark(*line.span())
        previous_blank = not text.strip()
    for match in re.finditer(r"<!--(?:[\s\S]*?-->|[\s\S]*$)|<(?P<raw>script|pre|style|textarea)\b[^>]*>"
                             r"[\s\S]*?(?:</(?P=raw)\s*>|$)|</?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])"
                             r'''(?:[^>"']|"[^"]*"|'[^']*')*>''', source, re.I):
        mark(*match.span())
    runs, following = [], {}
    for match in re.finditer(r"`+", source):
        if not mask[match.start()]:
            parser.context.charge("records", 1, location)
            runs.append(match.span())
    partners = {}
    for start, end in reversed(runs):
        width = end - start
        if width in following:
            partners[start] = following[width]
        following[width] = (start, end)
    autolinks = []
    for expression in (_EMAIL_AUTOLINK, re.compile(r"<[A-Za-z][A-Za-z0-9+.-]*:[^<>\s]*>")):
        for match in expression.finditer(source):
            parser.context.charge("records", 1, location)
            autolinks.append(match.span())
    autolinks.sort()
    autolink_index = 0
    for start, end in runs:
        while autolink_index < len(autolinks) and start >= autolinks[autolink_index][1]:
            autolink_index += 1
        inside_autolink = (autolink_index < len(autolinks) and
                           autolinks[autolink_index][0] <= start < autolinks[autolink_index][1])
        if not mask[start] and not inside_autolink and start in partners:
            mark(start, partners[start][1])
    position = 0
    while position + 1 < len(source):
        if source[position] == "\\" and not mask[position]:
            mask[position + 1] = 1
            position += 2
        else:
            position += 1
    return mask


def _markdown(source, parser):
    """Locate balanced inline labels and every reference-link use in source order."""
    inert = _markdown_inert(source, parser)
    pairs, pending = {}, []
    escaped = False
    for position, character in enumerate(source):
        if escaped or inert[position]:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character == "[":
            parser.context.charge("records", 1, {"kind": "text", "part": parser.name})
            pending.append(position)
        elif character == "]" and pending:
            parser.context.charge("records", 1, {"kind": "text", "part": parser.name})
            pairs[pending.pop()] = position
    definitions, definition_spans = {}, []
    occupied = []
    for match in _REFERENCE.finditer(source):
        if inert[match.start()]:
            continue
        parser.context.charge("records", 1, {"kind": "text", "part": parser.name})
        group = 2 if match.group(2) is not None else 3
        definitions.setdefault(_reference_key(match.group(1)), _markdown_value(match.group(group)))
        definition_spans.append(match.span())
        occupied.append(match.span(group))
        parser.emit("links", "markdown_reference", _text_location(parser, *match.span(group)),
                    target=_markdown_value(match.group(group)), reference=match.group(1),
                    source_target=match.group(group))
    for match in re.finditer(r"<([A-Za-z][A-Za-z0-9+.-]*:[^<>\s]*)>", source):
        if inert[match.start()]:
            continue
        occupied.append(match.span(1))
        parser.emit("links", "text_autolink", _text_location(parser, *match.span(1)),
                    target=match.group(1))
    for match in _EMAIL_AUTOLINK.finditer(source):
        if inert[match.start()]:
            continue
        occupied.append(match.span(1))
        parser.emit("links", "email_autolink", _text_location(parser, *match.span(1)),
                    target="mailto:" + match.group(1), source_address=match.group(1))
    position, definition_index = 0, 0
    while position < len(source):
        if inert[position]:
            position += 1
            continue
        if source[position] == "\\":
            position += 2
            continue
        while definition_index < len(definition_spans) and position >= definition_spans[definition_index][1]:
            definition_index += 1
        if definition_index < len(definition_spans) and position >= definition_spans[definition_index][0]:
            position = definition_spans[definition_index][1]
            continue
        if source[position] != "[":
            position += 1
            continue
        opening = position
        close = pairs.get(opening)
        if close is None:
            position += 1
            continue
        label = source[opening + 1:close]
        position = close + 1
        target, start, end = None, opening, position
        if source[position:position + 1] == "(":
            start = position + 1
            while start < len(source) and source[start].isspace():
                start += 1
            end = _destination_end(source, start, parser)
            if end is not None:
                if source[start:start + 1] == "<":
                    start += 1
                target = _markdown_value(source[start:end])
                position = end + 1
        elif source[position:position + 1] == "[":
            reference_end = pairs.get(position)
            if reference_end is not None:
                reference = source[position + 1:reference_end] or label
                target = definitions.get(_reference_key(reference))
                end = position = reference_end + 1
        else:
            target = definitions.get(_reference_key(label))
        if target is not None:
            occupied.append((start, end))
            parser.emit("links", "markdown_link", _text_location(parser, start, end),
                        target=target, source_target=source[start:end],
                        image=opening > 0 and source[opening - 1] == "!")
    occupied.sort()
    occupied_index = 0
    for match in re.finditer(r"(?i)\b(?:https?|ftp|file|mailto):[^\s<>\"']+", source):
        while occupied_index < len(occupied) and match.start() >= occupied[occupied_index][1]:
            occupied_index += 1
        if inert[match.start()] or (occupied_index < len(occupied) and
                                   occupied[occupied_index][0] <= match.start() < occupied[occupied_index][1]):
            continue
        parser.emit("links", "text_url", _text_location(parser, *match.span()), target=match.group())


def _markdown_value(value):
    return html.unescape(re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])", r"\1", value))


def _reference_key(value):
    return " ".join(_markdown_value(value).split()).casefold()


def _destination_end(source, start, parser):
    angle = source[start:start + 1] == "<"
    position, depth = start + int(angle), 0
    while position < len(source):
        parser.context.charge("records", 1, {"kind": "text", "part": parser.name})
        character = source[position]
        if character == "\\":
            position += 2
            continue
        if angle and character == ">":
            return position
        if not angle:
            if character == "(":
                depth += 1
            elif character == ")":
                if not depth:
                    return position
                depth -= 1
            elif character.isspace() and not depth:
                return position
        position += 1
    return None
