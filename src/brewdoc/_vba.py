"""Passive MS-OVBA source recovery with exact compiled payload retention."""

from __future__ import annotations

import codecs
import struct

from brewdoc._compound import read_compound
from brewdoc._inspection_common import InspectionLimit

CODEPAGE_ENCODINGS = {
    37: "cp037", 20273: "cp273", 20424: "cp424", 20932: "euc_jp", 20936: "gb2312",
    1200: "utf-16le", 1201: "utf-16be", 12000: "utf-32le", 12001: "utf-32be",
    10000: "mac_roman", 10004: "mac_arabic", 10006: "mac_greek", 10007: "mac_cyrillic",
    10010: "mac_romanian", 10029: "mac_latin2", 10079: "mac_iceland",
    10081: "mac_turkish", 10082: "mac_croatian", 20127: "ascii",
    20866: "koi8-r", 21866: "koi8-u", 28591: "iso8859-1", 28592: "iso8859-2",
    28593: "iso8859-3", 28594: "iso8859-4", 28595: "iso8859-5",
    28596: "iso8859-6", 28597: "iso8859-7", 28598: "iso8859-8",
    28599: "iso8859-9", 28603: "iso8859-13", 28605: "iso8859-15",
    38598: "iso8859-8", 50220: "iso2022_jp", 50221: "iso2022_jp_ext", 50225: "iso2022_kr",
    51932: "euc_jp", 51936: "gb2312", 51949: "euc_kr",
    52936: "hz", 54936: "gb18030", 65000: "utf-7", 65001: "utf-8",
}


def decompress_vba(data, context=None, *, location=None):
    """Decode a compressed container, refusing malformed chunks before growth."""
    location = location or {"kind": "vba"}
    if len(data) < 3 or data[0] != 1:
        raise ValueError("invalid VBA compressed container signature")
    output = bytearray()
    position = 1

    def reserve(amount):
        if context is not None:
            context.check("payload_bytes", len(output) + amount, location)
            context.charge("decoded_bytes", amount, location)
        elif len(output) + amount > 33_554_432:
            raise ValueError("VBA decompression size limit exceeded")

    while position < len(data):
        if position + 2 > len(data):
            raise ValueError("truncated VBA chunk header")
        header = struct.unpack_from("<H", data, position)[0]
        size = (header & 4095) + 3
        end = position + size
        if header & 0x7000 != 0x3000 or end > len(data):
            raise ValueError("invalid VBA chunk signature or length")
        position += 2
        start = len(output)
        if not header & 0x8000:
            if size != 4098:
                raise ValueError("invalid VBA raw chunk size")
            reserve(4096)
            output.extend(memoryview(data)[position:end])
            position = end
            continue
        while position < end:
            flags = data[position]
            position += 1
            for bit in range(8):
                if position == end:
                    break
                produced = len(output) - start
                if flags & (1 << bit):
                    if position + 2 > end:
                        raise ValueError("truncated VBA copy token")
                    token = struct.unpack_from("<H", data, position)[0]
                    position += 2
                    bits = max(4, (produced - 1).bit_length())
                    mask = 0xFFFF >> bits
                    length = (token & mask) + 3
                    distance = (token >> (16 - bits)) + 1
                    if distance > produced or produced + length > 4096:
                        raise ValueError("invalid VBA copy distance or chunk expansion")
                    reserve(length)
                    for _ in range(length):
                        output.append(output[-distance])
                else:
                    if produced >= 4096:
                        raise ValueError("VBA literal exceeds chunk capacity")
                    reserve(1)
                    output.append(data[position])
                    position += 1
    return bytes(output)


class _Directory:
    """Walk dir records by declared grammar rather than byte-pattern searches."""

    def __init__(self, data, context, location):
        self.data, self.context, self.location = data, context, location
        self.position = 0

    def take(self, length):
        if length < 0 or self.position + length > len(self.data):
            raise ValueError("truncated VBA dir record")
        result = self.data[self.position:self.position + length]
        self.position += length
        return result

    def integer(self, length):
        return int.from_bytes(self.take(length), "little")

    def variable(self):
        return self.take(self.integer(4))

    def record(self):
        offset = self.position
        self.context.charge("records", 1, self.location)
        kind = self.integer(2)
        if kind == 9:
            if self.integer(4) != 4:
                raise ValueError("invalid VBA PROJECTVERSION reserved size")
            value = self.take(6)
        elif kind in (13, 14, 47):
            self.take(4)
            if kind == 13:
                value = self.variable()
                self.take(6)
            elif kind == 14:
                value = self.variable()
                self.variable()
                self.take(6)
            else:
                value = self.variable()
                self.take(6)
                marker = self.integer(2)
                if marker == 22:
                    self.variable()
                    if self.integer(2) != 62:
                        raise ValueError("invalid VBA reference name Unicode marker")
                    self.variable()
                    marker = self.integer(2)
                if marker != 48:
                    raise ValueError("invalid VBA extended reference marker")
                self.take(4)
                self.variable()
                self.take(26)
        else:
            value = self.variable()
        return kind, value, offset


def _module_directory(data, context, location):
    """Read project codepage and terminated module metadata records."""
    reader = _Directory(data, context, location)
    codepage = None
    count = None
    modules = []
    current = None
    known = {1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 19, 20, 22, 25, 26,
             28, 30, 33, 34, 37, 40, 43, 44, 47, 49, 50, 51, 60, 61, 62, 64, 71, 72, 74, 16}
    while reader.position < len(data):
        kind, value, offset = reader.record()
        if kind not in known:
            raise ValueError("unsupported VBA dir record 0x%04x at %d" % (kind, offset))
        if kind == 3:
            if len(value) != 2 or codepage is not None:
                raise ValueError("invalid or duplicate VBA codepage")
            codepage = int.from_bytes(value, "little")
        elif kind == 15:
            if len(value) != 2 or count is not None:
                raise ValueError("invalid or duplicate VBA module count")
            count = int.from_bytes(value, "little")
        elif kind == 25:
            if current is not None or count is None:
                raise ValueError("invalid VBA module start")
            current = {"name_bytes": value, "record_offset": offset}
        elif kind in (71, 26, 50, 49):
            if current is None:
                raise ValueError("VBA module field outside module")
            field = {71: "unicode_name", 26: "stream_bytes", 50: "unicode_stream", 49: "source_offset"}[kind]
            if field in current:
                raise ValueError("duplicate VBA module field")
            if kind == 49:
                if len(value) != 4:
                    raise ValueError("invalid VBA MODULEOFFSET size")
                current[field] = int.from_bytes(value, "little")
            else:
                current[field] = value
        elif kind == 43:
            if current is None or "stream_bytes" not in current or "source_offset" not in current or value:
                raise ValueError("incomplete VBA module metadata")
            modules.append(current)
            current = None
        elif kind == 16:
            if value or reader.position != len(data):
                raise ValueError("invalid VBA project terminator")
    if codepage is None or count is None or current is not None or len(modules) != count:
        raise ValueError("incomplete VBA dir metadata")
    encoding = CODEPAGE_ENCODINGS.get(codepage, "cp%d" % codepage)
    codecs.lookup(encoding)
    for module in modules:
        module["codepage"] = codepage
        module["name"] = module.get("unicode_name", b"").decode("utf-16le") or module["name_bytes"].decode(encoding)
        module["stream"] = module.get("unicode_stream", b"").decode("utf-16le") or module["stream_bytes"].decode(encoding)
    return encoding, modules


def _inspect_module(module, entries, parent, encoding, context, document):
    """Retain one module's compiled prefix, compressed container and source."""
    matches = [entry for entry in entries if entry["kind"] == "stream"
               and entry["path"].casefold() == (parent + "/" + module["stream"]).casefold()]
    if len(matches) != 1 or matches[0]["data"] is None:
        raise ValueError("missing, ambiguous or unreadable VBA module stream: " + module["stream"])
    entry = matches[0]
    source_offset = module["source_offset"]
    raw = entry["data"]
    if source_offset >= len(raw):
        raise ValueError("VBA source offset outside module stream")
    where = dict(entry["location"], source_offset=source_offset)
    context.check("payload_bytes", len(raw), where)
    context.check("retained_bytes", context.consumed["retained_bytes"] + len(raw), where)
    prefix = context.payload(document, "vba_compiled_prefix", where, raw[:source_offset], "application/octet-stream")
    compressed = context.payload(document, "vba_compressed_source", where, raw[source_offset:], "application/octet-stream")
    source = decompress_vba(memoryview(raw)[source_offset:], context, location=where)
    artifact = context.payload(document, "vba_source", where, source, "text/plain")
    text = source.decode(encoding)
    context.finding(document, "macros", "vba_module", where, text=text,
                    artifact=artifact, module_name=module["name"], stream_name=module["stream"],
                    encoding=encoding, codepage=module["codepage"],
                    compiled_prefix=prefix, compressed_source=compressed)


def inspect_vba(data, name, context, document, *, location=None, streams=None):
    """Recover source without executing or interpreting compiled macro code."""
    base = dict(location or {"kind": "vba", "name": name})
    entries = read_compound(data, context, document, location=base) if streams is None else streams
    directories = [entry for entry in entries if entry["kind"] == "stream" and entry["name"].lower() == "dir"
                   and entry["path"].rsplit("/", 1)[0].split("/")[-1].lower() == "vba"]
    if not directories:
        if any(entry["name"].lower() in ("vba", "_vba_project", "project") for entry in entries):
            context.problem(document, ("macros",), "VBA metadata dir stream missing", base)
        else:
            context.cover(document, "macros", "complete", "No VBA storage found")
        return
    for directory in directories:
        where = directory["location"]
        try:
            if directory["data"] is None:
                raise ValueError("unreadable VBA dir stream")
            decoded = decompress_vba(directory["data"], context, location=where)
            context.payload(document, "vba_directory", where, decoded, "application/octet-stream")
            encoding, modules = _module_directory(decoded, context, where)
            parent = directory["path"].rsplit("/", 1)[0]
            failures = []
            for module in modules:
                try:
                    _inspect_module(module, entries, parent, encoding, context, document)
                except (ValueError, LookupError, UnicodeError, InspectionLimit) as error:
                    failure = (str(error), dict(where, module=module["name"]))
                    failures.append(failure)
            for reason, failed_location in failures:
                context.problem(document, ("macros",), reason, failed_location)
            context.cover(document, "macros", "complete", "VBA module source recovered; compiled p-code and performance caches preserved as opaque bytes")
        except (ValueError, LookupError, UnicodeError, InspectionLimit) as error:
            context.problem(document, ("macros",), str(error), where)
