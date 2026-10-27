"""Occurrence-preserving static inspection of Office, ODF and EPUB packages."""

from __future__ import annotations

import io
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import PurePosixPath
from urllib.parse import unquote

from brewdoc._inspection_common import InspectionContext, InspectionLimit
from brewdoc.common import BrewdocError, _local_name, _xml_preflight, _zip_member_bytes


PACKAGE_CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms",
                        "attachments", "images", "external_references", "macros", "hidden_structures")
WORKBOOK_SUFFIXES = frozenset((".xlsx", ".xlsm", ".xlsb", ".ods"))
DOCUMENT_SUFFIXES = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb",
                               ".xls", ".ods", ".odt", ".odp", ".html", ".epub",
                               ".eml", ".md", ".txt"))
IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
                            ".webp", ".emf", ".wmf", ".svg", ".ico"))
SCRIPT_SUFFIXES = frozenset((".js", ".mjs", ".cjs", ".vbs", ".bas", ".py", ".bsh"))
HTML_SUFFIXES = frozenset((".html", ".htm", ".xhtml", ".svg"))
XML_SUFFIXES = frozenset((".xml", ".rels", ".opf", ".ncx", ".xsd", ".xsl", ".xslt"))
URL_ATTRIBUTES = frozenset(("href", "src", "data", "codebase", "archive", "action",
                            "formaction", "url", "uri", "target", "full-path"))
TEXT_ATTRIBUTES = frozenset(("title", "alt", "descr", "tooltip", "label", "caption",
                             "prompt", "description"))
REL_NAMESPACES = frozenset(("http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                             "http://purl.oclc.org/ooxml/officeDocument/relationships"))
FORM_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:form:1.0"
SCRIPT_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:script:1.0"
WORD_NAMESPACES = frozenset(("http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                            "http://purl.oclc.org/ooxml/wordprocessingml/main"))
WORD_FORM_EXTENSION = "http://schemas.microsoft.com/office/word/2010/wordml"
WORD_FORM_NAMESPACES = WORD_NAMESPACES | {WORD_FORM_EXTENSION}
WORD_FORM_CONTROLS = frozenset(("ffData", "textInput", "checkBox", "ddList", "sdt"))
WORD_FORM_PROPERTIES = {
    "ffData": frozenset(("name", "enabled", "calcOnExit", "entryMacro", "exitMacro", "helpText",
                           "statusText", "textInput", "checkBox", "ddList")),
    "textInput": frozenset(("type", "default", "maxLength", "format")),
    "checkBox": frozenset(("size", "sizeAuto", "default", "checked")),
    "ddList": frozenset(("result", "default", "listEntry")),
    "sdtPr": frozenset(("alias", "tag", "id", "lock", "placeholder", "showingPlcHdr", "dataBinding",
                          "temporary", "rPr", "text", "richText", "picture", "date", "comboBox", "dropDownList",
                          "docPartObj", "docPartList", "citation", "equation", "bibliography", "group", "checkbox",
                          "appearance", "color", "repeatingSection", "repeatingSectionItem")),
    "dropDownList": frozenset(("listItem",)), "comboBox": frozenset(("listItem",)),
    "placeholder": frozenset(("docPart",)),
    "checkbox": frozenset(("checked", "checkedState", "uncheckedState")),
}
TABLE_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
SPREADSHEET_NAMESPACES = frozenset(("http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                                    "http://purl.oclc.org/ooxml/spreadsheetml/main"))
FIELD_COMMAND = re.compile(r"^\s*(HYPERLINK|INCLUDETEXT|INCLUDEPICTURE|DDEAUTO|DDE|LINK)\b", re.I)
FIELD_ARGUMENT = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|([^\s]+))')
URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MEDIA_TYPES = {".xml": "application/xml", ".rels": "application/xml",
               ".html": "text/html", ".xhtml": "application/xhtml+xml",
               ".js": "text/javascript", ".mjs": "text/javascript", ".py": "text/x-python",
               ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".svg": "image/svg+xml", ".txt": "text/plain"}
CFB_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
MANIFEST = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
ODF_MEDIA = {"application/vnd.oasis.opendocument.spreadsheet": ".ods",
             "application/vnd.oasis.opendocument.text": ".odt",
             "application/vnd.oasis.opendocument.presentation": ".odp"}


def _split_tag(tag: str) -> tuple[str, str]:
    return tuple(tag[1:].split("}", 1)) if tag.startswith("{") else ("", tag)


def _xml_nodes(root: ET.Element):
    """Yield exact element ordinals and bounded paths using an iterator stack."""
    stack = [(iter((root,)), "", {})]
    ordinal = 0
    while stack:
        iterator, parent_path, counts = stack[-1]
        node = next(iterator, None)
        if node is None:
            stack.pop()
            continue
        ordinal += 1
        counts[node.tag] = counts.get(node.tag, 0) + 1
        count = counts[node.tag]
        path = None
        if parent_path is not None and len(parent_path) + len(node.tag) + len(str(count)) + 3 <= 4096:
            path = "%s/%s[%d]" % (parent_path, node.tag, count)
        location = {"element_ordinal": ordinal}
        if path is not None:
            location["xpath"] = path
        yield node, location, len(stack)
        if len(node):
            stack.append((iter(node), path, {}))


def _relationship_source(part: str) -> str:
    """Map an OPC sidecar to its source part, including the package root."""
    if part == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(part)
    return posixpath.join(posixpath.dirname(directory), filename[:-5])


def _resolve_part(source: str, target: str) -> str | None:
    """Resolve a package URI for evidence only, never a filesystem destination."""
    if URL_SCHEME.match(target) or target.startswith("//"):
        return None
    path = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not path:
        return source or None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), path).lstrip("/"))
    return None if resolved in ("", ".", "..") or resolved.startswith("../") else resolved


def _member_kind(part: str) -> str:
    suffix = PurePosixPath(part).suffix.lower()
    if part.endswith("/"):
        return "directory"
    if "vba" in part.lower() or suffix in (".vba", ".xlm"):
        return "macro-project"
    if suffix == ".rels":
        return "relationships"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in SCRIPT_SUFFIXES:
        return "script"
    if suffix in XML_SUFFIXES or suffix in HTML_SUFFIXES:
        return "xml"
    if suffix in DOCUMENT_SUFFIXES:
        return "embedded-document"
    if "/embeddings/" in part or part.startswith("Object "):
        return "embedded-object"
    return "package-member"


def _parse_xml(data: bytes, part: str, context: InspectionContext) -> ET.Element:
    """Share operation-wide expansion limits with every inspected XML occurrence."""
    _xml_preflight(data, part, context.zip_budget)
    return ET.fromstring(data)


def _field_instruction(text: str, document: str, context: InspectionContext,
                       location: dict, component: str, **details) -> None:
    context.finding(document, "actions", "field-instruction", location,
                    component=component, text=text, **details)
    command = FIELD_COMMAND.match(text)
    if command is None:
        return
    argument = FIELD_ARGUMENT.match(text, command.end())
    target = (argument[1] if argument[1] is not None else argument[2]) if argument else None
    capability = "links" if command[1].upper() == "HYPERLINK" else "external_references"
    context.finding(document, capability, "field-target", location,
                    component=component, text=text, target=target, command=command[1])


def _relationships(root: ET.Element, part: str, ordinal: int, document: str,
                   context: InspectionContext, component: str, member_names: dict,
                   relationship_index: dict) -> None:
    """Preserve every relationship occurrence and its original and resolved targets."""
    source = _relationship_source(part)
    count = 0
    for node, node_location, _depth in _xml_nodes(root):
        if _local_name(node.tag) != "Relationship":
            continue
        count += 1
        location = {"kind": "package", "part": part, "member_ordinal": ordinal,
                    **node_location, "relationship_ordinal": count}
        attributes = dict(node.attrib)
        target = attributes.get("Target")
        external = attributes.get("TargetMode", "Internal") == "External"
        resolved = None if external or target is None else _resolve_part(source, target)
        matches = member_names.get(resolved, ())
        details = {"attributes": attributes, "source_part": source,
                   "relationship_id": attributes.get("Id"), "external": external,
                   "resolved_part": resolved, "target_member_ordinals": list(matches)}
        context.finding(document, "links", "relationship", location,
                        component=component, target=target, **details)
        if external:
            context.finding(document, "external_references", "relationship", location,
                            component=component, target=target, **details)
        relationship_index.setdefault((source, attributes.get("Id")), []).append(
            (target, location, attributes))
        if target is None:
            context.problem(document, ("links", "external_references"),
                            "relationship has no target", location)
        elif not external and (resolved is None or len(matches) != 1):
            context.problem(document, ("links", "external_references"),
                            "internal relationship target is missing, unsafe or ambiguous", location)


def _form_node(node, namespace, local, depth, location, context, document, component, controls, parents):
    """Emit scoped form properties in the existing single XML walk, without subtree joins."""
    while len(parents) >= depth:
        parents.pop()
    parent = parents[-1] if parents else None
    parents.append(node.tag)
    while controls and depth <= controls[-1]["depth"]:
        controls.pop()
    owner = controls[-1] if controls else None
    parent_namespace, parent_local = _split_tag(parent) if owner and parent else ("", "")
    if owner and owner["properties_depth"] is not None and depth <= owner["properties_depth"]:
        owner["properties_depth"] = None
    word = namespace in WORD_NAMESPACES
    word_property = namespace in WORD_FORM_NAMESPACES
    if (owner and parent_namespace in WORD_FORM_NAMESPACES
            and parent_local in WORD_FORM_PROPERTIES
            and (not word_property or local not in WORD_FORM_PROPERTIES[parent_local])):
        context.problem(document, ("forms",), "unsupported Word form property", location)
    orphan_properties = word and local == "sdtPr" and (owner is None or owner["local"] != "sdt")
    is_control = (namespace == FORM_NAMESPACE or (word and local in WORD_FORM_CONTROLS)
                  or local in ("ocx", "ocxPr", "control") or orphan_properties)
    if is_control:
        context.finding(document, "forms", "form-control", location, component=component,
                        text=node.text or "", qname=node.tag, attributes=dict(node.attrib),
                        parent_control_location=owner["location"] if owner else None)
        legacy = owner["legacy"] if owner else False
        if word and local in ("textInput", "checkBox", "ddList") and not legacy:
            context.problem(document, ("forms",), "legacy Word form control has no ffData scope", location)
        if orphan_properties:
            context.problem(document, ("forms",), "Word sdtPr has no enclosing content control", location)
        controls.append({"depth": depth, "qname": node.tag, "local": local, "location": location,
                         "properties_depth": None,
                         "legacy": word and local == "ffData" or legacy and local != "sdt"})
        return
    if owner is None:
        return
    if word and local == "sdtPr" and owner["local"] == "sdt":
        owner["properties_depth"] = depth
    content_value = (owner["local"] == "sdt" and owner["properties_depth"] is None
                     and word and local in ("t", "delText"))
    if owner["local"] == "sdt" and owner["properties_depth"] is None and not content_value:
        return
    attributes = dict(node.attrib)
    value_key = "{%s}%s" % (namespace, "value" if local == "listItem" else "val")
    value = attributes.get(value_key)
    context.finding(document, "forms", "form-value" if content_value else "form-property", location,
                    component=component, target=value, text=node.text,
                    property=local, qname=node.tag, value=value, attributes=attributes,
                    control_qname=owner["qname"], control_location=owner["location"])
    if word_property and local in ("name", "alias", "tag", "listEntry", "entryMacro", "exitMacro"):
        if value is None:
            context.problem(document, ("forms",), "Word form property has no required value: " + local, location)
    if word and parent_local == "textInput" and local == "default" and value is None:
        context.problem(document, ("forms",), "Word text default has no required value", location)
    if word_property and parent_local == "ddList" and local in ("default", "result"):
        if value is None or re.fullmatch(r"[0-9]+", value) is None:
            context.problem(document, ("forms",), "Word dropdown index is missing or malformed", location)
    if (word_property and (local == "checked" or parent_local == "checkBox" and local == "default")
            and value is not None and value not in ("0", "1", "true", "false", "on", "off")):
        context.problem(document, ("forms",), "Word checkbox state is malformed", location)
    if word and local in ("entryMacro", "exitMacro"):
        binding_location = {**location, "attribute": value_key} if value is not None else location
        context.finding(document, "actions", "form-macro-binding", binding_location,
                        component=component, target=value, qname=node.tag,
                        control_qname=owner["qname"], control_location=owner["location"])


def _xml_content(root: ET.Element, part: str, ordinal: int, document: str,
                 context: InspectionContext, component: str, relationship_index: dict,
                 *, workbook: bool) -> None:
    """Recover all XML branches, bindings and attributes without readable filtering."""
    fields = []
    form_controls, form_parents = [], []
    cell_depth, annotation_depth = None, None
    for node, node_location, depth in _xml_nodes(root):
        namespace, local = _split_tag(node.tag)
        location = {"kind": "package", "part": part, "member_ordinal": ordinal, **node_location}
        if cell_depth is not None and depth <= cell_depth:
            cell_depth = None
        if annotation_depth is not None and depth <= annotation_depth:
            annotation_depth = None
        if workbook and ((namespace in SPREADSHEET_NAMESPACES and local == "c")
                         or (namespace == TABLE_NAMESPACE and local in ("table-cell", "covered-table-cell"))):
            cell_depth = depth
        if cell_depth is not None and local in ("annotation", "comment"):
            annotation_depth = depth
        cell_text = cell_depth is not None and annotation_depth is None
        attributes = dict(node.attrib)
        if local == "fldChar":
            field_type = next((value for key, value in attributes.items()
                               if _local_name(key) == "fldCharType"), "")
            if field_type == "begin":
                fields.append(([], []))
            elif field_type in ("separate", "end") and fields:
                fragments, locations = fields[-1]
                if fragments:
                    _field_instruction("".join(fragments), document, context, locations[0],
                                       component, instruction_locations=locations)
                    fragments.clear()
                    locations.clear()
                if field_type == "end":
                    fields.pop()
        if local in ("instrText", "delInstrText"):
            text = node.text or ""
            if fields:
                fields[-1][0].append(text)
                fields[-1][1].append(location)
            else:
                _field_instruction(text, document, context, location, component)
        elif local == "fldSimple":
            instruction = next((value for key, value in attributes.items()
                                if _local_name(key) == "instr"), "")
            _field_instruction(instruction, document, context, location, component)
        is_script = (local in ("script", "module", "source")
                     and ("script" in namespace or "office" in namespace))
        if is_script and not any(_local_name(child.tag) in ("script", "module", "source")
                                 for child in node.iter() if child is not node):
            source = "".join(node.itertext())
            if source.strip():
                byte_size = len(source) if source.isascii() else sum(
                    1 if ord(char) < 128 else 2 if ord(char) < 2048 else 3
                    if ord(char) < 65536 else 4 for char in source)
                context.check("payload_bytes", byte_size, location)
                context.check("retained_bytes", context.consumed["retained_bytes"] + byte_size, location)
                encoded = source.encode("utf-8")
                artifact = context.payload(document, "script-source", location, encoded, "text/plain")
                context.component(document, "script-source", location, data=encoded,
                                  artifact=artifact, parent_component=component,
                                  representation="XML-decoded source encoded as UTF-8")
                context.finding(document, "scripts", "script-source", location,
                                component=component, text=source, artifact=artifact,
                                attributes=attributes, representation="XML-decoded source encoded as UTF-8")
        _form_node(node, namespace, local, depth, location, context, document, component,
                   form_controls, form_parents)
        if local in ("event-listener", "hlinkClick", "hlinkHover", "execute-macro"):
            context.finding(document, "actions", local, location,
                            component=component, text=node.text, attributes=attributes)
        if local in ("vanish", "webHidden", "hideMark", "hidden-text", "hidden-paragraph"):
            context.finding(document, "hidden_structures", "visibility-element", location,
                            component=component, element=local, attributes=attributes)
        if not cell_text and not is_script and local not in ("instrText", "delInstrText"):
            if node.text and node.text.strip():
                context.finding(document, "text", "xml-text", location,
                                component=component, text=node.text)
            if node.tail and node.tail.strip():
                context.finding(document, "text", "xml-tail", {**location, "text_position": "tail"},
                                component=component, text=node.tail)
        for attribute, value in attributes.items():
            attr_namespace, attr_local = _split_tag(attribute)
            attr_location = {**location, "attribute": attribute}
            lowered = attr_local.lower()
            if ("hidden" in lowered or lowered in ("display", "visibility")
                    or (lowered == "show" and local == "sld")):
                context.finding(document, "hidden_structures", "visibility-attribute", attr_location,
                                component=component, target=value, element=local)
            if attr_namespace in REL_NAMESPACES and lowered in ("id", "embed", "link"):
                matches = relationship_index.get((part, value), ())
                context.finding(document, "links", "relationship-reference", attr_location,
                                component=component, target=matches[0][0] if len(matches) == 1 else None,
                                relationship_id=value,
                                relationship_locations=[match[1] for match in matches])
                if len(matches) != 1:
                    context.problem(document, ("links",),
                                    "relationship reference is missing or ambiguous", attr_location)
            elif lowered in URL_ATTRIBUTES:
                context.finding(document, "links", "xml-url", attr_location,
                                component=component, target=value)
                if URL_SCHEME.match(value) or value.startswith("//"):
                    context.finding(document, "external_references", "xml-url", attr_location,
                                    component=component, target=value)
            if (lowered in ("macro", "onaction", "script", "action")
                    or attr_namespace == SCRIPT_NAMESPACE or lowered.startswith("on")):
                context.finding(document, "actions", "event-binding", attr_location,
                                component=component, target=value, attributes=attributes)
            if "dde-" in lowered:
                context.finding(document, "external_references", "dde-reference", attr_location,
                                component=component, target=value)
            if not cell_text and lowered in TEXT_ATTRIBUTES:
                context.finding(document, "text", "xml-text-attribute", attr_location,
                                component=component, text=value)
    for fragments, locations in fields:
        if fragments:
            _field_instruction("".join(fragments), document, context, locations[0], component,
                               instruction_locations=locations, unterminated=True)
            context.problem(document, ("actions",), "unterminated complex field", locations[0])


def _scope_path(prefix: str, target: str) -> str | None:
    """Resolve a normalized ODF member name inside its existing package scope."""
    target = unquote(target)
    if target == "/":
        return prefix
    bare = target.rstrip("/")
    if (not bare or bare.startswith("/") or "\\" in bare or URL_SCHEME.match(bare)
            or "?" in bare or "#" in bare or posixpath.normpath(bare) != bare
            or bare == ".." or bare.startswith("../")
            or any(ord(char) < 32 for char in bare)):
        return None
    return prefix + bare + ("/" if target.endswith("/") else "")


def _encrypted_parts(roots: dict, prefix: str = "") -> set[str]:
    """Identify ODF encryption and EPUB encrypted resources before reading their text."""
    encrypted = set()
    for (ordinal, part), root in roots.items():
        if part == prefix + "META-INF/manifest.xml":
            for node in root.iter():
                if (_local_name(node.tag) == "file-entry"
                        and any(_local_name(child.tag) == "encryption-data" for child in node)):
                    target = next((value for key, value in node.attrib.items()
                                   if _local_name(key) == "full-path"), None)
                    if target is not None:
                        resolved = _scope_path(prefix, target)
                        if resolved is not None:
                            encrypted.add(resolved)
        elif part == prefix + "META-INF/encryption.xml":
            for node in root.iter():
                if _local_name(node.tag) == "CipherReference" and "URI" in node.attrib:
                    encrypted.add(unquote(node.attrib["URI"].split("#", 1)[0].split("?", 1)[0]).lstrip("/"))
    return encrypted


def _resource_types(roots: dict, members, prefix: str = "") -> dict[str, list[str]]:
    """Read declared OPC, ODF and EPUB media types without collapsing occurrences."""
    overrides, defaults = {}, {}
    for (_ordinal, part), root in roots.items():
        if part not in ("[Content_Types].xml", prefix + "META-INF/manifest.xml") and PurePosixPath(part).suffix.lower() != ".opf":
            continue
        for node in root.iter():
            local = _local_name(node.tag)
            if part == "[Content_Types].xml" and local == "Default":
                defaults.setdefault(node.get("Extension", "").lower(), []).append(node.get("ContentType", ""))
            elif part == "[Content_Types].xml" and local == "Override":
                target = unquote(node.get("PartName", "").lstrip("/"))
                overrides.setdefault(target, []).append(node.get("ContentType", ""))
            elif part == prefix + "META-INF/manifest.xml" and local == "file-entry":
                attrs = {_local_name(key): value for key, value in node.attrib.items()}
                target = _scope_path(prefix, attrs.get("full-path", ""))
                if target is not None:
                    overrides.setdefault(target, []).append(attrs.get("media-type", ""))
            elif PurePosixPath(part).suffix.lower() == ".opf" and local == "item":
                target = _resolve_part(part, node.get("href", ""))
                if target is not None:
                    overrides.setdefault(target, []).append(node.get("media-type", ""))
    return {part: [media for media in overrides.get(
                part, defaults.get(PurePosixPath(part).suffix[1:].lower(), [])) if media]
            for _ordinal, part, _data in members}


def _empty_odf_resources(roots: dict, prefix: str = "") -> set[str]:
    """Honor explicit empty ODF media declarations for zero-byte data resources."""
    result = set()
    for (_ordinal, part), root in roots.items():
        if part != prefix + "META-INF/manifest.xml":
            continue
        for node in root.iter():
            attributes = {_local_name(key): value for key, value in node.attrib.items()}
            if _local_name(node.tag) == "file-entry" and attributes.get("media-type") == "":
                target = _scope_path(prefix, attributes.get("full-path", ""))
                if target is not None:
                    result.add(target)
    return result


def _directory_declarations(roots, source_members, prefix, context, document):
    """Preserve exact manifest declaration occurrences, including inherited scopes."""
    declarations = []
    for descriptor in source_members:
        declarations.extend(descriptor.get("directory_declarations", ()))
    for (ordinal, part), root in roots.items():
        if part != prefix + "META-INF/manifest.xml":
            continue
        for node, node_location, _depth in _xml_nodes(root):
            if node.tag != MANIFEST + "file-entry":
                continue
            target = node.get(MANIFEST + "full-path", "")
            media_type = node.get(MANIFEST + "media-type", "")
            if target == "/" or not target.endswith("/") or not media_type:
                continue
            if not media_type.startswith("application/vnd.oasis.opendocument."):
                continue
            location = {"kind": "package", "part": part, "member_ordinal": ordinal, **node_location}
            resolved = _scope_path(prefix, target)
            if resolved is None:
                context.finding(document, "attachments", "unsafe-odf-directory", location,
                                target=target, media_type=media_type)
                context.problem(document, ("attachments", "inventory"),
                                "ODF embedded directory leaves its normalized source scope", location)
                continue
            declarations.append({"prefix": resolved, "media_type": media_type,
                                 "location": location, "original_target": target,
                                 "encrypted": any(child.tag == MANIFEST + "encryption-data" for child in node)})
    unique = {}
    for declaration in declarations:
        where = declaration["location"]
        key = (where["member_ordinal"], where.get("element_ordinal"),
               declaration["prefix"], declaration["media_type"])
        unique.setdefault(key, declaration)
    return tuple(item for _key, item in sorted(unique.items())
                 if item["prefix"].startswith(prefix) and item["prefix"] != prefix)


def _directory_groups(declarations):
    groups = {}
    for declaration in declarations:
        groups.setdefault(declaration["prefix"], []).append(declaration)
    supported = [path for path, entries in groups.items()
                 if any(entry["media_type"] in ODF_MEDIA for entry in entries)]
    return {path: entries for path, entries in groups.items()
            if not any(path != other and path.startswith(other) for other in supported)}


def _source_metadata(source_index, roots, resource_types, encrypted, empty_resources, declarations, prefix):
    """Attach original manifest evidence to descriptors passed to logical children."""
    manifest_locations = {}
    for (ordinal, manifest_part), root in roots.items():
        if manifest_part != prefix + "META-INF/manifest.xml":
            continue
        for node, node_location, _depth in _xml_nodes(root):
            if node.tag == MANIFEST + "file-entry":
                target = _scope_path(prefix, node.get(MANIFEST + "full-path", ""))
                manifest_locations.setdefault(target, []).append({
                    "kind": "package", "part": manifest_part,
                    "member_ordinal": ordinal, **node_location})
    for descriptor in source_index.values():
        part = descriptor["original_part"]
        descriptor["media_types"] = resource_types.get(part, descriptor.get("media_types", []))
        if part in empty_resources:
            descriptor["empty_media_type"] = True
        if part in encrypted or any(entry["encrypted"] and part.startswith(entry["prefix"])
                                     for entry in declarations):
            descriptor["encrypted"] = True
        ancestors = [entry for entry in declarations if part.startswith(entry["prefix"])]
        if ancestors:
            descriptor["directory_declarations"] = ancestors
        locations = list(descriptor.get("manifest_locations", ()))
        locations.extend(manifest_locations.get(part, ()))
        if locations:
            descriptor["manifest_locations"] = locations


def _inspect_directories(groups, members, source_index, context, document, scan_embedded_members):
    """Admit nearest supported ODF children while retaining unsupported folder evidence."""
    for prefix, declarations in groups.items():
        media_types = list(dict.fromkeys(item["media_type"] for item in declarations))
        location = declarations[0]["location"]
        subset = tuple((ordinal, part, None if source_index[ordinal, part].get("encrypted") else payload)
                       for ordinal, part, payload in members if part.startswith(prefix))
        descriptors = tuple(source_index[ordinal, part] for ordinal, part, _payload in subset)
        supported = len(media_types) == 1 and media_types[0] in ODF_MEDIA
        reason = (None if supported else "ambiguous ODF directory media types" if len(media_types) > 1
                  else "ODF directory media type is outside supported formats")
        component = context.component(document, "odf-directory", location,
                                      disposition="extracted" if supported and scan_embedded_members else "opaque",
                                      reason=reason, prefix=prefix, media_types=media_types,
                                      declarations=declarations, source_members=descriptors)
        context.finding(document, "attachments", "odf-directory", location,
                        component=component, target=prefix, media_types=media_types,
                        declarations=declarations, reason=reason)
        if not supported:
            if len(media_types) > 1:
                context.problem(document, PACKAGE_CAPABILITIES, reason, location)
            continue
        if scan_embedded_members is None:
            context.problem(document, ("attachments", "text"),
                            "supported ODF directory inspection callback is unavailable", location)
            continue
        child = scan_embedded_members(prefix, media_types[0], subset, parent=document,
                                      location=location, component=component,
                                      source_members=descriptors, prefix=prefix)
        if len(declarations) > 1:
            reason = "ODF directory has duplicate source declarations"
            context.problem(document, ("attachments",), reason, location)
            context.problem(child, PACKAGE_CAPABILITIES, reason, location)


def _resource_kind(part: str, media_types: list[str]) -> str:
    if any(media.startswith("image/") for media in media_types):
        return "image"
    if any(media in ("text/javascript", "application/javascript", "text/x-python") for media in media_types):
        return "script"
    if any("vbaproject" in media.lower() for media in media_types):
        return "macro-project"
    return _member_kind(part)


def _is_html(part: str, media_types: list[str]) -> bool:
    return (PurePosixPath(part).suffix.lower() in HTML_SUFFIXES
            or any(media in ("text/html", "application/xhtml+xml", "image/svg+xml") for media in media_types))


def _embedded_name(name: str, data: bytes, context: InspectionContext,
                   location: dict) -> str | None:
    """Recognize existing document formats inside generic package object names."""
    from brewdoc._ole import embedded_document_name

    return embedded_document_name(name, data, context, location)


def _compound_payload(data: bytes, part: str, context: InspectionContext, document: str,
                       location: dict, component: str, scan_embedded) -> None:
    """Recover VBA and supported native document streams from an OLE container."""
    from brewdoc._compound import read_compound
    from brewdoc._ole import inspect_compound_payloads
    from brewdoc._vba import inspect_vba

    entries = read_compound(data, context, document, location=location)
    workbook = any(entry["kind"] == "stream" and entry["name"] in ("Workbook", "Book")
                   for entry in entries)
    if workbook:
        scan_embedded(data, part + ".xls", parent=document, location=location, component=component)
        return
    inspect_vba(data, part, context, document, location=location, streams=entries)
    macro_coverage = context.coverage.get((document, "macros"))
    if macro_coverage and macro_coverage["status"] in ("unavailable", "partial"):
        context.problem(document, ("scripts",), "VBA source inspection is incomplete", location)
    inspect_compound_payloads(entries, part, context, document,
                              scan_embedded=scan_embedded, location=location)


def _zip64_values(extra, fields, context, location):
    """Resolve only sentinel ZIP64 fields after validating every bounded extra record."""
    position, extended = 0, None
    while position < len(extra):
        context.charge("records", 1, location)
        if len(extra) - position < 4:
            raise ValueError("truncated ZIP extra field header")
        kind, size = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + size > len(extra):
            raise ValueError("ZIP extra field exceeds its declared header boundary")
        if kind == 1:
            if extended is not None:
                raise ValueError("ambiguous duplicate ZIP64 extra field")
            extended = memoryview(extra)[position:position + size]
        position += size
    result, position = {}, 0
    for key, value, width, sentinel in fields:
        if value == sentinel:
            if extended is None or position + width > len(extended):
                raise ValueError("required ZIP64 metadata is missing or truncated")
            value = int.from_bytes(extended[position:position + width], "little")
            position += width
        result[key] = value
    return result


def _encoded_package_member(data, info, central_offset, boundary, context, document,
                            component, location, reason):
    """Preserve validated encoded member bytes without decrypting or decoding them."""
    if central_offset < 0 or central_offset + 46 > len(data):
        raise ValueError("central ZIP header is outside the admitted source")
    central = struct.unpack_from("<4s6H3I5H2I", data, central_offset)
    if central[0] != b"PK\x01\x02":
        raise ValueError("central ZIP header signature is invalid")
    central_name = central_offset + 46
    central_extra = central_name + central[10]
    central_end = central_extra + central[11] + central[12]
    if central_end > len(data):
        raise ValueError("central ZIP variable fields exceed the admitted source")
    if (data[central_extra:central_extra + central[11]] != info.extra
            or data[central_extra + central[11]:central_end] != info.comment):
        raise ValueError("central ZIP extra or comment bytes conflict with the admitted metadata")
    sizes = _zip64_values(data[central_extra:central_extra + central[11]], (
        ("plaintext_size", central[9], 8, 0xFFFFFFFF),
        ("encoded_size", central[8], 8, 0xFFFFFFFF),
        ("header_offset", central[16], 8, 0xFFFFFFFF),
        ("disk", central[13], 4, 0xFFFF)), context, location)
    if ((central[3], central[4], central[7], sizes["plaintext_size"], sizes["encoded_size"])
            != (info.flag_bits, info.compress_type, info.CRC, info.file_size, info.compress_size)
            or sizes["disk"] != 0):
        raise ValueError("central ZIP metadata does not match the admitted member")
    header_offset = info.header_offset
    if header_offset < 0 or header_offset + 30 > min(boundary, len(data)):
        raise ValueError("local ZIP header is outside its source member boundary")
    local = struct.unpack_from("<4s5H3I2H", data, header_offset)
    if local[0] != b"PK\x03\x04":
        raise ValueError("local ZIP header signature is invalid")
    name_offset = header_offset + 30
    extra_offset = name_offset + local[9]
    encoded_offset = extra_offset + local[10]
    encoded_end = encoded_offset + info.compress_size
    if encoded_offset > min(boundary, len(data)) or encoded_end > min(boundary, len(data)):
        raise ValueError("encoded ZIP data boundary overlaps another member or leaves the admitted source")
    filename = data[name_offset:extra_offset]
    central_filename = data[central_name:central_extra]
    encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
    if (filename != central_filename or filename.decode(encoding) != info.orig_filename
            or local[2:4] != central[3:5]):
        raise ValueError("local and central ZIP names, flags or compression differ")
    local_sizes = _zip64_values(data[extra_offset:encoded_offset], (
        ("plaintext_size", local[8], 8, 0xFFFFFFFF),
        ("encoded_size", local[7], 8, 0xFFFFFFFF)), context, location)
    descriptor = bool(info.flag_bits & 8)
    expected = ((0, info.CRC), (0, info.compress_size), (0, info.file_size)) if descriptor else (
        (info.CRC,), (info.compress_size,), (info.file_size,))
    if (local[6] not in expected[0] or local_sizes["encoded_size"] not in expected[1]
            or local_sizes["plaintext_size"] not in expected[2]):
        raise ValueError("local ZIP size or CRC metadata conflicts with the central declaration")
    where = {**location, "representation": "encoded", "byte_offset": encoded_offset,
             "byte_length": info.compress_size, "local_header_offset": header_offset}
    context.check("payload_bytes", info.compress_size, where)
    context.check("retained_bytes", context.consumed["retained_bytes"] + info.compress_size, where)
    payload = data[encoded_offset:encoded_end]
    artifact = context.payload(document, "encoded-package-member", where, payload, "application/octet-stream")
    metadata = {"representation": "encoded ZIP member data; not decrypted or decoded",
                "declared_plaintext_size": info.file_size, "encoded_size": info.compress_size,
                "compression": info.compress_type, "flags": info.flag_bits,
                "local_header": {"offset": header_offset, "byte_size": encoded_offset - header_offset,
                                 "version_needed": local[1], "flags": local[2], "compression": local[3],
                                 "filename_offset": name_offset, "filename_bytes": local[9],
                                 "extra_offset": extra_offset, "extra_bytes": local[10],
                                 "crc32": "%08x" % local[6], **local_sizes},
                "central_directory": {"offset": central_offset, "byte_size": central_end - central_offset,
                                      "version_made_by": central[1], "version_needed": central[2],
                                      "flags": central[3], "compression": central[4],
                                      "member_ordinal": location["member_ordinal"], "original_filename": info.orig_filename,
                                      "filename_encoding": encoding, "crc32": "%08x" % info.CRC,
                                      "source_offset_adjustment": header_offset - sizes["header_offset"], **sizes}}
    encoded_component = context.component(document, "encoded-package-member", where, data=payload,
                                         artifact=artifact, disposition="opaque", reason=reason,
                                         parent_component=component, **metadata)
    context.finding(document, "attachments", "encoded-package-member", where,
                    component=encoded_component, artifact=artifact, target=info.filename, **metadata)


def inspect_package(data: bytes, name: str, context: InspectionContext, document: str,
                    *, scan_html, scan_embedded, scan_embedded_members=None
                    ) -> tuple[tuple[int, str, bytes | None], ...]:
    """Inspect each ZIP occurrence once and return admitted bytes for workbook overlays."""
    members, records = [], {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        context.problem(document, PACKAGE_CAPABILITIES, "package unreadable: %s" % exc,
                        {"kind": "package"})
        return ()
    stopped = None
    with archive:
        infos = archive.infolist()
        offsets = sorted({item.header_offset for item in infos} | {archive.start_dir})
        boundaries = dict(zip(offsets, offsets[1:]))
        central_offset = archive.start_dir
        for ordinal, info in enumerate(infos, 1):
            member_central = central_offset
            central_offset = (central_offset + 46 + sum(struct.unpack_from("<3H", data, central_offset + 28))
                              if 0 <= central_offset <= len(data) - 46 else -1)
            part = info.filename
            location = {"kind": "package", "part": part, "member_ordinal": ordinal}
            kind = _member_kind(part)
            try:
                if stopped is not None:
                    raise BrewdocError(stopped)
                context.check("payload_bytes", info.file_size, location)
                context.check("decoded_bytes", context.consumed["decoded_bytes"] + info.file_size, location)
                payload = _zip_member_bytes(archive, info, context.zip_budget)
                context.charge("decoded_bytes", len(payload), location)
                artifact = context.payload(document, kind, location, payload,
                                           MEDIA_TYPES.get(PurePosixPath(part).suffix.lower(),
                                                           "application/octet-stream"))
                disposition = "extracted" if kind == "directory" else "opaque"
                component = context.component(document, kind, location, data=payload,
                                              artifact=artifact, disposition=disposition,
                                              reason=None if kind == "directory" else
                                              "original member bytes; semantic findings are recorded separately",
                                              declared_byte_size=info.file_size,
                                              compression=info.compress_type,
                                              crc32="%08x" % info.CRC)
                records[ordinal] = (component, artifact)
            except (BrewdocError, zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError, zlib.error) as exc:
                payload = None
                reason = "package member unreadable: %s" % exc
                if isinstance(exc, InspectionLimit):
                    stopped = str(exc)
                component = context.component(document, kind, location, disposition="unreadable", reason=reason,
                                              declared_byte_size=info.file_size, compression=info.compress_type)
                records[ordinal] = (component, None)
                context.problem(document, PACKAGE_CAPABILITIES, reason, location)
                can_retain = (info.compress_size <= context.limits["payload_bytes"]
                              and info.compress_size <= context.remaining("retained_bytes"))
                if stopped is None or can_retain:
                    try:
                        boundary = min(archive.start_dir, boundaries.get(info.header_offset, archive.start_dir))
                        _encoded_package_member(data, info, member_central, boundary, context, document,
                                                component, location, reason)
                    except (BrewdocError, ValueError, UnicodeError, struct.error) as encoded_error:
                        context.problem(document, ("attachments", "inventory"),
                                        "encoded ZIP payload unavailable: %s" % encoded_error, location)
                        if isinstance(encoded_error, InspectionLimit):
                            stopped = str(encoded_error)
            members.append((ordinal, part, payload))
    components = {item["id"]: item for item in context.components}
    descriptors = tuple({"original_part": part, "member_ordinal": ordinal,
                         "component": records[ordinal][0], "artifact": records[ordinal][1],
                         "byte_size": components[records[ordinal][0]]["byte_size"],
                         "sha256": components[records[ordinal][0]]["sha256"],
                         "reason": components[records[ordinal][0]]["reason"]}
                        for ordinal, part, _payload in members)
    return inspect_package_members(tuple(members), name, context, document, scan_html=scan_html,
                                   scan_embedded=scan_embedded, scan_embedded_members=scan_embedded_members,
                                   source_members=descriptors)


def inspect_package_members(members, name: str, context: InspectionContext, document: str,
                            *, scan_html, scan_embedded, scan_embedded_members,
                            prefix: str = "", source_members=(), media_type=None
                            ) -> tuple[tuple[int, str, bytes | None], ...]:
    """Inspect admitted original member scopes without new ZIP reads or payload copies."""
    members = tuple(item for item in members if item[1].startswith(prefix))
    source_index = {(item["member_ordinal"], item["original_part"]): dict(item)
                    for item in source_members}
    records = {ordinal: (source_index[ordinal, part]["component"], source_index[ordinal, part]["artifact"])
               for ordinal, part, _payload in members}
    roots, member_names = {}, {}
    for ordinal, part, _payload in members:
        member_names.setdefault(part, []).append(ordinal)
    workbook = (ODF_MEDIA.get(media_type) == ".ods" if media_type is not None else
                PurePosixPath(name).suffix.lower() in WORKBOOK_SUFFIXES)
    encryption_metadata = frozenset((prefix + "META-INF/manifest.xml", prefix + "META-INF/encryption.xml"))
    encrypted = {item["original_part"] for item in source_index.values() if item.get("encrypted")}
    resource_types = {item["original_part"]: item.get("media_types", []) for item in source_index.values()}
    empty_resources = {item["original_part"] for item in source_index.values() if item.get("empty_media_type")}
    declarations = _directory_declarations({}, source_members, prefix, context, document)
    groups = _directory_groups(declarations)
    child_prefixes = tuple(path for path, entries in groups.items()
                           if any(entry["media_type"] in ODF_MEDIA for entry in entries))
    if prefix and media_type in ODF_MEDIA:
        content = [item for item in members if item[1] == prefix + "content.xml"]
        if len(content) != 1 or content[0][2] is None or source_index[content[0][0], content[0][1]].get("encrypted"):
            context.problem(document, PACKAGE_CAPABILITIES,
                            "ODF directory content.xml is missing, duplicate, encrypted or unreadable",
                            {"kind": "package", "part": prefix + "content.xml", "prefix": prefix})
    for stage in range(3):
        for ordinal, part, payload in members:
            suffix = PurePosixPath(part).suffix.lower()
            media_types = resource_types.get(part, [])
            member_stage = (0 if part in encryption_metadata else 1
                            if part == "[Content_Types].xml" or suffix == ".opf" else 2)
            is_xml = suffix in XML_SUFFIXES or any(
                media in ("application/xml", "text/xml") or media.endswith("+xml") for media in media_types)
            if (payload is None or not is_xml or part in encrypted or _is_html(part, media_types)
                    or (payload == b"" and ((part in empty_resources and not media_types) or part.endswith("/")))
                    or any(part.startswith(child_prefix) for child_prefix in child_prefixes)
                    or member_stage != stage):
                continue
            location = {"kind": "package", "part": part, "member_ordinal": ordinal}
            parent_component, artifact = records[ordinal]
            try:
                roots[ordinal, part] = _parse_xml(payload, part, context)
                context.component(document, "xml-tree", location, disposition="extracted",
                                  artifact=artifact, parent_component=parent_component)
            except InspectionLimit:
                raise
            except (BrewdocError, ET.ParseError, ValueError) as exc:
                reason = "XML unreadable: %s" % exc
                context.component(document, "xml-tree", location, disposition="unreadable",
                                  reason=reason, artifact=artifact, parent_component=parent_component)
                context.problem(document, ("text", "links", "scripts", "actions", "forms",
                                           "external_references", "macros", "hidden_structures"), reason, location)
        encrypted.update(_encrypted_parts(roots, prefix))
        for part, types in _resource_types(roots, members, prefix).items():
            if types:
                resource_types[part] = types
        empty_resources.update(_empty_odf_resources(roots, prefix))
        if stage == 0:
            declarations = _directory_declarations(roots, source_members, prefix, context, document)
            groups = _directory_groups(declarations)
            child_prefixes = tuple(path for path, entries in groups.items()
                                   if any(entry["media_type"] in ODF_MEDIA for entry in entries))
    _source_metadata(source_index, roots, resource_types, encrypted, empty_resources, declarations, prefix)
    relationship_index = {}
    for ordinal, part, _payload in members:
        if (ordinal, part) in roots and PurePosixPath(part).suffix.lower() == ".rels":
            _relationships(roots[ordinal, part], part, ordinal, document, context, records[ordinal][0],
                           member_names, relationship_index)
    for ordinal, part, payload in members:
        if payload is None or any(part.startswith(child_prefix) for child_prefix in child_prefixes):
            continue
        location = {"kind": "package", "part": part, "member_ordinal": ordinal}
        component, artifact = records[ordinal]
        suffix = PurePosixPath(part).suffix.lower()
        media_types = resource_types.get(part, [])
        kind = _resource_kind(part, media_types)
        if media_types:
            context.finding(document, "inventory", "package-resource-types", location,
                            component=component, artifact=artifact, target=part, media_types=media_types)
        if payload == b"" and part in empty_resources and not media_types:
            context.finding(document, "inventory", "empty-package-resource", location,
                            component=component, artifact=artifact, target=part,
                            reason="zero-byte resource with an explicitly empty ODF media type")
            continue
        if part in encrypted:
            context.finding(document, "attachments", "encrypted-resource", location,
                            component=component, artifact=artifact, target=part)
            context.problem(document, ("text", "links", "scripts", "actions", "forms", "hidden_structures"),
                            "encrypted package resource is retained without decryption", location)
            continue
        if kind == "image":
            context.finding(document, "images", "package-image", location,
                            component=component, artifact=artifact, target=part)
        if kind in ("embedded-object", "embedded-document", "macro-project"):
            capability = "macros" if kind == "macro-project" else "attachments"
            context.finding(document, capability, kind, location,
                            component=component, artifact=artifact, target=part)
        elif kind == "package-member":
            context.finding(document, "attachments", "opaque-package-resource", location,
                            component=component, artifact=artifact, target=part,
                            reason="no semantic reader for this resource type")
        if _is_html(part, media_types):
            scan_html(payload, part, context, document, location=location)
        elif (ordinal, part) in roots and suffix != ".rels":
            _xml_content(roots[ordinal, part], part, ordinal, document, context, component,
                         relationship_index, workbook=workbook)
        elif kind == "script":
            try:
                source = payload.decode("utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")
                context.component(document, "script-source", location, disposition="extracted",
                                  artifact=artifact, parent_component=component)
                context.finding(document, "scripts", "script-source", location,
                                component=component, artifact=artifact, text=source)
            except UnicodeError as exc:
                context.component(document, "script-source", location, disposition="unreadable",
                                  reason=str(exc), artifact=artifact, parent_component=component)
                context.problem(document, ("scripts",), "script source undecodable: %s" % exc, location)
        elif suffix in DOCUMENT_SUFFIXES:
            scan_embedded(payload, part, parent=document, location=location, component=component)
        elif payload.startswith(CFB_SIGNATURE):
            _compound_payload(payload, part, context, document, location, component, scan_embedded)
        else:
            child_name = _embedded_name(part, payload, context, location)
            if child_name is not None:
                scan_embedded(payload, child_name, parent=document, location=location, component=component)
    for capability in PACKAGE_CAPABILITIES:
        if workbook and capability == "text":
            continue
        context.cover(document, capability, "complete", "all package member occurrences scanned")
    _inspect_directories(groups, members, source_index, context, document, scan_embedded_members)
    return tuple((ordinal, part, None if source_index[ordinal, part].get("encrypted") else payload)
                 for ordinal, part, payload in members
                 if not any(part.startswith(child_prefix) for child_prefix in child_prefixes))
