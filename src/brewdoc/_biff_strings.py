"""Bounded BIFF8 strings with native CONTINUE record boundaries."""

from __future__ import annotations

from collections.abc import Sequence


class _Segments:
    """Keep record boundaries so only continued character data consumes an encoding byte."""

    def __init__(self, segments: Sequence[bytes], context, location: dict):
        if not segments:
            raise ValueError("missing BIFF string segments")
        context.charge("records", len(segments), location)
        total = 0
        for segment in segments:
            if not isinstance(segment, bytes):
                raise ValueError("BIFF string segments must contain bytes")
            total += len(segment)
            context.check("source_bytes", total, location)
        self.segments, self.context, self.location = segments, context, location
        self.index, self.offset, self.remaining = 0, 0, total

    def _next(self) -> None:
        if self.index + 1 >= len(self.segments):
            raise ValueError("truncated continued BIFF string")
        self.index += 1
        self.offset = 0

    def _ready(self) -> None:
        while self.offset == len(self.segments[self.index]):
            self._next()

    def fixed(self, length: int) -> bytes:
        """Require a fixed-field header to fit in its original record."""
        self._ready()
        if length > len(self.segments[self.index]) - self.offset:
            raise ValueError("BIFF string fixed fields split across records")
        start = self.offset
        self.offset += length
        self.remaining -= length
        return self.segments[self.index][start:self.offset]

    def skip(self, length: int) -> None:
        """Skip formatting and phonetic bytes without treating them as character continuations."""
        if length < 0 or length > self.remaining:
            raise ValueError("truncated BIFF string formatting or extension")
        self.context.check("payload_bytes", length, self.location)
        while length:
            self._ready()
            size = min(length, len(self.segments[self.index]) - self.offset)
            self.offset += size
            self.remaining -= size
            length -= size

    def string(self, *, rich: bool) -> str:
        """Decode UTF-16 code units across compressed and uncompressed segments."""
        self.context.charge("records", 1, self.location)
        self._ready()
        header_start = self.offset
        header = self.fixed(3)
        count, flags = int.from_bytes(header[:2], "little"), header[2]
        if not rich and count > 32767:
            raise ValueError("BIFF cached string exceeds 32767 characters")
        header_size = 3 + (2 if rich and flags & 8 else 0) + (4 if rich and flags & 4 else 0)
        if header_start + header_size > len(self.segments[self.index]):
            raise ValueError("BIFF string fixed fields split across records")
        runs = int.from_bytes(self.fixed(2), "little") if rich and flags & 8 else 0
        extension = int.from_bytes(self.fixed(4), "little", signed=True) if rich and flags & 4 else 0
        if extension < 0:
            raise ValueError("negative BIFF string extension length")
        self.context.check("payload_bytes", count * 2, self.location)
        self.context.charge("decoded_bytes", count * 4, self.location)
        encoded, wide, left = bytearray(), bool(flags & 1), count
        while left:
            if self.offset == len(self.segments[self.index]):
                self._next()
                if not self.segments[self.index]:
                    raise ValueError("missing BIFF continuation encoding flag")
                wide = bool(self.fixed(1)[0] & 1)
            width = 2 if wide else 1
            available = len(self.segments[self.index]) - self.offset
            if not available:
                continue
            if available < width:
                raise ValueError("BIFF UTF-16 character split across records")
            units = min(left, available // width)
            block = self.fixed(units * width)
            encoded.extend(block if wide else block.decode("latin1").encode("utf-16le"))
            left -= units
        text = encoded.decode("utf-16le")
        self.skip(runs * 4 + extension)
        return text

    def finish(self) -> None:
        """Refuse unvisited source bytes instead of silently accepting an incomplete table."""
        if self.remaining:
            raise ValueError("unvisited BIFF string bytes: %d" % self.remaining)


def parse_sst(segments: Sequence[bytes], context, location: dict) -> tuple[str, ...]:
    """Decode the declared SST strings from its payload and following CONTINUE payloads."""
    reader = _Segments(segments, context, location)
    header = reader.fixed(8)
    total = int.from_bytes(header[:4], "little", signed=True)
    unique = int.from_bytes(header[4:], "little", signed=True)
    if total < 0 or unique < 0 or unique > total:
        raise ValueError("invalid BIFF shared string counts")
    context.check("records", context.consumed["records"] + unique, location)
    strings = tuple(reader.string(rich=True) for _ in range(unique))
    reader.finish()
    return strings


def parse_xl_unicode_segments(segments: Sequence[bytes], context, location: dict) -> str:
    """Decode a cached STRING payload and its character continuations without repair."""
    reader = _Segments(segments, context, location)
    text = reader.string(rich=False)
    reader.finish()
    return text
