#!/usr/bin/env python3
"""Self-tests for Conjure's binary format logic.

These build fake .m2/.dbc/.skin files in memory / temp dirs so the binary
parsing and writing logic can be verified without needing real WoW game
files. Run with:

    python -m unittest tests.test_conjure -v

or simply:

    python tests/test_conjure.py
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conjure.core import (
    DEFAULT_DISPLAY_ID_FLOOR,
    DEFAULT_MODEL_DATA_FLOOR,
    bake_textures,
    build_dbc_rows,
    inspect_m2,
    next_free_id,
    set_texture_variations,
)
from conjure.dbc import DBCFile, float_to_u32, u32_to_float, verify_dbc
from conjure.errors import ConjureError
from conjure.m2 import M2File
from conjure.skin import read_skin_vertex_span


# ---------------------------------------------------------------------------
# synthetic file builders
# ---------------------------------------------------------------------------

M2_HEADER_SIZE = 0x100


def build_fake_m2(n_bones=10, n_vertices=100, n_views=1):
    """A minimal-but-structurally-valid MD20 264 file with 2 texture slots
    and 3 animation records (one inline, one alias, one external)."""
    data = bytearray(M2_HEADER_SIZE)
    data[0:4] = b"MD20"
    struct.pack_into("<I", data, 0x04, 264)

    ofs_textures = M2_HEADER_SIZE
    n_textures = 2
    ofs_animations = ofs_textures + n_textures * 16
    n_animations = 3
    string_area = ofs_animations + n_animations * 0x40

    struct.pack_into("<II", data, 0x1C, n_animations, ofs_animations)
    struct.pack_into("<I", data, 0x2C, n_bones)
    struct.pack_into("<II", data, 0x3C, n_vertices, 0)
    struct.pack_into("<I", data, 0x44, n_views)
    struct.pack_into("<II", data, 0x50, n_textures, ofs_textures)
    struct.pack_into("<3f", data, 0xA0, -1.0, -1.0, 0.0)
    struct.pack_into("<3f", data, 0xAC, 1.0, 1.0, 2.5)
    struct.pack_into("<f", data, 0xB8, 1.8)

    data.extend(b"\x00" * (string_area - len(data)))

    # texture slot 0: already has a baked name.
    name0 = b"old_name.blp\x00"
    ofs_name0 = string_area
    data.extend(name0)
    struct.pack_into("<4I", data, ofs_textures + 0 * 16, 0, 0, len(name0), ofs_name0)
    # texture slot 1: DBC-fed monster skin slot, no baked name.
    struct.pack_into("<4I", data, ofs_textures + 1 * 16, 11, 0, 0, 0)

    # anim 0: inline (flag 0x20 set, 0x40 clear)
    struct.pack_into("<HH", data, ofs_animations + 0 * 0x40, 0, 0)
    struct.pack_into("<I", data, ofs_animations + 0 * 0x40 + 0x0C, 0x20)
    # anim 1: alias (flag 0x40 set)
    struct.pack_into("<HH", data, ofs_animations + 1 * 0x40, 5, 0)
    struct.pack_into("<I", data, ofs_animations + 1 * 0x40 + 0x0C, 0x40)
    struct.pack_into("<H", data, ofs_animations + 1 * 0x40 + 0x3E, 5)
    # anim 2: external (both flags clear)
    struct.pack_into("<HH", data, ofs_animations + 2 * 0x40, 37, 1)
    struct.pack_into("<I", data, ofs_animations + 2 * 0x40 + 0x0C, 0x00)

    return bytes(data)


def build_fake_skin(vertex_indices):
    header_size = 12
    ofs_indices = header_size
    data = bytearray()
    data += b"SKIN"
    data += struct.pack("<II", len(vertex_indices), ofs_indices)
    for v in vertex_indices:
        data += struct.pack("<H", v)
    return bytes(data)


def build_fake_creature_model_data():
    dbc = DBCFile(field_count=28)
    ofs = dbc.add_string("Creature\\existingmodel\\existingmodel.mdx")
    F = float_to_u32
    dbc.add_record(
        [90000, 0, ofs, 1, F(1.0), 1, 0, F(12.0), F(10.0), F(1.0), 0, 0, 0, 0,
         F(0.6), F(2.2), F(1.87), F(-1.0), F(-1.0), F(0.0), F(1.0), F(1.0), F(2.5),
         F(1.0), F(1.0), 0, 0, 0]
    )
    return dbc


def build_fake_creature_display_info():
    dbc = DBCFile(field_count=16)
    F = float_to_u32
    dbc.add_record([80000, 90000, 0, 0, F(1.0), 255, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0])
    return dbc


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestDBCRoundTrip(unittest.TestCase):
    def test_generic_round_trip(self):
        dbc = DBCFile(field_count=3)
        s1 = dbc.add_string("hello")
        s2 = dbc.add_string("world")
        dbc.add_record([1, s1, 0])
        dbc.add_record([2, s2, 0])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.dbc")
            dbc.save(path)
            verify_dbc(path)  # must not raise

            reloaded = DBCFile.load(path)
            self.assertEqual(len(reloaded.records), 2)
            self.assertEqual(reloaded.field_count, 3)
            self.assertEqual(reloaded.get_string(reloaded.records[0][1]), "hello")
            self.assertEqual(reloaded.get_string(reloaded.records[1][1]), "world")
            self.assertEqual(reloaded.get_string(0), "")

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.dbc")
            with open(path, "wb") as f:
                f.write(b"NOPE" + b"\x00" * 16)
            with self.assertRaises(ConjureError):
                DBCFile.load(path)

    def test_float_round_trip(self):
        self.assertAlmostEqual(u32_to_float(float_to_u32(3.14159)), 3.14159, places=4)
        self.assertAlmostEqual(u32_to_float(float_to_u32(-1.0)), -1.0)

    def test_existing_strings_never_move(self):
        dbc = DBCFile(field_count=1)
        s1 = dbc.add_string("first")
        dbc.add_record([s1])
        before = bytes(dbc.string_block)
        dbc.add_string("second")
        # the bytes for the first string must be untouched, only appended after.
        self.assertEqual(bytes(dbc.string_block)[: len(before)], before)


class TestNextFreeId(unittest.TestCase):
    def test_skips_taken_ids(self):
        dbc = DBCFile(field_count=1)
        for existing in (100, 101, 103):
            dbc.add_record([existing])
        self.assertEqual(next_free_id(dbc, 100), 102)
        self.assertEqual(next_free_id(dbc, 104), 104)
        self.assertEqual(next_free_id(dbc, 50), 50)


class TestM2(unittest.TestCase):
    def test_load_rejects_md21(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "modern.m2")
            with open(path, "wb") as f:
                f.write(b"MD21" + b"\x00" * 20)
            with self.assertRaises(ConjureError):
                M2File.load(path)

    def test_inspect_reports_textures_and_anims(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2())
            report = inspect_m2(path)
            self.assertEqual(report["magic"], "MD20")
            self.assertEqual(report["version"], 264)
            self.assertIsNone(report["version_warning"])
            self.assertIsNone(report["bone_warning"])
            self.assertEqual(report["n_textures"], 2)
            self.assertEqual(report["textures"][0]["name"], "old_name.blp")
            self.assertEqual(report["textures"][1]["name"], "(empty / DBC-fed)")
            self.assertEqual(report["anim_inline_count"], 1)
            self.assertEqual(report["anim_alias_tags"], ["0005-00"])
            self.assertEqual(report["anim_external_tags"], ["0037-01"])

    def test_bone_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2(n_bones=300))
            report = inspect_m2(path)
            self.assertIsNotNone(report["bone_warning"])

    def test_bake_textures_writes_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2())

            result = bake_textures(
                path,
                {0: "Creature\\thrallshadowlands\\body.blp", 1: "Creature\\thrallshadowlands\\cape.blp"},
            )
            self.assertTrue(os.path.exists(result["output_path"]))
            self.assertTrue(os.path.exists(result["backup_path"]))

            reloaded = M2File.load(result["output_path"])
            textures = reloaded.read_textures()
            self.assertEqual(textures[0].type, 0)
            self.assertEqual(textures[0].name, "Creature\\thrallshadowlands\\body.blp")
            self.assertEqual(textures[1].type, 0)
            self.assertEqual(textures[1].name, "Creature\\thrallshadowlands\\cape.blp")

            # the original input file must be untouched.
            original = M2File.load(path)
            self.assertEqual(original.read_textures()[1].type, 11)

    def test_bake_rejects_out_of_range_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2())
            with self.assertRaises(ConjureError):
                bake_textures(path, {99: "x.blp"})

    def test_lod_warning_when_skin_span_much_smaller(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2(n_vertices=1000))
            skin_path = os.path.join(tmp, "model00.skin")
            with open(skin_path, "wb") as f:
                f.write(build_fake_skin([0, 1, 2, 3, 99]))  # span = 100
            report = inspect_m2(path)
            self.assertIsNotNone(report["lod_warning"])


class TestSkin(unittest.TestCase):
    def test_vertex_span(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x00.skin")
            with open(path, "wb") as f:
                f.write(build_fake_skin([2, 7, 3, 199, 15]))
            self.assertEqual(read_skin_vertex_span(path), 200)

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.skin")
            with open(path, "wb") as f:
                f.write(b"NOPE" + b"\x00" * 8)
            with self.assertRaises(ConjureError):
                read_skin_vertex_span(path)


class TestBuildDbcRows(unittest.TestCase):
    def test_appends_rows_and_chain_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "CreatureModelData.dbc")
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            build_fake_creature_model_data().save(md_path)
            build_fake_creature_display_info().save(di_path)

            result = build_dbc_rows(md_path, di_path, "thrallshadowlands")

            self.assertEqual(result["model_id"], DEFAULT_MODEL_DATA_FLOOR)
            self.assertEqual(result["display_id"], DEFAULT_DISPLAY_ID_FLOOR)
            self.assertEqual(result["model_name"], "Creature\\thrallshadowlands\\thrallshadowlands.mdx")

            check_md = DBCFile.load(result["model_data_output"])
            check_di = DBCFile.load(result["display_info_output"])
            self.assertEqual(len(check_md.records), 2)  # original + new
            self.assertEqual(len(check_di.records), 2)

            new_md = check_md.find_by_id(DEFAULT_MODEL_DATA_FLOOR)
            new_di = check_di.find_by_id(DEFAULT_DISPLAY_ID_FLOOR)
            self.assertEqual(new_di[1], new_md[0])  # ModelID -> ModelData ID

            # original file on disk must be untouched.
            original_md = DBCFile.load(md_path)
            self.assertEqual(len(original_md.records), 1)

    def test_never_collides_with_existing_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "CreatureModelData.dbc")
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            md = build_fake_creature_model_data()
            md.add_record(md.records[0][:])
            md.records[-1][0] = DEFAULT_MODEL_DATA_FLOOR  # pre-occupy the floor ID
            md.save(md_path)
            build_fake_creature_display_info().save(di_path)

            result = build_dbc_rows(md_path, di_path, "somemodel")
            self.assertEqual(result["model_id"], DEFAULT_MODEL_DATA_FLOOR + 1)

    def test_rejects_wrong_field_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "CreatureModelData.dbc")
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            DBCFile(field_count=5).save(md_path)  # wrong shape
            build_fake_creature_display_info().save(di_path)
            with self.assertRaises(ConjureError):
                build_dbc_rows(md_path, di_path, "somemodel")


class TestSetTextureVariations(unittest.TestCase):
    def test_applies_and_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            build_fake_creature_display_info().save(di_path)

            result = set_texture_variations(di_path, 80000, "sl_skin", "sl_base_armor", "sl_cloak", 5)
            r = result["resolved"]
            self.assertEqual(r["TextureVariation1"], "sl_skin")
            self.assertEqual(r["TextureVariation2"], "sl_base_armor")
            self.assertEqual(r["TextureVariation3"], "sl_cloak")
            self.assertEqual(r["CreatureGeosetData"], 5)

            # original file untouched.
            original = DBCFile.load(di_path)
            self.assertEqual(original.get_string(original.records[0][6]), "")

    def test_rejects_missing_display_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            build_fake_creature_display_info().save(di_path)
            with self.assertRaises(ConjureError):
                set_texture_variations(di_path, 12345, "a", "b", "c", 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
