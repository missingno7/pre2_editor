# Player ASM parity pass v7

Goal: keep `run_game` moving toward a tick-perfect DOS source port.  `blues-master/p2` is useful as a labelled map, but not treated as the final authority; every rule here should be checked against `disasm/PRE2_unpacked_full.asm` / `PRE2_load.bin` as our segmented ASM map improves.

## What `p2/game.c` adds

`p2/game.c` is mostly menu/input/game-loop glue, not player physics.  Useful confirmations:

- `update_input()` maps directions to `0xFF` byte booleans:
  - left/right/up/down from the direction bitfield
  - `key_space` from the space/action input
  - `key_jump` exists separately, but the player update chooses `key_up` unless `jump_button` option is enabled.
- `random_reset()` seed is `a=5, b=34, c=134, d=58765`.
- Main loop enters `do_level()`; player update logic remains in `p2/level.c`.

## Player data model confirmed by `p2/game.h`

Player is `objects_tbl[1]`, not a standalone rectangle:

```c
struct object_t {
    int16_t x_pos, y_pos;
    uint16_t spr_num;
    int16_t x_velocity;
    uint8_t x_friction;
    union { struct player_t p; ... } data;
    uint8_t hit_counter;
};
```

So the authoritative gameplay position is one bottom-origin anchor point.  Sprite offset/size tables define render and probe geometry.

## Applied to `run_game` in v7

### 1. Sprite flip anchor fix

The previous v6 fix still used `width - origin_x` for mirrored sprites.  The offset is a 0-based pixel coordinate, so the mirrored anchor is:

```text
origin_x_flipped = width - 1 - origin_x
```

This removes the remaining 1 px visual pop on direction flip.

### 2. Removed broad deadly/body sweep from tick path

`level_update_player_decor()` does not sample a broad player rectangle for death every tick.  The runtime now sets `player.death_flag` from the ASM-shaped tile probes instead:

- attr1 type `6` below the origin -> deadly top/spikes
- attr2 underside type `2` when moving up -> deadly underside
- attr0 type `2` in side/upper probe column -> deadly side/body tile

This avoids false resets from guessed hitbox corners.

### 3. `level_player_reset()` semantics

`nojump_counter` is not decremented globally every tick.  The C rewrite decrements it in `level_player_reset()`, called from the ground contact path.  Runtime now mirrors that with `_player_reset_after_ground_contact()`.

### 4. Landing / hard-fall branch

`level_update_tile_attr1_helper()` has important behavior after falling:

- if `jumping_counter > 4`, compare current `y` against `player_prev_y_pos`
- if fall delta is at least `32` and `y_velocity >= 80`, update `prev_y`
- if `jumping_counter > 10`, optionally bounce with `y_velocity=-32` and sprite `12`
- otherwise zero vertical velocity and run `level_player_reset()`

This branch is now represented in `_apply_attr1_ground()`.

### 5. `level_update_player_decor()` order

Runtime now routes post-velocity collision through `_asm_update_player_decor()`:

```text
y_pos = (player.y >> 4) - 1
spr_h = spr_size_tbl[spr_num * 2 + 1]
dx = +9 / -9 / 0 from x_velocity
x_pos = player.x >> 4
player_tile_flags = 0
level_update_tile0((y_pos << 8) | x_pos)
if player_tile_flags == 1:
    level_update_player_jump()
    if y_velocity > 0: jumping_counter++
else:
    jumping_counter = 0
level_update_tile1(side column)
repeat level_update_tile2(upward by sprite height)
```

The important part is that we do not sweep/snap a rectangle.  We apply the fixed-point move first, then let the original tile probes correct it.

### 6. Debug overlay

`F1` now shows the ASM probe model visually:

- red cross = player bottom-origin anchor point
- cyan line = side probe column at x+9/x-9/x
- yellow tile = tile under the bottom-origin point used by `level_update_tile0()`

This is intentionally not a guessed collision rectangle.

## Still pending against ASM

- Exact segmented address map for `level_update_player()`, `level_update_player_decor()`, `level_update_tile0/1/2`.
- Ceiling side nudge after underside collision.  `blues` has a suspicious local static `data[6]` artifact that is probably a bad decompilation of stack/register state.
- Exact scrolling-window death conditions.  v7 keeps only absolute level bounds until scroll is ported properly.
- Club overlay/projectile object `objects_tbl[0]` and hit objects `6..10`.
- Flying/wind/snow influence.
- Demo input playback from `KEYB.SQZ` as a regression test source.

## Useful repeatability tool

Use:

```bash
python reverse_engineering/tools/trace_player.py --steps 40 --inputs R:40
python reverse_engineering/tools/trace_player.py --steps 30 --inputs U+R:12,R:18
```

It prints per-tick `x,y,vx,vy,spr,anim,ground,tile_flags,nojump,jump_counter,friction,death` so we can compare traces after future ASM corrections.
