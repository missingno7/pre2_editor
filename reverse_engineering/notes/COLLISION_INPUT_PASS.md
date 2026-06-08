# Collision / attack / input accuracy pass

User report: attacking enemies wrong; hitboxes slightly off when jumping over
enemies; controls clumsy / not responsive.

## Fixes (runtime/game.py)

1. **Collision horizontal width was backwards** (`_objects_collide`). blues
   `level_objects_collide` uses the LEFT-most object's width as the overlap span
   (`b` = width of whichever object has the smaller left edge). The port had the
   width assignment swapped in both branches (used the right object's width), so
   horizontal hit tests were off. Now matches blues. -> "hitboxes slightly off".

2. **Club hit wrongly triggered wall-climb** (`_collide_attack_object_with_monsters`).
   blues sets `obj->data.m.flags |= 0x40` (object runtime flags), which is a dead
   write (that bit is never read). The port merged it into `monster_flags`
   (= blues `m->flags`), so the 0x40 bit fed the wall-climb path in
   `_update_monster_pos` (`m->flags & 0x40`) — a clubbed survivor would climb/jump
   instead of just taking damage. Added a separate `monster_obj_flags` field
   (= blues `obj->data.m.flags`); club now sets that. Also repointed type12's
   `& 0x20` check to `monster_obj_flags` (blues uses obj->data.m.flags there).
   -> "attacking enemies not correct".

3. **Tick rate 19 -> 30 Hz.** The physics constants are transcribed from blues p2,
   whose logic runs at 30 ticks/sec (`level_wait` targets 1000/30). Running those
   per-tick constants at the old 19 Hz bootstrap rate made motion ~37% too slow
   and input laggy (53 ms/tick vs 33 ms). -> "controls clumsy / unresponsive".
   CONFIRM SPEED against DOSBox; if the original differs, retune here.

## Verified

Headless tick 300 frames with move+jump+club on levels 0/1/2/4/8: no errors.

## Still pending

- `player_jump_monster_flag` / `collide_y_dist` stomp mechanism is still an inline
  approximation in `_update_player_collision` rather than the blues primitive
  (flag set inside level_objects_collide). Port faithfully next; note blues never
  resets the flag within a level (verify intent vs a missing per-frame reset).
- type10/type11 have specialized state==0xFF handlers in blues (use
  obj->data.m.flags & 0x20); the port routes all types through the generic
  `_monster_update_y_velocity_or_reset`.
- Hit-reaction sprite switch (`monster_spr_tbl` + `player_hit_monster_counter`,
  the 660-tick utensil power) + shake still missing.
