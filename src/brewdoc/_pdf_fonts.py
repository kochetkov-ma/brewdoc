"""Literal Type1 encoding names shared by readable and independent PDF extraction."""

import re


PDF_FONT_GLYPHS = {"summationtext": "[sum]", "summationdisplay": "[sum]",
                   "productdisplay": "[product]"}
for _glyph_stem, _glyph_token in (("parenleft", "("), ("parenright", ")"),
                                 ("bracketleft", "["), ("bracketright", "]"),
                                 ("braceleft", "{"), ("braceright", "}")):
    for _glyph_size in ("big", "Big", "bigg", "Bigg"):
        PDF_FONT_GLYPHS[_glyph_stem + _glyph_size] = _glyph_token


def _header_code(header: bytes) -> bytes:
    """Mask comments and literal data so only actual header tokens can declare names."""
    code, index = bytearray(header), 0
    while index < len(header):
        start = index
        if header[index:index + 2] in (b"<<", b">>"):
            index += 2
            continue
        if header[index] == 37:
            while index < len(header) and header[index] not in (10, 13):
                index += 1
        elif header[index] == 40:
            depth, index = 1, index + 1
            while index < len(header) and depth:
                value = header[index]
                index += 1
                if value == 92:
                    index = min(index + 1, len(header))
                elif value == 40:
                    depth += 1
                elif value == 41:
                    depth -= 1
        elif header[index:index + 1] == b"<" and header[index:index + 2] != b"<<":
            end = header.find(b">", index + 1)
            index = len(header) if end < 0 else end + 1
        else:
            index += 1
            continue
        code[start:index] = b" " * (index - start)
    return bytes(code)


def type1_encoding(header: bytes) -> dict[int, str | None]:
    """Read one literal Encoding array; ambiguous codes stay None and programs stay opaque."""
    code = _header_code(header)
    name = rb"[^\x00\t\n\f\r ()<>\[\]{}/%]+"
    declarations = re.finditer(rb"/Encoding\s+256\s+array\b", code)
    if next(declarations, None) is None or next(declarations, None) is not None:
        return {}
    definition = re.search(rb"/Encoding\s+256\s+array(.*?)(?<!\S)def\b", code, re.S)
    if definition is None:
        return {}
    outside = code[:definition.start()] + code[definition.end():]
    if any(token in (b"Encoding", b"/Encoding") for token in re.findall(rb"/" + name + b"|" + name, outside)):
        return {}
    body = definition[1]
    body = re.sub(rb"\A\s*0\s+1\s+255\s+\{\s*1\s+index\s+exch\s+/\.notdef\s+put\s*\}\s+for\b", b"", body)
    assignment = rb"dup\s+(\d+)\s+/(" + name + rb")\s+put\b"
    assignments = re.finditer(assignment, body)
    residue = re.sub(assignment, b"", body)
    if not re.fullmatch(rb"\s*(?:readonly\s*)?", residue):
        return {}
    names = {}
    for match in assignments:
        number = int(match[1])
        if not 0 <= number <= 255 or any(value > 127 for value in match[2]):
            return {}
        names[number] = match[2].decode("ascii") if number not in names else None
    return names
