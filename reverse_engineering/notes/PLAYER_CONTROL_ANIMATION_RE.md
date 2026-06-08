# Player control + animation RE pass

Source used in this pass: `reverse_engineering/reference/blues_p2/level.c` and `staticres.c`.

## Important correction

The previous `run_game` builds chose player frames manually. That could never match the DOS game, because the original does not use a simple `idle/run/jump -> sprite range` mapping. It builds a 5-bit input mask and looks it up in `player_anim_lut[]`, then advances one of the `object_anim_tbl[]` animation streams.

## Original input mask

From `level_update_player()`:

```c
mask |= key_right;
mask <<= 1;
mask |= key_left;
mask <<= 1;
mask |= jump_or_up;
mask <<= 1;
mask |= key_down;
mask <<= 1;
mask |= key_space;
al = player_anim_lut[mask];
```

With default controls, `Up` is jump and `Space` is club/action. `run_game` now follows that: arrows/A,D move, Up/W jump, Space/Ctrl attack.

## Animation states currently ported

`runtime/original_tables.py` now contains:

- `PLAYER_ANIM_LUT`
- `OBJECT_ANIMS`
- jump vertical impulse table `-65,-51,-35,-20,-10,-5,-2,-1,0`

`runtime/game.py` now contains a small animation-word VM that handles negative relative loop jumps from `object_anim_tbl[]`.

The current implementation ports the structure of these original functions:

- `level_update_player()`
- `level_update_player_anim_0()` simplified
- `level_update_player_anim_1()`
- `level_update_player_anim_2()` / `_helper()`
- `level_update_player_anim_3_6_7()` simplified to animation/movement lock only
- `level_update_player_anim_4()` simplified
- `level_update_player_anim_5()` simplified
- `level_update_player_anim_8()`
- `level_update_player_jump()` simplified

## Still missing / not exact yet

- exact club hit object placement from `club_anim_tbl[]`
- projectile club variants
- flying mode item and `player_flying_anim_data[]`
- exact `level_update_player_decor()` collision order; current Python collision is still a simplified hitbox resolver
- exact wind/snow horizontal velocity adjustment
- exact exhausted/idle timer behavior; current version approximates the timer windows
- exact hit/damage/monster bounce transitions

## Direct PRE2.EXE verification status

The C rewrite gives a strong map, but the table bytes were not trivially found as contiguous patterns in the current unpacked EXE artifact. This likely means either the unpacked binary/disasm artifact is not aligned the same way, or some tables differ/are relocated/generated. For now, `blues_p2` is the working map and each critical routine still needs direct confirmation against `PRE2_unpacked_full.asm`.
