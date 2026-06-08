# Monster animation + AI faithful pass

User report: enemies had "completely weird movement and sprites/animation",
"especially area-spawn" (types 0 and 10). Diagnosis vs `blues_p2/monsters.c` +
`level.c level_update_objects_monsters`:

## Root causes found

1. **Animation model was a slice approximation.** The runtime stored each
   monster's animation as a sliced word tuple cycled with modulo. The original
   uses an absolute byte-stream pointer into `monster_anim_tbl` with embedded
   signed negative jump-back offsets as loop points, and switches animation
   *sequences* with `monster_change_next_anim` / `monster_change_prev_anim`. The
   slice model literally cannot represent sequence switching, so animations were
   stuck/garbled on every enemy.
2. **No `monster_change_next/prev_anim` calls anywhere.** blues calls them on
   ~10 state transitions (types 0,2,3,7,8,10 and in `level_update_monster_pos`).
3. **Movement state machines diverged**: types 5 and 7 were merged (type5 lost
   its state10->11 dive phase; type7 lost its `next_anim`); type8 state12 went
   back to idle instead of re-jumping and skipped the double `prev_anim`; type9
   was missing its special despawn and was wrongly run through the generic
   offscreen helper (blues types 9 and 12 don't use it).

## Fixes applied (runtime/game.py)

- New faithful anim streaming: `_monster_anim_start` (resolve start word index),
  `_monster_anim_step` (read+advance, follow negative jump-backs),
  `_monster_change_next_anim` / `_monster_change_prev_anim`. RuntimeObject gains
  `anim_ptr` (word index) and `anim_fallback`. Removed the old
  `_runtime_anim_step` slice stepper. Other object types still use anim_words.
- Wired next/prev anim into types 0,2,3,7,8,10 and `_update_monster_pos`.
- Split type5/type7; fixed type8 state12 re-jump + double prev_anim; added type9
  special despawn; skip generic offscreen helper for types 9 and 12.

## Verified

Headless smoke (no GUI): `RuntimeWorld.tick` over 300-600 frames on levels
0/1/4, no errors. Type-10 area-spawn enemies now emerge from the floor and the
sprite cycles frame-to-frame (anim_ptr advances 355->356->357..., spr 381->380),
where before it was static.

## Follow-up fix: ground monsters bounced/"jumped" instead of walking

User report: the level-1 start area-spawn enemies (type 10, spr 381) bounced
vertically like jumping instead of walking.

Root cause in `_update_monster_pos`: blues does `SWAP(dl, dh)` after `y_pos -= 16`,
so it tests the tile now at the monster's feet (tile_ABOVE) first and only falls
back to the original lower tile if that's empty. The Python port had tile_below
and tile_above swapped, so a grounded monster was snapped one tile too high every
frame -> perpetually airborne -> gravity -> fall -> snapped up again = endless
bounce. Fixed to test tile_above first (matches blues lines 1440-1456).

Verified: type-10 emergers now keep vy=0 and walk horizontally toward the player
(tx decreases, ty constant) while animating. Affects every ground-following
monster (flags & 8), not just type 10.

## Still pending (needs DOSBox side-by-side)

- type4 (swinging spider) rotation needs `cos_tbl`/`sin_tbl` + orbs, not yet
  ported (orbs are decorative; trig tables can be extracted from staticres.c).
- `monster_add_orb` particle trails are no-ops.
- Hit-reaction sprite switch via `monster_spr_tbl` + `player_hit_monster_counter`
  (shake on counter==7) still missing; affects clubbing visuals, not movement.
- The forced `spr_num |= 0x2000` convention on monsters diverges from blues
  (blues uses absence of 0x2000 as a liveness gate); revisit if despawn timing
  looks off.
