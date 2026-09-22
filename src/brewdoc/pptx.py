"""PPTX adapter: declared slides, DrawingML text, tables, notes and image facts."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from brewdoc.common import (BrewdocError, Rendered, Route, _assemble, _local_name,
                            _relationship_part, _resolve_ooxml_part, _xml_part, new_tally,
                            reading, sanitise)

TRANSITIONAL_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STRICT_REL = "http://purl.oclc.org/ooxml/officeDocument/relationships"
OFFICE_DOCUMENT_TYPES = {TRANSITIONAL_REL + "/officeDocument",
                         STRICT_REL + "/officeDocument"}
SLIDE_TYPES = {TRANSITIONAL_REL + "/slide", STRICT_REL + "/slide"}
NOTES_TYPES = {TRANSITIONAL_REL + "/notesSlide", STRICT_REL + "/notesSlide"}
IMAGE_TYPES = {TRANSITIONAL_REL + "/image", STRICT_REL + "/image"}
RELATIONSHIP_NAMESPACES = {TRANSITIONAL_REL, STRICT_REL}

TRUE_VALUES = {"1", "on", "true"}
TITLE_PLACEHOLDERS = {"title", "ctrTitle"}
SHAPE_NAMES = {"sp", "grpSp", "graphicFrame", "pic"}


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in node if _local_name(child.tag) == name), None)


def _required_xml(package: zipfile.ZipFile, part: str) -> ET.Element:
    """Read one required XML part and reject ambiguous duplicate archive members."""
    matches = [item for item in package.infolist() if item.filename == part]
    if not matches:
        raise BrewdocError("OOXML part is missing: %s" % part)
    if len(matches) > 1:
        raise BrewdocError("OOXML part occurs more than once: %s" % part)
    return _xml_part(package, part)


def _required_bytes(package: zipfile.ZipFile, part: str) -> bytes:
    """Read one required binary part and reject ambiguous duplicate archive members."""
    matches = [item for item in package.infolist() if item.filename == part]
    if not matches:
        raise BrewdocError("OOXML part is missing: %s" % part)
    if len(matches) > 1:
        raise BrewdocError("OOXML part occurs more than once: %s" % part)
    return package.read(matches[0])


def _required_relationships(package: zipfile.ZipFile, part: str) -> tuple[ET.Element, ...]:
    """Read a required relationship part without collapsing duplicate relationship ids."""
    root = _required_xml(package, part)
    if _local_name(root.tag) != "Relationships":
        raise BrewdocError("OOXML relationship part has unexpected root: %s" % part)
    return tuple(element for element in root if _local_name(element.tag) == "Relationship")


def _optional_relationships(package: zipfile.ZipFile, part: str) -> tuple[ET.Element, ...]:
    """Read a relationship part when present; malformed or duplicate parts still refuse."""
    if not any(item.filename == part for item in package.infolist()):
        return ()
    return _required_relationships(package, part)


def _relationship_attribute(element: ET.Element, name: str) -> str | None:
    """Read an office relationship attribute without confusing unqualified ids."""
    for key, value in element.attrib.items():
        if not key.startswith("{") or _local_name(key) != name:
            continue
        namespace = key[1:].split("}", 1)[0]
        if namespace in RELATIONSHIP_NAMESPACES:
            return value
    return None


def _presentation_part(package: zipfile.ZipFile) -> str:
    """Resolve the single internal presentation through the package office relationship."""
    relationships = _required_relationships(package, "_rels/.rels")
    candidates = [item for item in relationships
                  if item.attrib.get("Type") in OFFICE_DOCUMENT_TYPES]
    if not candidates:
        raise BrewdocError("OOXML office document relationship is missing")
    if len(candidates) > 1:
        raise BrewdocError("multiple OOXML office document relationships")
    relationship = candidates[0]
    relation_id = relationship.attrib.get("Id")
    if not relation_id:
        raise BrewdocError("OOXML office document relationship id is missing")
    if sum(item.attrib.get("Id") == relation_id for item in relationships) > 1:
        raise BrewdocError("OOXML office document relationship %s occurs more than once"
                           % relation_id)
    if relationship.attrib.get("TargetMode") == "External":
        raise BrewdocError("OOXML office document relationship is external")
    return _resolve_ooxml_part("", relationship.attrib.get("Target", ""))


def _related_part(relationships: tuple[ET.Element, ...], relation_id: str,
                  allowed_types: set[str], source_part: str, noun: str,
                  *, external: bool = False) -> tuple[str, ET.Element]:
    """Resolve one exact typed relationship after rejecting ambiguity and unsafe targets."""
    matches = [item for item in relationships if item.attrib.get("Id") == relation_id]
    if not matches:
        raise BrewdocError("%s %s is missing in %s" % (noun, relation_id, source_part))
    if len(matches) > 1:
        raise BrewdocError("%s %s occurs more than once in %s"
                           % (noun, relation_id, source_part))
    relationship = matches[0]
    if relationship.attrib.get("Type") not in allowed_types:
        raise BrewdocError("%s %s has the wrong type in %s"
                           % (noun, relation_id, source_part))
    if relationship.attrib.get("TargetMode") == "External" and not external:
        raise BrewdocError("%s %s is external in %s" % (noun, relation_id, source_part))
    target = relationship.attrib.get("Target", "")
    if not target:
        raise BrewdocError("OOXML relationship target is missing")
    if relationship.attrib.get("TargetMode") == "External":
        return target, relationship
    return _resolve_ooxml_part(source_part, target), relationship


def _shape_tree(root: ET.Element, noun: str, part: str) -> ET.Element:
    """Return a required common-slide shape tree from its named part."""
    common_slide = _child(root, "cSld")
    tree = _child(common_slide, "spTree") if common_slide is not None else None
    if tree is None:
        raise BrewdocError("%s shape tree is missing in %s" % (noun, part))
    return tree


def _shape_nodes(tree: ET.Element):
    """Yield renderable shape-tree children in XML order, descending into groups in place."""
    for child in tree:
        name = _local_name(child.tag)
        if name == "grpSp":
            yield from _shape_nodes(child)
        elif name == "AlternateContent":
            fallback = _child(child, "Fallback")
            if fallback is not None:
                yield from _shape_nodes(fallback)
        elif name in SHAPE_NAMES:
            yield child


def _text_body(node: ET.Element) -> ET.Element | None:
    """Return a node's direct text body without inheriting layout or nested content."""
    return next((child for child in node if _local_name(child.tag) == "txBody"), None)


def _paragraph_lines(paragraph: ET.Element, tally: dict) -> list[str]:
    """Preserve run order while turning DrawingML tabs and breaks into plain lines."""
    lines, current = [], []
    for element in paragraph.iter():
        name = _local_name(element.tag)
        if name == "t":
            current.append(element.text or "")
        elif name == "tab":
            current.append(" ")
        elif name == "br":
            lines.append("".join(current))
            current = []
    lines.append("".join(current))
    return [text for text in (sanitise(line, tally).strip() for line in lines) if text]


def _shape_lines(shape: ET.Element, tally: dict) -> list[str]:
    """Read direct text-body paragraphs through the shared ASCII path."""
    body = _text_body(shape)
    if body is None:
        return []
    lines = []
    for paragraph in body:
        if _local_name(paragraph.tag) == "p":
            lines.extend(_paragraph_lines(paragraph, tally))
    return lines


def _placeholder_type(shape: ET.Element) -> str | None:
    """Return a shape's declared placeholder type without layout inheritance."""
    non_visual = _child(shape, "nvSpPr")
    properties = _child(non_visual, "nvPr") if non_visual is not None else None
    placeholder = _child(properties, "ph") if properties is not None else None
    return placeholder.attrib.get("type") if placeholder is not None else None


def _title_shape(tree: ET.Element, tally: dict) -> tuple[ET.Element | None, str]:
    """Select the first non-empty title placeholder and its sanitized label."""
    for shape in _shape_nodes(tree):
        if _local_name(shape.tag) != "sp" or _placeholder_type(shape) not in TITLE_PLACEHOLDERS:
            continue
        lines = _shape_lines(shape, tally)
        if lines:
            return shape, " ".join(lines)
    return None, "Untitled"


def _table_rows(table: ET.Element, tally: dict) -> list[list[str]]:
    """Keep DrawingML row and cell order while blanking merge continuations."""
    rows = []
    for row in table:
        if _local_name(row.tag) != "tr":
            continue
        cells = []
        for cell in row:
            if _local_name(cell.tag) != "tc":
                continue
            if (cell.attrib.get("hMerge", "").lower() in TRUE_VALUES
                    or cell.attrib.get("vMerge", "").lower() in TRUE_VALUES):
                cells.append("")
                continue
            lines = _shape_lines(cell, tally)
            cells.append(" ".join(lines))
        rows.append(cells)
    return rows


def _clean_attribute(value: str | None, tally: dict, default: str) -> str:
    """Normalize emitted XML metadata to one ASCII line."""
    text = default if value is None or value == "" else value
    return sanitise(text, tally).replace("\n", " ").strip()


def _picture_line(picture: ET.Element, package: zipfile.ZipFile,
                  slide_part: str, relationships: tuple[ET.Element, ...], tally: dict) -> str:
    """Describe one picture from OOXML metadata without decoding or fetching pixels."""
    properties = next((item for item in picture.iter() if _local_name(item.tag) == "cNvPr"), None)
    blip = next((item for item in picture.iter() if _local_name(item.tag) == "blip"), None)
    relation_id = None
    if blip is not None:
        relation_id = (_relationship_attribute(blip, "embed")
                       or _relationship_attribute(blip, "link"))
    if not relation_id:
        raise BrewdocError("picture relationship is missing in %s" % slide_part)
    target, relationship = _related_part(relationships, relation_id, IMAGE_TYPES, slide_part,
                                         "picture relationship", external=True)
    if relationship.attrib.get("TargetMode") == "External":
        source, byte_count = "external", "unknown"
    else:
        source, byte_count = "embedded", str(len(_required_bytes(package, target)))

    shape_properties = _child(picture, "spPr")
    transform = _child(shape_properties, "xfrm") if shape_properties is not None else None
    extent = _child(transform, "ext") if transform is not None else None
    cx = _clean_attribute(extent.attrib.get("cx") if extent is not None else None,
                          tally, "unknown")
    cy = _clean_attribute(extent.attrib.get("cy") if extent is not None else None,
                          tally, "unknown")
    name = _clean_attribute(properties.attrib.get("name") if properties is not None else None,
                            tally, "none")
    alt = _clean_attribute(properties.attrib.get("descr") if properties is not None else None,
                           tally, "none")
    caption = _clean_attribute(properties.attrib.get("title") if properties is not None else None,
                               tally, "none")
    return ('Image: name="%s"; size=%sx%s EMU; alt="%s"; caption="%s"; '
            'source=%s; bytes=%s' % (name, cx, cy, alt, caption, source, byte_count))


def _notes_block(package: zipfile.ZipFile, slide_part: str,
                 relationships: tuple[ET.Element, ...], tally: dict) -> tuple | None:
    """Read one optional notes part and retain only body-placeholder text."""
    notes = [item for item in relationships if item.attrib.get("Type") in NOTES_TYPES]
    if not notes:
        return None
    if len(notes) > 1:
        raise BrewdocError("multiple notes relationships in %s" % slide_part)
    relation_id = notes[0].attrib.get("Id")
    if not relation_id:
        raise BrewdocError("notes relationship id is missing in %s" % slide_part)
    notes_part, _relationship = _related_part(
        relationships, relation_id, NOTES_TYPES, slide_part, "notes relationship")
    root = _required_xml(package, notes_part)
    if _local_name(root.tag) != "notes":
        raise BrewdocError("OOXML notes part has unexpected root: %s" % notes_part)
    tree = _shape_tree(root, "notes", notes_part)
    lines = []
    for shape in _shape_nodes(tree):
        if _local_name(shape.tag) == "sp" and _placeholder_type(shape) == "body":
            lines.extend(_shape_lines(shape, tally))
    if not lines:
        return None
    tally["text_regions"] += 1
    return "text", ["Speaker notes:", *lines]


def _slide_blocks(tree: ET.Element, title_shape: ET.Element | None,
                  package: zipfile.ZipFile, slide_part: str,
                  relationships: tuple[ET.Element, ...], tally: dict) -> list[tuple]:
    """Render supported shape-tree children in XML order, then append speaker notes."""
    blocks = []
    for shape in _shape_nodes(tree):
        if shape is title_shape:
            continue
        name = _local_name(shape.tag)
        if name == "sp":
            lines = _shape_lines(shape, tally)
            if lines:
                tally["text_regions"] += 1
                blocks.append(("text", lines))
        elif name == "pic":
            tally["text_regions"] += 1
            blocks.append(("text", [_picture_line(
                shape, package, slide_part, relationships, tally)]))
        elif name == "graphicFrame":
            table = next((item for item in shape.iter() if _local_name(item.tag) == "tbl"), None)
            if table is not None:
                tally["tables"] += 1
                blocks.append(("table", _table_rows(table, tally)))
    notes = _notes_block(package, slide_part, relationships, tally)
    if notes is not None:
        blocks.append(notes)
    return blocks


def _render_presentation(path: Path, _sheets=None) -> Rendered:
    """Render every declared slide after validating each required OPC boundary."""
    tally = new_tally()
    units = []
    with reading(path, "presentation"), zipfile.ZipFile(path) as package:
        presentation_part = _presentation_part(package)
        presentation = _required_xml(package, presentation_part)
        if _local_name(presentation.tag) != "presentation":
            raise BrewdocError("OOXML presentation part has unexpected root: %s"
                               % presentation_part)
        relationships = _required_relationships(package, _relationship_part(presentation_part))
        slide_list = _child(presentation, "sldIdLst")
        declarations = [] if slide_list is None else [
            item for item in slide_list if _local_name(item.tag) == "sldId"]
        used_relationships = set()
        for ordinal, declaration in enumerate(declarations, 1):
            relation_id = _relationship_attribute(declaration, "id")
            if not relation_id:
                raise BrewdocError("slide relationship id is missing for slide %d" % ordinal)
            if relation_id in used_relationships:
                raise BrewdocError("slide relationship %s is declared more than once"
                                   % relation_id)
            used_relationships.add(relation_id)
            slide_part, _relationship = _related_part(
                relationships, relation_id, SLIDE_TYPES, presentation_part,
                "slide relationship")
            slide = _required_xml(package, slide_part)
            if _local_name(slide.tag) != "sld":
                raise BrewdocError("OOXML slide part has unexpected root: %s" % slide_part)
            slide_relationships = _optional_relationships(
                package, _relationship_part(slide_part))
            tree = _shape_tree(slide, "slide", slide_part)
            title_shape, title = _title_shape(tree, tally)
            blocks = _slide_blocks(tree, title_shape, package, slide_part,
                                   slide_relationships, tally)
            units.append((ordinal, title, blocks))
    return _assemble(path, ROUTE.name, ROUTE.unit_kind, len(declarations), units, tally,
                     not_carried=ROUTE.not_carried)


ROUTE = Route("presentation", "slide", (".pptx",), _render_presentation,
              ("charts, SmartArt and embedded objects",
               "image pixels, audio and video",
               "animations, transitions, layout and master text, styling and visual positioning"))


def render_presentation(path) -> tuple[str, dict]:
    """Render declared slides in presentation order with stable slide keys."""
    return _render_presentation(Path(path))[:2]
