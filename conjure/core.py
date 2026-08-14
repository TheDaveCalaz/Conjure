"""Business logic behind each of Conjure's four tabs.

Kept separate from the GUI so it can be unit-tested without tkinter, and so
the GUI layer stays a thin wrapper around these functions.

Rules enforced everywhere in this module:
  - never overwrite an input file; always write to a new output path.
  - always leave a .bak copy of the original input next to the output.
  - after every write, re-read the output and verify it before reporting
    success; on verification failure, delete the bad output and raise.
"""

import os
import shutil

from .dbc import DBCFile, float_to_u32, verify_dbc
from .errors import ConjureError
from .m2 import M2File, verify_m2
from .skin import read_skin_vertex_span

DEFAULT_DISPLAY_ID_FLOOR = 90013
DEFAULT_MODEL_DATA_FLOOR = 91014

MODEL_DATA_FIELD_COUNT = 28
DISPLAY_INFO_FIELD_COUNT = 16


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def default_output_dir(input_path: str) -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(input_path)) or ".", "conjure_output")
    os.makedirs(d, exist_ok=True)
    return d


def backup_input(input_path: str, output_path: str) -> str:
    """Copy the untouched original `input_path` to `<output_path>.bak`."""
    bak_path = output_path + ".bak"
    shutil.copyfile(input_path, bak_path)
    return bak_path


def write_verified(output_path: str, data: bytes, verify_fn) -> str:
    """Write `data` to `output_path`, then call verify_fn(output_path).
    If verification raises, delete the bad output and re-raise."""
    with open(output_path, "wb") as f:
        f.write(data)
    try:
        verify_fn(output_path)
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise
    return output_path


def next_free_id(dbc: DBCFile, floor: int, id_field: int = 0) -> int:
    existing = {rec[id_field] for rec in dbc.records}
    candidate = floor
    while candidate in existing:
        candidate += 1
    return candidate


# --------------------------------------------------------------------------
# Tab 1 — Inspect M2
# --------------------------------------------------------------------------

def inspect_m2(m2_path: str) -> dict:
    m2 = M2File.load(m2_path)
    textures = m2.read_textures()
    anims = m2.read_animations()

    texture_report = [
        {
            "index": t.index,
            "type": t.type,
            "type_label": t.type_label,
            "name": t.name if t.name else "(empty / DBC-fed)",
        }
        for t in textures
    ]

    external = [a.tag for a in anims if a.kind == "external"]
    alias = [a.tag for a in anims if a.kind == "alias"]
    inline_count = sum(1 for a in anims if a.kind == "inline")

    lod_warning = None
    base, _ext = os.path.splitext(m2_path)
    skin_path = base + "00.skin"
    if os.path.exists(skin_path):
        span = read_skin_vertex_span(skin_path)
        if span and m2.n_vertices > span * 1.5:
            lod_warning = (
                f"M2 declares {m2.n_vertices} vertices but {os.path.basename(skin_path)} "
                f"only spans {span} of them. This looks like an un-merged multi-LOD export — "
                "re-convert the model (e.g. with MultiConverter/wow.export merge) instead of "
                "just packing it."
            )

    return {
        "path": m2_path,
        "magic": m2.magic.decode("ascii", errors="replace"),
        "version": m2.version,
        "version_warning": m2.version_warning,
        "n_bones": m2.n_bones,
        "bone_warning": (
            f"{m2.n_bones} bones exceeds the WotLK per-draw bone ceiling of 256."
            if m2.n_bones > 256
            else None
        ),
        "n_vertices": m2.n_vertices,
        "n_views": m2.n_views,
        "n_textures": m2.n_textures,
        "textures": texture_report,
        "anim_inline_count": inline_count,
        "anim_alias_tags": alias,
        "anim_external_tags": external,
        "lod_warning": lod_warning,
    }


# --------------------------------------------------------------------------
# Tab 2 — Bake Textures
# --------------------------------------------------------------------------

def bake_textures(m2_path: str, slot_paths: dict, output_dir: str = None) -> dict:
    """slot_paths: {texture_slot_index: baked_path_string}"""
    m2 = M2File.load(m2_path)
    n = m2.n_textures
    for idx in slot_paths:
        if idx < 0 or idx >= n:
            raise ConjureError(f"texture slot {idx} out of range (this model has {n} slots).")

    output_dir = output_dir or default_output_dir(m2_path)
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(m2_path))[0]
    output_path = os.path.join(output_dir, f"{base}_baked.m2")

    bak_path = backup_input(m2_path, output_path)

    for idx, path in slot_paths.items():
        m2.set_texture_path(idx, path)

    def verify(p):
        verify_m2(p)
        check = M2File.load(p)
        textures = check.read_textures()
        for idx, path in slot_paths.items():
            if textures[idx].type != 0 or textures[idx].name != path:
                raise ConjureError(f"verification failed: slot {idx} does not read back as baked.")

    write_verified(output_path, bytes(m2.data), verify)

    check = M2File.load(output_path)
    verification = [
        {"index": t.index, "type": t.type, "type_label": t.type_label, "name": t.name}
        for t in check.read_textures()
    ]

    return {
        "output_path": output_path,
        "backup_path": bak_path,
        "verification": verification,
    }


# --------------------------------------------------------------------------
# Tab 3 — Build DBC Rows
# --------------------------------------------------------------------------

def build_dbc_rows(
    model_data_path: str,
    display_info_path: str,
    model_name: str,
    output_dir: str = None,
    display_floor: int = DEFAULT_DISPLAY_ID_FLOOR,
    modeldata_floor: int = DEFAULT_MODEL_DATA_FLOOR,
    creature_entry: str = "<entry>",
) -> dict:
    if not model_name or any(c in model_name for c in '\\/:*?"<>|'):
        raise ConjureError(f"'{model_name}' is not a valid bare model folder/name (no path separators).")

    md = DBCFile.load(model_data_path)
    di = DBCFile.load(display_info_path)

    if md.field_count != MODEL_DATA_FIELD_COUNT:
        raise ConjureError(
            f"{model_data_path}: expected {MODEL_DATA_FIELD_COUNT} fields for "
            f"CreatureModelData.dbc, found {md.field_count}. Wrong file or wrong client build?"
        )
    if di.field_count != DISPLAY_INFO_FIELD_COUNT:
        raise ConjureError(
            f"{display_info_path}: expected {DISPLAY_INFO_FIELD_COUNT} fields for "
            f"CreatureDisplayInfo.dbc, found {di.field_count}. Wrong file or wrong client build?"
        )

    model_id = next_free_id(md, modeldata_floor)
    display_id = next_free_id(di, display_floor)

    model_path_str = f"Creature\\{model_name}\\{model_name}.mdx"
    ofs_name = md.add_string(model_path_str)

    F = float_to_u32
    md_record = [
        model_id,           # [0] ID
        0,                  # [1] Flags
        ofs_name,           # [2] ModelName
        1,                  # [3] SizeClass
        F(1.0),             # [4] ModelScale
        1,                  # [5] BloodID
        0,                  # [6] FootprintTextureID
        F(12.0),            # [7] FootprintTextureLength
        F(10.0),            # [8] FootprintTextureWidth
        F(1.0),             # [9] FootprintParticleScale
        0, 0, 0, 0,         # [10-13]
        F(0.6),             # [14] CollisionWidth
        F(2.2),             # [15] CollisionHeight
        F(1.87),            # [16] MountHeight
        F(-1.0), F(-1.0), F(0.0),  # [17-19] GeoBoxMin xyz
        F(1.0), F(1.0), F(2.5),    # [20-22] GeoBoxMax xyz
        F(1.0),             # [23] WorldEffectScale
        F(1.0),             # [24] AttachedEffectScale
        0, 0, 0,            # [25-27]
    ]
    md.add_record(md_record)

    di_record = [
        display_id,   # [0] ID
        model_id,     # [1] ModelID
        0,            # [2] SoundID
        0,            # [3] ExtendedDisplayInfoID
        F(1.0),       # [4] CreatureModelScale
        255,          # [5] CreatureModelAlpha
        0,            # [6] TextureVariation1
        0,            # [7] TextureVariation2
        0,            # [8] TextureVariation3
        0,            # [9] PortraitTextureName
        1,            # [10] BloodLevel
        0,            # [11] BloodID
        0,            # [12] NPCSoundID
        0,            # [13] ParticleColorID
        0,            # [14] CreatureGeosetData
        0,            # [15] ObjectEffectPackageID
    ]
    di.add_record(di_record)

    output_dir = output_dir or default_output_dir(model_data_path)
    os.makedirs(output_dir, exist_ok=True)
    md_out = os.path.join(output_dir, "CreatureModelData_edited.dbc")
    di_out = os.path.join(output_dir, "CreatureDisplayInfo_edited.dbc")

    md_bak = backup_input(model_data_path, md_out)
    di_bak = backup_input(display_info_path, di_out)

    write_verified(md_out, md.to_bytes(), verify_dbc)
    write_verified(di_out, di.to_bytes(), verify_dbc)

    check_md = DBCFile.load(md_out)
    check_di = DBCFile.load(di_out)
    md_rec = check_md.find_by_id(model_id)
    di_rec = check_di.find_by_id(display_id)
    if md_rec is None or di_rec is None or di_rec[1] != model_id:
        raise ConjureError("post-write verification failed: DisplayInfo -> ModelData chain does not resolve.")
    resolved_name = check_md.get_string(md_rec[2])

    sql = (
        f"UPDATE creature_template_model SET CreatureDisplayID={display_id} "
        f"WHERE CreatureID={creature_entry}; -- check display-sharing before running this"
    )

    return {
        "model_id": model_id,
        "display_id": display_id,
        "model_name": resolved_name,
        "model_data_output": md_out,
        "model_data_backup": md_bak,
        "display_info_output": di_out,
        "display_info_backup": di_bak,
        "message": f"DisplayID {display_id} → ModelData {model_id} → {resolved_name}; chain resolves ✓",
        "reminder": "Place BOTH edited DBCs into patch-b (DBFilesClient\\) AND the server dbc\\ folder.",
        "sql": sql,
    }


# --------------------------------------------------------------------------
# Tab 4 — Set TextureVariations (community-port mode)
# --------------------------------------------------------------------------

def set_texture_variations(
    display_info_path: str,
    display_id: int,
    tex1: str,
    tex2: str,
    tex3: str,
    geoset: int,
    output_dir: str = None,
) -> dict:
    di = DBCFile.load(display_info_path)
    if di.field_count != DISPLAY_INFO_FIELD_COUNT:
        raise ConjureError(
            f"{display_info_path}: expected {DISPLAY_INFO_FIELD_COUNT} fields for "
            f"CreatureDisplayInfo.dbc, found {di.field_count}. Wrong file or wrong client build?"
        )

    idx = None
    for i, rec in enumerate(di.records):
        if rec[0] == display_id:
            idx = i
            break
    if idx is None:
        raise ConjureError(f"DisplayID {display_id} was not found in {os.path.basename(display_info_path)}.")

    o1 = di.add_string(tex1) if tex1 else 0
    o2 = di.add_string(tex2) if tex2 else 0
    o3 = di.add_string(tex3) if tex3 else 0

    di.records[idx][6] = o1
    di.records[idx][7] = o2
    di.records[idx][8] = o3
    di.records[idx][14] = geoset & 0xFFFFFFFF

    output_dir = output_dir or default_output_dir(display_info_path)
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "CreatureDisplayInfo_edited.dbc")
    bak_path = backup_input(display_info_path, out_path)

    write_verified(out_path, di.to_bytes(), verify_dbc)

    check = DBCFile.load(out_path)
    rec = check.find_by_id(display_id)
    if rec is None:
        raise ConjureError("post-write verification failed: DisplayID row went missing.")

    resolved = {
        "DisplayID": display_id,
        "TextureVariation1": check.get_string(rec[6]),
        "TextureVariation2": check.get_string(rec[7]),
        "TextureVariation3": check.get_string(rec[8]),
        "CreatureGeosetData": rec[14],
    }

    return {
        "output_path": out_path,
        "backup_path": bak_path,
        "resolved": resolved,
    }
