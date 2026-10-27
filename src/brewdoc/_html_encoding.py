"""Shared accepted WHATWG decoding for readable and original-source HTML."""

from __future__ import annotations

import codecs
import functools
import re

from brewdoc.common import BrewdocError

_TAG_NAME = re.compile(r"</?([A-Za-z][^\t\n\f\r />]*)")
_ATTRIBUTE = re.compile(r"([^\s/=<>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")
def _tag_end(prefix: str, start: int) -> int | None:
    """Find a complete tag boundary without treating quoted attribute text as markup."""
    quote_character = ""
    for index in range(start, len(prefix)):
        character = prefix[index]
        if quote_character:
            if character == quote_character:
                quote_character = ""
        elif character in "\"'":
            quote_character = character
        elif character == ">":
            return index
    return None


def _meta_attributes(prefix: str):
    """Yield real meta start tags within the prefix, excluding comments and raw text."""
    position = 0
    while (start := prefix.find("<", position)) >= 0:
        if prefix.startswith("<!--", start):
            empty = re.match(r"<!---?>", prefix[start:])
            closing = re.search(r"--!?>", prefix[start + 4:])
            if empty:
                position = start + empty.end()
            elif closing:
                position = start + 4 + closing.end()
            else:
                return
            continue
        match = _TAG_NAME.match(prefix, start)
        if match is None and not prefix.startswith(("<!", "<?"), start):
            position = start + 1
            continue
        end = _tag_end(prefix, start + 1)
        if end is None:
            return
        position = end + 1
        if match is None or prefix.startswith("</", start):
            continue
        name = match.group(1).lower()
        if name == "meta":
            yield prefix[match.end():end]
        elif name == "plaintext":
            return
        elif name in ("script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title"):
            closing = re.search(r"</%s(?=[\t\n\f\r />])" % name, prefix[position:], re.I)
            if closing is None:
                return
            end = _tag_end(prefix, position + closing.end())
            if end is None:
                return
            position = end + 1


_BOMS = {b"\xef\xbb\xbf": "utf-8", b"\xfe\xff": "utf-16be", b"\xff\xfe": "utf-16le"}
# WHATWG Encoding Standard labels; replacement and x-user-defined labels are absent, so they refuse.
_ENCODINGS = {label: (name, codec) for name, codec, labels in (
    ("UTF-8", "utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
    ("IBM866", "cp866", "866 cp866 csibm866 ibm866"),
    ("ISO-8859-2", "iso8859_2", "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 "
     "iso_8859-2:1987 l2 latin2"),
    ("ISO-8859-3", "iso8859_3", "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 "
     "iso_8859-3:1988 l3 latin3"),
    ("ISO-8859-4", "iso8859_4", "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 "
     "iso_8859-4:1988 l4 latin4"),
    ("ISO-8859-5", "iso8859_5", "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 "
     "iso_8859-5 iso_8859-5:1988"),
    ("ISO-8859-6", "iso8859_6", "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 "
     "iso-8859-6 iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987"),
    ("ISO-8859-7", "iso8859_7", "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 "
     "iso8859-7 iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek"),
    ("ISO-8859-8", "iso8859_8", "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 "
     "iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 visual"),
    ("ISO-8859-8-I", "iso8859_8", "csiso88598i iso-8859-8-i logical"),
    ("ISO-8859-10", "iso8859_10", "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6"),
    ("ISO-8859-13", "iso8859_13", "iso-8859-13 iso8859-13 iso885913"),
    ("ISO-8859-14", "iso8859_14", "iso-8859-14 iso8859-14 iso885914"),
    ("ISO-8859-15", "iso8859_15", "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9"),
    ("ISO-8859-16", "iso8859_16", "iso-8859-16"),
    ("KOI8-R", "koi8_r", "cskoi8r koi koi8 koi8-r koi8_r"),
    ("KOI8-U", "koi8_u", "koi8-ru koi8-u"),
    ("macintosh", "mac_roman", "csmacintosh mac macintosh x-mac-roman"),
    ("windows-874", "cp874", "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874"),
    ("windows-1250", "cp1250", "cp1250 windows-1250 x-cp1250"),
    ("windows-1251", "cp1251", "cp1251 windows-1251 x-cp1251"),
    ("windows-1252", "cp1252", "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 "
     "iso-ir-100 iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252"),
    ("windows-1253", "cp1253", "cp1253 windows-1253 x-cp1253"),
    ("windows-1254", "cp1254", "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
     "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254"),
    ("windows-1255", "cp1255", "cp1255 windows-1255 x-cp1255"),
    ("windows-1256", "cp1256", "cp1256 windows-1256 x-cp1256"),
    ("windows-1257", "cp1257", "cp1257 windows-1257 x-cp1257"),
    ("windows-1258", "cp1258", "cp1258 windows-1258 x-cp1258"),
    ("x-mac-cyrillic", "mac_cyrillic", "x-mac-cyrillic x-mac-ukrainian"),
    ("GBK", "gb18030", "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk"),
    ("gb18030", "gb18030", "gb18030"),
    ("Big5", "big5hkscs", "big5 big5-hkscs cn-big5 csbig5 x-x-big5"),
    ("EUC-JP", "euc_jp", "cseucpkdfmtjapanese euc-jp x-euc-jp"),
    ("ISO-2022-JP", "iso2022_jp", "csiso2022jp iso-2022-jp"),
    ("Shift_JIS", "cp932", "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis"),
    ("EUC-KR", "cp949", "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
     "ksc5601 ksc_5601 windows-949"),
    ("UTF-16BE", "utf-16-be", "unicodefffe utf-16be"),
    ("UTF-16LE", "utf-16-le", "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le"),
) for label in labels.split()}
_STRICT_CODECS = frozenset({"utf-8", "utf-16-be", "utf-16-le", "gb18030", "big5hkscs", "euc_jp",
                            "iso2022_jp", "cp932", "cp949"})


def _gb18030_euro(error: UnicodeDecodeError) -> tuple[str, int]:
    """Decode a lead byte 0x80 as U+20AC and re-read what follows, as WHATWG does; other errors refuse."""
    if error.object[error.start] != 0x80:
        raise error
    return "\u20ac", error.start + 1


codecs.register_error("brewdoc.gb18030-euro", _gb18030_euro)
# One lone surrogate per invalid UTF-8 sequence; strict UTF-8 never decodes to a surrogate.
_INVALID_UTF8 = "\ud800"
codecs.register_error("brewdoc.utf8-invalid", lambda error: (_INVALID_UTF8, error.end))
# Latin text when at most half its high bytes touch another; Cyrillic words pair them.
_LATIN_PAIRED_SHARE = 0.5
# Cyrillic letters are 0xC0-0xFF in windows-1251 and KOI8-R; measured CJK text stays below 0.72.
_CYRILLIC_SHARE = 0.8
_HIGH_RUNS = re.compile(rb"[\x80-\xff]{2,}")
_ASCII = bytes(range(128))
# 20 two-byte codes Python's 2005 tables decode to Private Use Area, plus WHATWG's swap of
# A8 BC and 81 35 F4 37; Python decodes these 21 characters from no other bytes.
_WHATWG_GB18030 = dict(zip(
    "\ue5e5\ue78d\ue78e\ue78f\ue790\ue791\ue792\ue793\ue794\ue795\ue796\ue7c7"
    "\ue81e\ue826\ue82b\ue82c\ue832\ue843\ue854\ue864\u1e3f",
    "\u3000\ufe10\ufe12\ufe11\ufe13\ufe14\ufe15\ufe16\ufe17\ufe18\ufe19\u1e3f"
    "\u9fb4\u9fb5\u9fb6\u9fb7\u9fb8\u9fb9\u9fba\u9fbb\ue7c7"))
_GB18030_PRIVATE_USE = re.compile("[%s]" % "".join(_WHATWG_GB18030))


def _codec(label: str) -> tuple[str, str]:
    """Resolve a WHATWG label to (encoding name, Python codec); refusals never echo the label."""
    label = label.strip("\t\n\f\r ")
    if len(label) > 1 and label[0] == label[-1] and label[0] in "\"'":
        label = label[1:-1]
    if (found := _ENCODINGS.get(label.lower())) is None:
        raise BrewdocError("HTML charset declaration refused")
    return found


@functools.cache
def _single_byte_table(codec: str) -> str:
    """Map undefined bytes 0x80-0x9F to U+0080+n and higher ones to U+FFFD, like WHATWG indexes."""
    decoded = bytes(range(256)).decode(codec, "replace")
    return "".join(chr(byte) if character == "\ufffd" and 0x80 <= byte < 0xA0 else character
                   for byte, character in enumerate(decoded))


def _decode_bytes(raw: bytes, label: str | None) -> str:
    """Decode by BOM, then `label`, then strict UTF-8; single-byte encodings never fail."""
    for bom, bom_label in _BOMS.items():
        if raw.startswith(bom):
            raw, label = raw[len(bom):], bom_label
            break
    name, codec = _codec("utf-8" if label is None else label)
    if codec not in _STRICT_CODECS:
        return codecs.charmap_decode(raw, "strict", _single_byte_table(codec))[0]
    try:
        if codec == "gb18030":
            return _GB18030_PRIVATE_USE.sub(lambda match: _WHATWG_GB18030[match.group()],
                                            raw.decode(codec, "brewdoc.gb18030-euro"))
        return raw.decode(codec)
    except UnicodeDecodeError as exc:
        if codec == "utf-8":
            raise BrewdocError("HTML UTF-8 decoding failed: %s" % exc) from exc
        raise BrewdocError("HTML %s encoding refused: invalid bytes" % name) from exc


def _meta_charset(source_attributes: str) -> str | None:
    """Return one meta's declared label, "" for a valueless declaration, or None if it declares none."""
    attributes = {}
    for item in _ATTRIBUTE.finditer(source_attributes):
        value = next((value for value in item.groups()[1:] if value is not None), "")
        attributes.setdefault(item.group(1).lower(), value)
    if "charset" in attributes:
        return attributes["charset"]
    if attributes.get("http-equiv", "").strip().lower() != "content-type":
        return None
    charset = re.search(r"(?:^|;)\s*charset\b([^;]*)", attributes.get("content", ""), re.I)
    if charset is None:
        return None
    declared = re.fullmatch(r"\s*=\s*([^\s]+)\s*", charset.group(1))
    return declared.group(1) if declared else ""


def _meta_labels(prefix: bytes):
    """Yield declared meta charset labels within the prefix in source order."""
    return (declared for attributes in _meta_attributes(prefix.decode("latin1"))
            if (declared := _meta_charset(attributes)) is not None)


def _legacy_charset(sample: bytes) -> str | None:
    """Name an undeclared single-byte charset: isolated high bytes mean Latin, else Cyrillic case decides.
    Return None when paired high bytes are not mostly Cyrillic letters."""
    paired, high = sum(map(len, _HIGH_RUNS.findall(sample))), len(sample.translate(None, _ASCII))
    if paired <= high * _LATIN_PAIRED_SHARE:
        return "windows-1252"
    letters = sample.translate(None, bytes(range(0xC0)))
    if len(letters) < high * _CYRILLIC_SHARE:
        return None
    lower = len(letters.translate(None, bytes(range(0xE0))))
    return "windows-1251" if 2 * lower >= len(letters) else "KOI8-R"


def _decode_html(raw: bytes, header_charset: str | None = None) -> tuple[str, str, str]:
    """Decode by BOM, header, meta in 1024 bytes, then UTF-8; return text, WHATWG name and its source.
    Failing UTF-8 stays lossy if valid non-ASCII outnumbers invalid, else uses a 16 KiB meta or statistics."""
    bom = next((label for bom, label in _BOMS.items() if raw.startswith(bom)), None)
    label, source = (bom, "bom") if bom else (header_charset, "header")
    if label is None:
        label, source = next(_meta_labels(raw[:1024]), None), "meta"
    if label is None:
        label, source = "utf-8", "default"
    name, codec = _codec(label)
    if source == "meta" and codec in ("utf-16-be", "utf-16-le"):
        name, codec = "UTF-8", "utf-8"
    if codec not in _STRICT_CODECS and not raw.isascii():
        try:
            return raw.decode("utf-8"), "UTF-8", "fallback"
        except UnicodeDecodeError:
            pass
    if codec != "utf-8" or source == "bom":
        return _decode_bytes(raw, label), name, source
    text = raw.decode("utf-8", "brewdoc.utf8-invalid")
    if (invalid := text.count(_INVALID_UTF8)) == 0:
        return text, name, source
    # Invalid sequences never take an ASCII byte, so each ASCII byte stays one character.
    if len(text) - invalid - len(raw.translate(None, bytes(range(128, 256)))) > invalid:
        return text.replace(_INVALID_UTF8, "\ufffd"), "UTF-8", "fallback"
    del text
    late = next((declared for label in _meta_labels(raw[:16384])
                 if (declared := _codec(label)[0]) not in ("UTF-8", "UTF-16BE", "UTF-16LE")), None)
    name = late or _legacy_charset(raw[:65536])
    # No name repeats strict UTF-8, which refuses these bytes.
    return _decode_bytes(raw, name), name, "meta" if late else "fallback"
