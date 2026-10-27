"""Bounded, passive MS-CFB allocation and directory inspection."""

from __future__ import annotations

import struct

from brewdoc._inspection_common import InspectionLimit

FREE, END, FAT, DIF = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")


def read_compound(data, context, document, *, location=None):
    """Inventory allocated entries in directory order, retaining exact stream bytes."""
    base = dict(location or {"kind": "compound"})
    result = []

    def problem(reason, where=base):
        context.problem(document, ("inventory", "macros", "cells", "formulas"), reason, where)

    try:
        context.check("source_bytes", len(data), base)
        reader = _Compound(data, context, base)
        entries = reader.entries()
        paths = reader.paths(entries, problem)
        root = entries[0]
        mini_data, _ = reader.chain(root["start"], reader.fat, reader.sector_size,
                                    root["size"], "root mini stream")
        for entry in entries:
            if entry["kind"] == "empty":
                continue
            path = paths.get(entry["entry_id"])
            if path is None:
                path = entry["name"] if reader.path_limited else "@orphan/" + str(entry["entry_id"])
            where = dict(base, kind="compound", entry_id=entry["entry_id"],
                         path=path)
            if reader.path_limited and entry["entry_id"] not in paths:
                where["hierarchy_available"] = False
            stream = None
            reason = None
            artifact = None
            component = None
            try:
                if entry["kind"] == "stream":
                    small = entry["size"] < 4096
                    stream, sectors = reader.chain(entry["start"], reader.mini_fat if small else reader.fat,
                                                   64 if small else reader.sector_size,
                                                   entry["size"], "stream/%d" % entry["entry_id"],
                                                   mini_data if small else None)
                    where["sectors"] = sectors
                    where["sector_kind"] = "mini" if small else "regular"
                    where["sector_size"] = 64 if small else reader.sector_size
                artifact = context.payload(document, "compound_stream", where, stream,
                                           "application/octet-stream") if stream is not None else None
                component = context.component(document, "compound_" + entry["kind"], where, data=stream,
                                  artifact=artifact, disposition="opaque" if stream is not None else "extracted",
                                  name=entry["name"], declared_size=entry["size"])
            except (ValueError, InspectionLimit) as error:
                reason = str(error)
                stream = None
                problem(reason, where)
                if not isinstance(error, InspectionLimit):
                    component = context.component(document, "compound_stream", where, disposition="unreadable",
                                      reason=reason, name=entry["name"], declared_size=entry["size"])
            result.append({"name": entry["name"], "path": where["path"], "entry_id": entry["entry_id"],
                           "kind": entry["kind"], "data": stream, "location": where, "reason": reason,
                           "artifact": artifact, "component": component})
        reader.account(problem)
        context.cover(document, "inventory", "complete", "CFB directory and allocation chains inspected")
    except (ValueError, InspectionLimit) as error:
        problem(str(error))
    return tuple(result)


class _Compound:
    """Validate allocation before materializing bounded stream bytes."""

    def __init__(self, data, context, location):
        self.data, self.context, self.location = data, context, location
        self.used, self.mini_used = {}, {}
        if len(data) < 512 or data[:8] != SIGNATURE:
            raise ValueError("invalid CFB signature or truncated header")
        version, order, shift, mini_shift = struct.unpack_from("<4H", data, 26)
        if (version, shift) not in ((3, 9), (4, 12)) or order != 65534 or mini_shift != 6:
            raise ValueError("unsupported CFB version, byte order or sector shifts")
        self.version, self.sector_size = version, 1 << shift
        if len(data) % self.sector_size or len(data) < 3 * self.sector_size:
            raise ValueError("truncated CFB sector")
        self.count = len(data) // self.sector_size - 1
        self.directory_count, fat_count, self.directory_start, _, cutoff, mini_start, mini_count, dif_start, dif_count = struct.unpack_from("<9I", data, 40)
        if cutoff != 4096 or (version == 3 and self.directory_count):
            raise ValueError("invalid CFB cutoff or directory count")
        if any(data[34:40]) or any(data[8:24]) or any(data[512:self.sector_size]):
            raise ValueError("nonzero reserved CFB header bytes")
        if fat_count > self.count or dif_count > self.count or mini_count > self.count:
            raise ValueError("CFB allocation count exceeds sector count")
        difat = [item for item in struct.unpack_from("<109I", data, 76) if item != FREE]
        seen = set()
        current = dif_start
        for _ in range(dif_count):
            if current in seen:
                raise ValueError("CFB DIFAT cycle")
            seen.add(current)
            block = self.sector(current, "DIFAT")
            context.charge("decoded_bytes", self.sector_size, location)
            values = struct.unpack("<%dI" % (self.sector_size // 4), block)
            difat.extend(item for item in values[:-1] if item != FREE)
            current = values[-1]
        if current != END or len(difat) != fat_count or len(set(difat)) != len(difat):
            raise ValueError("invalid CFB DIFAT count, terminator or duplicate FAT sector")
        self.fat = []
        for sector in difat:
            block = self.sector(sector, "FAT")
            context.charge("decoded_bytes", self.sector_size, location)
            self.fat.extend(struct.unpack("<%dI" % (self.sector_size // 4), block))
        if len(self.fat) < self.count:
            raise ValueError("CFB FAT does not account for all physical sectors")
        if any(self.fat[item] != FAT for item in difat) or any(self.fat[item] != DIF for item in seen):
            raise ValueError("invalid CFB FAT/DIFAT sector markers")
        raw, _ = self.chain(mini_start, self.fat, self.sector_size,
                            mini_count * self.sector_size, "mini FAT")
        self.mini_fat = list(struct.unpack("<%dI" % (len(raw) // 4), raw))

    def sector(self, number, owner):
        """Admit one physical sector, rejecting overlapping allocations."""
        if not 0 <= number < self.count:
            raise ValueError("CFB sector outside file: %d" % number)
        if number in self.used:
            raise ValueError("CFB cross-linked sector %d: %s / %s" % (number, self.used[number], owner))
        self.context.charge("records", 1, self.location)
        self.used[number] = owner
        offset = (number + 1) * self.sector_size
        return memoryview(self.data)[offset:offset + self.sector_size]

    def chain(self, start, table, size, length, owner, mini_data=None):
        """Walk through the terminator, requiring exact declared chain length."""
        self.context.check("payload_bytes", length, self.location) if length is not None else None
        expected = (length + size - 1) // size if length is not None else None
        sectors, seen = [], set()
        current = start
        while current != END:
            if current in seen:
                raise ValueError("CFB allocation cycle in " + owner)
            if not 0 <= current < len(table):
                raise ValueError("CFB allocation index outside table in " + owner)
            if expected is not None and len(sectors) >= expected:
                raise ValueError("CFB chain exceeds declared size in " + owner)
            seen.add(current)
            if mini_data is None:
                self.sector(current, owner)
            else:
                if (current + 1) * size > len(mini_data) or current in self.mini_used:
                    raise ValueError("CFB mini sector out of bounds or cross-linked")
                self.context.charge("records", 1, self.location)
                self.mini_used[current] = owner
            self.context.charge("decoded_bytes", size, self.location)
            sectors.append(current)
            current = table[current]
        if expected is not None and len(sectors) != expected:
            raise ValueError("truncated CFB chain in " + owner)
        total = len(sectors) * size if length is None else length
        self.context.check("payload_bytes", total, self.location)
        source = self.data if mini_data is None else mini_data
        output = bytearray()
        for number in sectors:
            offset = (number + 1) * size if mini_data is None else number * size
            output.extend(memoryview(source)[offset:offset + min(size, total - len(output))])
        return bytes(output), sectors

    def entries(self):
        """Read every directory slot, including unreachable allocated entries."""
        raw, sectors = self.chain(self.directory_start, self.fat, self.sector_size, None, "directory")
        if self.version == 4 and len(sectors) != self.directory_count:
            raise ValueError("CFB directory sector count mismatch")
        entries = []
        for offset in range(0, len(raw), 128):
            self.context.charge("records", 1, self.location)
            block = raw[offset:offset + 128]
            length, kind = struct.unpack_from("<HB", block, 64)
            if kind not in (0, 1, 2, 5):
                raise ValueError("invalid CFB directory object type")
            if kind and (length < 2 or length > 64 or length % 2 or block[length - 2:length] != b"\0\0"):
                raise ValueError("invalid CFB directory name length or terminator")
            try:
                name = block[:max(0, length - 2)].decode("utf-16le") if kind else ""
            except UnicodeError as error:
                raise ValueError("invalid CFB directory Unicode name") from error
            left, right, child = struct.unpack_from("<3I", block, 68)
            start, length = struct.unpack_from("<IQ", block, 116)
            if self.version == 3:
                length &= 0xFFFFFFFF
            entries.append(dict(entry_id=offset // 128, name=name, kind={0: "empty", 1: "storage", 2: "stream", 5: "root"}[kind],
                                left=left, right=right, child=child, start=start, size=length))
        if not entries or entries[0]["kind"] != "root":
            raise ValueError("missing CFB root directory")
        return entries

    def paths(self, entries, problem):
        """Resolve directory trees iteratively while retaining orphans separately."""
        self.path_limited = False
        paths = {0: ""}
        stack = [(entries[0]["child"], "", 0)]
        while stack:
            number, parent, parent_size = stack.pop()
            if number == FREE:
                continue
            if number >= len(entries) or number in paths or entries[number]["kind"] in ("empty", "root"):
                problem("invalid CFB directory reference, duplicate or cycle")
                continue
            entry = entries[number]
            where = dict(self.location, kind="compound", entry_id=number)
            name_size = sum(1 if ord(char) < 128 else 2 if ord(char) < 2048 else 3 if ord(char) < 65536 else 4
                            for char in entry["name"])
            path_size = parent_size + name_size + (1 if parent else 0)
            try:
                self.context.check("report_bytes", self.context.consumed["report_bytes"] + path_size, where)
                self.context.charge("records", 1, where)
                self.context.charge("decoded_bytes", path_size, where)
            except InspectionLimit as error:
                self.path_limited = True
                problem(str(error), where)
                return paths
            path = "/".join((parent, entry["name"])) if parent else entry["name"]
            paths[number] = path
            stack.extend(((entry["right"], parent, parent_size), (entry["left"], parent, parent_size)))
            if entry["kind"] == "storage":
                stack.append((entry["child"], path, path_size))
            elif entry["child"] != FREE:
                problem("CFB stream has a child directory")
        for entry in entries:
            if entry["kind"] != "empty" and entry["entry_id"] not in paths:
                problem("orphan CFB directory entry %d" % entry["entry_id"])
        return paths

    def account(self, problem):
        """Expose allocated physical or mini sectors with no owning chain."""
        for number in range(self.count):
            if number not in self.used and self.fat[number] != FREE:
                problem("orphan allocated CFB sector %d" % number)
        for number, target in enumerate(self.mini_fat):
            if target != FREE and number not in self.mini_used:
                problem("orphan allocated CFB mini sector %d" % number)
