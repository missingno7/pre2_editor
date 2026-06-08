# Gameplay / ASM parity pass v9

Goal: keep `blues_p2` as a high-level engine map, but move `run_game` toward a
slot/state model that can be matched against the DOS ASM.  This pass focused on
player action/club state because it is a large part of the original player
engine and it touches multiple `objects_tbl` slots.

## Sources checked

Primary high-level map:

- `reverse_engineering/reference/blues_p2/level.c`
  - `level_update_player()`
  - `level_update_player_anim_3_6_7()`
  - `level_update_player_anim_4()`
  - `level_update_player_anim_5()`
  - `level_update_objects_club_projectiles()`
  - `level_init_object_hit_from_player_pos()`
  - `level_init_object_hit_from_xy_pos()`
- `reverse_engineering/reference/blues_p2/staticres.c`
  - `club_anim_data[]`
  - `club_anim_tbl[]`

ASM status:

- `PRE2_load.bin` still does **not** contain the club animation data as a simple
  flat byte sequence according to `asm_constant_audit.py`.
- That means these club tables are still **blues-confirmed**, not yet
  **ASM-confirmed**.
- The runtime code now names every field after the C rewrite/ASM concepts so it
  should be straightforward to replace constants when the segmented ASM map is
  improved.

## Important original object slots

The player is not isolated.  The original update uses a fixed `objects_tbl`:

| Slot(s) | Meaning |
|---:|---|
| `0` | visible club / auxiliary player object |
| `1` | player |
| `2..5` | club projectiles |
| `6..10` | hit/spark objects created by landings and some club frames |
| `11..22` | monsters |
| `23..54` | bonus/flying bonus objects |
| `55..74` | visible level items |
| `75..90` | score popups |
| `91..97` | visible platforms/decors |

Earlier runtime versions drew the player only.  That made attack frames look
wrong and hid side effects that later collision code needs.  v9 adds a small
runtime subset for slots `0..10`.

## Implemented in v9

### `runtime/original_tables.py`

Added:

- `CLUB_ANIM_DATA_BYTES`
- `CLUB_ANIM_SPECS`
- `club_anim_words()`
- `club_anim_spec()`
- `club_overlay_for_player_sprite()`
- `club_projectile_start()`

`club_anim_data` format:

```text
player_spr, club_spr, x_offset, y_offset   repeated until 0x55AA
0x55AA, x_velocity16, y_velocity16, first_projectile_spr, ... anim words
```

The second part exists only for projectile club types where `club_anim_tbl.c & 1`.

### `runtime/game.py`

Added:

- `RuntimeObject`
- `self.runtime_objects[0..10]`
- `self.current_hit_object_slot`
- `PlayerState.club_type`
- `PlayerState.club_power`
- `PlayerState.club_powerup_duration`
- `PlayerState.club_anim_duration`
- `PlayerState.anim_0x40_flag`

Player action handling is now closer to `level_update_player_anim_3_6_7()`:

- `objects_tbl[0]` is cleared at the start of each tick.
- During matching club frames, `objects_tbl[0]` is recreated from
  `club_anim_data` using the current player sprite number.
- The 0x40 animation word flag is interpreted as the active club-impact marker.
- On the active club frame:
  - `player_club_anim_duration` is set from `club_anim_tbl.a`.
  - `player_club_power` is set from `club_anim_tbl.power`, with the powerup
    duration multiplier behaviour preserved.
  - the original upward impulse branches are applied:
    - anim `6`: `dy = 0`
    - anim `3`: `dy = -32`
    - other club actions: `dy = -48`
  - some frames create a hit object in slots `6..10`.
  - projectile clubs spawn into slots `2..5`.

Projectile update now follows the C rewrite structure:

```text
x += x_velocity >> 4
y += y_velocity >> 4
advance animation word, respecting negative relative jumps
if x_friction == 0: y_velocity += 32
if x_friction == 1: y_velocity -= 16
```

The original checks `spr_num & 0x2000`; v9 does not yet enforce that flag because
our renderer/object activation flag path is not fully ported.  This is marked as
a known divergence.

### Tools

Added:

```bash
python reverse_engineering/tools/decode_club_anim.py
```

Extended:

```bash
python reverse_engineering/tools/trace_player.py --objects
```

Example:

```bash
python reverse_engineering/tools/trace_player.py --steps 12 --inputs F:8,-:4 --objects
```

This now prints the visible club object, projectile objects and hit-spark slots.

## Known divergence / next ASM work

Still not tick-perfect:

1. Need a better segmented disassembly map; flat `objdump` does not reliably
   expose the small data tables.
2. Need to confirm `club_anim_data`, `object_anim_tbl`, `player_anim_lut`, jump
   table and `spr_size_tbl` directly from `PRE2.EXE` or from a live DOS trace.
3. Need exact renderer/object activation semantics for bit `0x2000` and `0x4000`.
4. Need to port `level_objects_collide()` before club/projectiles can damage
   monsters correctly.
5. Need to port `level_update_player_collision()` after monsters/items are
   runtime objects rather than static visual context.

## Recommended next step

Port the collision primitive next:

```text
level_objects_collide(obj_a, obj_b)
```

That function is the common dependency for:

- player vs monsters
- player vs items
- player standing on platforms
- club/projectile vs monsters

It also likely explains remaining perceived hitbox/origin mismatches because it
uses `spr_size_tbl` and object origins rather than a guessed rectangle.
