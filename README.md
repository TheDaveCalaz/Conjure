**Conjure** takes retail/modern WoW model exports and does the fiddly binary plumbing to make
them load on a 3.3.5a client — baking texture paths into `.m2` files and writing the
`CreatureModelData` / `CreatureDisplayInfo` rows — so you can go from a wow.export dump to an
in-game model without a hex editor.

Conjure is a local desktop tool for porting World of Warcraft 3.3.5a (WotLK, build 12340)
creature models. It automates binary edits to `.m2` model files and `.dbc` database files that
you'd otherwise do by hand in a hex editor. It's pure, deterministic file manipulation — no
machine learning, no network access, nothing leaves your machine.

## Requirements

- Python 3.7+ (Windows/macOS/Linux). Conjure's GUI uses `tkinter`, which ships with the standard
  Python installer on Windows and macOS.
- On some Linux distributions `tkinter` is a separate package, e.g. `sudo apt install python3-tk`.

## Running it

```
python conjure.py
```

That's it — no dependencies to install for normal use. A window titled
**"Conjure — WoW 3.3.5a Model Porting"** opens with four tabs.

## The four tabs

### 1. Inspect M2 (read-only)
Load an `.m2` and get a plain report: magic/version (flagged if not `MD20` 264), bone count
(flagged if over the WotLK 256 per-draw bone ceiling), vertex count, view count, and a
per-slot texture breakdown (index, type, and a plain-English label — e.g. "type 0 = baked
path", "type 11 = DBC TextureVariation1"). It also sweeps the animation table and reports
inline/alias/external counts, listing the `NNNN-SS` tags of any external sequences (the
`.anim` files you'll need to pack) and any alias sequences (which resolve internally, no file
needed). If a `<name>00.skin` file sits next to the `.m2`, Conjure compares the M2's vertex
count against the skin's actual vertex span and warns if the M2 looks like an un-merged
multi-LOD export that needs re-converting rather than packing.

### 2. Bake Textures
Load an `.m2`; Conjure pre-fills a row per texture slot showing its current type and name.
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

### 3. Build DBC Rows
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

### 4. Set TextureVariations (community-port mode)
For when you're wiring up an already-existing DisplayID to community-made texture variations
instead of baking a texture into the M2. Load `CreatureDisplayInfo.dbc`, pick a DisplayID that
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

## Building a Windows .exe

```
pip install pyinstaller
pyinstaller --onefile --noconsole conjure.py
```

The resulting `conjure.exe` will be in `dist/`.

## Running the self-tests

Conjure ships with self-tests that build fake `.m2`/`.dbc`/`.skin` files in memory/temp
directories and verify the binary read/write logic — no real game files needed:

```
python -m unittest tests.test_conjure -v
```

## Suggested GitHub "About" description

> Port modern WoW models to 3.3.5a — bake M2 textures and build DBC rows without touching a hex editor.

## Suggested GitHub topics

`wow`, `world-of-warcraft`, `wotlk`, `3.3.5a`, `m2`, `dbc`, `modding`, `azerothcore`, `wow-modding`

## License

Conjure's own code is MIT-licensed — see [LICENSE](LICENSE). This license covers the tool
only. The WoW models and assets Conjure operates on are Blizzard Entertainment's property and
are not covered by this license.
