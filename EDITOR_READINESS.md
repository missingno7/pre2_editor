# Editor readiness status

## Current state

The project has crossed from viewer shell to **working editor**.

Implemented editor foundation:

- mutable in-memory level state,
- decompressed payload serializer,
- valid SQZ re-encoding for edited levels,
- untouched serialize roundtrip checks across all shipped `LEVEL*.SQZ` files,
- save/export UI with first-overwrite backup,
- undo/redo,
- delete for slot-based object tables,
- non-destructive `View` tool and editing `Select` / `Place` tools.

## Editing currently exposed

- tile painting,
- item placement and dragging,
- platform placement and dragging,
- gate placement, endpoint picking, and dragging,
- secret dragging,
- shifting-column origin dragging,
- boss dragging and deletion,
- fixed-position monster dragging,
- trigger-region monster dragging by region origin.

## Current save guarantees

The serializer patches the editable level tables and preserves all still-unknown bytes from the original decompressed payload. Unchanged levels serialize back to the exact same decompressed bytes.

The SQZ writer currently favors compatibility over compression ratio. Edited `LEVEL*.SQZ` files may therefore be larger than the originals, while still decoding correctly with the project decoder and matching the level structure on reparse.

## Deliberately not finished yet

- monster deletion / insertion into the variable-length monster record area,
- broad field-level editing for every inspector property,
- compact/dictionary-optimal SQZ recompression,
- extracting all remaining bootstrap sidecars directly from `PRE2.EXE`.

## Practical next steps

1. Boot-test edited SQZ levels in the original game/DOSBox.
2. Promote the most useful inspector properties into real editable forms.
3. Add variable-length monster table authoring once record growth/compaction is safely bounded.
4. Replace conservative SQZ output with a compact encoder after game compatibility is proven.
