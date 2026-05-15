# Prehistorik 2 — viewer evolving into editor

This project is now intentionally structured as a **read-only editor shell** for the DOS game **Prehistorik 2**. The current milestone is still viewer-first, but the UI and data model are being shaped so the next phase can unlock real editing rather than rebuild the application.

## Run

Place the original game files into:

```text
game_data/
```

Then run:

```bash
python gui.py
```

Or point to another folder explicitly:

```bash
python gui.py /path/to/prehistorik2/game_data
```

## Project layout

```text
gui.py                         user-facing launcher
ui/app.py                      Tk editor/viewer shell
pre2lib/formats.py             raw game file decompression + level parsing
pre2lib/renderer.py            tile/background/level bitmap rendering
pre2lib/sprites.py             sprite bank decoding and sprite rendering
pre2lib/labels.py              editor-facing human labels for items/enemies
resources/                      temporary RE bootstrap sidecars
reverse_engineering/           notes, helper scripts, reference excerpts, executable notes
game_data/                     expected location of the user's original game files
```

The active Python implementation now lives under `pre2lib/`; old root-level compatibility wrappers have been removed.

## Current capabilities

### Original game data loading

- Reads original `LEVEL*.SQZ`, `UNION.SQZ`, `FRONT.SQZ`, `SPRITES.SQZ`, and `BACK*.SQZ` directly.
- Implements the EAT and SQZ decompressors in Python.
- Infers level height from the level file structure itself rather than from a hidden Python constant.
- Parses the full level metadata payload:
  - tile attribute tables 0–3,
  - scrolling/start header,
  - front tile LUT,
  - gates,
  - shifting columns,
  - variable-length monster records,
  - sprite-number base offsets,
  - secrets,
  - items,
  - platforms,
  - boss block.

### Level viewer / editor shell

- Main **Level Viewer** with drag panning, default 3× zoom, and initial camera focus on player start.
- Background image layer using the original `BACK*.SQZ` assets.
- Front-mask layer rendering using `FRONT.SQZ`.
- Independent vector/canvas overlays rather than baked bitmap debug colors.
- Real object sprite previews for items, platforms, and fixed-position enemies.
- Trigger-region monsters shown as actual trigger rectangles rather than fake spawn points.
- Mechanics redesigned as read-only future editor panels:
  - human-facing object names,
  - compact object picker -> focus map,
  - click map overlay -> open the matching inspector,
  - gate source/destination focus cycling,
  - form-like inspectors with disabled future-editable controls.
- **Tile Props** inspector for tiles clicked in the Level Viewer, shaped as the future tile behavior editor.

### Tile behavior visualization

- User-facing collision/surface diagram:
  - green = solid/supporting collision,
  - cyan = slippery top surface,
  - yellow = drop-through top surface,
  - red = deadly contact.
- Sides, top, bottom and slope geometry are drawn on the physically relevant tile edge.
- Tile Props presents `attr0/1/2/3` as editor-like controls instead of raw byte dumps first.

### Animated tiles — now modeled explicitly

The viewer now understands the real **3-tile animated tile group** structure:

- `attr2 & 0x80` marks only the **base** tile of a group,
- the two following tile IDs are also animation phases,
- all three phases are treated as animated members,
- toolbar can switch the level preview between **Frame 1 / Frame 2 / Frame 3**,
- Tile Props shows animation group base, group members, selected phase and per-tile visual sequence.

See:

```text
reverse_engineering/notes/ANIMATED_TILES.md
```

## Temporary RE bootstrap data still isolated

Two things are still represented as sidecars under `resources/` rather than extracted directly from an unpacked `PRE2.EXE`:

- `palettes.json`
- `sprite_tables_reference.json`

The project keeps them isolated so they can be replaced cleanly by direct EXE extraction later.

## Reverse-engineering support material

`reverse_engineering/` now includes:

- animated-tile technical notes,
- a Python animated-tile reporting script,
- source excerpts from the user-provided `blues` reference engine used for the animation interpretation,
- executable/disassembly bookkeeping notes. `PRE2.EXE` is still LZEXE-packed; a packed-binary objdump excerpt is included only as a reference artifact, not as a semantic source.

## Next phase

See:

- `VIEWER_WRAPUP.md`
- `EDITOR_READINESS.md`

The next large technical task should be **mutable document + serializer + roundtrip tests**, with remaining viewer improvements kept directly relevant to editing.
