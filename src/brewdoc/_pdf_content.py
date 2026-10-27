"""Bounded source framing for PDF operators and exact encoded inline images."""

from __future__ import annotations

import bisect
import io
import zlib

from pdfminer.ccitt import CCITTG4Parser
from pdfminer.psparser import PSBaseParser, PSEOF, PSException, PSKeyword, PSLiteral, literal_name

from brewdoc.common import BrewdocError


_SPACE = b"\x00\t\n\x0c\r "
_DELIMITERS = b"()<>[]{}/%"
_ALIASES = {"W": "Width", "H": "Height", "BPC": "BitsPerComponent",
            "CS": "ColorSpace", "F": "Filter", "DP": "DecodeParms", "IM": "ImageMask",
            "D": "Decode", "I": "Interpolate"}


class ContentError(BrewdocError):
    """An exact source extent could not be proved; the container remains evidence."""

    def __init__(self, reason, start, end, *, segments=()):
        super().__init__("PDF content at %d:%d: %s" % (start, end, reason))
        self.reason, self.span = reason, [start, end]
        self.segments = list(segments)


class _Cursor:
    """Read admitted segments as one logical source without copying the document."""

    def __init__(self, segments, context, location):
        self.segments, self.context, self.location = segments, context, location
        self.starts, total = [], 0
        for segment in segments:
            context.charge("records", 1, location)
            self.starts.append(total)
            total += len(segment["data"])
        self.length, self.pos, self.payload_start = total, 0, None

    def fail(self, reason, start=None):
        start = self.pos if start is None else start
        raise ContentError(reason, start, self.pos, segments=self.spans(start, self.pos))

    def byte(self, offset=0):
        position = self.pos + offset
        if position >= self.length:
            self.fail("truncated source")
        index = bisect.bisect_right(self.starts, position) - 1
        return self.segments[index]["data"][position - self.starts[index]]

    def take(self):
        """Charge scanned work and an active payload bound before advancing one byte."""
        value = self.byte()
        if self.payload_start is not None:
            self.context.check("payload_bytes", self.pos + 1 - self.payload_start, self.location)
        self.context.charge("decoded_bytes", 1, self.location)
        self.pos += 1
        return value

    def skip(self, count):
        if count < 0 or self.pos + count > self.length:
            self.fail("truncated source")
        if self.payload_start is not None:
            self.context.check("payload_bytes", self.pos + count - self.payload_start, self.location)
        self.context.charge("decoded_bytes", count, self.location)
        self.pos += count

    def spans(self, start, end):
        """Map a half-open logical extent to its original decoded source members."""
        result = []
        for index, segment in enumerate(self.segments):
            left, right = max(start, self.starts[index]), min(end, self.starts[index] + len(segment["data"]))
            if left < right:
                self.context.charge("records", 1, self.location)
                result.append({"source": dict(segment["source"]), "segment_ordinal": index + 1,
                               "span": [left - self.starts[index], right - self.starts[index]]})
        return result

    def copy(self, start, end):
        """Admit both segment slices and their joined representation before copying."""
        size = end - start
        self.context.check("payload_bytes", size, self.location)
        self.context.charge("retained_bytes", size * 2, self.location)
        parts = []
        for item in self.spans(start, end):
            data = self.segments[item["segment_ordinal"] - 1]["data"]
            parts.append(data[item["span"][0]:item["span"][1]])
        return b"".join(parts)

    def whitespace(self):
        while self.pos < self.length and self.byte() in _SPACE:
            self.take()


def _token(cursor):
    """Bound a lexical token before native parsing can grow its token accumulator."""
    while cursor.pos < cursor.length:
        cursor.whitespace()
        if cursor.pos == cursor.length:
            return None
        if cursor.byte() != 37:
            break
        while cursor.pos < cursor.length and cursor.take() not in (10, 13):
            pass
    else:
        return None
    start, first = cursor.pos, cursor.take()
    if first == 40:
        depth = 1
        cursor.context.check("nesting", depth, cursor.location)
        while depth:
            value = cursor.take()
            if value == 92:
                cursor.take()
            elif value == 40:
                depth += 1
                cursor.context.check("nesting", depth, cursor.location)
            elif value == 41:
                depth -= 1
            cursor.context.check("payload_bytes", cursor.pos - start, cursor.location)
    elif first == 60 and cursor.pos < cursor.length and cursor.byte() != 60:
        while cursor.take() != 62:
            cursor.context.check("payload_bytes", cursor.pos - start, cursor.location)
    elif first in (60, 62) and cursor.pos < cursor.length and cursor.byte() == first:
        cursor.take()
    elif first not in b"[]<>{})":
        while cursor.pos < cursor.length and cursor.byte() not in _SPACE + _DELIMITERS:
            cursor.context.check("payload_bytes", cursor.pos - start + 1, cursor.location)
            cursor.take()
    cursor.context.charge("records", 1, cursor.location)
    raw = cursor.copy(start, cursor.pos)
    cursor.context.charge("retained_bytes", len(raw) * 3 + 1, cursor.location)
    cursor.context.charge("decoded_bytes", len(raw) * (len(raw) // PSBaseParser.BUFSIZ + 1), cursor.location)
    try:
        parser = PSBaseParser(io.BytesIO(raw + b" "))
        token = parser.nexttoken()[1]
        try:
            parser.nexttoken()
        except PSEOF:
            pass
        else:
            cursor.fail("multiple native tokens in one lexical extent", start)
    except (PSException, ValueError, OverflowError) as exc:
        cursor.fail("invalid native syntax: " + type(exc).__name__, start)
    if isinstance(token, PSKeyword) and token.name == b"null":
        token = None
    return start, cursor.pos, token


def _object(cursor, token, depth=0):
    """Assemble only bounded arrays and strict name-keyed dictionaries."""
    start, end, value = token
    if not isinstance(value, PSKeyword) or value.name not in (b"[", b"<<"):
        if isinstance(value, PSKeyword):
            cursor.fail("invalid object keyword", start)
        return value
    cursor.context.check("nesting", depth + 1, cursor.location)
    closing, values = (b"]" if value.name == b"[" else b">>"), []
    while True:
        item = _token(cursor)
        if item is None:
            cursor.fail("unterminated container", start)
        if isinstance(item[2], PSKeyword) and item[2].name == closing:
            break
        if isinstance(item[2], PSKeyword) and item[2].name in (b"]", b">>", b"BI", b"ID", b"EI"):
            cursor.fail("invalid container token", start)
        cursor.context.charge("records", 1, cursor.location)
        values.append(_object(cursor, item, depth + 1))
    if closing == b"]":
        return values
    if len(values) % 2 or any(not isinstance(key, PSLiteral) for key in values[::2]):
        cursor.fail("dictionary keys must be names", start)
    return {literal_name(values[index]): values[index + 1] for index in range(0, len(values), 2)}


def _name(value):
    return literal_name(value) if isinstance(value, PSLiteral) else ""


def _plain(value):
    if isinstance(value, PSLiteral):
        return "/" + literal_name(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _dimensions(attrs, resolve_color, cursor):
    """Establish packed sample size from checked image and resource declarations."""
    width, height = attrs.get("Width"), attrs.get("Height")
    if type(width) is not int or width < 1 or type(height) is not int or height < 1:
        cursor.fail("invalid inline image dimensions")
    mask = attrs.get("ImageMask", False)
    if type(mask) is not bool:
        cursor.fail("ImageMask must be boolean")
    bits = attrs.get("BitsPerComponent", 1 if mask else None)
    if type(bits) is not int or bits not in (1, 2, 4, 8, 16) or (mask and bits != 1):
        cursor.fail("invalid inline image BitsPerComponent")
    if mask:
        if "ColorSpace" in attrs:
            cursor.fail("image mask must not declare ColorSpace")
        components = 1
    else:
        color, seen = attrs.get("ColorSpace"), set()
        while isinstance(color, PSLiteral) and _name(color) not in ("G", "RGB", "CMYK", "DeviceGray", "DeviceRGB", "DeviceCMYK"):
            name = _name(color)
            if name in seen:
                cursor.fail("cyclic inline ColorSpace resource")
            cursor.context.check("nesting", len(seen) + 1, cursor.location)
            cursor.context.charge("records", 1, cursor.location)
            seen.add(name)
            color = resolve_color(name)
        name = _name(color)
        components = {"G": 1, "DeviceGray": 1, "RGB": 3, "DeviceRGB": 3,
                      "CMYK": 4, "DeviceCMYK": 4}.get(name)
        if isinstance(color, list) and color:
            family = _name(color[0])
            if family in ("Indexed", "I", "Separation") and len(color) == 4:
                components = 1
            elif family == "ICCBased" and len(color) == 2:
                profile = color[1]
                declaration = profile if isinstance(profile, dict) else getattr(profile, "attrs", {})
                components = declaration.get("N")
                if components not in (1, 3, 4):
                    cursor.fail("invalid ICCBased component declaration")
            elif (family == "DeviceN" and len(color) >= 4 and isinstance(color[1], list)
                  and all(isinstance(item, PSLiteral) for item in color[1])):
                components = len(color[1])
            elif family in ("CalGray", "CalRGB", "Lab"):
                components = 1 if family == "CalGray" else 3
        if type(components) is not int or components < 1:
            cursor.fail("missing or invalid inline ColorSpace resource")
    size = height * ((width * components * bits + 7) // 8)
    cursor.context.check("payload_bytes", size, cursor.location)
    return size


def _jpeg(cursor):
    """Measure JPEG marker framing, including stuffed entropy and later scans."""
    if (cursor.take(), cursor.take()) != (255, 216):
        cursor.fail("DCT data has no SOI")
    entropy, scans = False, 0
    while True:
        value = cursor.take()
        if entropy and value != 255:
            continue
        if value != 255:
            cursor.fail("invalid DCT marker")
        marker = cursor.take()
        while marker == 255:
            marker = cursor.take()
        if entropy and (marker == 0 or 208 <= marker <= 215):
            continue
        in_scan, entropy = entropy, False
        if marker == 217:
            if not scans:
                cursor.fail("DCT image has no scan")
            return
        if marker in (0, 216) or 208 <= marker <= 215:
            cursor.fail("unexpected DCT marker")
        if marker == 1:
            entropy = in_scan
            continue
        length = (cursor.take() << 8) | cursor.take()
        if length < 2:
            cursor.fail("invalid DCT marker length")
        cursor.skip(length - 2)
        if marker == 218:
            entropy, scans = True, scans + 1
        elif marker == 220:
            entropy = in_scan


class _Bits:
    """Own physical bit consumption independently of native CCITT state positions."""

    def __init__(self, cursor):
        self.cursor, self.current, self.left = cursor, 0, 0

    def read(self, count=1):
        value = 0
        for _ in range(count):
            if not self.left:
                self.current, self.left = self.cursor.take(), 8
            self.left -= 1
            value = (value << 1) | ((self.current >> self.left) & 1)
        return value

    def align(self, *, zero=False):
        """Discard physical padding, requiring zero fill at declared row boundaries."""
        if zero and self.current & ((1 << self.left) - 1):
            self.cursor.fail("CCITT byte alignment has nonzero fill bits")
        self.left = 0

    def peek(self, count):
        """Look ahead with cumulative scan charges but without advancing physical bits."""
        position, current, left = self.cursor.pos, self.current, self.left
        try:
            return self.read(count)
        finally:
            self.cursor.pos, self.current, self.left = position, current, left


class _Fax(CCITTG4Parser):
    """Reuse native tables/state with admitted byte rows and no sample output."""

    def __init__(self, width, cursor):
        self.cursor = cursor
        cursor.context.check("payload_bytes", width * 2, cursor.location)
        cursor.context.check("decoded_bytes", cursor.context.consumed["decoded_bytes"] + width * 2, cursor.location)
        super().__init__(width, bytealign=False)

    def _row(self):
        self.cursor.context.charge("decoded_bytes", self.width, self.cursor.location)
        return bytearray(b"\x01") * self.width

    def reset(self):
        """Replace native temporary lists with two pre-admitted byte rows."""
        self._y, self._curline = 0, self._row()
        self._reset_line()
        self._accept, self._state = self._parse_mode, self.MODE

    def _reset_line(self):
        self._refline, self._curline = self._curline, self._row()
        self._curpos, self._color = -1, 1

    def output_line(self, y, bits):
        """Charge one completed row without printing or retaining sample output."""
        self.cursor.context.charge("records", 1, self.cursor.location)

    def row_2d(self, bits):
        """Advance native 2D state through a row while preserving uncompressed mode."""
        row = self._y
        try:
            while self._y == row:
                self._parse_bit(bits.read())
        except (self.InvalidData, self.EOFB, IndexError) as exc:
            self.cursor.fail("invalid CCITT row: " + type(exc).__name__)

    def _do_uncompressed(self, values):
        self.cursor.context.charge("decoded_bytes", len(values), self.cursor.location)
        for value in values:
            self._curpos = max(0, self._curpos)
            self._curline[self._curpos] = int(value)
            self._curpos += 1
            self._flush_line()

    def _parse_horiz1(self, value):
        if type(value) is int and self._n1 + value > self.width:
            self.cursor.fail("CCITT horizontal run exceeds Columns")
        return super()._parse_horiz1(value)

    def _parse_horiz2(self, value):
        if type(value) is int and max(0, self._curpos) + self._n1 + self._n2 + value > self.width:
            self.cursor.fail("CCITT horizontal run exceeds Columns")
        return super()._parse_horiz2(value)

    def _do_vertical(self, distance):
        self.cursor.context.charge("decoded_bytes", self.width * 3, self.cursor.location)
        target = self._curpos + 1
        while target < self.width:
            if target == 0:
                if self._color == 1 and self._refline[target] != self._color:
                    break
            elif self._refline[target - 1] == self._color and self._refline[target] != self._color:
                break
            target += 1
        target += distance
        if target < max(0, self._curpos) or target > self.width:
            self.cursor.fail("CCITT vertical run exceeds row bounds")
        super()._do_vertical(distance)

    def _do_pass(self):
        self.cursor.context.charge("decoded_bytes", self.width * 3, self.cursor.location)
        super()._do_pass()

    def _do_horizontal(self, first, second):
        self.cursor.context.charge("decoded_bytes", first + second, self.cursor.location)
        super()._do_horizontal(first, second)

    def row_1d(self, bits):
        """Decode Group 3 runs against native white/black tables with exact bounds."""
        position, color, run = 0, 1, 0
        while position < self.width:
            table = self.WHITE if color else self.BLACK
            while isinstance(table, list):
                table = table[bits.read()]
            if type(table) is not int:
                self.cursor.fail("invalid CCITT Group 3 run")
            run += table
            if position + run > self.width:
                self.cursor.fail("CCITT run exceeds Columns")
            if table < 64:
                for index in range(position, position + run):
                    self._curline[index] = color
                position, color, run = position + run, 1 - color, 0
        self.cursor.context.charge("records", 1, self.cursor.location)
        self._y += 1
        self._reset_line()
        self._accept, self._state = self._parse_mode, self.MODE


def _eol(bits):
    zeros = 0
    while not bits.read():
        zeros += 1
    if zeros < 11:
        bits.cursor.fail("CCITT EOL is missing")


def _fax(cursor, parameter):
    """Measure Group 3/4 rows and their required RTC/EOFB without rendering pixels."""
    width, rows, k = parameter.get("Columns", 1728), parameter.get("Rows", 0), parameter.get("K", 0)
    if any(type(item) is not int for item in (width, rows, k)) or width < 1 or rows < 0:
        cursor.fail("invalid CCITT dimensions or K")
    endblock, eol, align = (parameter.get("EndOfBlock", True), parameter.get("EndOfLine", False),
                            parameter.get("EncodedByteAlign", False))
    if any(type(item) is not bool for item in (endblock, eol, align)):
        cursor.fail("invalid CCITT boolean parameter")
    damaged = parameter.get("DamagedRowsBeforeError", 0)
    if type(damaged) is not int or damaged != 0:
        cursor.fail("CCITT damaged-row recovery is not permitted")
    if not endblock and not rows:
        cursor.fail("CCITT Rows is required without EndOfBlock")
    fax, bits = _Fax(width, cursor), _Bits(cursor)
    while True:
        has_eol = k >= 0 and (eol or k > 0 or bits.peek(12) <= 1)
        if align and not has_eol and not (k < 0 and eol):
            bits.align(zero=True)
        if endblock and k < 0 and bits.peek(24) == 0x001001:
            bits.read(24)
            bits.align()
            return
        if has_eol:
            _eol(bits)
            if align:
                bits.align(zero=True)
            one_dimensional = bool(bits.read()) if k > 0 else True
            if endblock and bits.peek(12) == 1:
                if not one_dimensional:
                    cursor.fail("CCITT RTC tag must be one")
                for _ in range(5):
                    _eol(bits)
                    if k > 0 and bits.read() != 1:
                        cursor.fail("CCITT RTC tag must be one")
                bits.align()
                return
        else:
            one_dimensional = k == 0
            if k < 0 and eol:
                _eol(bits)
                if align:
                    bits.align(zero=True)
        if k > 0 and fax._y == 0 and not one_dimensional:
            cursor.fail("CCITT mixed mode must start with a one-dimensional row")
        if one_dimensional:
            fax.row_1d(bits)
        else:
            fax.row_2d(bits)
        if not endblock and fax._y > rows:
            cursor.fail("CCITT symbol exceeds declared Rows")
        if not endblock and fax._y == rows:
            bits.align(zero=align)
            return


def _lzw_extent(cursor, parameter):
    """Find the real LZW EOD using code widths, without allocating a string table."""
    early = parameter.get("EarlyChange", 1)
    if type(early) is not int or early not in (0, 1):
        cursor.fail("invalid LZW EarlyChange")
    bits, width, size, previous = _Bits(cursor), 9, 258, False
    while True:
        code = bits.read(width)
        if code == 257:
            return
        if code == 256:
            width, size, previous = 9, 258, False
            continue
        if code > size or (code == size and not previous):
            cursor.fail("invalid LZW code")
        if previous and size < 4096:
            size += 1
            if width < 12 and size == (1 << width) - early:
                width += 1
        previous = True


def _filtered_extent(cursor, attrs):
    """Frame the outer encoded representation before admitting an exact payload copy."""
    filters = attrs["Filter"]
    filters = filters if isinstance(filters, list) else [filters]
    if not filters or any(not isinstance(item, PSLiteral) for item in filters):
        cursor.fail("invalid inline Filter")
    if any(_name(item) in ("JPXDecode", "JBIG2Decode") for item in filters):
        cursor.fail("JPX/JBIG2 filters are forbidden in inline images")
    if any(_name(item) in ("DCTDecode", "DCT", "CCITTFaxDecode", "CCF") for item in filters[:-1]):
        cursor.fail("an image codec must be the terminal inline filter")
    parameter = attrs.get("DecodeParms", {})
    if isinstance(parameter, list):
        if len(parameter) != len(filters):
            cursor.fail("inline filter and parameter counts differ")
        parameter = parameter[0]
    parameter = {} if parameter is None else parameter
    if not isinstance(parameter, dict):
        cursor.fail("invalid inline DecodeParms")
    name = _name(filters[0])
    if name in ("DCTDecode", "DCT"):
        _jpeg(cursor)
    elif name in ("CCITTFaxDecode", "CCF"):
        _fax(cursor, parameter)
    elif name in ("ASCIIHexDecode", "AHx"):
        while cursor.take() != 62:
            pass
    elif name in ("ASCII85Decode", "A85"):
        while cursor.take() != 126:
            pass
        if cursor.take() != 62:
            cursor.fail("invalid ASCII85 terminator")
    elif name in ("RunLengthDecode", "RL"):
        while True:
            count = cursor.take()
            if count == 128:
                break
            cursor.skip(count + 1 if count < 128 else 1)
    elif name in ("LZWDecode", "LZW"):
        _lzw_extent(cursor, parameter)
    elif name in ("FlateDecode", "Fl"):
        decoder = zlib.decompressobj()
        while not decoder.eof:
            maximum = min(cursor.context.remaining("decoded_bytes"), cursor.context.limits["payload_bytes"])
            try:
                decoded = decoder.decompress(bytes((cursor.take(),)), maximum + 1)
            except zlib.error:
                cursor.fail("invalid inline Flate framing")
            cursor.context.check("payload_bytes", len(decoded), cursor.location)
            cursor.context.charge("decoded_bytes", len(decoded), cursor.location)
    else:
        cursor.fail("unsupported inline Filter: " + name)
    return filters


def _inline(cursor, start, resolve_color, version, decode):
    """Measure BI through EI and retain only a codec-proven encoded payload."""
    values = {}
    while True:
        key = _token(cursor)
        if key is None:
            cursor.fail("inline image has no ID", start)
        if isinstance(key[2], PSKeyword) and key[2].name == b"ID":
            break
        if not isinstance(key[2], PSLiteral):
            cursor.fail("inline dictionary key must be a name", start)
        item = _token(cursor)
        if item is None or (isinstance(item[2], PSKeyword) and item[2].name not in (b"[", b"<<")):
            cursor.fail("invalid inline dictionary value", start)
        cursor.context.charge("records", 1, cursor.location)
        values[_name(key[2])] = _object(cursor, item)
    attrs = dict(values)
    for short, full in _ALIASES.items():
        if short in values and (version >= (2, 0) or full not in values):
            attrs[full] = values[short]
    size = _dimensions(attrs, resolve_color, cursor)
    if cursor.take() not in _SPACE:
        cursor.fail("ID must have one whitespace separator", start)
    payload_start = cursor.pos
    cursor.payload_start = payload_start
    filters = _filtered_extent(cursor, attrs) if "Filter" in attrs else []
    if not filters:
        cursor.skip(size)
    payload_end = cursor.pos
    cursor.payload_start = None
    cursor.context.check("payload_bytes", payload_end - payload_start, cursor.location)
    payload = cursor.copy(payload_start, payload_end)
    if filters and _name(filters[0]) not in ("DCTDecode", "DCT", "CCITTFaxDecode", "CCF"):
        if decode is None:
            cursor.fail("filtered inline data requires bounded decoder validation", start)
        decoded = decode(attrs, payload, cursor.context, cursor.location)
        if not isinstance(decoded, bytes):
            cursor.fail("bounded decoder must return validated bytes", start)
        terminal = _name(filters[-1])
        if terminal in ("DCTDecode", "DCT", "CCITTFaxDecode", "CCF"):
            inner = _Cursor([{"source": cursor.location, "data": decoded}], cursor.context, cursor.location)
            if terminal in ("DCTDecode", "DCT"):
                _jpeg(inner)
            else:
                parameters = attrs.get("DecodeParms", {})
                parameter = parameters[-1] if isinstance(parameters, list) else parameters
                _fax(inner, parameter or {})
            if inner.pos != inner.length:
                cursor.fail("bytes follow terminal image codec", start)
        elif len(decoded) != size:
            cursor.fail("decoded inline image size differs from its dimensions", start)
    if cursor.pos >= cursor.length or (cursor.byte() not in _SPACE and payload[-1] not in _SPACE):
        cursor.fail("inline payload must be followed by whitespace and EI", start)
    cursor.whitespace()
    ending = _token(cursor)
    if ending is None or not isinstance(ending[2], PSKeyword) or ending[2].name != b"EI":
        cursor.fail("inline payload has no exact following EI", start)
    return {"operator": "BI", "attributes": _plain(attrs), "span": [start, cursor.pos],
            "segments": cursor.spans(start, cursor.pos), "payload": payload,
            "payload_span": [payload_start, payload_end],
            "payload_segments": cursor.spans(payload_start, payload_end)}


def iter_content(segments, context, location, *, resolve_color, version, decode):
    """Yield bounded plain operator records; framing failure raises with an ambiguous span."""
    if (not isinstance(version, tuple) or len(version) != 2
            or any(type(item) is not int for item in version)
            or version not in {(1, minor) for minor in range(8)} | {(2, 0)}):
        raise ContentError("caller must supply a checked PDF version", 0, 0)
    cursor, operands, first, ordinal = _Cursor(segments, context, location), [], None, 0
    while True:
        token = _token(cursor)
        if token is None:
            if operands:
                cursor.fail("operands have no following operator", first)
            return
        start, end, value = token
        if not isinstance(value, PSKeyword) or value.name in (b"[", b"<<"):
            first = start if first is None else first
            context.charge("records", 1, location)
            operands.append(_object(cursor, token))
            continue
        ordinal += 1
        context.charge("records", 1, location)
        if value.name == b"BI":
            if operands:
                cursor.fail("BI has unexpected operands", first)
            cursor.location = location | {"operator": "BI", "operator_ordinal": ordinal, "decoded_offset": start}
            try:
                record = _inline(cursor, start, resolve_color, version, decode)
            except ContentError as exc:
                end = max(cursor.pos, exc.span[1])
                raise ContentError(exc.reason, start, end, segments=cursor.spans(start, end)) from exc
            cursor.location = location
        else:
            if value.name in (b"ID", b"EI", b"]", b">>", b"{", b"}"):
                cursor.fail("unexpected content operator", start)
            span = [start if first is None else first, end]
            record = {"operator": value.name.decode("ascii"), "operands": _plain(operands),
                      "span": span, "segments": cursor.spans(*span)}
            if value.name == b"Do":
                if len(operands) != 1 or not isinstance(operands[0], PSLiteral):
                    cursor.fail("Do requires one resource name", span[0])
                record["resource_name"] = _name(operands[0])
        record["operator_ordinal"] = ordinal
        record["span_basis"] = "decoded-stream"
        yield record
        operands, first = [], None
