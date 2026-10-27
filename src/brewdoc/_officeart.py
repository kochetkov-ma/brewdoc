"""Bounded passive OfficeArt records and BIFF8 object evidence."""

from __future__ import annotations

import hashlib
import struct
import zlib

from brewdoc._inspection_common import InspectionLimit
from brewdoc._excel_formula import decode_formula

_CAPABILITIES = ("inventory", "text", "links", "scripts", "actions", "forms", "images", "attachments")
_STRINGS = {0x0380: "shape-name", 0x0381: "shape-description", 0x00C0: "shape-text"}
_RASTER = {0xF01D: ("image/jpeg", (0x46A, 0x6E2)),
           0xF02A: ("image/jpeg", (0x46A, 0x6E2)),
           0xF01E: ("image/png", (0x6E0,)), 0xF01F: ("image/bmp", (0x7A8,)),
           0xF029: ("image/tiff", (0x6E4,))}
_METAFILE = {0xF01A: ("image/emf", 0x3D4), 0xF01B: ("image/wmf", 0x216),
             0xF01C: ("image/x-pict", 0x542)}
_STDHLINK = bytes.fromhex("d0c9ea79f9bace118c8200aa004ba90b")
_URLMONIKER = bytes.fromhex("e0c9ea79f9bace118c8200aa004ba90b")
_FILEMONIKER = bytes.fromhex("0303000000000000c000000000000046")


class _Cursor:
    """Read framed fields without accepting truncated structures."""

    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        """Return a checked view without growing a payload buffer."""
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("truncated OfficeArt/object field at %d" % self.offset)
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def number(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def string(self):
        return _wide(self.take(self.number("I") * 2))


def _wide(data):
    """Decode a specified terminated UTF-16LE field, never scan unrelated bytes."""
    text = bytes(data).decode("utf-16le")
    if not text.endswith("\0"):
        raise ValueError("unterminated OfficeArt Unicode string")
    return text[:-1]


def _payload(context, document, kind, location, data, media_type):
    """Check retained capacity before copying a view into an artifact."""
    context.check("payload_bytes", len(data), location)
    context.check("retained_bytes", context.consumed["retained_bytes"] + len(data), location)
    return context.payload(document, kind, location, bytes(data), media_type)


def _hyperlink(data):
    """Decode IHlink string, URL and file monikers using serialized field lengths."""
    cursor = _Cursor(data)
    if cursor.take(16) != _STDHLINK or cursor.number("I") != 2:
        raise ValueError("unsupported IHlink identity/version")
    flags = cursor.number("I")
    display = cursor.string() if flags & 0x10 else None
    frame = cursor.string() if flags & 0x80 else None
    target = ""
    if flags & 1:
        if flags & 0x100:
            target = cursor.string()
        else:
            clsid = cursor.take(16)
            if clsid == _URLMONIKER:
                raw = bytes(cursor.take(cursor.number("I")))
                end = next((i + 2 for i in range(0, len(raw) - 1, 2) if raw[i:i + 2] == b"\0\0"), None)
                if end is None or len(raw) - end not in (0, 24):
                    raise ValueError("invalid URLMoniker length")
                target = _wide(raw[:end])
            elif clsid == _FILEMONIKER:
                parents = cursor.number("H")
                raw = bytes(cursor.take(cursor.number("I")))
                if not raw.endswith(b"\0") or any(byte > 127 for byte in raw):
                    raise ValueError("file moniker ANSI codepage unavailable")
                target = raw[:-1].decode("ascii")
                cursor.take(24)
                size = cursor.number("I")
                if size:
                    count, key = cursor.number("I"), cursor.number("H")
                    if key != 3 or size != count + 6:
                        raise ValueError("invalid file moniker Unicode framing")
                    target = bytes(cursor.take(count)).decode("utf-16le")
                target = "../" * parents + target
            else:
                raise ValueError("unsupported IHlink moniker CLSID %s" % bytes(clsid).hex())
    if flags & 8:
        target += "#" + cursor.string()
    if flags & 0x20:
        cursor.take(16)
    if flags & 0x40:
        cursor.take(8)
    if cursor.offset != len(data):
        raise ValueError("unparsed IHlink trailing bytes")
    return target, display, frame


def _properties(data, count, context, document, location, component):
    """Decode FOPT table entries before their ordered complex fields."""
    if count * 6 > len(data):
        raise ValueError("truncated OfficeArt property table")
    complex_offset = count * 6
    embedded = []
    for index in range(count):
        opid, value = struct.unpack_from("<HI", data, index * 6)
        prop = opid & 0x3FFF
        loc = {**location, "property_index": index, "property_id": prop}
        raw = None
        raw_offset = complex_offset
        if opid & 0x8000:
            end = complex_offset + value
            if end > len(data):
                raise ValueError("truncated OfficeArt complex property")
            raw = data[complex_offset:end]
            complex_offset = end
        context.component(document, "officeart-property", loc, data=raw,
                          disposition="extracted" if prop in _STRINGS or prop in (0x0382, 0x0104) else "opaque",
                          property_id=prop, value=value, complex=bool(opid & 0x8000), blip=bool(opid & 0x4000))
        if prop in _STRINGS and raw is not None:
            context.finding(document, "text", _STRINGS[prop], loc, component=component, text=_wide(raw))
        elif prop == 0x038E and raw is not None:
            script_loc = {**loc, "officeart_payload_offset": location["officeart_offset"] + 8 + raw_offset}
            artifact = _payload(context, document, "script-source", script_loc, raw, "text/plain;charset=utf-16le")
            context.finding(document, "scripts", "shape-script", script_loc, component=component,
                            text=_wide(raw), artifact=artifact, byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        elif prop == 0x0382 and raw is not None:
            target, display, frame = _hyperlink(raw)
            context.finding(document, "links", "shape-hyperlink", loc, component=component,
                            target=target, display_name=display, target_frame=frame)
        elif prop == 0x0104 and raw is None and value:
            context.finding(document, "images", "shape-blip-reference", loc, component=component,
                            blip_index=value)
        elif prop == 0x0104 and raw is not None:
            embedded.append((location["officeart_offset"] + 8 + raw_offset, len(raw), loc))
        elif prop in (0x0186, 0x0187, 0x0197, 0x01A1, 0x01A5) and raw is not None:
            if len(raw) == 0:
                context.component(document, "officeart-empty-fill-property", loc,
                                  disposition="extracted", property_id=prop,
                                  reserved=prop in (0x01A1, 0x01A5),
                                  nonconforming_complex_flag=prop in (0x01A1, 0x01A5))
            elif prop == 0x0186:
                embedded.append((location["officeart_offset"] + 8 + raw_offset, len(raw), loc))
            elif prop == 0x0187:
                name = _wide(raw)
                blip_flags = next((struct.unpack_from("<I", data, i * 6 + 2)[0] for i in range(count)
                                   if struct.unpack_from("<H", data, i * 6)[0] & 0x3FFF == 0x0188), 0)
                context.finding(document, "links" if blip_flags & 3 else "text", "fill-blip-name", loc,
                                component=component, target=name if blip_flags & 3 else None,
                                text=name, blip_flags=blip_flags)
            elif prop == 0x0197:
                array = _Cursor(raw)
                length, allocated, element = array.number("H"), array.number("H"), array.number("H")
                if allocated < length or element not in (8, 0xFFF0):
                    raise ValueError("invalid fillShadeColors array header")
                expected = length * (4 if element == 0xFFF0 else 8)
                if len(raw) != 6 + expected:
                    raise ValueError("invalid fillShadeColors array length")
                previous = -1
                for color_index in range(length):
                    color = array.number("I")
                    position = array.number("I") if element == 8 else 0
                    if position > 65536 or position < previous:
                        raise ValueError("invalid fillShadeColors position")
                    previous = position
                    context.component(document, "officeart-gradient-color", {**loc, "color_index": color_index},
                                      disposition="extracted", color=color, position=position / 65536)
            else:
                raise ValueError("nonempty reserved OfficeArt fill property")
        elif raw is not None and prop not in _STRINGS:
            context.problem(document, ("inventory", "text", "links", "scripts", "actions", "images"),
                            "unknown complex OfficeArt property 0x%04X" % prop, loc)
    if complex_offset != len(data):
        raise ValueError("unparsed OfficeArt property bytes")
    return embedded


def _image(kind, instance, data, context, document, location, component):
    """Retrieve raster bytes or boundedly decompress specified metafile bytes."""
    compressed = False
    if kind in _RASTER:
        media, bases = _RASTER[kind]
        if instance not in bases and instance - 1 not in bases:
            raise ValueError("unsupported BLIP raster instance")
        skip = 17 if instance in bases else 33
        if len(data) < skip:
            raise ValueError("truncated BLIP UID/tag")
        image = data[skip:]
    else:
        media, base = _METAFILE[kind]
        if instance not in (base, base + 1):
            raise ValueError("unsupported BLIP metafile instance")
        uid_size = 16 if instance == base else 32
        if len(data) < uid_size + 34:
            raise ValueError("truncated BLIP metafile header")
        expected = struct.unpack_from("<I", data, uid_size)[0]
        saved = struct.unpack_from("<I", data, uid_size + 28)[0]
        compression, filter_value = data[uid_size + 32:uid_size + 34]
        skip = uid_size + 34
        image = data[skip:]
        if len(image) != saved or filter_value != 0xFE:
            raise ValueError("invalid BLIP metafile size/filter")
        if compression == 0:
            context.check("decoded_bytes", context.consumed["decoded_bytes"] + expected, location)
            context.check("payload_bytes", expected, location)
            context.check("retained_bytes", context.consumed["retained_bytes"] + expected, location)
            decoder = zlib.decompressobj()
            image = decoder.decompress(image, expected + 1)
            if len(image) != expected or not decoder.eof or decoder.unused_data:
                raise ValueError("invalid BLIP metafile compressed data")
            context.charge("decoded_bytes", len(image), location)
            compressed = True
        elif compression != 0xFE or len(image) != expected:
            raise ValueError("unsupported BLIP metafile compression/size")
    loc = {**location, "officeart_payload_offset": location["officeart_offset"] + 8 + skip}
    artifact = _payload(context, document, "image", loc, image, media)
    context.finding(document, "images", "officeart-blip", loc, component=component,
                    artifact=artifact, media_type=media, byte_size=len(image),
                    sha256=hashlib.sha256(image).hexdigest(), decompressed=compressed)


def inspect_officeart(data: bytes, context, document, *, location=None) -> None:
    """Inspect assembled record bytes; offsets are relative, caller provenance is preserved."""
    origin = dict(location or {"kind": "officeart-stream"})
    try:
        context.charge("decoded_bytes", len(data), origin)
        artifact = _payload(context, document, "officeart-source", origin, data, "application/octet-stream")
        view = memoryview(data)
        stack = [(0, len(data), 0, {})]
        while stack:
            offset, end, depth, shape = stack.pop()
            while offset < end:
                loc = {**origin, **shape, "officeart_offset": offset}
                if end - offset < 8:
                    raise ValueError("truncated OfficeArt header")
                header, kind, size = struct.unpack_from("<HHI", view, offset)
                version, instance = header & 15, header >> 4
                finish = offset + 8 + size
                if finish > end:
                    raise ValueError("truncated OfficeArt payload")
                context.check("records", context.consumed["records"] + 1, loc)
                body = view[offset + 8:finish]
                component = context.component(document, "officeart-record", loc, data=view[offset:finish],
                                              disposition="opaque", artifact=artifact, record_type=kind,
                                              version=version, instance=instance, payload_bytes=size)
                if version == 15 or kind == 0xF007:
                    if version == 15 and kind not in (0xF000, 0xF001, 0xF002, 0xF003, 0xF004, 0xF005):
                        context.problem(document, _CAPABILITIES,
                                        "unknown OfficeArt record 0x%04X" % kind, loc)
                    child = offset + 8
                    if kind == 0xF007:
                        if size < 36:
                            raise ValueError("truncated OfficeArt FBSE")
                        child += 36 + body[33]
                        if child > finish:
                            raise ValueError("truncated OfficeArt FBSE name")
                        if child == finish and struct.unpack_from("<I", body, 24)[0]:
                            delay = struct.unpack_from("<I", body, 28)[0]
                            context.finding(document, "images", "delayed-blip-reference", loc,
                                            component=component, delay_stream_offset=delay)
                            context.problem(document, "images", "BLIP data resides in a separate delay stream", loc)
                    if child < finish:
                        context.check("nesting", depth + 1, loc)
                        stack.append((finish, end, depth, shape))
                        stack.append((child, finish, depth + 1, {} if kind == 0xF004 else dict(shape)))
                        break
                elif kind in (0xF00B, 0xF121, 0xF122):
                    embedded = _properties(body, instance, context, document, loc, component)
                    if embedded:
                        context.check("nesting", depth + 1, loc)
                        stack.append((finish, end, depth, shape))
                        for start, length, prop_loc in reversed(embedded):
                            stack.append((start, start + length, depth + 1, prop_loc))
                        break
                elif kind in _RASTER or kind in _METAFILE:
                    if version != 0:
                        raise ValueError("invalid BLIP record version")
                    _image(kind, instance, body, context, document, loc, component)
                elif kind == 0xF00A:
                    if size != 8:
                        raise ValueError("invalid OfficeArt FSP size")
                    shape["shape_id"] = struct.unpack_from("<I", body)[0]
                elif kind == 0xF11E:
                    if version != 0 or instance != 4 or size != 16:
                        raise ValueError("invalid OfficeArtSplitMenuColorContainer header")
                    colors = []
                    for index, role in enumerate(("fill", "line", "shadow", "3d")):
                        red, green, blue, flags = body[index * 4:index * 4 + 4]
                        if flags & 8 and (green or blue):
                            raise ValueError("invalid MSOCR scheme index")
                        colors.append({"role": role, "red": red, "green": green, "blue": blue,
                                       "scheme_index": bool(flags & 8)})
                    context.component(document, "officeart-split-menu-colors", loc,
                                      disposition="extracted", colors=colors)
                elif kind not in (0xF008, 0xF009, 0xF006, 0xF00D, 0xF010, 0xF011):
                    context.problem(document, _CAPABILITIES, "unknown OfficeArt record 0x%04X" % kind, loc)
                offset = finish
    except (ValueError, UnicodeError, zlib.error, InspectionLimit) as error:
        context.problem(document, _CAPABILITIES, str(error), locals().get("loc", origin))


def _object_formula(data, *, embedded=False):
    """Read ObjFmla and its sole ObjectParsedFormula token sequence."""
    cursor = _Cursor(data)
    size = cursor.number("H")
    if size % 2:
        raise ValueError("odd ObjFmla byte size")
    formula = _Cursor(cursor.take(size))
    if not size:
        return b"", cursor.offset
    count = formula.number("H") & 0x7FFF
    formula.take(4)
    tokens = formula.take(count)
    if embedded and tokens[:1] == b"\x02":
        marker, class_size, _ = formula.number("B"), formula.number("B"), formula.number("B")
        if marker != 3:
            raise ValueError("invalid PictFmlaEmbedInfo marker")
        formula.take(class_size)
    return tokens, cursor.offset


def _formula_details(tokens):
    """Recover native names, external indexes or local cell references without evaluation."""
    details = {"tokens_hex": bytes(tokens).hex()}
    if not tokens:
        return {**details, "formula": None}
    token = tokens[0]
    if token in (0x23, 0x43, 0x63) and len(tokens) == 5:
        return {**details, "name_index": struct.unpack_from("<I", tokens, 1)[0]}
    if token in (0x39, 0x59, 0x79) and len(tokens) == 7:
        sheet, name = struct.unpack_from("<HI", tokens, 1)
        return {**details, "external_sheet_index": sheet, "external_name_index": name}
    if token in (0x3A, 0x5A, 0x7A, 0x3B, 0x5B, 0x7B):
        return {**details, "external_sheet_index": struct.unpack_from("<H", tokens, 1)[0],
                "reference_tokens_hex": bytes(tokens[3:]).hex()}
    return {**details, "formula": decode_formula(bytes(tokens), dialect="biff8")}


def _xlstring(cursor):
    """Read an uncontinued BIFF Unicode list string, including rich/extension framing."""
    count, flags = cursor.number("H"), cursor.number("B")
    runs = cursor.number("H") if flags & 8 else 0
    extra = cursor.number("I") if flags & 4 else 0
    raw = cursor.take(count * (2 if flags & 1 else 1))
    text = bytes(raw).decode("utf-16le" if flags & 1 else "latin1")
    cursor.take(runs * 4 + extra)
    return text


def _list_control(body, object_type, context, document, loc, component, artifact):
    """Recover native list items, current dropdown string and selection bytes."""
    cursor = _Cursor(body)
    tokens, consumed = _object_formula(body)
    cursor.take(consumed)
    count, selection, flags, edit = (cursor.number("H") for _ in range(4))
    if count > 32767 or selection > count or (flags >> 4 & 3) == 3:
        raise ValueError("invalid list control count/selection")
    details = {"item_count": count, "selection": selection, "flags": flags, "edit_object_id": edit,
               "source_formula": _formula_details(tokens)}
    if object_type == 0x14:
        drop_flags, lines, width = cursor.number("H"), cursor.number("H"), cursor.number("H")
        start = cursor.offset
        current = _xlstring(cursor)
        if (cursor.offset - start) % 2:
            cursor.take(1)
        details.update(dropdown_flags=drop_flags, visible_lines=lines, minimum_width=width, current_value=current)
    items = []
    if flags & 2:
        for _ in range(count):
            context.charge("records", 1, loc)
            items.append(_xlstring(cursor))
    selected = [selection] if selection else []
    if flags >> 4 & 3:
        selected = []
        for index in range(count):
            value = cursor.number("B")
            if value not in (0, 1):
                raise ValueError("invalid list selection Boolean")
            if value:
                selected.append(index + 1)
    if cursor.offset != len(body):
        raise ValueError("unparsed list control bytes")
    context.finding(document, "forms", "list-control", loc, component=component, artifact=artifact,
                    items=items, selected_indexes=selected, **details)


def _native_control(kind, body):
    """Decode documented fixed-size BIFF control state fields."""
    layouts = {
        0x12: ("checkbox-state", "HHHH", ("checked", "accelerator", "reserved", "flags")),
        0x11: ("radio-button", "HH", ("next_object_id", "first_button")),
        0x10: ("edit-control", "HHHH", ("validation", "multiline", "vertical_scroll", "list_object_id")),
        0x0F: ("group-box", "HHH", ("accelerator", "reserved", "flags")),
        0x0C: ("scroll-control", "IhhhhhhhH", ("unused", "value", "minimum", "maximum", "increment",
                                             "page_increment", "horizontal", "width", "flags")),
        0x07: ("picture-format", "H", ("clipboard_format",)),
        0x06: ("group-control", "H", ("unused",)),
    }
    name, fmt, fields = layouts[kind]
    if len(body) != struct.calcsize("<" + fmt):
        raise ValueError("invalid %s subrecord length" % name)
    details = dict(zip(fields, struct.unpack("<" + fmt, body)))
    if kind == 0x12 and details["checked"] not in (0, 1, 2):
        raise ValueError("invalid checkbox state")
    boolean_fields = {0x11: ("first_button",), 0x10: ("multiline", "vertical_scroll"), 0x0C: ("horizontal",)}
    if any(details[field] not in (0, 1) for field in boolean_fields.get(kind, ())):
        raise ValueError("invalid native control Boolean")
    if kind == 0x10 and details["validation"] > 4:
        raise ValueError("invalid edit control validation")
    if kind == 0x0C and not details["minimum"] <= details["value"] <= details["maximum"]:
        raise ValueError("invalid scroll control bounds")
    if kind == 0x0C and min(details[field] for field in ("increment", "page_increment", "width")) < 0:
        raise ValueError("invalid scroll control increments")
    return name, details


def inspect_biff_object(payload: bytes, context, document, *, location=None) -> None:
    """Retain OBJ bytes and recover control identity, macro indexes and storage targets."""
    origin = dict(location or {"kind": "biff-object"})
    try:
        context.charge("decoded_bytes", len(payload), origin)
        artifact = _payload(context, document, "biff-object-source", origin, payload, "application/octet-stream")
        cursor, picture_flags, object_type = _Cursor(memoryview(payload)), 0, None
        while cursor.offset < len(payload):
            loc = {**origin, "object_offset": cursor.offset}
            kind, size = cursor.number("H"), cursor.number("H")
            body = cursor.take(len(payload) - cursor.offset if kind == 0x13 and size else size)
            component = context.component(document, "biff-object-subrecord", loc, data=body,
                                          disposition="opaque", artifact=artifact, subrecord_type=kind)
            if kind == 0:
                if size or cursor.offset != len(payload):
                    raise ValueError("invalid OBJ end subrecord")
                return
            if kind == 0x15:
                if size != 18:
                    raise ValueError("invalid FtCmo length")
                object_type, object_id, flags = struct.unpack_from("<HHH", body)
                context.finding(document, "forms", "biff-control", loc, component=component,
                                object_type=object_type, object_id=object_id, flags=flags)
            elif kind == 8:
                if size != 2:
                    raise ValueError("invalid FtPioGrbit length")
                picture_flags = struct.unpack_from("<H", body)[0]
            elif kind in (0x06, 0x07, 0x0C, 0x0F, 0x10, 0x11, 0x12):
                native_kind, details = _native_control(kind, body)
                context.finding(document, "forms", native_kind, loc, component=component, artifact=artifact, **details)
            elif kind in (0x0E, 0x14):
                tokens, consumed = _object_formula(struct.pack("<H", size) + bytes(body))
                context.finding(document, "forms", "object-linked-formula", loc, component=component,
                                artifact=artifact, **_formula_details(tokens))
            elif kind == 0x13:
                if size < len(body):
                    raise ValueError("continued FtLbsData requires BIFF segment assembly")
                if size:
                    _list_control(body, object_type, context, document, loc, component, artifact)
                return
            elif kind == 0x0D:
                if size != 22:
                    raise ValueError("invalid FtNts length")
                context.finding(document, "forms", "note-control", loc, component=component,
                                artifact=artifact, guid=bytes(body[:16]).hex(),
                                shared=struct.unpack_from("<H", body, 16)[0])
            elif kind == 4:
                tokens, _ = _object_formula(struct.pack("<H", size) + bytes(body))
                if len(tokens) == 5 and tokens[0] in (0x23, 0x43, 0x63):
                    name = struct.unpack_from("<I", tokens, 1)[0]
                    context.finding(document, "actions", "object-macro", loc, component=component,
                                    target="defined-name-index:%d" % name, name_index=name, artifact=artifact)
                else:
                    context.problem(document, "actions", "unsupported object macro formula", loc)
            elif kind == 9:
                tokens, consumed = _object_formula(body, embedded=True)
                if len(tokens) == 5 and tokens[0] == 2:
                    source = _Cursor(body[consumed:])
                    position = source.number("I")
                    details = {"stream_offset": position, "byte_size": source.number("I")} if picture_flags & 0x20 else {}
                    target = "Ctls" if picture_flags & 0x20 else "MBD%08X" % position
                    context.finding(document, "attachments", "object-data-source", loc,
                                    component=component, target=target, artifact=artifact, **details)
                    if picture_flags & 0x10:
                        context.problem(document, "forms", "ActiveX key data retained without control instantiation", loc)
                else:
                    context.problem(document, "attachments", "unsupported object picture formula", loc)
            else:
                context.problem(document, ("forms", "actions"), "unknown OBJ subrecord 0x%04X" % kind, loc)
        raise ValueError("missing OBJ end subrecord")
    except (ValueError, UnicodeError, InspectionLimit) as error:
        context.problem(document, ("forms", "actions", "attachments"), str(error), locals().get("loc", origin))
