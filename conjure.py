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
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from conjure.core import (
    DEFAULT_DISPLAY_ID_FLOOR,
    DEFAULT_MODEL_DATA_FLOOR,
    bake_textures,
    build_dbc_rows,
    inspect_m2,
    set_texture_variations,
)
from conjure.errors import ConjureError

WINDOW_TITLE = "Conjure — WoW 3.3.5a Model Porting"


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
# Tab 1 — Inspect M2
# ---------------------------------------------------------------------------

class InspectTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.path_var = tk.StringVar()

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
    root.geometry("900x700")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    notebook.add(InspectTab(notebook), text="1. Inspect M2")
    notebook.add(BakeTab(notebook), text="2. Bake Textures")
    notebook.add(BuildDbcTab(notebook), text="3. Build DBC Rows")
    notebook.add(TexVarTab(notebook), text="4. Set TextureVariations")

    root.mainloop()


if __name__ == "__main__":
    if sys.version_info < (3, 7):
        print("Conjure requires Python 3.7+.", file=sys.stderr)
        sys.exit(1)
    main()
