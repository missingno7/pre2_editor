# Pickups / enemy-hit / secrets pass

User reports: (1) non-pickup items (exit semaphore "traffic light", washing-machine
checkpoint) get picked up; (2) player hitting enemies is wrong; (3) there should be
secrets activated by the player hitting them — investigate.

Reminder: ASM is ground truth; blues is only a map for *where* things live. Item
semantics below are mapped from blues (low tick-importance); the physics/hit
behaviour is the part that must end up ASM-accurate.

## (1) Wrong pickups — FIXED

`_pickup_bonus_or_item` cleared `spr_num=0xFFFF` and scored for everything. Special
items (num = (spr&0x1FFF)-53) must be handled first:
- num 226 / 258 = end-of-level / game-complete semaphore -> advance level (deferred
  to end-of-tick via `self._pending_level`, since load_level rebuilds the object
  table/player mid-iteration).
- num 228 = checkpoint -> set `checkpoint_x/y` (used on death respawn), stays in
  world, no score.
Added `checkpoint_x/y` (default = level start) and used them in the death-respawn
branch instead of the level start.

## (2) Player hitting enemies — FIXED (faithful, normal mode)

Two bugs vs blues `level_update_player_collision` / `level_objects_collide`:
- The stomp condition used a reconstructed guess. blues sets
  `player_jump_monster_flag` + `collide_y_dist` INSIDE the collide primitive:
  flagged when player vy>=128 OR (vertical overlap depth <= lower-object height/2
  AND the player is the upper object). Now set inside `_objects_collide` and read
  per-pair (reset before each monster check).
- **Jumping on a monster wrongly KILLED it** (invented `energy -= club_power//2`).
  In normal (non-gravity) mode blues does NOT kill on stomp — it bounces the
  player (vy = -224 if jump held else -64), zeroes jumping_counter, snaps the
  player up by `collide_y_dist`, and counts the stomp in `hit_jump_counter` (score
  on every other stomp). Only the CLUB kills (axe collision). Rewrote the stomp
  branch to match; hurt branch unchanged (hurt when not a stomp or moving up).
Note: blues' gravity_flag / flying_flag and the player_hit_monster_counter (660-tick
utensil invincibility that kills monsters on touch) are not modelled yet.

## (3) Secrets activated by hitting — INVESTIGATED, not yet implemented

Mechanism (blues `level_collide_axe_bonuses`, called from level_update_objects_axe
next to `level_collide_axe_monsters`):
- Level stores 80 `bonus` records (already parsed: `tile_num0`=initial, `tile_num1`
  =revealed, `count`, `pos`=x|(y<<8)).
- Level init (`load_level_data_init_secret_bonus_tiles`): for active records, if the
  current map tile at pos == revealed_tile, set it to initial_tile (hide it).
- Each CLUB/axe object (objects_tbl[0] + projectiles 2..5) is tested vs every active
  bonus: hit if |bonus_x - axe_x_tile| <= 1 and |bonus_y_px - (axe_y-16)| < 16.
  On hit: `level_handle_bonuses_found` runs the count-class logic
    * count & 0x80 (big random): decrement; when high bit clears, spawn one of
      110+random(1..7); ++secrets_count.
    * count & 0x40 (tile reveal): emit level-dependent food/effect sprites
      (306/300/308, then 229/310), throttled by draw-counter delta >= 6.
    * else (small random): spawn `level_get_random_bonus_spr_num()`.
  When count's high bit becomes set after decrement, `level_update_found_bonus_tile`
  restores `tile_num1` (reveal) and clears `pos=0xFFFF`.

Implementation plan (next turn):
1. Add a mutable runtime copy of the bonus records (pos must be cleared on consume).
2. Install secret tiles into `runtime_tilemap` at load_level (mirror init pass).
3. Add `_collide_axe_bonuses(axe_obj)`; call it from `_update_axe_collisions` for
   objects_tbl[0] and 2..5 (after/with the monster check).
4. Port `level_handle_bonuses_found` count classes + `_add_bonus_object` spawns +
   tile reveal. Verify spawn sprite numbers against ASM where feasible.

## Verified

Headless tick 300-500 frames on levels 0/1/2/4/6/8/13 with move/jump/club: no
errors; checkpoints set; semaphore advances level.
