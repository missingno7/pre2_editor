# Enemy death animation + secret bonus tiles

## Enemy death animation

`_monster_die` (blues level_monster_die):
- Respawning monster (monster_flags & 1): now calls `_monster_update_anim` (blues
  level_monster_update_anim) which scans the anim stream forward to the `0x7D00`
  death marker and points just past it -> the monster shows its death sprite while
  it is flung up (y_velocity = -min(club_power,25)<<3) and falls via the state==0xFF
  gravity path. Fling x sign now from the attacker's flip bit (by_obj.spr_num &
  0x8000), and `&= ~8` is conditional ((flags & ~0x37) != 0x88), matching blues.
- Non-respawning: drops a bones bonus (unchanged).
Verified: clubbed monster -> state 0xFF, anim_ptr jumps past 0x7D00, death sprite,
flung up and falling.

## Secret bonus tiles (hit to reveal / drop food)

Ported the club-vs-secret-tile path:
- `_init_secret_bonus_tiles()` (blues load_level_data_init_secret_bonus_tiles):
  per active record, hides the current map tile behind `tile_num0` and stores the
  original as `tile_num1` (restored on reveal). Records kept mutable in
  `self.bonuses_rt = [pos, tile_num0, tile_num1, count]`.
- `_collide_axe_bonuses(obj)` (blues level_collide_axe_bonuses): each club/axe
  object (slot 0 + projectiles 2..5) tests vs active records (|bx-axe_tile_x|<=1,
  |by_px-(axe_y-16)|<16). Wired into `_update_axe_collisions`.
- `_handle_bonuses_found` (blues level_handle_bonuses_found): count classes
    * & 0x80 big random: multi-hit, then spawn 110+rand(1..7), reveal.
    * & 0x40 tile reveal/food: spawn level food sprite (306/300/308, then 229/310),
      throttled by tick delta >= 6, decrement, reveal when exhausted.
    * else small random: spawn `_random_bonus_spr_num()` (0x2080+rand, <0x5F) each
      hit, reveal when count underflows.
- `_add_object23_bonus` (blues level_add_object23_bonus): spawns N bonus objects,
  TTL counter 198, alternating/decaying velocity (replaces the old single-object
  `_add_bonus_object` semantics for these paths).
- `_update_found_bonus_tile`: set map tile to tile_num1, clear pos.

Verified on level 0: small (count 2) -> 3 hits, bonus per hit, then revealed; big
(count 0x8a) -> ~11 hits then one large bonus + reveal. Smoke levels 0/1/2/4/8 OK.

## Neighbour reveal (hidden platforms appear whole)

blues `level_collide_axe_bonuses` only COUNTS adjacent secrets after a reveal
(for the completion %); it never reveals their tiles, so in blues a hidden
platform appears one tile at a time. The DOS game reveals the whole cluster when
one tile is struck. The original `level_collide_axe_bonuses` sits in a
data-interleaved region of the flat disasm that does not recover cleanly
(verified: the only `0x2080` immediate, file 0x64D6, decodes as data/garbage),
so this is implemented from the observed behaviour + the readable blues adjacency
test:

`_update_found_bonus_tile` now, when the revealed tile VISIBLY changes
(rec tile0 != tile2 — a hidden platform/wall, not a plain bonus-drop spot),
flood-fills (`_flood_reveal_secret_neighbours`) to adjacent (Chebyshev 1) active
secrets that also visibly change, revealing their tiles too. Plain bonus-drop
secrets (tile0 == tile2) do not flood. Verified: a 4-tile cluster on level 0
fully reveals from a single hit.

Pending: the completion-% secret counting is still skipped; 0x40 food sprites
unverified visually.
