"""Offline EPUB 2/3 conversion with bounded archive and qualified XML reads."""

from __future__ import annotations

import posixpath
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit
from xml.parsers import expat

from brewdoc.common import (BrewdocError, Rendered, Route, _ascii_integer, _assemble,
                            _close_list_item, _emphasis, _escape_markdown_text, _join_escaped,
                            _merge_list, _ordered_marker, _source_lines, _url_target, new_tally,
                            reading)

CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB = "{http://www.idpf.org/2007/ops}"
NCX = "{http://www.daisy.org/z3986/2005/ncx/}"
XML = "{http://www.w3.org/XML/1998/namespace}"
XENC = "{http://www.w3.org/2001/04/xmlenc#}"
XHTML_MEDIA = "application/xhtml+xml"
FONT_MEDIA = {"font/otf", "font/ttf", "font/woff", "font/woff2", "application/font-sfnt",
              "application/font-woff", "application/vnd.ms-opentype", "application/x-font-ttf",
              "application/x-font-opentype", "application/x-font-truetype"}
FONT_ALGORITHMS = {"http://www.idpf.org/2008/embedding",
                   "http://ns.adobe.com/pdf/enc#RC"}
LIMITS = {
    "source_bytes": 33_554_432, "members": 1_024, "member_bytes": 8_388_608,
    "declared_bytes": 33_554_432, "consumed_bytes": 33_554_432,
    "document_elements": 65_536, "total_elements": 262_144, "depth": 64,
    "spine_items": 512, "table_columns": 256, "table_slots": 65_536,
    "total_table_slots": 262_144, "markdown_bytes": 8_388_608,
}
OMISSIONS = ("CSS, layout, pagination and font rendering", "scripts, forms and active media",
             "image pixels, images without alt text and graphical SVG content",
             "unsupported SVG and MathML semantics", "navigation semantics beyond declared TOC hierarchy")


def _limit(name: str, value: int) -> None:
    if value > LIMITS[name]:
        raise BrewdocError("EPUB %s limit exceeded" % name)


@dataclass(slots=True)
class _Budget:
    """Charge physical reads and logical parses across the complete publication."""

    consumed_bytes: int = 0
    total_elements: int = 0
    total_table_slots: int = 0

    def charge(self, name: str, amount: int) -> None:
        """Refuse an aggregate overrun before retaining the newly consumed content."""
        value = getattr(self, name) + amount
        _limit(name, value)
        setattr(self, name, value)


def _decode_url(value: str, *, path: bool = True) -> str:
    """Decode one URL component strictly without admitting encoded separators or traversal."""
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise BrewdocError("EPUB malformed percent escape: %s" % value)
    if path and re.search(r"%(?:2f|5c)", value, re.I):
        raise BrewdocError("EPUB encoded path separator: %s" % value)
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise BrewdocError("EPUB target is not UTF-8: %s" % value) from exc
    if path and re.search(r"%(?:2e|2f|5c|25)", decoded, re.I):
        raise BrewdocError("EPUB nested encoded target: %s" % value)
    if (path and "\\" in decoded) or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise BrewdocError("EPUB unsafe target: %s" % value)
    return decoded


def _resolve_target(source: str, target: str, *, fragments: bool = False) -> tuple[str, str]:
    """Resolve an internal URL once, permitting parent steps only inside the archive."""
    if not target:
        raise BrewdocError("EPUB target is missing")
    if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
        raise BrewdocError("EPUB unsafe target: %s" % target)
    components = urlsplit(target)
    if components.scheme or components.netloc:
        raise BrewdocError("EPUB required target is external: %s" % target)
    if components.query or (components.fragment and not fragments):
        raise BrewdocError("EPUB unsupported target component: %s" % target)
    path = _decode_url(components.path)
    fragment = _decode_url(components.fragment, path=False) if components.fragment else ""
    if path.startswith("/"):
        raise BrewdocError("EPUB absolute target: %s" % target)
    joined = posixpath.join(posixpath.dirname(source), path) if path else source
    resolved = posixpath.normpath(joined)
    if resolved in ("", ".", "..") or resolved.startswith("../"):
        raise BrewdocError("EPUB target leaves archive: %s" % target)
    return resolved, fragment


def _archive_members(package: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Validate central-directory names, file kinds, compression and declared expansion."""
    infos = package.infolist()
    _limit("members", len(infos))
    members, folded, total = {}, set(), 0
    for info in infos:
        name = info.filename
        bare = name.rstrip("/") if info.is_dir() else name
        if (name != info.orig_filename or not bare or bare.startswith("/") or "\\" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or posixpath.normpath(bare) != bare or bare == ".." or bare.startswith("../")):
            raise BrewdocError("EPUB unsafe archive member: %s" % name)
        collision = unicodedata.normalize("NFC", bare).casefold()
        if name in members or collision in folded:
            raise BrewdocError("EPUB duplicate or ambiguous archive member: %s" % name)
        folded.add(collision)
        mode = stat.S_IFMT(info.external_attr >> 16)
        allowed = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
        if mode not in allowed:
            raise BrewdocError("EPUB unsupported archive member kind: %s" % name)
        if info.flag_bits & 1:
            raise BrewdocError("EPUB encrypted ZIP member: %s" % name)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BrewdocError("EPUB unsupported ZIP compression: %s" % name)
        total += info.file_size
        _limit("declared_bytes", total)
        members[name] = info
    return members


def _read_member(package: zipfile.ZipFile, members: dict, name: str, budget: _Budget) -> bytes:
    """Read one member in bounded chunks, charging every reread and checking actual length."""
    info = members.get(name)
    if info is None or info.is_dir():
        raise BrewdocError("EPUB member is missing: %s" % name)
    _limit("member_bytes", info.file_size)
    chunks, size = [], 0
    try:
        with package.open(info) as stream:
            while True:
                chunk = stream.read(min(65_536, LIMITS["member_bytes"] - size + 1,
                                        LIMITS["consumed_bytes"] - budget.consumed_bytes + 1))
                if not chunk:
                    break
                size += len(chunk)
                _limit("member_bytes", size)
                budget.charge("consumed_bytes", len(chunk))
                chunks.append(chunk)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError) as exc:
        raise BrewdocError("EPUB member unreadable: %s: %s" % (name, exc)) from exc
    if size != info.file_size:
        raise BrewdocError("EPUB member length disagrees: %s" % name)
    return b"".join(chunks)


def _xml_encoding(data: bytes, name: str) -> None:
    """Admit strict UTF-8/UTF-16 and require XML declarations to match detected byte order."""
    if data.startswith((b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\x00<", b"<\x00\x00\x00")):
        raise BrewdocError("EPUB unsupported XML encoding: %s" % name)
    if data.startswith(b"\xff\xfe"):
        encoding, text = "utf-16-le", data[2:]
    elif data.startswith(b"\xfe\xff"):
        encoding, text = "utf-16-be", data[2:]
    elif data.startswith(b"<\x00"):
        encoding, text = "utf-16-le", data
    elif data.startswith(b"\x00<"):
        encoding, text = "utf-16-be", data
    else:
        encoding, text = "utf-8", data.removeprefix(b"\xef\xbb\xbf")
    try:
        decoded = text.decode(encoding, "strict")
    except UnicodeError as exc:
        raise BrewdocError("EPUB invalid XML encoding: %s" % name) from exc
    declaration = re.match(r"<\?xml\s+[^?]*?encoding\s*=\s*(['\"])(.*?)\1", decoded)
    if declaration is not None:
        declared = declaration[2].lower()
        allowed = {"utf-8"} if encoding == "utf-8" else {"utf-16", encoding}
        if declared not in allowed:
            raise BrewdocError("EPUB unsupported or mismatched XML encoding: %s" % name)


def _parse_xml(data: bytes, name: str, budget: _Budget) -> ET.Element:
    """Preflight the exact bounded bytes before allocating a namespace-aware XML tree."""
    _xml_encoding(data, name)
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    depth, elements = 0, 0

    def start(tag, attributes):
        nonlocal depth, elements
        depth += 1
        elements += 1
        _limit("depth", depth)
        _limit("document_elements", elements)
        budget.charge("total_elements", 1)
        if tag.startswith("http://www.w3.org/2001/XInclude}"):
            raise BrewdocError("EPUB XInclude is unsupported: %s" % name)
        if "http://www.w3.org/XML/1998/namespace}base" in attributes:
            raise BrewdocError("EPUB base resolution is unsupported: %s" % name)

    def end(tag):
        nonlocal depth
        depth -= 1

    def doctype(tag, system, public, internal):
        if internal or tag not in {"html", "ncx"}:
            raise BrewdocError("EPUB unsupported XML doctype: %s" % name)

    def entity(*arguments):
        raise BrewdocError("EPUB XML entity declaration is unsupported: %s" % name)

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartDoctypeDeclHandler, parser.EntityDeclHandler = doctype, entity
    parser.ExternalEntityRefHandler = lambda *arguments: 1
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise BrewdocError("EPUB malformed XML: %s: %s" % (name, exc)) from exc
    return root


def _xml_member(package, members, name: str, budget: _Budget) -> ET.Element:
    return _parse_xml(_read_member(package, members, name, budget), name, budget)


def _single_child(root: ET.Element, tag: str, noun: str) -> ET.Element:
    children = root.findall(tag)
    if len(children) != 1:
        raise BrewdocError("EPUB requires one %s" % noun)
    return children[0]


def _xml_ids(root: ET.Element, name: str) -> dict[str, ET.Element]:
    """Retain exact declared IDs and refuse ambiguity before source-fragment indexing."""
    ids = {}
    for node in root.iter():
        for key in ("id", XML + "id"):
            value = node.get(key)
            if value is not None:
                if value in ids:
                    raise BrewdocError("EPUB duplicate XML id: %s in %s" % (value, name))
                ids[value] = node
        if XML + "base" in node.attrib or node.tag == XHTML + "base":
            raise BrewdocError("EPUB base resolution is unsupported: %s" % name)
    return ids


def _plain_text(node: ET.Element) -> str:
    return re.sub(r"[ \t\r\n]+", " ", "".join(node.itertext())).strip(" \t\r\n")


@dataclass(frozen=True, slots=True)
class _Resource:
    """One manifest declaration without retaining its content tree."""

    member: str
    media: str
    properties: frozenset[str]
    fallback: str
    external: bool = False


@dataclass(frozen=True, slots=True)
class _SpineItem:
    """One declared occurrence and its supported fallback endpoint."""

    member: str
    auxiliary: bool


@dataclass(slots=True)
class _Publication:
    """Keep small ordered package records after releasing the package XML tree."""

    version: str
    manifest: dict[str, _Resource]
    spine: list[_SpineItem]
    navigation: str
    metadata: list[tuple[str, str]] = field(default_factory=list)


def _effective_resource(identifier: str, manifest: dict, members: dict) -> str:
    """Follow only declared fallback edges to a local supported XHTML endpoint."""
    visited = set()
    while identifier:
        if identifier in visited:
            raise BrewdocError("EPUB fallback cycle: %s" % identifier)
        visited.add(identifier)
        resource = manifest.get(identifier)
        if resource is None:
            raise BrewdocError("EPUB manifest id is missing: %s" % identifier)
        if resource.external:
            raise BrewdocError("EPUB required resource is external: %s" % identifier)
        if resource.member not in members or members[resource.member].is_dir():
            raise BrewdocError("EPUB member is missing: %s" % resource.member)
        if resource.media == XHTML_MEDIA:
            return resource.member
        identifier = resource.fallback
    raise BrewdocError("EPUB unsupported required media without XHTML fallback")


def _fixed_layout(root: ET.Element) -> None:
    for node in root.iter():
        properties = node.get("properties", "").split()
        if "rendition:layout-pre-paginated" in properties:
            raise BrewdocError("EPUB fixed layout is unsupported")
        if node.tag == OPF + "meta":
            prop, name = node.get("property"), node.get("name", "").lower()
            value = _plain_text(node) if prop else node.get("content", "").strip().lower()
            if ((prop == "rendition:layout" and value == "pre-paginated")
                    or (name in {"fixed-layout", "ibooks:fixed-layout", "rendition:layout"}
                        and value in {"true", "yes", "pre-paginated"})):
                raise BrewdocError("EPUB fixed layout is unsupported")


def _package(package, members: dict, budget: _Budget) -> _Publication:
    """Resolve the single rendition, ordered manifest/spine and declared publication metadata."""
    if _read_member(package, members, "mimetype", budget) != b"application/epub+zip":
        raise BrewdocError("EPUB mimetype must be application/epub+zip")
    container = _xml_member(package, members, "META-INF/container.xml", budget)
    if container.tag != CONTAINER + "container":
        raise BrewdocError("EPUB container root has wrong namespace")
    rootfiles = _single_child(container, CONTAINER + "rootfiles", "container rootfiles")
    roots = rootfiles.findall(CONTAINER + "rootfile")
    if len(roots) != 1:
        raise BrewdocError("EPUB supported profile requires one rootfile")
    if roots[0].get("media-type") != "application/oebps-package+xml":
        raise BrewdocError("EPUB rootfile has unsupported media type")
    part, _fragment = _resolve_target("", roots[0].get("full-path", ""))
    root = _xml_member(package, members, part, budget)
    if root.tag != OPF + "package":
        raise BrewdocError("EPUB package root has wrong namespace")
    version = root.get("version", "")
    if version not in {"2.0", "3.0"}:
        raise BrewdocError("EPUB unsupported package version: %s" % version)
    _xml_ids(root, part)
    _fixed_layout(root)
    manifest_node = _single_child(root, OPF + "manifest", "manifest")
    spine_node = _single_child(root, OPF + "spine", "spine")
    metadata_node = _single_child(root, OPF + "metadata", "metadata")
    manifest, targets = {}, set()
    for item in manifest_node.findall(OPF + "item"):
        identifier, href = item.get("id", ""), item.get("href", "")
        if not identifier or identifier in manifest:
            raise BrewdocError("EPUB missing or duplicate manifest id: %s" % identifier)
        if not href or not item.get("media-type"):
            raise BrewdocError("EPUB manifest resource declaration is incomplete: %s" % identifier)
        components = urlsplit(href)
        external = bool(components.scheme or components.netloc)
        member = href if external else _resolve_target(part, href)[0]
        if member in targets:
            raise BrewdocError("EPUB duplicate manifest resource URL: %s" % member)
        targets.add(member)
        manifest[identifier] = _Resource(member, item.get("media-type", ""),
            frozenset(item.get("properties", "").split()), item.get("fallback", ""), external)
    spine, idrefs = [], set()
    for item in spine_node.findall(OPF + "itemref"):
        identifier = item.get("idref", "")
        if identifier in idrefs:
            raise BrewdocError("EPUB repeated spine idref: %s" % identifier)
        idrefs.add(identifier)
        linear = item.get("linear", "yes")
        if linear not in {"yes", "no"}:
            raise BrewdocError("EPUB invalid spine linear value: %s" % linear)
        _limit("spine_items", len(spine) + 1)
        spine.append(_SpineItem(_effective_resource(identifier, manifest, members),
                                linear == "no"))
    if not spine:
        raise BrewdocError("EPUB spine is empty")
    if version == "3.0":
        navs = [item for item in manifest.values() if "nav" in item.properties]
        if len(navs) != 1 or navs[0].media != XHTML_MEDIA or navs[0].external:
            raise BrewdocError("EPUB requires one local XHTML navigation document")
        navigation = navs[0].member
    else:
        nav = manifest.get(spine_node.get("toc", ""))
        if nav is None or nav.media != "application/x-dtbncx+xml" or nav.external:
            raise BrewdocError("EPUB 2 spine requires a local declared NCX")
        navigation = nav.member
    metadata = []
    for local, label in (("title", "Publication title"), ("creator", "Creator"),
                         ("language", "Language"), ("identifier", "Identifier")):
        metadata.extend(("%s %d" % (label, number), _plain_text(node))
                        for number, node in enumerate(metadata_node.findall(DC + local), 1))
    unique = root.get("unique-identifier")
    if unique is not None:
        declared = [node for node in metadata_node.findall(DC + "identifier")
                    if node.get("id") == unique or node.get(XML + "id") == unique]
        if len(declared) != 1:
            raise BrewdocError("EPUB unique identifier does not resolve: %s" % unique)
        metadata.append(("Unique identifier", _plain_text(declared[0])))
    del root, container
    return _Publication(version, manifest, spine, navigation, metadata)


def _encryption(package, members: dict, publication: _Publication, budget: _Budget) -> None:
    """Accept recognized font-only obfuscation declarations without reading font pixels."""
    name = "META-INF/encryption.xml"
    if name not in members:
        return
    root = _xml_member(package, members, name, budget)
    if root.tag != CONTAINER + "encryption":
        raise BrewdocError("EPUB encryption root has wrong structure")
    targets = set()
    if not len(root):
        raise BrewdocError("EPUB encryption declaration is empty")
    for encrypted in root:
        if encrypted.tag != XENC + "EncryptedData":
            raise BrewdocError("EPUB encryption declaration has unsupported structure")
        method = _single_child(encrypted, XENC + "EncryptionMethod", "encryption method")
        data = _single_child(encrypted, XENC + "CipherData", "encryption cipher data")
        reference = _single_child(data, XENC + "CipherReference", "encryption reference")
        if len(encrypted) != 2 or len(method) or len(data) != 1 or len(reference):
            raise BrewdocError("EPUB encryption declaration has unsupported structure")
        target, _fragment = _resolve_target("", reference.get("URI", ""))
        if target in targets:
            raise BrewdocError("EPUB duplicate encryption target: %s" % target)
        targets.add(target)
        resource = next((item for item in publication.manifest.values()
                         if not item.external and item.member == target), None)
        if (method.get("Algorithm") not in FONT_ALGORITHMS or resource is None
                or resource.media not in FONT_MEDIA or target not in members
                or members[target].is_dir()):
            raise BrewdocError("EPUB unsupported encryption declaration: %s" % target)


OMITTED = {"script", "style", "template", "input", "button", "select", "option", "optgroup",
           "datalist", "textarea", "output", "progress", "meter",
           "audio", "video", "object", "embed", "iframe", "canvas", "source", "track"}
BLOCKS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "hgroup",
          "address", "figure", "figcaption", "dl", "dt", "dd", "hr", "ul", "ol", "li",
          "blockquote", "pre", "code", "table", "h1", "h2", "h3", "h4", "h5", "h6",
          "form", "fieldset", "legend", "label", "details", "summary"}
SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"
PUBLICATION_ANCHOR = "brewdoc-publication-navigation"
EMPHASIS = {"em": "*", "i": "*", "strong": "**", "b": "**"}
# Cell whitespace collapses except inside literal code records, which keep source spacing.
CELL_SPACE = re.compile(r'(Code: text="[^"]*")|[ \t\r\n]+')


def _name(node: ET.Element) -> str:
    return node.tag[len(XHTML):] if node.tag.startswith(XHTML) else ""


def _omitted(node: ET.Element) -> bool:
    return (_name(node) in OMITTED or "hidden" in node.attrib
            or node.get("aria-hidden", "").lower() == "true")


def _toc(node: ET.Element) -> bool:
    return node.tag == XHTML + "nav" and "toc" in node.get(EPUB + "type", "").split()


def _body(root: ET.Element, name: str) -> ET.Element:
    if root.tag != XHTML + "html":
        raise BrewdocError("EPUB reading document requires qualified XHTML html: %s" % name)
    bodies = root.findall(XHTML + "body")
    if len(bodies) != 1:
        raise BrewdocError("EPUB reading document requires one qualified body: %s" % name)
    _xml_ids(root, name)
    return bodies[0]


def _included_text(node: ET.Element) -> str:
    """Keep text and tails in XML order while excluding omitted descendant subtrees."""
    if _omitted(node):
        return ""
    parts = [node.text or ""]
    for child in node:
        parts.append(_included_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _collapse(text: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", text).strip(" \t\r\n")


def _has_code(node: ET.Element) -> bool:
    if _omitted(node):
        return False
    return _name(node) == "code" or any(_has_code(child) for child in node)


def _prose_text(node: ET.Element) -> str:
    """Read link labels around literal code without duplicating code into prose."""
    if _omitted(node) or _name(node) == "code":
        return ""
    parts = [node.text or ""]
    for child in node:
        parts.extend((_prose_text(child), child.tail or ""))
    return "".join(parts)


def _escape(text: str, *, quoted: bool = False, literal: bool = False) -> str:
    return _escape_markdown_text(text, preserve_unicode=True, quoted=quoted, literal=literal,
                                 max_markdown_bytes=LIMITS["markdown_bytes"])


def _checked_join(parts: list[str], separator: str = "") -> str:
    """Join escaped pieces within the byte limit; a source `&` cannot open an entity across pieces."""
    _limit("markdown_bytes", sum(len(part.encode("utf-8")) for part in parts)
           + len(separator.encode("utf-8")) * max(0, len(parts) - 1))
    return separator.join(parts) if separator else _join_escaped(parts)


def _inline_text(text: str | None) -> str:
    return _escape(re.sub(r"[ \t\r\n]+", " ", text or ""))


def _region(tokens: list[str | tuple[str, bool]]) -> str:
    """Resolve one region's text and emphasis tokens into Markdown within the byte limit."""
    text = _emphasis(tokens)
    _limit("markdown_bytes", len(text.encode("utf-8")))
    return text


def _navigation(package, members: dict, publication: _Publication,
                budget: _Budget) -> list[tuple[int, str, str | None]]:
    """Read declared TOC entries in document preorder, retaining unlinked groups."""
    name = publication.navigation
    root = _xml_member(package, members, name, budget)
    entries = []
    if publication.version == "3.0":
        body = _body(root, name)

        def included_tocs(node):
            if _omitted(node):
                return
            if _toc(node):
                yield node
            for child in node:
                yield from included_tocs(child)

        tocs = list(included_tocs(body))
        if len(tocs) != 1:
            raise BrewdocError("EPUB requires one qualified TOC navigation subtree: %s" % name)
        top = _single_child(tocs[0], XHTML + "ol", "TOC navigation list")

        def walk_list(node, depth):
            if _omitted(node):
                raise BrewdocError("EPUB hidden TOC navigation list: %s" % name)
            for item in node:
                if _omitted(item):
                    continue
                if item.tag != XHTML + "li":
                    raise BrewdocError("EPUB malformed TOC navigation item: %s" % name)
                labels = [child for child in item if child.tag in (XHTML + "a", XHTML + "span")]
                if len(labels) != 1 or _omitted(labels[0]):
                    raise BrewdocError("EPUB navigation label is missing: %s" % name)
                label = _collapse(_included_text(labels[0]))
                if not label:
                    raise BrewdocError("EPUB navigation label is missing: %s" % name)
                target = labels[0].get("href") if labels[0].tag == XHTML + "a" else None
                if labels[0].tag == XHTML + "a" and not target:
                    raise BrewdocError("EPUB navigation target is missing: %s" % name)
                entries.append((depth, label, target))
                for nested in item.findall(XHTML + "ol"):
                    if not _omitted(nested):
                        walk_list(nested, depth + 1)

        walk_list(top, 0)
    else:
        if root.tag != NCX + "ncx":
            raise BrewdocError("EPUB NCX root has wrong namespace: %s" % name)
        _xml_ids(root, name)
        top = _single_child(root, NCX + "navMap", "NCX navigation map")

        def walk_points(node, depth):
            for point in node.findall(NCX + "navPoint"):
                label_node = _single_child(point, NCX + "navLabel", "NCX navigation label")
                label = _plain_text(_single_child(label_node, NCX + "text", "NCX label text"))
                if not label:
                    raise BrewdocError("EPUB navigation label is missing: %s" % name)
                content = point.findall(NCX + "content")
                if len(content) > 1 or content and not content[0].get("src"):
                    raise BrewdocError("EPUB navigation target is missing: %s" % name)
                entries.append((depth, label, content[0].get("src") if content else None))
                walk_points(point, depth + 1)

        walk_points(top, 0)
    if not entries:
        raise BrewdocError("EPUB navigation is empty: %s" % name)
    return entries


@dataclass(slots=True)
class _ChapterIndex:
    """Keep labels and available fragment destinations between independent XML passes."""

    ordinal: int
    member: str
    title: str
    heading: str
    fragments: dict[str, str]

    @property
    def anchor(self) -> str:
        """Return the stable destination for this declared spine occurrence."""
        return "brewdoc-chapter-%06d" % self.ordinal


def _chapter_index(root: ET.Element, member: str, ordinal: int) -> _ChapterIndex:
    body = _body(root, member)
    fragments, heading = {}, ""

    def visit(node):
        nonlocal heading
        if _omitted(node):
            return
        svg_metadata = node.tag in {SVG + name for name in ("svg", "title", "desc", "image")}
        if not (_name(node) or node.tag.startswith(SVG)):
            return
        if _name(node) or svg_metadata:
            for key in ("id", XML + "id"):
                value = node.get(key)
                if value is not None:
                    fragments[value] = "brewdoc-chapter-%06d-fragment-%06d" % (
                        ordinal, len(fragments) + 1)
        if not heading and _name(node) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = _collapse(_included_text(node))
        for child in node:
            visit(child)

    visit(body)
    titles = root.findall(XHTML + "head/" + XHTML + "title")
    title = next((_collapse(_included_text(node)) for node in titles
                  if _collapse(_included_text(node))), "")
    return _ChapterIndex(ordinal, member, title, heading, fragments)


def _destination(source: str, target: str, indices: list[_ChapterIndex],
                 current: _ChapterIndex | None = None, *, required: bool = False) -> str | None:
    """Bind self-links to the current occurrence and other links to the earliest endpoint."""
    member, fragment = (_resolve_target(source, target, fragments=True) if target else (source, ""))
    index = current if current is not None and member == current.member else next(
        (item for item in indices if item.member == member), None)
    destination = (index.fragments.get(fragment) if fragment else index.anchor) if index else None
    if required and destination is None:
        raise BrewdocError("EPUB navigation target is unavailable: %s" % target)
    return destination


def _image(node: ET.Element, source: str) -> str:
    """Render `![alt](target)`, or nothing without alt text; unsafe targets still refuse.
    Like HTML, only http(s), mailto and relative targets survive; other schemes give `()`."""
    target = node.get("src") or ""
    if target:
        if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
            raise BrewdocError("EPUB unsafe target: %s" % target)
        components = urlsplit(target)
        if not components.scheme and not components.netloc:
            _resolve_target(source, components.path or target, fragments=True)
        elif components.scheme.lower() not in {"", "http", "https", "mailto"}:
            target = ""
    alt = _collapse(node.get("alt", ""))
    if not alt:
        return ""
    return _checked_join(["![", _escape(alt), "](", _url_target(target), ")"])


def _link(label: str, target: str | None, current: _ChapterIndex,
          indices: list[_ChapterIndex]) -> list[str | tuple[str, bool]]:
    """Render a link as `_emphasis` tokens, so label edge spaces move outside it.
    A blank label stays plain text once its target passes the usual checks."""
    if target is None:
        return [label]
    if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
        raise BrewdocError("EPUB unsafe target: %s" % target)
    components = urlsplit(target)
    if components.scheme.lower() in {"http", "https", "mailto"}:
        closing = "](%s)" % _url_target(target)
        _limit("markdown_bytes", len(label.encode("utf-8")) + len(closing) + 1)
    else:
        destination = ""
        if components.query and not (components.scheme or components.netloc):
            _resolve_target(current.member, components.path or "#" + components.fragment, fragments=True)
        elif not (components.scheme or components.netloc):
            destination = _destination(current.member, target, indices, current)
        if not destination:
            return [label, (' (Link target: "%s")' % _escape(target, quoted=True), False)]
        closing = "](#%s)" % destination
    return [("[", True), label, (closing, False)] if label.strip(" \t\r\n") else [label]


def _inline(node: ET.Element, current: _ChapterIndex, indices: list[_ChapterIndex], *,
            cell: bool = False, nested_table=None) -> list[str | tuple[str, bool]]:
    """Render inline text and links as `_emphasis` tokens without dropping excluded-subtree tails."""
    tag = _name(node)
    if _omitted(node):
        return []
    if tag == "br":
        return [" / " if cell else "\n"]
    tokens = [_inline_text(node.text)]
    for child in node:
        name = _name(child)
        if _omitted(child) or not name and not child.tag.startswith(SVG):
            parts = []
        elif name == "img":
            parts = [_image(child, current.member)]
        elif name == "table":
            parts = [nested_table(child)] if nested_table is not None else []
        elif cell and name in {"code", "pre"}:
            parts = [_checked_join(['Code: text="', _escape(_included_text(child), literal=True), '"'])]
        elif child.tag == SVG + "svg":
            parts = [_checked_join(_svg_lines(child, current.member), "; ")]
        else:
            parts = _inline(child, current, indices, cell=cell, nested_table=nested_table)
        if cell and name in BLOCKS and name != "table" and any(
                isinstance(part, str) and part for part in parts):
            parts = [" ", *parts, " "]
        tokens += parts
        tokens.append(_inline_text(child.tail))
    if tag in EMPHASIS:
        return [(EMPHASIS[tag], True), *tokens, (EMPHASIS[tag], False)]
    if tag == "a" and node.get("href") is not None:
        return _link(_region(tokens), node.get("href"), current, indices)
    return tokens


def _svg_lines(node: ET.Element, source: str) -> list[str]:
    """Describe declared SVG text and image references while omitting graphical nodes."""
    lines = []
    for child in node:
        if child.tag in {SVG + "title", SVG + "desc"} and not _omitted(child):
            lines.append('SVG %s: "%s"' % (child.tag[len(SVG):],
                                            _escape(_collapse(_included_text(child)), quoted=True)))
    def included_images(element):
        if _omitted(element):
            return
        if element.tag == SVG + "image":
            yield element
        for child in element:
            yield from included_images(child)

    for image in included_images(node):
        target = image.get("href", image.get(XLINK + "href"))
        if target:
            if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
                raise BrewdocError("EPUB unsafe target: %s" % target)
            components = urlsplit(target)
            if not components.scheme and not components.netloc:
                _resolve_target(source, components.path or target, fragments=True)
        lines.append('SVG image reference: %s' % ('"%s"' % _escape(target, quoted=True)
                                                if target is not None else "unspecified"))
    return lines


def _table_rows(node: ET.Element, budget: _Budget, current: _ChapterIndex,
                indices: list[_ChapterIndex]) -> list[list[str]]:
    """Resolve row-group spans before allocating the padded grid and nested-cell records."""
    groups, direct = [], []
    for child in node:
        if _omitted(child):
            continue
        if child.tag == XHTML + "tr":
            direct.append(child)
        elif _name(child) in {"thead", "tbody", "tfoot"}:
            if direct:
                groups.append(direct)
                direct = []
            groups.append([row for row in child if row.tag == XHTML + "tr" and not _omitted(row)])
    if direct:
        groups.append(direct)
    row_count = sum(len(group) for group in groups)
    _limit("table_slots", row_count)
    occupied, cells, width, offset = set(), {}, 0, 0
    for group in groups:
        for row_number, row in enumerate(group):
            y, x = offset + row_number, 0
            for cell in row:
                if _name(cell) not in {"td", "th"} or _omitted(cell):
                    continue
                while (y, x) in occupied:
                    x += 1
                cols = max(1, _ascii_integer(cell.get("colspan"), 1))
                rows = _ascii_integer(cell.get("rowspan"), 1)
                rows = len(group) - row_number if rows == 0 else max(1, rows)
                _limit("table_columns", x + cols)
                _limit("table_slots", rows * cols)
                rows = min(rows, len(group) - row_number)
                width = max(width, x + cols)
                _limit("table_slots", row_count * width)
                _limit("total_table_slots", budget.total_table_slots + row_count * width)
                for sy in range(y, y + rows):
                    for sx in range(x, x + cols):
                        if (sy, sx) in occupied:
                            raise BrewdocError("EPUB overlapping table spans")
                        occupied.add((sy, sx))
                cells[y, x] = cell
                x += cols
        offset += len(group)
    slots = row_count * width
    _limit("table_slots", slots)
    budget.charge("total_table_slots", slots)
    grid = [[""] * width for _row in range(row_count)]

    def nested_records(nested):
        """Flatten a nested table into one cell: optional caption, then quoted row/column records."""
        rows = _table_rows(nested, budget, current, indices)
        records, size = [], 0
        for ny, row in enumerate(rows):
            for nx, content in enumerate(row):
                prefix = '(row %d, column %d) "' % (ny + 1, nx + 1)
                size += len(prefix) + len(content.encode("utf-8")) + 1 + bool(records) * 2
                _limit("markdown_bytes", size)
                records.append(_checked_join([prefix, content, '"']))
        captions = [_region(_inline(caption, current, indices, cell=True)).strip(" \t\r\n")
                    for caption in nested.findall(XHTML + "caption") if not _omitted(caption)]
        prefix = ('Nested table caption: "%s"; ' % _checked_join(captions, " ") if captions else "")
        return _checked_join([" ", prefix, "Nested table: ", _checked_join(records, "; "), " "])

    for (y, x), cell in cells.items():
        content = _region(_inline(cell, current, indices, cell=True, nested_table=nested_records))
        grid[y][x] = CELL_SPACE.sub(lambda match: match[1] or " ", content).strip(" \t\r\n")
    return grid


def _region_anchors(node: ET.Element, index: _ChapterIndex, *, descendants: bool = True) -> list[str]:
    anchors = []

    def visit(element):
        if _omitted(element):
            return
        for key in ("id", XML + "id"):
            identifier = element.get(key)
            if identifier in index.fragments:
                anchors.append(index.fragments[identifier])
        if descendants:
            for child in element:
                visit(child)

    visit(node)
    return anchors


def _render_blocks(body: ET.Element, current: _ChapterIndex, indices: list[_ChapterIndex],
                   budget: _Budget, tally: dict, *, consume_toc: bool) -> list[tuple]:
    """Render ordered XHTML regions; list items and quotes carry their nesting as line prefixes."""
    blocks, emitted = [], set()
    prefix, floor = "", 0
    if _omitted(body):
        return blocks

    def anchors(node, descendants=True):
        for anchor in _region_anchors(node, current, descendants=descendants):
            if anchor not in emitted:
                blocks.append(("anchor", anchor, prefix))
                emitted.add(anchor)

    def text(lines, *, source=True):
        if not lines:
            return
        _limit("markdown_bytes", sum(len(line.encode("utf-8")) + 1 for line in lines))
        blocks.append(("text", lines, prefix))
        if source:
            tally["text_regions"] += 1

    def list_blocks(node):
        """Put each item's first text on its marker line; tight item lines share one block."""
        nonlocal prefix, floor
        outer, start, scope = prefix, len(blocks), floor
        items = [child for child in node if child.tag == XHTML + "li" and not _omitted(child)]
        reversed_order = "reversed" in node.attrib
        number = _ascii_integer(node.get("start"), len(items) if reversed_order else 1)
        step = -1 if reversed_order else 1
        for item in items:
            number = _ascii_integer(item.get("value"), number)
            marker = _ordered_marker(number) if _name(node) == "ol" else "- "
            number += step
            prefix, first = outer + " " * len(marker), len(blocks)
            anchors(item, False)
            floor = len(blocks)
            flow(item)
            prefix, floor = outer, scope
            _close_list_item(blocks, marker, first, outer)
        _merge_list(blocks, start, floor)

    def walk(node):
        """Render one visible element as its block kind; unknown containers flow their children."""
        nonlocal prefix, floor
        if _omitted(node):
            return
        name = _name(node)
        if consume_toc and _toc(node):
            anchors(node)
            text(["[Publication navigation](#%s)" % PUBLICATION_ANCHOR], source=False)
        elif name == "table":
            for caption in node.findall(XHTML + "caption"):
                walk(caption)
            anchors(node)
            rows = _table_rows(node, budget, current, indices)
            if rows:
                blocks.append(("table", rows, prefix))
                tally["tables"] += 1
        elif name in {"ul", "ol"}:
            anchors(node, False)
            list_blocks(node)
        elif name == "blockquote":
            anchors(node, False)
            outer, scope = prefix, floor
            prefix, floor = prefix + "> ", len(blocks)
            flow(node)
            prefix, floor = outer, scope
            blocks.append(("end", None, prefix))
        elif name in {"pre", "code"}:
            anchors(node)
            code = _included_text(node)
            _limit("markdown_bytes", len(code.encode("utf-8")) + 2)
            blocks.append(("block", ["```", *code.split("\n"), "```"], prefix))
            tally["text_regions"] += 1
        elif name in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "caption", "dt", "dd"}:
            level = int(name[1:]) if name.startswith("h") else None
            if _has_code(node):
                anchors(node, False)
                flow(node, heading_level=level)
                return
            anchors(node)
            content = _region(_inline(node, current, indices)).strip(" \t\r\n")
            if content:
                text(_source_lines(content, level))
        elif name == "img":
            anchors(node)
            if image := _image(node, current.member):
                text([image])
        elif node.tag == SVG + "svg":
            anchors(node)
            for line in _svg_lines(node, current.member):
                text([line])
        elif name:
            anchors(node, False)
            flow(node)

    def flow(node, *, heading_level=None):
        """Split literal code in place; the text between splits is one emphasis region.
        A piece carries its open emphasis markers and link elements; a split closes, then reopens them."""
        pending = [([_inline_text(node.text)], ())]
        inline_nodes = []

        def flush():
            """Emit pending pieces as one region, reopening only frames that change between pieces."""
            buffers, opened = [[]], ()
            for tokens, frames in [*pending, ([], ())]:
                keep = 0
                while keep < min(len(opened), len(frames)) and opened[keep] == frames[keep]:
                    keep += 1
                for frame in reversed(opened[keep:]):
                    if isinstance(frame, str):
                        buffers[-1].append((frame, False))
                        continue
                    label = _region(buffers.pop())
                    buffers[-1] += (_link(label, frame.get("href"), current, indices)
                                    if label.strip(" \t\r\n") else [label])
                for frame in frames[keep:]:
                    if isinstance(frame, str):
                        buffers[-1].append((frame, True))
                    else:
                        buffers.append([])
                buffers[-1] += tokens
                opened = frames
            content = _region(buffers[0]).strip(" \t\r\n")
            for inline_node, descendants in inline_nodes:
                anchors(inline_node, descendants)
            if content:
                text(_source_lines(content, heading_level))
            pending.clear()
            inline_nodes.clear()

        def mixed_inline(element, frames=()):
            """Queue inline pieces with their open frames; code, images and SVG split as blocks."""
            if _omitted(element):
                return
            name = _name(element)
            if name == "code":
                flush()
                walk(element)
                return
            if name in EMPHASIS:
                frames += (EMPHASIS[name],)
            elif name == "a" and element.get("href") is not None:
                frames += (element,)
            inline_nodes.append((element, False))
            pending.append(([_inline_text(element.text)], frames))
            for child in element:
                if _name(child) == "img" or child.tag == SVG + "svg":
                    flush()
                    walk(child)
                elif _has_code(child):
                    mixed_inline(child, frames)
                else:
                    inline_nodes.append((child, True))
                    pending.append((_inline(child, current, indices), frames))
                pending.append(([_inline_text(child.tail)], frames))
            if name == "a" and element.get("href") is not None and not _collapse(_prose_text(element)):
                flush()
                text([_region(_link("Link target", element.get("href"), current, indices))], source=False)

        for child in node:
            name = _name(child)
            if name in BLOCKS or name == "img" or child.tag == SVG + "svg":
                flush()
                walk(child)
            elif _has_code(child):
                mixed_inline(child)
            else:
                inline_nodes.append((child, True))
                pending.append((_inline(child, current, indices), ()))
            pending.append(([_inline_text(child.tail)], ()))
        flush()

    anchors(body, False)
    flow(body)
    for anchor in current.fragments.values():
        if anchor not in emitted:
            blocks.append(("anchor", anchor))
    return blocks


def _render_epub(path: Path, _sheets=None) -> Rendered:
    """Index available destinations, then render each declared spine occurrence atomically."""
    tally, budget = new_tally(), _Budget()
    try:
        _limit("source_bytes", path.stat().st_size)
        with reading(path, "EPUB"), zipfile.ZipFile(path) as package:
            members = _archive_members(package)
            publication = _package(package, members, budget)
            _encryption(package, members, publication, budget)
            entries = _navigation(package, members, publication, budget)
            indices = []
            for ordinal, item in enumerate(publication.spine, 1):
                root = _xml_member(package, members, item.member, budget)
                indices.append(_chapter_index(root, item.member, ordinal))
                del root
            navigation, labels = [], {}
            for depth, label, target in entries:
                destination = (_destination(publication.navigation, target, indices, required=True)
                               if target is not None else None)
                navigation.append((depth, label, destination))
                if target is not None:
                    member, _fragment = _resolve_target(publication.navigation, target, fragments=True)
                    labels.setdefault(member, label)
            units = []
            for item, index in zip(publication.spine, indices):
                root = _xml_member(package, members, item.member, budget)
                blocks = _render_blocks(_body(root, item.member), index, indices, budget, tally,
                                        consume_toc=item.member == publication.navigation)
                if item.auxiliary:
                    blocks.insert(0, ("text", ["Auxiliary content."]))
                label = labels.get(item.member) or index.title or index.heading or "Chapter %d" % index.ordinal
                units.append((index.ordinal, label, blocks))
                del root
        metadata = tuple((field, '"%s"' % _escape(value, quoted=True)) for field, value in publication.metadata)
        return _assemble(path, "epub", "chapter", len(publication.spine), units, tally,
                         not_carried=OMISSIONS, preserve_unicode=True, publication_rows=metadata,
                         publication_navigation=tuple(navigation), max_markdown_bytes=LIMITS["markdown_bytes"])
    except BrewdocError as exc:
        if str(exc).startswith("Markdown byte limit exceeded:"):
            raise BrewdocError("EPUB markdown_bytes limit exceeded") from exc
        raise


def render_epub(path) -> tuple[str, dict]:
    """Return Markdown and tally for a static EPUB in declared spine order."""
    return _render_epub(Path(path))[:2]


ROUTE = Route("epub", "chapter", (".epub",), _render_epub, OMISSIONS)
