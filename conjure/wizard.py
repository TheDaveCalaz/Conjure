"""Guided-workflow logic behind the "Port a Model (Guided)" tab.

This module is the state-driven layer on top of conjure/core.py — it does
not implement any new binary format logic itself. It decides WHAT to do
(bake vs. TextureVariations, halt vs. proceed) and produces plain-English,
actionable messaging, but every actual file read/write still goes through
core.py's existing, already-verified functions.

Core principle: Conjure does deterministic binary work only. It cannot
convert models or merge un-merged LODs. When it detects a state it can't
fix itself, it halts and returns a "guided_fix" panel describing exactly
what to do next in the right external tool, plus how to resume here.
"""

import os

from .core import (
    build_dbc_rows,
    inspect_m2,
)
from .errors import ConjureError
from .m2 import M2File

BAKED_TYPE = 0
TEXTURE_VARIATION_TYPES = (11, 12, 13)


# --------------------------------------------------------------------------
# Stage 0 — model + folder name, sibling file detection
# --------------------------------------------------------------------------

def guess_model_name(m2_path: str) -> str:
    return os.path.splitext(os.path.basename(m2_path))[0]


def detect_siblings(m2_path: str) -> dict:
    folder = os.path.dirname(os.path.abspath(m2_path))
    base = os.path.splitext(os.path.basename(m2_path))[0].lower()
    try:
        entries = os.listdir(folder)
    except OSError:
        entries = []
    skins = sorted(e for e in entries if e.lower().startswith(base) and e.lower().endswith(".skin"))
    anims = sorted(e for e in entries if e.lower().startswith(base) and e.lower().endswith(".anim"))
    blps = sorted(e for e in entries if e.lower().endswith(".blp"))
    return {"folder": folder, "skins": skins, "anims": anims, "blps": blps}


# --------------------------------------------------------------------------
# Stage 1 — readiness check (the gate)
# --------------------------------------------------------------------------

def _md21_guided_fix():
    return {
        "title": "This model hasn't been converted yet",
        "steps": [
            "This is a modern chunked model (MD21). Conjure can only read WotLK MD20 files, and "
            "it can't do the conversion itself — that's a separate tool's job.",
            "Check the model's source folder for a .skel file.",
            "If there IS a .skel file: convert it with MultiConverter Shadowlands-Wotlk.exe "
            "(listfile.csv must sit next to the exe, or it won't open).",
            "If there is NO .skel file: convert it with MultiConverter Legion-Wotlk.exe instead.",
            "Come back here and load the NEW converted .m2 (it should now start with MD20).",
        ],
        "next_action": "Browse to the converted .m2 and click \"Re-check model\".",
        "allow_override": False,
    }


def _multi_lod_guided_fix(report):
    return {
        "title": "This model isn't ready — its LODs aren't merged",
        "steps": [
            f"Conjure can't merge LODs itself, but here's how to fix it: the .m2 declares "
            f"{report['n_vertices']} vertices, while its skin file only covers a fraction of that "
            f"— a sign the LOD meshes were exported separately and never merged into one.",
            "Open the RAW wow.export .m2 (before any conversion) in MultiConverter.",
            "If the model folder contains a .skel file: use MultiConverter Shadowlands-Wotlk.exe "
            "(listfile.csv must sit next to the exe).",
            "If there is NO .skel file: use MultiConverter Legion-Wotlk.exe instead.",
            "Convert it, then load the NEW converted .m2 back into Conjure here and press \"Re-check model\".",
            "If it STILL mismatches after a clean convert, the LODs genuinely need collapsing by hand "
            "— it's usually faster to search the WoW modding community for a pre-made 3.3.5a port of "
            "this character than to fight it.",
        ],
        "next_action": "Convert (or find a pre-made port), then browse to the result and click \"Re-check model\".",
        "allow_override": True,
        "override_warning": (
            "Packing this model anyway will very likely crash the client or render broken geometry. "
            "Only continue if you understand and accept that."
        ),
    }


def _generic_load_failure_fix(message):
    return {
        "title": "Conjure couldn't read this file",
        "steps": [message],
        "next_action": "Fix the file, or choose a different one, then click \"Re-check model\".",
        "allow_override": False,
    }


def readiness_check(m2_path: str, allow_lod_override: bool = False) -> dict:
    """Run Stage 1. Returns a dict with per-check verdicts and, if the model
    is not portable, a 'guided_fix' panel describing exactly how to fix it
    (never a bare error)."""
    try:
        M2File.load(m2_path)
    except ConjureError as e:
        message = str(e)
        is_md21 = "MD21" in message
        return {
            "ok": False,
            "halted": True,
            "checks": [{"name": "Format", "status": "✗", "detail": message}],
            "report": None,
            "guided_fix": _md21_guided_fix() if is_md21 else _generic_load_failure_fix(message),
        }

    report = inspect_m2(m2_path)
    checks = [{"name": "Format", "status": "✓", "detail": f"{report['magic']} — OK"}]

    if report["version_warning"]:
        checks.append({"name": "Version", "status": "!", "detail": report["version_warning"]})
    else:
        checks.append({"name": "Version", "status": "✓", "detail": f"{report['version']} — OK"})

    if report["bone_warning"]:
        checks.append({"name": "Bones", "status": "!", "detail": report["bone_warning"]})
    else:
        checks.append({"name": "Bones", "status": "✓", "detail": f"{report['n_bones']} bones — OK"})

    halted = False
    guided_fix = None
    if report["lod_warning"]:
        checks.append({"name": "Multi-LOD check", "status": "✗", "detail": report["lod_warning"]})
        if not allow_lod_override:
            halted = True
            guided_fix = _multi_lod_guided_fix(report)
    else:
        checks.append({
            "name": "Multi-LOD check", "status": "✓",
            "detail": "vertex count matches the skin span (or there's no skin file here to check against).",
        })

    ext = report["anim_external_tags"]
    if ext:
        checks.append({
            "name": "Animations", "status": "!",
            "detail": (
                f"{len(ext)} external .anim file(s) required: {', '.join(ext)} — "
                "these MUST be packed alongside the model or those animations will crash the client."
            ),
        })
    else:
        checks.append({
            "name": "Animations", "status": "✓",
            "detail": (
                f"{report['anim_inline_count']} inline, {len(report['anim_alias_tags'])} alias, "
                "0 external — nothing extra to pack for animations."
            ),
        })

    return {
        "ok": not halted,
        "halted": halted,
        "checks": checks,
        "report": report,
        "guided_fix": guided_fix,
        "lod_override_used": allow_lod_override and bool(report["lod_warning"]),
    }


# --------------------------------------------------------------------------
# Stage 2 — texture routing
# --------------------------------------------------------------------------

def route_textures(m2_path: str, model_name: str) -> dict:
    report = inspect_m2(m2_path)
    bake_slots = [t for t in report["textures"] if t["type"] == BAKED_TYPE]
    texvar_slots = [t for t in report["textures"] if t["type"] in TEXTURE_VARIATION_TYPES]

    if bake_slots and texvar_slots:
        mode = "mixed"
    elif texvar_slots:
        mode = "texvar"
    elif bake_slots:
        mode = "bake"
    else:
        mode = "none"

    default_paths = {
        t["index"]: f"Creature\\{model_name}\\slot{t['index']}.blp" for t in bake_slots
    }

    return {
        "mode": mode,
        "bake_slots": bake_slots,
        "texvar_slots": texvar_slots,
        "default_paths": default_paths,
    }


# --------------------------------------------------------------------------
# Stage 3 — DBC rows
# --------------------------------------------------------------------------

def build_rows(
    model_data_path: str,
    display_info_path: str,
    model_name: str,
    texture_routing: dict,
    display_floor: int,
    modeldata_floor: int,
    creature_entry: str = "<entry>",
    output_dir: str = None,
) -> dict:
    kwargs = {}
    if texture_routing and texture_routing.get("mode") in ("texvar", "mixed"):
        variations = texture_routing.get("variations", {})
        kwargs["tex_variation_1"] = variations.get("tex1", "")
        kwargs["tex_variation_2"] = variations.get("tex2", "")
        kwargs["tex_variation_3"] = variations.get("tex3", "")
        kwargs["creature_geoset_data"] = variations.get("geoset", 0)

    return build_dbc_rows(
        model_data_path,
        display_info_path,
        model_name,
        output_dir=output_dir,
        display_floor=display_floor,
        modeldata_floor=modeldata_floor,
        creature_entry=creature_entry,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Stage 5 — the packing bundle
# --------------------------------------------------------------------------

def build_packing_checklist(
    m2_path: str,
    model_name: str,
    siblings: dict,
    readiness: dict,
    bake_result,
    dbc_result: dict,
    sql_block: str,
) -> str:
    report = readiness["report"]
    lines = [
        f"CONJURE — READY TO PACK: {model_name}",
        "=" * 60,
        "",
        f"1) Pack into patch-c.mpq under Creature\\{model_name}\\ :",
    ]

    pack_files = []
    if bake_result:
        pack_files.append(os.path.basename(bake_result["output_path"]))
    else:
        pack_files.append(os.path.basename(m2_path))

    if report["n_views"] <= 1:
        pack_files.append(f"{model_name}00.skin")
    else:
        pack_files.extend(siblings.get("skins", []))

    for tag in report["anim_external_tags"]:
        pack_files.append(f"{model_name}{tag}.anim")

    if bake_result:
        for t in bake_result["verification"]:
            if t["type"] == BAKED_TYPE:
                pack_files.append(t["name"].split("\\")[-1])

    for f in pack_files:
        lines.append(f"   - {f}")

    lines += [
        "",
        "2) Place these DBCs in BOTH locations:",
        f"   - {os.path.basename(dbc_result['model_data_output'])}",
        f"   - {os.path.basename(dbc_result['display_info_output'])}",
        "   (a) the client patch's DBFilesClient\\  AND  (b) the server's dbc\\ folder.",
        "   Missing the server copy = the creature will be invisible/unreachable.",
        "",
        "3) Run the SQL below (also saved as REPOINT.sql) against acore_world.",
        "",
        "4) Clear the WDB cache (the launcher/hub usually does this automatically on launch),",
        "   restart worldserver, and look at the creature in-game.",
        "",
        "-" * 60,
        sql_block or "-- (SQL not generated yet — go back to Stage 4)",
    ]
    return "\n".join(lines)


def write_packing_bundle(output_dir: str, checklist_text: str, sql_text: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    packing_path = os.path.join(output_dir, "PACKING.txt")
    sql_path = os.path.join(output_dir, "REPOINT.sql")
    with open(packing_path, "w", encoding="utf-8") as f:
        f.write(checklist_text)
    with open(sql_path, "w", encoding="utf-8") as f:
        f.write(sql_text or "")
    return {"packing_path": packing_path, "sql_path": sql_path}
