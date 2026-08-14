**Conjure** takes retail/modern WoW model exports and does the fiddly binary plumbing to make
them load on a 3.3.5a client — baking texture paths into `.m2` files and writing the
`CreatureModelData` / `CreatureDisplayInfo` rows — so you can go from a wow.export dump to an
in-game model without a hex editor.

Conjure is a local desktop tool for porting World of Warcraft 3.3.5a (WotLK, build 12340)
creature models. It automates binary edits to `.m2` model files and `.dbc` database files that
you'd otherwise do by hand in a hex editor. It's pure, deterministic file manipulation — no
machine learning, no network access, nothing leaves your machine.

## Windows: just want a double-clickable app?

Grab the always-up-to-date prebuilt `conjure.exe` from the
**[latest release](../../releases/latest)** — no Python install needed. It's rebuilt
automatically by GitHub Actions every time `main` changes, so it never goes stale the way a
manually-committed `.exe` would. Download `conjure.exe` from that page and double-click it.

## Requirements (running from source)

- Python 3.7+ (Windows/macOS/Linux). Conjure's GUI uses `tkinter`, which ships with the standard
  Python installer on Windows and macOS.
- On some Linux distributions `tkinter` is a separate package, e.g. `sudo apt install python3-tk`.

## Running it

```
python conjure.py
```

That's it — no dependencies to install for normal use. A window titled
**"Conjure — WoW 3.3.5a Model Porting"** opens with five tabs: a guided walkthrough plus four
manual/advanced tabs.

## Port a Model (Guided) — start here

This is the default tab, and the one most people want. It walks a model through six numbered
stages in order — you can't skip ahead, and each stage validates before the next one unlocks. A
persistent checklist down the left shows ✓ / ✗ / — / → for every stage so you always know where
you are. For a correctly-converted model, one pass through it produces ready-to-pack files with
no guesswork. For a model that **isn't** ready, the wizard halts and tells you exactly what to do
about it instead of quietly producing something broken.

**Conjure does the deterministic binary work — inspecting, baking, writing DBC rows, and
generating exact SQL. It does NOT convert models (that's MultiConverter, a separate tool) and it
cannot merge an un-merged multi-LOD export or convert chunked animations.** When the wizard hits
one of those states, it doesn't just show an error: it opens a "How to fix this" panel with
numbered steps for the external tool you need, a **Re-check model** button so you can convert →
re-check → convert without restarting the wizard, and (for the multi-LOD case only) an explicit,
clearly-labelled "I understand the risk, continue anyway" override for advanced users.

- **Stage 0 — Model + folder name.** Browse to the converted `.m2` and give it the in-game model
  folder name (pre-filled from the filename). Conjure scans the folder for sibling `.skin`,
  `.anim`, and `.blp` files and lists what it found.
- **Stage 1 — Readiness check (the gate).** Runs the full inspection: format (`MD20`/`MD21`),
  version, bone count, and the **multi-LOD vertex check** — if a `<name>00.skin` sits next to the
  model and its vertex span is much smaller than the M2's vertex count, that's an un-merged
  multi-LOD export, and the wizard halts with the guided-fix panel described above. It also lists
  any external `.anim` files the model needs. Only a passing (or explicitly overridden) check
  unlocks Stage 2.
- **Stage 2 — Textures.** Conjure reads the texture slots and picks the method for you: **type 0**
  slots get baked (edit one path per slot, with a Swap-slots helper for reversed skin/armor
  order); **type 11/12/13** slots mean this model is fed via DBC TextureVariations instead, so it
  collects the three variation names + a CreatureGeosetData integer for Stage 3; a **mixed** model
  gets both.
- **Stage 3 — DBC rows.** Loads `CreatureModelData.dbc` + `CreatureDisplayInfo.dbc`, auto-picks the
  next free IDs (never colliding with an existing row), and writes both edited DBCs — wiring in
  the TextureVariations from Stage 2 if this is a DBC-fed model.
- **Stage 4 — Repoint SQL.** Generates copy-paste-ready MySQL: a name-search statement (with
  apostrophes in creature names escaped correctly), then — once you paste back the creature
  entry/entries — the sharing-check and repoint statements with the real entries and the real new
  DisplayID substituted in. Includes a strong warning about never editing a shared display.
- **Stage 5 — READY TO PACK.** A single consolidated checklist: the exact files to pack and where,
  a reminder to place both edited DBCs in **both** the client patch and the server `dbc\` folder,
  the SQL to run, and the post-patch steps (clear WDB cache, restart worldserver). Everything —
  the baked `.m2`, the edited DBCs, a `PACKING.txt` with this whole checklist, and a `REPOINT.sql`
  with the final SQL — lands together in one `conjure_output/` folder, with an "Open output
  folder" button.

Conjure remembers your last-used file paths and ID floors between runs (in a small
`conjure_config.json` next to the app), so repeated ports don't mean re-browsing everything.

## The manual/advanced tabs

These are the same tabs from Conjure's original release — useful for one-off edits, or when you
want to do a step in isolation instead of running the whole wizard.

### Inspect M2 (read-only)
Load an `.m2` and get a plain report: magic/version (flagged if not `MD20` 264), bone count
(flagged if over the WotLK 256 per-draw bone ceiling), vertex count, view count, and a
per-slot texture breakdown (index, type, and a plain-English label — e.g. "type 0 = baked
path", "type 11 = DBC TextureVariation1"). It also sweeps the animation table and reports
inline/alias/external counts, listing the `NNNN-SS` tags of any external sequences (the
`.anim` files you'll need to pack) and any alias sequences (which resolve internally, no file
needed). If a `<name>00.skin` file sits next to the `.m2`, Conjure compares the M2's vertex
count against the skin's actual vertex span and warns if the M2 looks like an un-merged
multi-LOD export that needs re-converting rather than packing.

### Bake Textures (manual)
Use this when textures are baked INTO the `.m2` — most of your own ports. (For community/
downloaded models whose instructions say "set TextureVariation1/2/3", use the TextureVariations
tab instead.) Load an `.m2`; Conjure pre-fills a row per texture slot showing its current type and name.
Give it a BLP folder name once (e.g. `thrallshadowlands`) and type a filename per slot — each
row is a plain editable text field, defaulting to `Creature\<folder>\<filename>.blp`. Clicking
**Bake**:
- Sets each edited slot's type to 0 (hardcoded/baked).
- Appends the new path string to the end of the file (valid, since M2 name offsets are
  absolute) and points `lenName`/`ofsName` at it, leaving every other offset untouched.
- Writes `<name>_baked.m2` plus a `.bak` copy of the original, re-parses the output, and shows
  a verification table proving each slot now resolves to the right type/name.
- A **Swap slots** helper lets you swap two rows' text (for when skin/armor slot order comes
  out reversed) without retyping anything.

### Build DBC Rows (manual)
Load `CreatureModelData.dbc` and `CreatureDisplayInfo.dbc`, enter the model's folder+name
(builds `ModelName` as `Creature\<name>\<name>.mdx`), and optionally override the DisplayID/
ModelData ID floors (defaults `90013` / `91014`). Clicking **Build**:
- Picks the next free ID at or above each floor, scanning existing IDs so it never collides.
- Appends a `CreatureModelData` row and a `CreatureDisplayInfo` row pointing `ModelID` at the
  new ModelData row (texture-variation fields left at 0 for the baked-texture case).
- Writes `CreatureModelData_edited.dbc` and `CreatureDisplayInfo_edited.dbc` plus `.bak`
  copies, re-parses both, and confirms the DisplayID → ModelData → ModelName chain resolves.
- Reminds you to place both edited DBCs into the client patch's `DBFilesClient\` **and** the
  server's `dbc\` folder, and prints the SQL line to repoint a creature at the new display
  (with a note to check display-sharing first):
  ```sql
  UPDATE creature_template_model SET CreatureDisplayID=<X> WHERE CreatureID=<entry>;
  ```

### Set TextureVariations (manual / community ports)
Use this when textures are fed via the DBC instead of baked into the `.m2` — typically a
community/downloaded model whose instructions say "set TextureVariation1/2/3", on a DisplayID
that already exists. Load `CreatureDisplayInfo.dbc`, pick a DisplayID that
already exists in the file, enter the three `TextureVariation` names (bare names, e.g.
`sl_skin`, `sl_base_armor`, `sl_cloak`) and a `CreatureGeosetData` integer. Clicking **Apply**
appends the three strings to the string block, points fields `[6]`/`[7]`/`[8]` at them, sets
field `[14]` to the geoset value, writes `CreatureDisplayInfo_edited.dbc` plus a `.bak`, and
shows the re-parsed, resolved values.

## Safety rules Conjure always follows

- Never overwrites an input file — every edit writes to a new output file.
- Every edit also leaves a `.bak` copy of the original next to its output.
- After every write, the output is re-read and verified before Conjure reports success; if
  verification fails, the bad output file is deleted and you get an error, not a broken file.
- Non-`MD20`/non-`WDBC` files are refused outright with a clear message (a modern chunked
  `MD21` model tells you to run it through MultiConverter first).
- DBC writes assert `record_count` matches the actual record array, `string_block_size` matches
  the actual string block, and `record_size == field_count × 4` before being accepted.
- No network calls, ever.

Output files land in a `conjure_output/` folder next to your input files.

## Building a Windows .exe yourself

Most people should just grab the [latest release](../../releases/latest) instead (see above) —
this is only for building your own copy, e.g. to test local changes:

```
pip install pyinstaller
pyinstaller --onefile --noconsole --icon=assets/icon.ico conjure.py
```

The resulting `conjure.exe` will be in `dist/`. Don't commit it to the repo — it goes stale the
moment `conjure.py` changes, which is exactly what the automated release build (see above) exists
to avoid.

## Running the self-tests

Conjure ships with self-tests that build fake `.m2`/`.dbc`/`.skin` files in memory/temp
directories and verify the binary read/write logic and the guided wizard's decision-making
(the multi-LOD gate, texture routing, SQL substitution) — no real game files needed:

```
python -m unittest tests.test_conjure tests.test_wizard -v
```

## Suggested GitHub "About" description

> Port modern WoW models to 3.3.5a — bake M2 textures and build DBC rows without touching a hex editor.

## Suggested GitHub topics

`wow`, `world-of-warcraft`, `wotlk`, `3.3.5a`, `m2`, `dbc`, `modding`, `azerothcore`, `wow-modding`

## License

Conjure's own code is MIT-licensed — see [LICENSE](LICENSE). This license covers the tool
only. The WoW models and assets Conjure operates on are Blizzard Entertainment's property and
are not covered by this license.
