# Gameplay RE pass — C# project + PRE2.EXE notes

## What the uploaded C# `pre2-master` project contributes

The project is useful as a second independent parser/asset reference, but it is not a gameplay source port.

Confirmed reusable pieces:

- `Level.cs` confirms the post-tile metadata block layout:
  - three 256-byte tile attribute tables,
  - 9-byte scrolling/start header,
  - 256 front-tile LUT words,
  - 20 gates,
  - 15 shifting tile blocks,
  - 2048-byte enemy record region,
  - item/platform sprite base word,
  - enemy sprite base word,
  - 80 secret records,
  - fourth 256-byte tile attribute/profile table,
  - 70 items,
  - 16 platforms,
  - boss/Kong record.
- `Level.cs::FixSpriteIndices()` confirms the runtime sprite-bank normalization:
  - items/platforms normalize to global base `53`,
  - enemies normalize to global base `312`,
  - enemy raw sprite IDs can still point into the item/platform bank when below the enemy base.
- `Enemy.cs` confirms enemy record framing:
  - first byte is record length,
  - `type & 0x7F` is behavior type `0..12`,
  - `type & 0x80` is expert-only,
  - record stream stops at `len > 50`, including `0xFF`.
- `Prng.cs` gives a compact original-looking PRNG seeded by `05 22 86 8D E5`.
  The same seed byte sequence appears in the unpacked load module around flat offset `0x79D1`.
- `AssetConverter.cs` gives level order, background mapping, palette mapping, track mapping, and sprite sheet metadata conventions.

Not reusable for gameplay:

- `Pre2.cs` only scrolls a rendered tilemap with arrow keys.
- `GameSession.cs` is empty.
- There is no player physics, enemy AI, item pickup, platform riding, damage, or level progression implementation.

## Runtime changes from this pass

Implemented in the Python `run_game` branch:

- Added `runtime/prng.py` with a direct port of the C# PRNG.
- Corrected `tile_attributes3` slope interpretation to use the quantized `/3` profile found in the existing RE notes rather than a full 0..15 pixel diagonal.
- Added underside checks from `tile_attributes2 & 0x0F` for ceiling contacts/deadly underside behavior.
- Added first runtime state for platforms:
  - type `0..7` use the decoded movement-vector table,
  - `unkA` is treated as the travel distance/range,
  - `max_velocity` advances a 1/16-pixel fixed-point travel accumulator,
  - type `8` has a first falling-platform state machine triggered by riding.
- Rendering now uses mutable platform state rather than the static level record position.

The platform implementation is intentionally marked as a live RE hypothesis. The vector/type mapping is grounded in prior notes; exact fixed-point scale, reset timing, collision width, and the high flag bits still need direct PRE2.EXE confirmation.

## Current disassembly status

The bundled `PRE2_unpacked_full.asm` is a flat objdump pass over the load module. That means data tables are often disassembled as code, so direct control-flow reconstruction needs function boundary recovery first.

Useful anchors already found:

- MZ entry point in the report: `0986:5E48`, linear load-module offset `0x0F6A8`.
- ASCII `LEVEL` string around flat offset `0x84AA`, useful for finding loader/open-file paths.
- PRNG seed bytes `05 22 86 8D E5` around flat offset `0x79D1`.
- Existing parser-derived metadata layout matches the C# project, so gameplay work can now focus on runtime state machines rather than file format guessing.

## Next exact-gameplay targets

1. Recover player movement routine boundaries around the main tick loop:
   - input bitfield update,
   - horizontal acceleration/friction,
   - jump impulse,
   - gravity/fall cap,
   - hitbox sample points.
2. Recover scrolling routine:
   - how `scrolling_top`, `tilemap_w`, and `scrolling_mask` affect camera clamps and gates.
3. Replace platform hypothesis with direct code-derived behavior:
   - generic shuttle fixed-point scale,
   - high bits in platform flags,
   - type 8 falling delay/reset.
4. Implement object interactions:
   - item pickup table,
   - secret block mutation,
   - player-step decorative tile cycling (`attr2 & 0x20`),
   - fly emitter (`attr2 & 0x10`).
5. Implement enemy behavior one type at a time using the already-decoded records.
