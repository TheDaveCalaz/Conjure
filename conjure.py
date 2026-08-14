#!/usr/bin/env python3
"""Conjure — WoW 3.3.5a Model Porting Toolkit.

A local, offline desktop tool for porting modern WoW creature model exports
onto a 3.3.5a (WotLK, build 12340) client: baking texture paths into .m2
files and writing CreatureModelData / CreatureDisplayInfo .dbc rows.

Run it with:
    python conjure.py

No network access, no machine learning — just deterministic binary edits.
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from conjure.config import Config
from conjure.core import (
    DEFAULT_DISPLAY_ID_FLOOR,
    DEFAULT_MODEL_DATA_FLOOR,
    bake_textures,
    build_dbc_rows,
    default_output_dir,
    inspect_m2,
    set_texture_variations,
)
from conjure.errors import ConjureError
from conjure.sql import build_name_search_sql, build_repoint_sql_block
from conjure.wizard import (
    build_packing_checklist,
    build_rows,
    detect_siblings,
    guess_model_name,
    readiness_check,
    route_textures,
    write_packing_bundle,
)

WINDOW_TITLE = "Conjure — WoW 3.3.5a Model Porting"

GUIDED_STAGE_NAMES = [
    "0. Model + folder",
    "1. Readiness check",
    "2. Textures",
    "3. DBC rows",
    "4. Repoint SQL",
    "5. Ready to pack",
]


def browse_open(entry, filetypes, title="Select file"):
    path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)


def report_error(exc: Exception):
    if isinstance(exc, ConjureError):
        messagebox.showerror("Conjure — refused", str(exc))
    else:
        messagebox.showerror("Conjure — unexpected error", f"{type(exc).__name__}: {exc}")


def set_report_text(widget: scrolledtext.ScrolledText, text: str):
    widget.configure(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, text)
    widget.configure(state="disabled")


# ---------------------------------------------------------------------------
# Guided tab — "Port a Model"
# ---------------------------------------------------------------------------

class GuidedTab(ttk.Frame):
    """A stage-by-stage wizard: each stage validates before the next unlocks.
    For a correctly-converted model this produces ready-to-pack output in one
    pass; for a model that isn't ready, it halts with a guided-fix panel
    instead of silently producing something broken."""

    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.app_config = Config()
        self.stage = 0
        self.max_unlocked = 0

        self.m2_path = None
        self.model_name = None
        self.siblings = None
        self.output_dir = None
        self.readiness = None
        self.texture_routing = None
        self.bake_result = None
        self.dbc_result = None
        self.sql_block = None
        self._lod_override_var = tk.BooleanVar(value=False)
        self._bake_path_cache = {}  # {slot_index: last-typed path}, survives Stage 2 re-renders
        self._texvar_cache = {"tex1": "", "tex2": "", "tex3": "", "geoset": "0"}

        header = ttk.Label(
            self,
            text=(
                "Port a Model — go stage by stage; Conjure checks each one before letting you "
                "continue, and only calls a model \"ready to pack\" once every check passes."
            ),
            wraplength=840,
        )
        header.pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        side = ttk.Frame(body, width=190)
        side.pack(side="left", fill="y", padx=(0, 12))
        side.pack_propagate(False)
        self.checklist_labels = []
        for name in GUIDED_STAGE_NAMES:
            lbl = ttk.Label(side, text=f"—  {name}")
            lbl.pack(anchor="w", pady=3)
            self.checklist_labels.append(lbl)

        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)

        self._render_stage()
        self._update_checklist()

    # -- navigation -------------------------------------------------------

    def _clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def _advance_to(self, n, rerender_only=False):
        if not rerender_only:
            self.max_unlocked = max(self.max_unlocked, n)
        self.stage = n
        self._render_stage()
        self._update_checklist()

    def _render_stage(self):
        dispatch = [
            self._build_stage0,
            self._build_stage1,
            self._build_stage2,
            self._build_stage3,
            self._build_stage4,
            self._build_stage5,
        ]
        dispatch[self.stage]()

    def _update_checklist(self):
        for i, lbl in enumerate(self.checklist_labels):
            if i < self.stage:
                symbol = "✓"
            elif i == self.stage:
                symbol = "✗" if (i == 1 and self.readiness and self.readiness["halted"]) else "→"
            else:
                symbol = "—"
            lbl.configure(text=f"{symbol}  {GUIDED_STAGE_NAMES[i]}")

    def _output_dir(self):
        return self.output_dir

    # -- Stage 0: model + folder name --------------------------------------

    def _build_stage0(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 0 — Choose the model + folder name", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Browse to the CONVERTED .m2 you want to port, and give it the in-game model folder name.",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=".m2 file:", width=18).pack(side="left")
        self.m2_entry = ttk.Entry(row, width=55)
        self.m2_entry.insert(0, self.app_config.get("last_m2_path", ""))
        self.m2_entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._stage0_browse).pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Model folder name:", width=18).pack(side="left")
        self.model_name_entry = ttk.Entry(row, width=30)
        self.model_name_entry.insert(0, self.app_config.get("last_model_name", ""))
        self.model_name_entry.pack(side="left", padx=5)

        self.stage0_report = scrolledtext.ScrolledText(f, height=10, state="disabled", font=("Courier New", 10))
        self.stage0_report.pack(fill="both", expand=True, pady=(10, 0))

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="Next →", command=self._stage0_next).pack(side="right")

    def _stage0_browse(self):
        path = filedialog.askopenfilename(
            title="Select the converted .m2", filetypes=[("M2 model", "*.m2"), ("All files", "*.*")]
        )
        if path:
            self.m2_entry.delete(0, tk.END)
            self.m2_entry.insert(0, path)
            if not self.model_name_entry.get().strip():
                self.model_name_entry.insert(0, guess_model_name(path))

    def _stage0_next(self):
        path = self.m2_entry.get().strip()
        name = self.model_name_entry.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Conjure", "Choose a valid .m2 file.")
            return
        if not name:
            messagebox.showwarning("Conjure", "Enter a model folder name.")
            return

        self.m2_path = path
        self.model_name = name
        self.output_dir = default_output_dir(path)
        self.app_config.set("last_m2_path", path)
        self.app_config.set("last_model_name", name)
        self.app_config.save()

        self.siblings = detect_siblings(path)
        lines = [
            f"Folder: {self.siblings['folder']}",
            "",
            f"Skin files found: {', '.join(self.siblings['skins']) or '(none)'}",
            f".anim files found: {', '.join(self.siblings['anims']) or '(none)'}",
            f".blp files found: {', '.join(self.siblings['blps']) or '(none)'}",
        ]
        set_report_text(self.stage0_report, "\n".join(lines))
        self._advance_to(1)

    # -- Stage 1: readiness check (the gate) -------------------------------

    def _build_stage1(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 1 — Readiness check", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Conjure verifies this model is actually safe to pack before doing anything else.",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 8))

        self.stage1_report = scrolledtext.ScrolledText(f, height=14, state="disabled", font=("Courier New", 10))
        self.stage1_report.pack(fill="both", expand=True)

        self.stage1_fix_frame = ttk.Frame(f)
        self.stage1_fix_frame.pack(fill="x", pady=(8, 0))

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="← Back", command=lambda: self._advance_to(0, rerender_only=True)).pack(side="left")
        self.stage1_next_btn = ttk.Button(nav, text="Next →", command=lambda: self._advance_to(2))
        self.stage1_next_btn.pack(side="right")

        self._run_stage1_check()

    def _run_stage1_check(self, m2_path_override=None):
        path = m2_path_override or self.m2_path
        self.readiness = readiness_check(path, allow_lod_override=self._lod_override_var.get())
        self.m2_path = path

        lines = [f"[{c['status']}] {c['name']}: {c['detail']}" for c in self.readiness["checks"]]
        set_report_text(self.stage1_report, "\n".join(lines))

        for child in self.stage1_fix_frame.winfo_children():
            child.destroy()

        if self.readiness["halted"]:
            fix = self.readiness["guided_fix"]
            box = ttk.LabelFrame(self.stage1_fix_frame, text=f"How to fix this: {fix['title']}")
            box.pack(fill="x")
            for i, step in enumerate(fix["steps"], 1):
                ttk.Label(box, text=f"{i}. {step}", wraplength=740, justify="left").pack(anchor="w", padx=5, pady=2)
            ttk.Label(
                box, text=f"Next action: {fix['next_action']}", wraplength=740, justify="left",
                font=("", 9, "italic"),
            ).pack(anchor="w", padx=5, pady=(4, 4))

            row = ttk.Frame(box)
            row.pack(fill="x", pady=4)
            ttk.Button(row, text="Browse to fixed .m2…", command=self._stage1_recheck_browse).pack(side="left")
            ttk.Button(row, text="Re-check model", command=lambda: self._run_stage1_check()).pack(side="left", padx=6)

            if fix.get("allow_override"):
                cb = ttk.Checkbutton(
                    box,
                    text="I understand the risk — let me continue anyway (advanced)",
                    variable=self._lod_override_var,
                    command=lambda: self._run_stage1_check(),
                )
                cb.pack(anchor="w", padx=5, pady=(4, 2))
                if fix.get("override_warning"):
                    ttk.Label(
                        box, text=fix["override_warning"], wraplength=740, foreground="#b00020", justify="left",
                    ).pack(anchor="w", padx=5, pady=(0, 4))
            self.stage1_next_btn.state(["disabled"])
        else:
            self.stage1_next_btn.state(["!disabled"])
        self._update_checklist()

    def _stage1_recheck_browse(self):
        path = filedialog.askopenfilename(
            title="Select the converted .m2", filetypes=[("M2 model", "*.m2"), ("All files", "*.*")]
        )
        if path:
            self._run_stage1_check(m2_path_override=path)

    # -- Stage 2: textures --------------------------------------------------

    def _build_stage2(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 2 — Textures", font=("", 11, "bold")).pack(anchor="w")

        routing = route_textures(self.m2_path, self.model_name)
        self.texture_routing = routing
        self.bake_entries = []

        # Stage 2 always re-inspects the ORIGINAL .m2, so on a re-visit (Back, or
        # after a bake) it has no idea what you typed last time or that a bake
        # already happened. Prefer, in order: the value actually verified on disk
        # by the last bake, then whatever you last typed, then the generic default.
        already_baked = {t["index"]: t["name"] for t in (self.bake_result or {}).get("verification", [])} \
            if self.bake_result else {}

        if already_baked:
            ttk.Label(
                f,
                text=(
                    f"Already baked to {self.bake_result['output_path']} — the values below are what "
                    "was actually verified on disk. Edit and hit Next again to re-bake."
                ),
                wraplength=760, font=("", 9, "italic"),
            ).pack(anchor="w", pady=(0, 6))

        if routing["mode"] == "none":
            ttk.Label(
                f, text="No bakeable or DBC-fed texture slots were found on this model — nothing to do here.",
                wraplength=760,
            ).pack(anchor="w", pady=8)

        if routing["mode"] in ("bake", "mixed"):
            ttk.Label(
                f,
                text="This model bakes texture paths INTO the .m2 (most of your own ports use this).",
                wraplength=760,
            ).pack(anchor="w", pady=(4, 2))
            ttk.Label(
                f,
                text=(
                    "⚠ Each path below must be the IN-GAME path — the same path the .blp will have "
                    "INSIDE patch-c.mpq (e.g. Creature\\velen2\\body.blp). It is NOT the file's current "
                    "location on your PC — do not paste something like "
                    "C:\\Users\\You\\wow.export\\creature\\velen2\\body.blp here, Conjure will reject it."
                ),
                wraplength=760, foreground="#b00020",
            ).pack(anchor="w", pady=(0, 6))
            for t in routing["bake_slots"]:
                row = ttk.Frame(f)
                row.pack(fill="x", pady=1)
                slot_label = t["name"] if t["name"] != "(empty / DBC-fed)" else "(empty)"
                ttk.Label(row, text=f"[{t['index']}] {slot_label}", width=26).pack(side="left")
                e = ttk.Entry(row, width=48)
                prefill = (
                    already_baked.get(t["index"])
                    or self._bake_path_cache.get(t["index"])
                    or routing["default_paths"].get(t["index"], "")
                )
                e.insert(0, prefill)
                e.pack(side="left", padx=5, fill="x", expand=True)
                self.bake_entries.append((t["index"], e))
            ttk.Label(
                f,
                text=(
                    "Flat white in game = a path is wrong/blank (or the file doesn't exist inside "
                    "patch-c.mpq at that path yet, or the .blp itself is unreadable). Scrambled "
                    "textures = slot order is wrong — use Swap and re-bake."
                ),
                wraplength=760, font=("", 9, "italic"),
            ).pack(anchor="w", pady=(4, 4))
            swap_row = ttk.Frame(f)
            swap_row.pack(fill="x")
            ttk.Label(swap_row, text="Swap slots:").pack(side="left")
            self.swap_a = ttk.Entry(swap_row, width=4)
            self.swap_a.pack(side="left", padx=3)
            ttk.Label(swap_row, text="<->").pack(side="left")
            self.swap_b = ttk.Entry(swap_row, width=4)
            self.swap_b.pack(side="left", padx=3)
            ttk.Button(swap_row, text="Swap", command=self._stage2_swap).pack(side="left", padx=5)

        self.tex1_var = tk.StringVar(value=self._texvar_cache.get("tex1", ""))
        self.tex2_var = tk.StringVar(value=self._texvar_cache.get("tex2", ""))
        self.tex3_var = tk.StringVar(value=self._texvar_cache.get("tex3", ""))
        self.geoset_var = tk.StringVar(value=self._texvar_cache.get("geoset", "0"))
        if routing["mode"] in ("texvar", "mixed"):
            ttk.Label(
                f, text="This model is fed via DBC TextureVariations (community/downloaded port).",
                wraplength=760,
            ).pack(anchor="w", pady=(10, 2))
            for label, var in [
                ("TextureVariation1:", self.tex1_var),
                ("TextureVariation2:", self.tex2_var),
                ("TextureVariation3:", self.tex3_var),
                ("CreatureGeosetData:", self.geoset_var),
            ]:
                row = ttk.Frame(f)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=label, width=20).pack(side="left")
                ttk.Entry(row, textvariable=var, width=30).pack(side="left", padx=5)

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="← Back", command=self._stage2_back).pack(side="left")
        ttk.Button(nav, text="Next →", command=self._stage2_next).pack(side="right")

    def _stage2_snapshot(self):
        """Remember whatever's currently typed so it survives a Back/re-render,
        even if the user never got as far as clicking Next."""
        for idx, e in getattr(self, "bake_entries", []):
            self._bake_path_cache[idx] = e.get()
        if hasattr(self, "tex1_var"):
            self._texvar_cache = {
                "tex1": self.tex1_var.get(),
                "tex2": self.tex2_var.get(),
                "tex3": self.tex3_var.get(),
                "geoset": self.geoset_var.get(),
            }

    def _stage2_back(self):
        self._stage2_snapshot()
        self._advance_to(1, rerender_only=True)

    def _stage2_swap(self):
        try:
            a, b = int(self.swap_a.get()), int(self.swap_b.get())
        except ValueError:
            messagebox.showwarning("Conjure", "Enter two integer slot indices.")
            return
        by_idx = {idx: e for idx, e in self.bake_entries}
        if a not in by_idx or b not in by_idx:
            messagebox.showwarning("Conjure", "One or both slot indices don't exist.")
            return
        va, vb = by_idx[a].get(), by_idx[b].get()
        by_idx[a].delete(0, tk.END)
        by_idx[a].insert(0, vb)
        by_idx[b].delete(0, tk.END)
        by_idx[b].insert(0, va)

    def _stage2_next(self):
        self._stage2_snapshot()
        routing = self.texture_routing
        bake_result = None
        if routing["mode"] in ("bake", "mixed"):
            slot_paths = {idx: e.get().strip() for idx, e in self.bake_entries if e.get().strip()}
            if not slot_paths:
                messagebox.showwarning("Conjure", "Enter at least one texture path to bake.")
                return
            try:
                bake_result = bake_textures(self.m2_path, slot_paths, output_dir=self._output_dir())
            except Exception as e:
                report_error(e)
                return
            lines = [f"Baked: {bake_result['output_path']}", ""]
            for t in bake_result["verification"]:
                lines.append(f"  [{t['index']}] type {t['type']} ({t['type_label']}): {t['name']}")
            messagebox.showinfo("Conjure — Textures baked", "\n".join(lines))
        self.bake_result = bake_result

        if routing["mode"] in ("texvar", "mixed"):
            try:
                geoset = int(self.geoset_var.get())
            except ValueError:
                messagebox.showwarning("Conjure", "CreatureGeosetData must be an integer.")
                return
            routing["variations"] = {
                "tex1": self.tex1_var.get().strip(),
                "tex2": self.tex2_var.get().strip(),
                "tex3": self.tex3_var.get().strip(),
                "geoset": geoset,
            }

        self._advance_to(3)

    # -- Stage 3: DBC rows ----------------------------------------------------

    def _build_stage3(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 3 — DBC rows", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(f, text="IDs are picked automatically and never collide with an existing row.", wraplength=760).pack(
            anchor="w", pady=(0, 8)
        )

        self.model_data_entry = self._stage3_file_row(f, "CreatureModelData.dbc:", "last_model_data_path")
        self.display_info_entry = self._stage3_file_row(f, "CreatureDisplayInfo.dbc:", "last_display_info_path")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="DisplayID floor:", width=20).pack(side="left")
        self.display_floor_entry = ttk.Entry(row, width=12)
        self.display_floor_entry.insert(0, str(self.app_config.get("display_floor", DEFAULT_DISPLAY_ID_FLOOR)))
        self.display_floor_entry.pack(side="left", padx=5)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="ModelData floor:", width=20).pack(side="left")
        self.modeldata_floor_entry = ttk.Entry(row, width=12)
        self.modeldata_floor_entry.insert(0, str(self.app_config.get("modeldata_floor", DEFAULT_MODEL_DATA_FLOOR)))
        self.modeldata_floor_entry.pack(side="left", padx=5)

        ttk.Button(f, text="Build DBC rows", command=self._stage3_build).pack(pady=6)

        self.stage3_report = scrolledtext.ScrolledText(f, height=8, state="disabled", font=("Courier New", 10))
        self.stage3_report.pack(fill="both", expand=True)

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="← Back", command=lambda: self._advance_to(2, rerender_only=True)).pack(side="left")
        self.stage3_next_btn = ttk.Button(nav, text="Next →", command=lambda: self._advance_to(4))
        self.stage3_next_btn.pack(side="right")
        self.stage3_next_btn.state(["!disabled"] if self.dbc_result else ["disabled"])

    def _stage3_file_row(self, parent, label, config_key):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=24).pack(side="left")
        entry = ttk.Entry(row, width=50)
        entry.insert(0, self.app_config.get(config_key, ""))
        entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(
            row, text="Browse…",
            command=lambda: browse_open(entry, [("DBC", "*.dbc"), ("All files", "*.*")]),
        ).pack(side="left")
        return entry

    def _stage3_build(self):
        md_path = self.model_data_entry.get().strip()
        di_path = self.display_info_entry.get().strip()
        if not md_path or not di_path:
            messagebox.showwarning("Conjure", "Choose both DBC files.")
            return
        try:
            display_floor = int(self.display_floor_entry.get())
            modeldata_floor = int(self.modeldata_floor_entry.get())
        except ValueError:
            messagebox.showwarning("Conjure", "ID floors must be integers.")
            return

        try:
            result = build_rows(
                md_path, di_path, self.model_name, self.texture_routing,
                display_floor, modeldata_floor, output_dir=self._output_dir(),
            )
        except Exception as e:
            report_error(e)
            return

        self.dbc_result = result
        self.app_config.set("last_model_data_path", md_path)
        self.app_config.set("last_display_info_path", di_path)
        self.app_config.set("display_floor", display_floor)
        self.app_config.set("modeldata_floor", modeldata_floor)
        self.app_config.save()

        lines = [
            result["message"], "",
            f"Wrote: {result['model_data_output']}",
            f"Wrote: {result['display_info_output']}",
        ]
        set_report_text(self.stage3_report, "\n".join(lines))
        self.stage3_next_btn.state(["!disabled"])

    # -- Stage 4: repoint SQL --------------------------------------------------

    def _build_stage4(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 4 — Repoint SQL", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(
            f, text="Conjure can't query your database, so it generates copy-paste-ready SQL instead.",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Creature name to search:", width=26).pack(side="left")
        self.search_entry = ttk.Entry(row, width=30)
        self.search_entry.pack(side="left", padx=5)
        ttk.Button(row, text="Generate name-search SQL", command=self._stage4_search).pack(side="left", padx=5)

        self.stage4_search_text = scrolledtext.ScrolledText(f, height=2, state="disabled", font=("Courier New", 10))
        self.stage4_search_text.pack(fill="x", pady=(2, 8))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Creature entry/entries (comma-separated):", width=32).pack(side="left")
        self.entries_entry = ttk.Entry(row, width=24)
        self.entries_entry.pack(side="left", padx=5)
        ttk.Label(
            row, text="ⓘ the numeric \"entry\" from acore_world.creature_template — find it above",
            font=("", 8, "italic"),
        ).pack(side="left", padx=5)

        ttk.Button(f, text="Generate repoint SQL", command=self._stage4_generate).pack(pady=6)

        self.stage4_report = scrolledtext.ScrolledText(f, height=13, state="disabled", font=("Courier New", 10))
        self.stage4_report.pack(fill="both", expand=True)

        ttk.Label(
            f,
            text=(
                "Never edit a display that's shared with other creatures — always repoint by CreatureID "
                "instead. Repointing by CreatureID updates every model Idx row for that creature."
            ),
            wraplength=760, foreground="#b00020",
        ).pack(anchor="w", pady=(6, 0))

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="← Back", command=lambda: self._advance_to(3, rerender_only=True)).pack(side="left")
        self.stage4_next_btn = ttk.Button(nav, text="Next →", command=lambda: self._advance_to(5))
        self.stage4_next_btn.pack(side="right")
        self.stage4_next_btn.state(["!disabled"] if self.sql_block else ["disabled"])

    def _stage4_search(self):
        term = self.search_entry.get().strip()
        try:
            sql = build_name_search_sql(term)
        except Exception as e:
            report_error(e)
            return
        set_report_text(self.stage4_search_text, sql)

    def _stage4_generate(self):
        if not self.dbc_result:
            messagebox.showwarning("Conjure", "Build the DBC rows in Stage 3 first.")
            return
        try:
            sql_block = build_repoint_sql_block(
                self.search_entry.get().strip(), self.entries_entry.get().strip(), self.dbc_result["display_id"],
            )
        except Exception as e:
            report_error(e)
            return
        self.sql_block = sql_block
        set_report_text(self.stage4_report, sql_block)
        self.stage4_next_btn.state(["!disabled"])

    # -- Stage 5: ready to pack -------------------------------------------------

    def _build_stage5(self):
        self._clear_content()
        f = self.content
        ttk.Label(f, text="Stage 5 — READY TO PACK", font=("", 13, "bold")).pack(anchor="w", pady=(0, 6))

        checklist_text = build_packing_checklist(
            self.m2_path, self.model_name, self.siblings, self.readiness,
            self.bake_result, self.dbc_result, self.sql_block,
        )
        bundle = write_packing_bundle(self._output_dir(), checklist_text, self.sql_block)
        self._last_bundle = bundle

        report = scrolledtext.ScrolledText(f, height=24, font=("Courier New", 10))
        report.pack(fill="both", expand=True)
        report.insert(tk.END, checklist_text)
        report.configure(state="disabled")

        ttk.Label(f, text=f"Saved: {bundle['packing_path']} and {bundle['sql_path']}", wraplength=760).pack(
            anchor="w", pady=(6, 0)
        )

        nav = ttk.Frame(f)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="← Back", command=lambda: self._advance_to(4, rerender_only=True)).pack(side="left")
        ttk.Button(nav, text="Open output folder", command=self._open_output_folder).pack(side="right")

    def _open_output_folder(self):
        path = self._output_dir()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: (Windows-only API, guarded by platform check)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Conjure", f"Couldn't open the folder automatically ({e}).\n\nIt's at:\n{path}")


# ---------------------------------------------------------------------------
# Tab 1 — Inspect M2
# ---------------------------------------------------------------------------

class InspectTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.path_var = tk.StringVar()

        ttk.Label(self, text="Read-only: load an .m2 and inspect it — no files are written. (advanced/manual)").pack(
            anchor="w"
        )
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=".m2 file:").pack(side="left")
        self.path_entry = ttk.Entry(row, textvariable=self.path_var, width=70)
        self.path_entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left")
        ttk.Button(row, text="Inspect", command=self.inspect).pack(side="left", padx=5)

        self.report = scrolledtext.ScrolledText(self, height=28, state="disabled", font=("Courier New", 10))
        self.report.pack(fill="both", expand=True, pady=(10, 0))

    def browse(self):
        browse_open(self.path_entry, [("M2 model", "*.m2"), ("All files", "*.*")], "Select .m2 file")

    def inspect(self):
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("Conjure", "Choose an .m2 file first.")
            return
        try:
            report = inspect_m2(path)
        except Exception as e:
            report_error(e)
            return

        lines = []
        lines.append(f"File: {report['path']}")
        lines.append(f"Magic: {report['magic']}    Version: {report['version']}")
        if report["version_warning"]:
            lines.append(f"  ! {report['version_warning']}")
        lines.append(f"Bones: {report['n_bones']}")
        if report["bone_warning"]:
            lines.append(f"  ! {report['bone_warning']}")
        lines.append(f"Vertices: {report['n_vertices']}")
        lines.append(f"Views (.skin files expected): {report['n_views']}")
        lines.append(f"Textures: {report['n_textures']}")
        lines.append("")
        lines.append("Texture slots:")
        for t in report["textures"]:
            lines.append(f"  [{t['index']}] type {t['type']:>2} ({t['type_label']}): {t['name']}")
        lines.append("")
        lines.append(
            f"Animations: {report['anim_inline_count']} inline, "
            f"{len(report['anim_alias_tags'])} alias, "
            f"{len(report['anim_external_tags'])} external"
        )
        if report["anim_external_tags"]:
            lines.append("  External .anim files needed:")
            for tag in report["anim_external_tags"]:
                lines.append(f"    <name>{tag}.anim")
        if report["anim_alias_tags"]:
            lines.append("  Alias sequences (resolve internally, no file needed):")
            lines.append("    " + ", ".join(report["anim_alias_tags"]))
        if report["lod_warning"]:
            lines.append("")
            lines.append(f"! {report['lod_warning']}")

        set_report_text(self.report, "\n".join(lines))


# ---------------------------------------------------------------------------
# Tab 2 — Bake Textures
# ---------------------------------------------------------------------------

class BakeTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.m2_path_var = tk.StringVar()
        self.folder_var = tk.StringVar()
        self.slot_entries = []  # list of (index, type, tk.Entry)

        ttk.Label(
            self,
            text=(
                "Manual/advanced. Use this when textures are baked INTO the .m2 — most of your own "
                "ports. For community/downloaded models fed via DBC TextureVariations, use that tab instead."
            ),
            wraplength=820,
        ).pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=".m2 file:").pack(side="left")
        self.path_entry = ttk.Entry(row, textvariable=self.m2_path_var, width=55)
        self.path_entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left")

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(5, 0))
        ttk.Label(row2, text="BLP folder (e.g. thrallshadowlands):").pack(side="left")
        ttk.Entry(row2, textvariable=self.folder_var, width=30).pack(side="left", padx=5)
        ttk.Button(row2, text="Load Slots", command=self.load_slots).pack(side="left", padx=5)

        ttk.Label(
            self,
            text="Edit the path for each slot you want to bake. Leave blank to leave a slot untouched.",
        ).pack(fill="x", pady=(8, 2))
        ttk.Label(
            self,
            text=(
                "⚠ Each path must be the IN-GAME path — the same path the .blp will have INSIDE "
                "patch-c.mpq (e.g. Creature\\velen2\\body.blp). It is NOT the file's current location on "
                "your PC — do not paste something like C:\\Users\\You\\wow.export\\creature\\velen2\\body.blp "
                "here, Conjure will reject it."
            ),
            wraplength=820, foreground="#b00020",
        ).pack(anchor="w", pady=(0, 4))

        self.slots_frame = ttk.Frame(self)
        self.slots_frame.pack(fill="both", expand=True)

        swap_row = ttk.Frame(self)
        swap_row.pack(fill="x", pady=(6, 0))
        ttk.Label(swap_row, text="Swap slots:").pack(side="left")
        self.swap_a = ttk.Entry(swap_row, width=4)
        self.swap_a.pack(side="left", padx=3)
        ttk.Label(swap_row, text="<->").pack(side="left")
        self.swap_b = ttk.Entry(swap_row, width=4)
        self.swap_b.pack(side="left", padx=3)
        ttk.Button(swap_row, text="Swap", command=self.swap_slots).pack(side="left", padx=5)

        ttk.Button(self, text="Bake", command=self.bake).pack(pady=8)

        self.report = scrolledtext.ScrolledText(self, height=12, state="disabled", font=("Courier New", 10))
        self.report.pack(fill="both", expand=True)

    def browse(self):
        browse_open(self.path_entry, [("M2 model", "*.m2"), ("All files", "*.*")], "Select .m2 file")

    def load_slots(self):
        path = self.m2_path_var.get().strip()
        if not path:
            messagebox.showwarning("Conjure", "Choose an .m2 file first.")
            return
        try:
            report = inspect_m2(path)
        except Exception as e:
            report_error(e)
            return

        for child in self.slots_frame.winfo_children():
            child.destroy()
        self.slot_entries = []

        folder = self.folder_var.get().strip()
        for t in report["textures"]:
            row = ttk.Frame(self.slots_frame)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"[{t['index']}] type {t['type']} ({t['type_label']})", width=32).pack(side="left")
            ttk.Label(row, text=f"was: {t['name']}", width=30).pack(side="left")
            entry = ttk.Entry(row, width=45)
            default_name = f"slot{t['index']}.blp"
            if folder:
                entry.insert(0, f"Creature\\{folder}\\{default_name}")
            entry.pack(side="left", padx=5, fill="x", expand=True)
            self.slot_entries.append((t["index"], t["type"], entry))

    def swap_slots(self):
        try:
            a = int(self.swap_a.get())
            b = int(self.swap_b.get())
        except ValueError:
            messagebox.showwarning("Conjure", "Enter two integer slot indices to swap.")
            return
        by_index = {idx: entry for idx, _t, entry in self.slot_entries}
        if a not in by_index or b not in by_index:
            messagebox.showwarning("Conjure", "One or both slot indices don't exist. Load slots first.")
            return
        va, vb = by_index[a].get(), by_index[b].get()
        by_index[a].delete(0, tk.END)
        by_index[a].insert(0, vb)
        by_index[b].delete(0, tk.END)
        by_index[b].insert(0, va)

    def bake(self):
        path = self.m2_path_var.get().strip()
        if not path:
            messagebox.showwarning("Conjure", "Choose an .m2 file first.")
            return
        if not self.slot_entries:
            messagebox.showwarning("Conjure", "Load slots first.")
            return
        slot_paths = {}
        for idx, _t, entry in self.slot_entries:
            value = entry.get().strip()
            if value:
                slot_paths[idx] = value
        if not slot_paths:
            messagebox.showwarning("Conjure", "Enter at least one texture path to bake.")
            return
        try:
            result = bake_textures(path, slot_paths)
        except Exception as e:
            report_error(e)
            return

        lines = [
            f"Wrote: {result['output_path']}",
            f"Backup of original: {result['backup_path']}",
            "",
            "Verification (re-parsed from the written file):",
        ]
        for t in result["verification"]:
            lines.append(f"  [{t['index']}] type {t['type']} ({t['type_label']}): {t['name']}")
        set_report_text(self.report, "\n".join(lines))
        messagebox.showinfo("Conjure", f"Baked successfully:\n{result['output_path']}")


# ---------------------------------------------------------------------------
# Tab 3 — Build DBC Rows
# ---------------------------------------------------------------------------

class BuildDbcTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.model_data_var = tk.StringVar()
        self.display_info_var = tk.StringVar()
        self.model_name_var = tk.StringVar()
        self.display_floor_var = tk.StringVar(value=str(DEFAULT_DISPLAY_ID_FLOOR))
        self.modeldata_floor_var = tk.StringVar(value=str(DEFAULT_MODEL_DATA_FLOOR))
        self.entry_var = tk.StringVar(value="<entry>")

        ttk.Label(
            self,
            text="Manual/advanced. Appends one CreatureModelData row and one CreatureDisplayInfo row, auto-picking free IDs.",
            wraplength=820,
        ).pack(anchor="w", pady=(0, 6))

        self._file_row("CreatureModelData.dbc:", self.model_data_var, [("DBC", "*.dbc"), ("All files", "*.*")])
        self._file_row("CreatureDisplayInfo.dbc:", self.display_info_var, [("DBC", "*.dbc"), ("All files", "*.*")])

        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Model folder+name (e.g. thrallshadowlands):", width=38).pack(side="left")
        ttk.Entry(row, textvariable=self.model_name_var, width=30).pack(side="left", padx=5)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="DisplayID floor:", width=38).pack(side="left")
        ttk.Entry(row, textvariable=self.display_floor_var, width=12).pack(side="left", padx=5)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="ModelData floor:", width=38).pack(side="left")
        ttk.Entry(row, textvariable=self.modeldata_floor_var, width=12).pack(side="left", padx=5)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Creature entry (for the SQL reminder, optional):", width=38).pack(side="left")
        ttk.Entry(row, textvariable=self.entry_var, width=12).pack(side="left", padx=5)
        ttk.Label(
            self,
            text="ⓘ Creature entry = the numeric \"entry\" column in acore_world.creature_template.",
            font=("", 8, "italic"),
        ).pack(anchor="w")

        ttk.Button(self, text="Build", command=self.build).pack(pady=8)

        self.report = scrolledtext.ScrolledText(self, height=16, state="disabled", font=("Courier New", 10))
        self.report.pack(fill="both", expand=True)

    def _file_row(self, label, var, filetypes):
        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=24).pack(side="left")
        entry = ttk.Entry(row, textvariable=var, width=50)
        entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=lambda: browse_open(entry, filetypes)).pack(side="left")

    def build(self):
        md_path = self.model_data_var.get().strip()
        di_path = self.display_info_var.get().strip()
        model_name = self.model_name_var.get().strip()
        if not md_path or not di_path or not model_name:
            messagebox.showwarning("Conjure", "Fill in both DBC paths and the model name.")
            return
        try:
            display_floor = int(self.display_floor_var.get())
            modeldata_floor = int(self.modeldata_floor_var.get())
        except ValueError:
            messagebox.showwarning("Conjure", "ID floors must be integers.")
            return

        try:
            result = build_dbc_rows(
                md_path,
                di_path,
                model_name,
                display_floor=display_floor,
                modeldata_floor=modeldata_floor,
                creature_entry=self.entry_var.get().strip() or "<entry>",
            )
        except Exception as e:
            report_error(e)
            return

        lines = [
            result["message"],
            "",
            f"Wrote: {result['model_data_output']}  (backup: {result['model_data_backup']})",
            f"Wrote: {result['display_info_output']}  (backup: {result['display_info_backup']})",
            "",
            result["reminder"],
            "",
            "SQL to repoint a creature to this display:",
            f"  {result['sql']}",
        ]
        set_report_text(self.report, "\n".join(lines))
        messagebox.showinfo("Conjure", "DBC rows built successfully.")


# ---------------------------------------------------------------------------
# Tab 4 — Set TextureVariations (community-port mode)
# ---------------------------------------------------------------------------

class TexVarTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.display_info_var = tk.StringVar()
        self.display_id_var = tk.StringVar()
        self.tex1_var = tk.StringVar()
        self.tex2_var = tk.StringVar()
        self.tex3_var = tk.StringVar()
        self.geoset_var = tk.StringVar(value="0")

        ttk.Label(
            self,
            text=(
                "Manual/advanced — community ports. Use this when textures are fed via the DBC "
                "(a downloaded model whose instructions say \"set TextureVariation1/2/3\"), on a "
                "DisplayID that already exists. For your own baked-texture ports, use Bake Textures instead."
            ),
            wraplength=820,
        ).pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="CreatureDisplayInfo.dbc:", width=22).pack(side="left")
        entry = ttk.Entry(row, textvariable=self.display_info_var, width=50)
        entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(
            row, text="Browse…",
            command=lambda: browse_open(entry, [("DBC", "*.dbc"), ("All files", "*.*")]),
        ).pack(side="left")

        for label, var in [
            ("Target DisplayID (must exist):", self.display_id_var),
            ("TextureVariation1:", self.tex1_var),
            ("TextureVariation2:", self.tex2_var),
            ("TextureVariation3:", self.tex3_var),
            ("CreatureGeosetData:", self.geoset_var),
        ]:
            row = ttk.Frame(self)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=22).pack(side="left")
            ttk.Entry(row, textvariable=var, width=30).pack(side="left", padx=5)

        ttk.Button(self, text="Apply", command=self.apply).pack(pady=8)

        self.report = scrolledtext.ScrolledText(self, height=14, state="disabled", font=("Courier New", 10))
        self.report.pack(fill="both", expand=True)

    def apply(self):
        di_path = self.display_info_var.get().strip()
        if not di_path:
            messagebox.showwarning("Conjure", "Choose a CreatureDisplayInfo.dbc file first.")
            return
        try:
            display_id = int(self.display_id_var.get())
            geoset = int(self.geoset_var.get())
        except ValueError:
            messagebox.showwarning("Conjure", "DisplayID and CreatureGeosetData must be integers.")
            return

        try:
            result = set_texture_variations(
                di_path,
                display_id,
                self.tex1_var.get().strip(),
                self.tex2_var.get().strip(),
                self.tex3_var.get().strip(),
                geoset,
            )
        except Exception as e:
            report_error(e)
            return

        r = result["resolved"]
        lines = [
            f"Wrote: {result['output_path']}",
            f"Backup of original: {result['backup_path']}",
            "",
            "Verification (re-parsed from the written file):",
            f"  DisplayID: {r['DisplayID']}",
            f"  TextureVariation1: {r['TextureVariation1']}",
            f"  TextureVariation2: {r['TextureVariation2']}",
            f"  TextureVariation3: {r['TextureVariation3']}",
            f"  CreatureGeosetData: {r['CreatureGeosetData']}",
        ]
        set_report_text(self.report, "\n".join(lines))
        messagebox.showinfo("Conjure", "TextureVariations applied successfully.")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("980x760")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    notebook.add(GuidedTab(notebook), text="Port a Model (Guided)")
    notebook.add(InspectTab(notebook), text="Inspect M2 (manual)")
    notebook.add(BakeTab(notebook), text="Bake Textures (manual)")
    notebook.add(BuildDbcTab(notebook), text="Build DBC Rows (manual)")
    notebook.add(TexVarTab(notebook), text="Set TextureVariations (manual / community ports)")
    notebook.select(0)

    root.mainloop()


if __name__ == "__main__":
    if sys.version_info < (3, 7):
        print("Conjure requires Python 3.7+.", file=sys.stderr)
        sys.exit(1)
    main()
