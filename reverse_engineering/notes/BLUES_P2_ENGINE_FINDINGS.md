# Findings from uploaded `blues-master.zip`

`blues-master` is a C rewrite of several Titus engines and includes a `p2/`
Prehistorik 2 engine.  This is much more useful than the C# viewer project: it
contains actual player, platform, monster, item, secret and boss update code.

## Files worth mining repeatedly

A snapshot was copied into:

`reverse_engineering/reference/blues_p2/`

Important files:

- `resource.h` — structs for `level_gate_t`, `level_column_t`, `level_bonus_t`,
  `level_item_t`, `level_platform_t`, `level_monster_t`, `level_t` and global resources.
- `level.c` — main gameplay loop, level loading, tile collision, player movement,
  platform update, item/bonus updates.
- `monsters.c` — monster movement/update routines.
- `bosses.c` — boss logic.
- `staticres.c` — player animation LUT, object animation streams, sprite sizes,
  score tables, trig tables.

## Concrete values transcribed into Python

`runtime/original_tables.py` now contains:

- `PLAYER_ANIM_LUT` from `p2/staticres.c`.
- `PLAYER_JUMP_Y_DELTA16 = (-65, -51, -35, -20, -10, -5, -2, -1, 0)` from
  `level_update_player_anim_2_helper()`.
- platform movement vector mapping from `level_update_objects_decors()`.
- fixed-point player constants used by `level_update_player_*()`:
  - run accel: `16`
  - run max: `80`
  - jump run max: `48`
  - ground friction: `12`
  - gravity: `16`
  - max fall: `192`

These are 1/16 px per tick values, matching the C rewrite's `x_velocity >> 4`
and `y_velocity >> 4` position updates.

## Platform update logic from `p2/level.c`

For platform types `0..7`, the source does not compute position as a pure
base+range sine/offset. It stores a signed `velocity`, ramps it by one unit per
tick toward `max_velocity`, applies the type vector, increments `counter` only
when the current velocity equals max velocity, and flips `max_velocity` when
`counter + 1 == unkA`.

Vector mapping:

```text
0: dx=0,  dy=-v
1: dx=v,  dy=-v
2: dx=v,  dy=0
3: dx=v,  dy=v
4: dx=0,  dy=v
5: dx=-v, dy=v
6: dx=-v, dy=0
7: dx=-v, dy=-v
```

Type `8` is a falling platform. It has state `0/1/2`, a `y_delta`, `y_velocity`,
`counter` and `unk9` reset delay. In state 0 it retracts `y_delta` toward zero by
8 per tick. When the player stands on it and the delay expires, it enters state
1 and accelerates downward by `+8` up to `192` in 1/16 px units. When it hits
solid ground/out-of-level it enters state 2 for 22 ticks, then resets after the
player is no longer on it.

## Player movement implications

The original-ish rewrite uses fixed-point velocities, not whole-pixel constants:

- Horizontal position update: `x_pos += x_velocity >> 4`.
- Vertical position update: `y_pos += y_velocity >> 4`.
- Running acceleration is `hdir << 4` and max run speed is `80` = 5 px/tick.
- Friction subtracts `12 >> x_friction` from absolute horizontal velocity.
- Jump does not start with one single velocity. The jump animation adds the
  impulse sequence `-65,-51,-35,-20,-10,-5,-2,-1,0` for the first 9 jump frames,
  then normal gravity takes over.
- Gravity adds `+16` per tick, capped to `192` = 12 px/tick.

`run_game` now uses these fixed-point constants as a closer bootstrap model.

## Still needs direct PRE2.EXE confirmation

- exact player sprite frame stream handling from `object_anim_tbl[]`
- exact collision boxes from `spr_size_tbl[]` and sprite origin tables
- full tile collision side effects: decorative tiles, water/snow/wind, spikes,
  destructible/animated tiles
- item pickup state and scoring
- monster/boss state machines
