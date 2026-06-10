# Prehistorik 2 — level editor

This project is a working **Prehistorik 2 DOS level editor** built directly on the original game data. It still contains reverse-engineering notes and inspectors, but the main Level tab is now an editor rather than a viewer-only shell.

## Run

```bash
python gui.py [game_data_folder]
```

By default it uses `./game_data`.

## Editor workflow

### Tools

- **View** — non-destructive browsing and panning only.
- **Select** — select objects from the map and drag supported objects without the camera following them.
- **Place** — paint tiles in the Tiles editor or place catalog-selected items/platforms/gates in the Objects editor.

### Supported map editing

- paint map tiles from the tile catalog,
- place new items in free item slots,
- place new platforms in free platform slots,
- place gates and then pick source/destination tiles,
- drag items, platforms, gates, secrets, shifting-column origins, bosses, and monsters,
- drag trigger-region monsters by their trigger-region origin,
- delete items, platforms, gates, shifting columns, secrets, and the boss controller,
- inspect all parsed level tables and overlays.

Monster deletion is intentionally not exposed yet because those records are packed variable-length inside a fixed monster attribute region. Fixed-position and trigger-region monster movement is supported.

### Save / history

The toolbar now includes:

- **Save level** — overwrites the current `LEVEL*.SQZ`, creating `LEVEL*.SQZ.bak` on the first overwrite,
- **Export level…** — writes an edited copy elsewhere,
- **Undo / Redo** — command history for in-editor edits,
- **Delete selected** — removes the currently selected supported object.

Keyboard shortcuts:

- `Ctrl+S` save,
- `Ctrl+Z` undo,
- `Ctrl+Y` redo,
- `Delete` delete selected object.

When switching levels, changing data folder, or reloading, the editor asks whether unsaved edits should be saved first.

## Save format note

The editor now contains a real serializer for the mutable level model and round-trips unchanged decompressed level payloads exactly. Edited levels are re-encoded as valid SQZ streams. The current SQZ writer is intentionally conservative: it prioritizes correctness and decoder compatibility over compression ratio, so an edited `LEVEL*.SQZ` can be larger than the original compressed file.

## Project layout

```text
gui.py                         user-facing launcher
ui/app.py                      Tk editor UI
pre2lib/formats.py             parsers, serializer, SQZ writer
pre2lib/renderer.py            level/tile rendering
pre2lib/sprites.py             sprite decoding/rendering helpers
pre2lib/labels.py              user-facing object labels
resources/                     palette/sprite bootstrap sidecars
reverse_engineering/           notes, snippets, helper scripts
game_data/                     original game files used by the editor
```

## Current RE/editor boundary

The editor is already useful for tile/object layout work and can save those edits. Some deeper property panels still function primarily as inspectors while the underlying semantics are refined. The save path preserves untouched unknown bytes and serializes the tables currently edited by the UI.

## Gameplay reverse-engineering runtime

A second launcher runs the reverse-engineered **game** (not the editor). The game
uses the pygame/SDL2 renderer only — install it with `pip install pygame`:

```bash
python run_game.py [game_data_folder]
python run_game.py --scale 3 --level 1
python run_game.py --audio-debug   # print mixer/SFX diagnostics
```

See `docs/RENDERING_BACKENDS.md` for the renderer notes and benchmarking details.

Controls:

- arrows or `WASD` move,
- `Space` / `Ctrl` club/fire,
- `Enter` action,
- `[` / `]` switch levels,
- `R` reloads current level,
- `F1` debug overlay, `F2` FPS/TPS, `F3` interpolation toggle,
- `F4` camera mode (vanilla is the default; smooth is the optional modern feel),
- `F8` cycle/test raw SAMPLE.SQZ sound effects,
- `Esc` exits.

`run_game.py` is intentionally separate from `gui.py`.  The editor stays an
asset/level authoring tool, while `runtime/game.py` is the reverse-engineered game:
mutable gameplay state, a fixed-timestep tick loop at the measured DOS rate
(`TICK_HZ ≈ 21.8`) with render interpolation, original `LEVEL*.SQZ`, `UNION.SQZ`,
`FRONT.SQZ`, `BACK*.SQZ`, `SPRITES.SQZ`, `MAP/MOTIF/MENU/...`, palettes, sprite
tables, tile collision attributes, and integer physics.

Implemented game flow: TITUS / "still working" easter-egg / PRESENT / MENU startup
screens, BEGINNER-EXPERT mode select, the per-level "you are here" map intro, the
level-entry curtain and level-exit iris wipes, gameplay (player physics, club, the
glider flight state, monsters/AI, secrets, bonuses, the sun/light palette fade, the
level-E earthquake columns, auto-scroll levels), the three bosses (gorilla, level-5
tree, level-9 minotaur), the level-completed bonus tally, and the THEEND screen.

**Accuracy policy:** the original PRE2.EXE (disassembled in `disasm/`) is the only
ground truth; the bundled `blues` C reimplementation under
`reverse_engineering/reference/` is a guide and is *not* tick-accurate, so behaviour
and constants are verified against the ASM. Remaining known inaccuracies (boss
position tables, some const-segment data, the glider in-air flap control) are tracked
in `reverse_engineering/notes/`.

On case-sensitive filesystems, the loaders now resolve DOS uppercase data files case-insensitively, so the same code works with the original `LEVEL1.SQZ` style names.
