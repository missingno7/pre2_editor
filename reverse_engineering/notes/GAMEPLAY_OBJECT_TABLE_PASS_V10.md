# Gameplay object table pass v10

Goal: move the runtime away from editor-style static item drawing and closer to the
original DOS engine object table.  `blues/p2/level.c` is still treated as the high
level map; constants and final behavior remain pending segmented ASM confirmation.

## What was ported / reshaped

### Object table slots

The runtime now allocates `objects_tbl[0..90]` instead of only `0..10`:

- `0`: visible club overlay
- `1`: player is still represented by `PlayerState`, but collision can create a temporary object adapter
- `2..5`: club projectiles
- `6..10`: hit/spark objects
- `23..54`: dynamic bonus objects created by secret tiles / drops
- `55..74`: visible level items, populated from `level.items` each tick like `level_update_objects_items()`
- `75..90`: floating score popup objects from `level_add_object75_score()` / `level_update_objects_bonus_scores()`

### Collision primitive

Added `_objects_collide()`, shaped after `level_objects_collide()`:

1. reject if `abs(dx) >= 64`
2. reject if `abs(dy) >= 70`
3. vertical overlap uses `spr_size_tbl[spr*2+1]`
4. horizontal overlap uses the sprite X origin / `spr_offs_tbl[spr*2]`
5. non-club collisions halve the tested width

This is important because item/bonus/monster collision should not reuse a broad
player hitbox.  It should use the same object anchor model as rendering.

### Visible items

`render_frame()` no longer draws `level.active_items` directly.  Instead
`_update_runtime_items()` fills slots `55..74` from the level item records using
the original visibility window and the small up/down bobbing via `y_delta`.

### Pickups / score popups

Added a first practical subset of `level_update_player_collision()` for
`objects_tbl[23..74]`:

- club pickups set `player.club_type` for runtime sprite numbers matching the
  DOS branches (`num == 13, 44, 182, 224` after subtracting the item base 53)
- picked items clear their backing `level.items[]` record when present
- score/feedback objects are allocated in slots `75..90`
- score popup slots move upward and expire after their counter reaches zero

This is not the full inventory/energy/level-completion logic yet.  It is the
minimal object-table structure required before porting monsters and exact item
semantics.

### Bonus object physics

Added `_update_runtime_bonuses()` for slots `23..54`:

- counter countdown / delayed vanish shape
- x/y velocity integration
- gravity `+9` up to `<256`
- bounce on attr1 floor
- side reflection on attr0 collision when moving upward
- rolling food-frame animation `70..73`

No secret-tile spawning path is wired yet; this only makes spawned bonus objects
behave in the original style once created.

### Hit/spark objects

`objects_tbl[6..10]` now use `level_update_objects_hit_animation()` shape:
advance sprites `53..57`, move `y--`, then clear.  The previous temporary TTL
spark logic was removed.

## ASM audit status

`asm_constant_audit.py` now also checks `score_spr_lut` and `score_tbl` heads.
Current result is saved in `ASM_CONSTANT_AUDIT_V10.txt`.

Important: these score/pickup tables still do **not** appear as direct flat byte
sequences in `PRE2_load.bin`, same as the player/club animation tables.  Treat
this as `blues-confirmed`, not `ASM-confirmed`, until the segmented disassembly
map is improved.

## Next best steps

1. Port visible monster object slots `11..22` with `monster_func2()` spawn rules.
2. Add `_update_runtime_axe_collisions()` against monsters and bonus tiles.
3. Expand item pickup branch exactly, especially level-end semaphore, checkpoint,
   food/energy, BONUS letters, utensils, and screen-kill/bomb items.
4. Replace current camera approximation with the original tilemap scroll state,
   because item visibility and draw flag `0x2000` depend on the previous draw
   window in the DOS loop.
