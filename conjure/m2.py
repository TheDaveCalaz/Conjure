"""WotLK "MD20" .m2 model file reader/writer (client build 12340, version 264).

All multi-byte values are little-endian. Offsets below are byte offsets
from the start of the file (or, for record arrays, from the start of the
record).
"""

import struct

from .errors import ConjureError

MAGIC_MD20 = b"MD20"
MAGIC_MD21 = b"MD21"
EXPECTED_VERSION = 264

TEXTURE_RECORD_SIZE = 16
ANIM_RECORD_SIZE = 0x40

TEXTURE_TYPE_LABELS = {
    0: "baked path",
    1: "DBC body/skin",
    2: "DBC cape",
    11: "DBC TextureVariation1",
    12: "DBC TextureVariation2",
    13: "DBC TextureVariation3",
}


class M2Texture:
    __slots__ = ("index", "type", "flags", "len_name", "ofs_name", "name")

    def __init__(self, index, type_, flags, len_name, ofs_name, name):
        self.index = index
        self.type = type_
        self.flags = flags
        self.len_name = len_name
        self.ofs_name = ofs_name
        self.name = name

    @property
    def type_label(self):
        return TEXTURE_TYPE_LABELS.get(self.type, "DBC-fed / not baked")


class M2Anim:
    __slots__ = ("anim_id", "sub_id", "flags", "alias_next", "kind")

    def __init__(self, anim_id, sub_id, flags, alias_next):
        self.anim_id = anim_id
        self.sub_id = sub_id
        self.flags = flags
        self.alias_next = alias_next
        if flags & 0x40:
            self.kind = "alias"
        elif not (flags & 0x20):
            self.kind = "external"
        else:
            self.kind = "inline"

    @property
    def tag(self):
        return f"{self.anim_id:04d}-{self.sub_id:02d}"


class M2File:
    def __init__(self, data: bytearray, path: str = None):
        self.data = bytearray(data)
        self.path = path
        self.version_warning = None
        self._parse_header()

    # ----------------------------------------------------------------- load

    @classmethod
    def load(cls, path: str) -> "M2File":
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 4:
            raise ConjureError(f"{path}: file is too small to be a valid M2.")
        magic = bytes(data[0:4])
        if magic == MAGIC_MD21:
            raise ConjureError(
                f"{path}: this is a modern chunked model (MD21), not a WotLK MD20 file. "
                "Run it through MultiConverter to produce a 3.3.5a-compatible M2 first."
            )
        if magic != MAGIC_MD20:
            raise ConjureError(
                f"{path}: not a valid M2 file (expected magic 'MD20', got {magic!r}). Refusing to load."
            )
        return cls(data, path)

    def _parse_header(self):
        d = self.data
        try:
            self.magic = bytes(d[0:4])
            self.version = struct.unpack_from("<I", d, 0x04)[0]
            self.n_animations, self.ofs_animations = struct.unpack_from("<II", d, 0x1C)
            self.n_bones = struct.unpack_from("<I", d, 0x2C)[0]
            self.n_vertices, self.ofs_vertices = struct.unpack_from("<II", d, 0x3C)
            self.n_views = struct.unpack_from("<I", d, 0x44)[0]
            self.n_textures, self.ofs_textures = struct.unpack_from("<II", d, 0x50)
            self.bb_min = struct.unpack_from("<3f", d, 0xA0)
            self.bb_max = struct.unpack_from("<3f", d, 0xAC)
            self.bb_radius = struct.unpack_from("<f", d, 0xB8)[0]
        except struct.error as e:
            raise ConjureError(f"M2 header is truncated or corrupt: {e}")

        if self.version != EXPECTED_VERSION:
            if (self.version & 0xFFFF) == EXPECTED_VERSION:
                self.version_warning = (
                    f"version is 0x{self.version:08X} — high bits set, looks like a "
                    "converter artefact. Treating it as 264 and continuing."
                )
            else:
                self.version_warning = (
                    f"unexpected version {self.version} (0x{self.version:08X}); "
                    f"expected {EXPECTED_VERSION}. Continuing anyway, but this may not be a "
                    "true WotLK 3.3.5a model."
                )

        if self.n_textures and (
            self.ofs_textures + self.n_textures * TEXTURE_RECORD_SIZE > len(d)
        ):
            raise ConjureError("M2 texture array offset/count runs past end of file — file is corrupt or truncated.")
        if self.n_animations and (
            self.ofs_animations + self.n_animations * ANIM_RECORD_SIZE > len(d)
        ):
            raise ConjureError("M2 animation array offset/count runs past end of file — file is corrupt or truncated.")

    # ------------------------------------------------------------- textures

    def read_textures(self):
        textures = []
        for i in range(self.n_textures):
            off = self.ofs_textures + i * TEXTURE_RECORD_SIZE
            ttype, flags, len_name, ofs_name = struct.unpack_from("<4I", self.data, off)
            name = ""
            if ofs_name and len_name:
                if ofs_name + len_name > len(self.data):
                    raise ConjureError(f"texture slot {i}: name string runs past end of file.")
                raw = bytes(self.data[ofs_name : ofs_name + len_name])
                name = raw.split(b"\x00", 1)[0].decode("latin-1", errors="replace")
            textures.append(M2Texture(i, ttype, flags, len_name, ofs_name, name))
        return textures

    def set_texture_path(self, index: int, path: str):
        """Bake `path` into texture slot `index`: type -> 0 (hardcoded), append the
        string at end-of-file (name offsets are absolute, so this is always valid),
        and point lenName/ofsName at it. Existing header/array offsets are untouched."""
        textures = self.read_textures()
        if index < 0 or index >= len(textures):
            raise ConjureError(f"texture slot {index} out of range (this model has {len(textures)} slots).")
        if not path:
            raise ConjureError(f"texture slot {index}: path cannot be empty.")

        name_bytes = path.encode("latin-1") + b"\x00"
        ofs_name = len(self.data)
        self.data += name_bytes

        off = self.ofs_textures + index * TEXTURE_RECORD_SIZE
        struct.pack_into("<4I", self.data, off, 0, textures[index].flags, len(name_bytes), ofs_name)

    # ------------------------------------------------------------ animations

    def read_animations(self):
        anims = []
        for i in range(self.n_animations):
            off = self.ofs_animations + i * ANIM_RECORD_SIZE
            anim_id, sub_id = struct.unpack_from("<HH", self.data, off)
            flags = struct.unpack_from("<I", self.data, off + 0x0C)[0]
            alias_next = struct.unpack_from("<H", self.data, off + 0x3E)[0]
            anims.append(M2Anim(anim_id, sub_id, flags, alias_next))
        return anims

    # ----------------------------------------------------------------- save

    def save(self, path: str) -> bytes:
        data = bytes(self.data)
        with open(path, "wb") as f:
            f.write(data)
        return data


def verify_m2(path: str) -> None:
    """Re-read a written M2 and assert it still parses cleanly. Raises ConjureError on failure."""
    M2File.load(path)
