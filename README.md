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
