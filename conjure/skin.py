"""Minimal .skin reader — just enough to compute the vertex span for the
multi-LOD sanity check on Tab 1.

Layout: 0x00 magic 'SKIN' (4 bytes); 0x04 nIndices u32; 0x08 ofsIndices u32;
indices are u16 vertex indices located at ofsIndices.
"""

import struct

from .errors import ConjureError

MAGIC = b"SKIN"


def read_skin_vertex_span(path: str) -> int:
    """Return (max vertex index referenced by this skin) + 1, i.e. how many
    vertices this LOD skin actually uses."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12 or data[0:4] != MAGIC:
        raise ConjureError(f"{path}: not a valid .skin file (expected 'SKIN' magic).")
    n_indices, ofs_indices = struct.unpack_from("<II", data, 0x04)
    if n_indices == 0:
        return 0
    end = ofs_indices + n_indices * 2
    if end > len(data):
        raise ConjureError(f"{path}: index array runs past end of file — file is truncated or corrupt.")
    indices = struct.unpack_from(f"<{n_indices}H", data, ofs_indices)
    return max(indices) + 1
