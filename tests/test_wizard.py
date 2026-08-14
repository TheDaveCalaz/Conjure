#!/usr/bin/env python3
"""Self-tests for the guided-workflow layer: the multi-LOD/MD21 gate, texture
routing, DBC-row wrapping, config persistence, and SQL substitution.

Run with:
    python -m unittest tests.test_wizard -v
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_conjure import (
    build_fake_creature_display_info,
    build_fake_creature_model_data,
    build_fake_m2,
    build_fake_skin,
)

from conjure.config import Config
from conjure.core import DEFAULT_DISPLAY_ID_FLOOR, DEFAULT_MODEL_DATA_FLOOR
from conjure.dbc import DBCFile
from conjure.errors import ConjureError
from conjure.sql import (
    build_name_search_sql,
    build_repoint_sql_block,
    escape_sql_like,
    parse_entries,
)
from conjure.wizard import (
    build_packing_checklist,
    build_rows,
    detect_siblings,
    guess_model_name,
    readiness_check,
    route_textures,
    write_packing_bundle,
)


def build_m2_with_texture_types(types):
    """Minimal MD20 file with no animations, just `len(types)` texture slots
    of the given types — enough for route_textures to make its decision."""
    header_size = 0x100
    data = bytearray(header_size)
    data[0:4] = b"MD20"
    struct.pack_into("<I", data, 0x04, 264)
    n_textures = len(types)
    ofs_textures = header_size
    struct.pack_into("<II", data, 0x1C, 0, 0)
    struct.pack_into("<I", data, 0x2C, 5)
    struct.pack_into("<II", data, 0x3C, 10, 0)
    struct.pack_into("<I", data, 0x44, 1)
    struct.pack_into("<II", data, 0x50, n_textures, ofs_textures)
    struct.pack_into("<3f", data, 0xA0, 0.0, 0.0, 0.0)
    struct.pack_into("<3f", data, 0xAC, 0.0, 0.0, 0.0)
    struct.pack_into("<f", data, 0xB8, 0.0)
    data.extend(b"\x00" * (n_textures * 16))
    for i, t in enumerate(types):
        struct.pack_into("<4I", data, ofs_textures + i * 16, t, 0, 0, 0)
    return bytes(data)


class TestReadinessGate(unittest.TestCase):
    def test_good_model_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2())  # no sibling skin file -> nothing to compare against
            result = readiness_check(path)
            self.assertTrue(result["ok"])
            self.assertFalse(result["halted"])
            self.assertIsNone(result["guided_fix"])

    def test_multi_lod_mismatch_halts_with_guided_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2(n_vertices=1000))
            with open(os.path.join(tmp, "model00.skin"), "wb") as f:
                f.write(build_fake_skin([0, 1, 2, 99]))  # span 100, way under 1000

            result = readiness_check(path)
            self.assertFalse(result["ok"])
            self.assertTrue(result["halted"])
            fix = result["guided_fix"]
            self.assertIn("LODs aren't merged", fix["title"])
            self.assertTrue(fix["allow_override"])
            self.assertGreater(len(fix["steps"]), 0)

    def test_multi_lod_override_unblocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.m2")
            with open(path, "wb") as f:
                f.write(build_fake_m2(n_vertices=1000))
            with open(os.path.join(tmp, "model00.skin"), "wb") as f:
                f.write(build_fake_skin([0, 1, 2, 99]))

            result = readiness_check(path, allow_lod_override=True)
            self.assertTrue(result["ok"])
            self.assertFalse(result["halted"])
            self.assertTrue(result["lod_override_used"])

    def test_md21_halts_with_guided_fix_and_no_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "modern.m2")
            with open(path, "wb") as f:
                f.write(b"MD21" + b"\x00" * 20)

            result = readiness_check(path)
            self.assertTrue(result["halted"])
            fix = result["guided_fix"]
            self.assertIn("hasn't been converted", fix["title"])
            self.assertFalse(fix["allow_override"])


class TestSiblingDetection(unittest.TestCase):
    def test_finds_matching_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            m2_path = os.path.join(tmp, "thrall.m2")
            open(m2_path, "wb").close()
            open(os.path.join(tmp, "thrall00.skin"), "wb").close()
            open(os.path.join(tmp, "thrall0037-01.anim"), "wb").close()
            open(os.path.join(tmp, "body.blp"), "wb").close()
            open(os.path.join(tmp, "unrelated.txt"), "wb").close()

            result = detect_siblings(m2_path)
            self.assertEqual(result["skins"], ["thrall00.skin"])
            self.assertEqual(result["anims"], ["thrall0037-01.anim"])
            self.assertEqual(result["blps"], ["body.blp"])

    def test_guess_model_name(self):
        self.assertEqual(guess_model_name("/x/y/thrallshadowlands.m2"), "thrallshadowlands")


class TestTextureRouting(unittest.TestCase):
    def _route(self, tmp, types):
        path = os.path.join(tmp, "model.m2")
        with open(path, "wb") as f:
            f.write(build_m2_with_texture_types(types))
        return route_textures(path, "somemodel")

    def test_pure_bake_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            routing = self._route(tmp, [0, 0])
            self.assertEqual(routing["mode"], "bake")
            self.assertEqual(len(routing["bake_slots"]), 2)
            self.assertEqual(len(routing["texvar_slots"]), 0)

    def test_pure_texvar_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            routing = self._route(tmp, [11, 12, 13])
            self.assertEqual(routing["mode"], "texvar")
            self.assertEqual(len(routing["texvar_slots"]), 3)

    def test_mixed_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            routing = self._route(tmp, [0, 11])
            self.assertEqual(routing["mode"], "mixed")
            self.assertEqual(len(routing["bake_slots"]), 1)
            self.assertEqual(len(routing["texvar_slots"]), 1)

    def test_none_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            routing = self._route(tmp, [1, 2])  # DBC body/cape, not variation-fed
            self.assertEqual(routing["mode"], "none")


class TestBuildRowsWrapper(unittest.TestCase):
    def test_texvar_mode_writes_variation_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "CreatureModelData.dbc")
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            build_fake_creature_model_data().save(md_path)
            build_fake_creature_display_info().save(di_path)

            routing = {
                "mode": "texvar",
                "variations": {"tex1": "sl_skin", "tex2": "sl_base_armor", "tex3": "sl_cloak", "geoset": 5},
            }
            result = build_rows(md_path, di_path, "somemodel", routing, DEFAULT_DISPLAY_ID_FLOOR, DEFAULT_MODEL_DATA_FLOOR)

            check = DBCFile.load(result["display_info_output"])
            rec = check.find_by_id(result["display_id"])
            self.assertEqual(check.get_string(rec[6]), "sl_skin")
            self.assertEqual(check.get_string(rec[7]), "sl_base_armor")
            self.assertEqual(check.get_string(rec[8]), "sl_cloak")
            self.assertEqual(rec[14], 5)

    def test_bake_mode_leaves_variation_fields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "CreatureModelData.dbc")
            di_path = os.path.join(tmp, "CreatureDisplayInfo.dbc")
            build_fake_creature_model_data().save(md_path)
            build_fake_creature_display_info().save(di_path)

            routing = {"mode": "bake", "bake_slots": [], "texvar_slots": []}
            result = build_rows(md_path, di_path, "somemodel", routing, DEFAULT_DISPLAY_ID_FLOOR, DEFAULT_MODEL_DATA_FLOOR)

            check = DBCFile.load(result["display_info_output"])
            rec = check.find_by_id(result["display_id"])
            self.assertEqual(rec[6], 0)
            self.assertEqual(rec[7], 0)
            self.assertEqual(rec[8], 0)
            self.assertEqual(rec[14], 0)


class TestPackingBundle(unittest.TestCase):
    def test_checklist_lists_expected_files_and_bundle_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            m2_path = os.path.join(tmp, "model.m2")
            with open(m2_path, "wb") as f:
                f.write(build_fake_m2())
            readiness = readiness_check(m2_path)
            siblings = {"skins": [], "anims": [], "blps": []}
            dbc_result = {
                "model_data_output": os.path.join(tmp, "CreatureModelData_edited.dbc"),
                "display_info_output": os.path.join(tmp, "CreatureDisplayInfo_edited.dbc"),
            }
            checklist = build_packing_checklist(
                m2_path, "model", siblings, readiness, None, dbc_result, "-- sql here"
            )
            self.assertIn("0037-01.anim", checklist)  # external anim from build_fake_m2
            self.assertIn("CreatureModelData_edited.dbc", checklist)

            bundle = write_packing_bundle(tmp, checklist, "-- sql here")
            self.assertTrue(os.path.exists(bundle["packing_path"]))
            self.assertTrue(os.path.exists(bundle["sql_path"]))
            with open(bundle["sql_path"]) as f:
                self.assertEqual(f.read(), "-- sql here")


class TestConfig(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "conjure_config.json")
            cfg = Config(path)
            cfg.set("last_m2_path", "C:\\models\\thrall.m2")
            cfg.set("display_floor", 90013)
            cfg.save()

            reloaded = Config(path)
            self.assertEqual(reloaded.get("last_m2_path"), "C:\\models\\thrall.m2")
            self.assertEqual(reloaded.get("display_floor"), 90013)
            self.assertIsNone(reloaded.get("missing_key"))

    def test_corrupt_file_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "conjure_config.json")
            with open(path, "w") as f:
                f.write("{not valid json")
            cfg = Config(path)
            self.assertEqual(cfg.data, {})


class TestSql(unittest.TestCase):
    def test_escape_apostrophe(self):
        self.assertEqual(escape_sql_like("Vol'jin"), "Vol\\'jin")

    def test_name_search_sql(self):
        sql = build_name_search_sql("Vol'jin")
        self.assertIn("LIKE '%Vol\\'jin%'", sql)

    def test_name_search_requires_term(self):
        with self.assertRaises(ConjureError):
            build_name_search_sql("")

    def test_parse_entries(self):
        self.assertEqual(parse_entries("100, 101,102"), [100, 101, 102])
        self.assertEqual(parse_entries("50;51"), [50, 51])

    def test_parse_entries_rejects_non_numeric(self):
        with self.assertRaises(ConjureError):
            parse_entries("abc")

    def test_parse_entries_rejects_empty(self):
        with self.assertRaises(ConjureError):
            parse_entries("")

    def test_repoint_block_substitutes_entries_and_display_id(self):
        block = build_repoint_sql_block("Thrall", "100, 101", 90020)
        self.assertIn("WHERE ct.entry IN (100, 101)", block)
        self.assertIn("CreatureID IN (100, 101)", block)
        self.assertIn("UPDATE creature_template_model SET CreatureDisplayID = 90020", block)
        self.assertIn("LIKE '%Thrall%'", block)

    def test_repoint_block_without_search_term(self):
        block = build_repoint_sql_block("", "100", 90020)
        self.assertIn("no name search term given", block)
        self.assertIn("CreatureDisplayID = 90020", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
