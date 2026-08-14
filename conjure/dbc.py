"""Generic WDBC reader/writer for WotLK 3.3.5a .dbc files.

Format:
    [20-byte header][records: record_count * record_size][string block: string_block_size]

Header (20 bytes): magic 'WDBC', record_count u32, field_count u32,
record_size u32, string_block_size u32.

Records are packed as `field_count` little-endian uint32 fields
(record_size == field_count * 4 for the DBCs Conjure edits). String fields
hold a uint32 offset into the string block; offset 0 means an empty string.
"""

import struct

from .errors import ConjureError

MAGIC = b"WDBC"
HEADER_FORMAT = "<4sIIII"
HEADER_SIZE = 20


def float_to_u32(x: float) -> int:
    """Reinterpret an IEEE-754 float's bits as a uint32, for storing in a DBC field."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def u32_to_float(u: int) -> float:
    """Reinterpret a uint32 field's bits back into an IEEE-754 float."""
    return struct.unpack("<f", struct.pack("<I", u))[0]


class DBCFile:
    def __init__(self, field_count: int, record_size: int = None):
        self.field_count = field_count
        self.record_size = record_size if record_size is not None else field_count * 4
        if self.record_size != self.field_count * 4:
            raise ConjureError(
                f"record_size ({self.record_size}) != field_count * 4 "
                f"({self.field_count * 4}) — unsupported DBC layout."
            )
        self.records = []  # list[list[int]], each inner list has `field_count` uint32 values
        self.string_block = bytearray(b"\x00")  # offset 0 is always the empty string
        self.path = None

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: str) -> "DBCFile":
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < HEADER_SIZE:
            raise ConjureError(f"{path}: file too small to be a valid DBC.")
        magic, record_count, field_count, record_size, string_block_size = struct.unpack_from(
            HEADER_FORMAT, data, 0
        )
        if magic != MAGIC:
            raise ConjureError(
                f"{path}: not a WDBC file (expected magic 'WDBC', got {magic!r}). "
                "Refusing to load."
            )
        if field_count == 0 or record_size != field_count * 4:
            raise ConjureError(
                f"{path}: record_size ({record_size}) does not match "
                f"field_count * 4 ({field_count * 4}) — unsupported or corrupt DBC."
            )
        expected_len = HEADER_SIZE + record_count * record_size + string_block_size
        if len(data) < expected_len:
            raise ConjureError(
                f"{path}: file is truncated — header declares {expected_len} bytes, "
                f"file only has {len(data)}."
            )

        obj = cls(field_count, record_size)
        fmt = f"<{field_count}I"
        offset = HEADER_SIZE
        for _ in range(record_count):
            rec = list(struct.unpack_from(fmt, data, offset))
            obj.records.append(rec)
            offset += record_size

        str_start = offset
        str_end = str_start + string_block_size
        obj.string_block = bytearray(data[str_start:str_end])
        if not obj.string_block:
            obj.string_block = bytearray(b"\x00")
        obj.path = path
        return obj

    # ------------------------------------------------------------- strings

    def get_string(self, offset: int) -> str:
        if offset == 0:
            return ""
        if offset < 0 or offset >= len(self.string_block):
            raise ConjureError(f"string offset {offset} out of range of string block.")
        end = self.string_block.index(b"\x00", offset)
        return self.string_block[offset:end].decode("latin-1")

    def add_string(self, s: str) -> int:
        """Append a new string to the end of the string block; return its offset.
        Never touches or rewrites any existing string."""
        if s == "":
            return 0
        offset = len(self.string_block)
        self.string_block += s.encode("latin-1") + b"\x00"
        return offset

    # ------------------------------------------------------------- records

    def add_record(self, fields):
        fields = list(fields)
        if len(fields) != self.field_count:
            raise ConjureError(
                f"record has {len(fields)} fields, expected {self.field_count}."
            )
        self.records.append(fields)

    def find_by_id(self, record_id: int, id_field: int = 0):
        for rec in self.records:
            if rec[id_field] == record_id:
                return rec
        return None

    # ---------------------------------------------------------------- save

    def to_bytes(self) -> bytes:
        record_count = len(self.records)
        string_block_size = len(self.string_block)
        header = struct.pack(
            HEADER_FORMAT, MAGIC, record_count, self.field_count, self.record_size, string_block_size
        )
        fmt = f"<{self.field_count}I"
        body = bytearray()
        for rec in self.records:
            if len(rec) != self.field_count:
                raise ConjureError("internal error: record field count mismatch before write.")
            body += struct.pack(fmt, *[v & 0xFFFFFFFF for v in rec])
        return header + bytes(body) + bytes(self.string_block)

    def save(self, path: str) -> bytes:
        data = self.to_bytes()
        with open(path, "wb") as f:
            f.write(data)
        return data


def verify_dbc(path: str) -> None:
    """Re-read a written DBC and assert its header is internally consistent.
    Raises ConjureError on any mismatch."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER_SIZE or data[0:4] != MAGIC:
        raise ConjureError(f"{path}: written file does not start with WDBC magic.")
    magic, record_count, field_count, record_size, string_block_size = struct.unpack_from(
        HEADER_FORMAT, data, 0
    )
    if record_size != field_count * 4:
        raise ConjureError(f"{path}: record_size != field_count * 4 after write.")
    expected_len = HEADER_SIZE + record_count * record_size + string_block_size
    if len(data) != expected_len:
        raise ConjureError(
            f"{path}: file length {len(data)} does not match header-declared "
            f"length {expected_len} after write."
        )
    # Full structural re-parse, including every string offset.
    reloaded = DBCFile.load(path)
    if len(reloaded.records) != record_count:
        raise ConjureError(f"{path}: record_count header says {record_count}, parsed {len(reloaded.records)}.")
    if len(reloaded.string_block) != string_block_size:
        raise ConjureError(
            f"{path}: string_block_size header says {string_block_size}, "
            f"actual block is {len(reloaded.string_block)} bytes."
        )
