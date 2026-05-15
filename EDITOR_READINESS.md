# Editor readiness assessment

## Bottom line

The project is now past the point where it makes sense to keep expanding a separate “viewer-only” product. The UI should continue to evolve directly into the editor.

**The core level model is sufficiently understood to begin editor infrastructure**, provided the next engineering milestone is not a painting tool first, but:

1. mutable `LevelDocument`,
2. serializer / re-encoder,
3. roundtrip tests,
4. then small edit operations with undo/redo.

## What is already clear enough

### Level container and assets

- decompression works in Python,
- level map and local/shared tile LUTs are parsed,
- level height is inferred from file structure,
- background/front/base render stack is understood,
- sprite bank preview works,
- object metadata tables parse consistently across shipped levels.

### Tile behavior

- side/top/bottom collision semantics are decoded,
- surface shape / slopes are decoded,
- front-mask tiles are understood for rendering,
- animated tiles are now decoded as 3-phase groups,
- `attr2 0x10` is grounded as the fly-emitter flag in runtime logic,
- `attr2 0x20` is tied to the player-step/decor tile-change logic.

The editor can expose these through Tile Props, while preserving raw attrs for exact save.

### Object categories

- gates,
- shifting columns,
- secrets,
- items,
- platforms,
- monsters,
- boss block.

All have parseable slot tables and can be selected/focused in the current UI.

### Enemy/platform behavior

- monster behavior record variants `T0–T12` are structurally parsed,
- fixed spawn vs trigger region is understood,
- behavior-specific parameters are named for editor use,
- platform behavior low nibble has user-facing labels,
- runtime-reset fields are separated conceptually from authored fields.

## What still needs work before “real editing” is trustworthy

### 1. Serializer and roundtrip tests — main blocker

This is the real transition point.

Needed tests:

- parse -> serialize -> binary equality for untouched original files where possible,
- parse -> serialize -> reparse structural equality,
- table capacity and record-length validation,
- preserving unedited raw bytes exactly where the editor has no semantic model yet.

### 2. Explicit mutable document layer

The current parser returns convenient read-only-ish structures. The editor should not mutate raw parser results ad hoc.

Recommended split:

- `LevelData` = faithful parsed source view,
- `LevelDocument` = mutable editor state,
- commands = add/move/delete/change operations,
- serializer = `LevelDocument -> encoded level payload`.

### 3. Remaining EXE-derived bootstrap sidecars

Not a blocker for basic level editing, but still a “data purity” task:

- palettes,
- sprite geometry/origin tables,
- background routing.

These should eventually be extracted from unpacked `PRE2.EXE` instead of shipped in `resources/` sidecars.

### 4. A few object semantics can still be refined

Not editor blockers, but useful before polishing final UX:

- more human names for enemies/items,
- selected object behavior geometry overlays,
- clearer grouping of multi-record secret structures,
- precise wording for any attr2 behaviors whose naming is still a little editor-facing rather than original-game terminology.

## Suggested next implementation order

### Milestone A — editor foundation

- introduce `LevelDocument`,
- add serializer skeleton,
- implement untouched roundtrip tests,
- keep UI read-only while this stabilizes.

### Milestone B — safe micro edits

- move player start,
- move an item,
- move a monster fixed anchor,
- change difficulty flag / easy binary field,
- save and boot-test.

These are ideal first mutations because they touch existing records without growing tables.

### Milestone C — editor tools

- Level Editor tab becomes active,
- selection/move tool,
- tile paint tool,
- object list + placement/edit panels,
- undo/redo.

### Milestone D — complex authoring

- new/delete records in capped slot tables,
- gate editing with source/destination placement,
- secret grouping UI,
- tile behavior editing,
- animated tile group authoring.

## Decision

**Yes: the fundamentals are clear enough to start the editor architecture now.**
The next big risk is not missing RE on maps or core objects; it is making the first save path exact and robust.
