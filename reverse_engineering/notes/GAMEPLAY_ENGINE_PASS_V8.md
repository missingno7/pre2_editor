# Gameplay/engine pass v8

Goal: use `blues-master/p2` as a high-level control-flow map, but keep PRE2.EXE / ASM as the source of truth for constants and edge cases.

## What was taken from blues p2 in this pass

### `p2/game.c`

`game.c` does not contain most player physics. Its useful contribution is the exact input/demo shape:

- live input maps to `key_left/right/up/down/space/jump`
- default jump source is `key_up`; `key_jump` is only used with the `jump_button` option
- demo playback reads `KEYB.SQZ` as little-endian words:
  - low byte = input mask
  - high byte = counter
  - bit 0 = left
  - bit 1 = right
  - bit 2 = up
  - bit 3 = down
  - bit 4 = space/action

Runtime addition:

- `runtime/demo.py`
- `trace_player.py --demo-keyb`

This gives us a repeatable original-input stream for later DOSBox comparisons.

### `p2/staticres.c`

The biggest player-collision fix in this pass is `spr_size_tbl[]`.

Earlier runtime used decoded bitmap dimensions to determine player collision/probe height. The original code uses:

```c
spr_h = spr_size_tbl[(spr_num & 0x1FFF) * 2 + 1];
```

This is not the same thing as visual sprite height. Using bitmap height makes collision vary with draw data instead of logic data and contributes to floor/ceiling weirdness. `runtime/original_tables.py` now contains `SPR_SIZE_TBL` and `sprite_logic_size()`.

### `p2/level.c`

`level_update_tile0()` upward tail was ported more exactly:

- attr2 is read from the tile one row above the current upper tile, not from the same tile row as the player origin probe
- attr2 type 1 zeroes y velocity and snaps `y` to the next 16 px line
- attr2 type 2 kills player
- if `attr0` of the current upper tile is solid-ish, the original attempts a tiny horizontal nudge of `x += +/-2`

This is important because a broad rectangle collider makes these micro-corrections look like random jitter.

## Runtime tilemap state

The original mutates the tilemap inside `level_update_tile0()` for tiles with `tile_attributes2 & 0x20`. The runtime now keeps a mutable `runtime_tilemap` and a `decor_tile0_offset` instead of treating the map as immutable renderer data.

This is not just visual polish. It keeps gameplay state and renderer state closer to the original engine model.

## ASM audit status

Added:

```bash
python reverse_engineering/tools/asm_constant_audit.py
```

Current result is stored in:

```text
reverse_engineering/notes/ASM_CONSTANT_AUDIT_V8.txt
```

Current finding: the PRNG seed is byte-for-byte visible in `PRE2_load.bin` at `0x079D1`, but the player animation/jump/club/staticres tables do not appear as simple flat byte sequences. That means one of the following is true:

1. the current flat load-module disassembly is not enough to locate those tables reliably;
2. the relevant tables are generated/decoded/transformed before use;
3. the `blues` staticres tables are from a nearby but not byte-identical reconstruction/version.

Until this is resolved, the runtime marks those as `blues-confirmed, ASM-unconfirmed` rather than pretending they are proven.

## Concrete runtime changes

- Added `SPR_SIZE_TBL` and `sprite_logic_size()`.
- Player vertical probe height now uses `sprite_logic_size(spr_num).height`.
- Added mutable `runtime_tilemap` for engine-side tile changes.
- Added `decor_tile0_offset` handling for `attr2 & 0x20` tile changes under the player.
- Ported the upward attr2 collision/nudge tail from `level_update_tile0()` more exactly.
- Added missing `player_action_counter` decrement at the end of each tick.
- Added KEYB demo decoding and trace playback.

## Next best steps

1. Build a segmented disassembly map for PRE2.EXE instead of relying on the flat objdump.
2. Locate the equivalent of `level_update_player()` and the static animation tables in the real binary.
3. Compare `trace_player.py --demo-keyb` against DOSBox frame/tick captures.
4. Port `objects_tbl[0]` club hit object and projectile logic from `level_update_player_anim_3_6_7()`.
5. Start moving monsters from static render-only records into runtime `objects_tbl[11+]` state.
