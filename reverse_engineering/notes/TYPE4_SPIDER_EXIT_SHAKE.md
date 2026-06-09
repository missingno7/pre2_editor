# Type-4 swinging spider, level exit, screen shake

## Type-4 spider AI (was missing)

`_update_monster_ai` handled types 0-3, 5-12 but not 4, so swinging spiders never
moved. Ported blues monster_func1_type4 + monster_rotate_pos:
- Added COS_TBL/SIN_TBL (p2/staticres.c trig tables, used as signed int8) to
  runtime/original_tables.py and `RuntimeMonsterState.type4_angle/type4_angle_step`.
- `_monster_rotate_pos`: obj.x/y = anchor + ((radius * (int8(cos/sin[angle])>>2))>>4).
- state 0 lowers the spider until the string reaches `swing_radius_px`; state 1
  swings the angle += 4 up to `swing_angle_limit`; state 2 oscillates the angle by
  an accumulating angle_step (sign from the angle's high bit). Reset angle/step on
  spawn (func2_type4).
Verified: spider sweeps ~110px horizontally through states 0->1->2.

## Level exit (was not activating)

The exit semaphore item starts inactive (runtime sprite 278, num 225, not
pickable). The LIGHTER (utensil num 46) activates it: blues changes every item
sprite 278 -> 279 (num 226, the exit). The runtime treated the lighter as a
generic utensil, so the exit never became touchable. Added
`_activate_exit_semaphore` (item rt-sprite 278 -> 279) called from the num==46
branch; touching num 226 then completes the level (-> _pending_level).
Verified: lighter flips the exit 278->279.

## Screen shake (counter set but never applied)

`shake_screen_counter` was set on the player's hard landing and decremented, but
the offset was never rendered, and food didn't set it. Now:
- render_frame applies a damped vertical jolt to the camera while the counter > 1
  (`cam_y += +/-counter` on alternating ticks).
- big food landing hard (`data_y_velocity >= 128` at the bounce) sets the counter
  to 7 (blues), in addition to the existing hard-fall = 8.
Verified: camera jolts +/-8 on alternating frames.

All 16 levels smoke + render clean.

Pending: the remaining shake sources (monster hit counter==7 -> 9, shifting
columns -> 7) and the orb particles on the spider string.

## Follow-up corrections (2026-06-09)

- Screen shake on SMALL food was wrong: I had added shake when any food bonus
  bounced on landing, which the ASM does NOT do. Removed it. blues only shakes
  on: hard player fall (8), monster hit counter==7 (9), damage item (7),
  screen-kill (9), shifting columns (7), and the food-pickup-throw at high speed
  (7) — never on a food landing. Added the damage (7) and screen-kill (9) shakes
  to the pickup handler; the hard-fall shake was already there.
- Player death animation no longer follows the camera (removed the _update_camera
  call from _update_death_animation; the camera stays where the player died and
  the body flies out of frame). Verified the camera doesn't move during death.

## Investigated, match blues (no change)

- Spawn-area enemy frequency (type 0/10): timing matches blues — respawn gate
  `current_tick>>2 < respawn_ticks` (= respawn_ticks*4 ticks), current_tick reset
  to 0 on despawn (blues monster_reset does the same), one active enemy per
  record. A type-10 enemy lives ~127 ticks (state-2 attack current_tick=30
  decremented every 4th frame = ~120). The "more often in DOS" is the frame-tied
  timers running at the slower 20 tps; not a logic bug. (NOTE: blues type-10
  state-2 has an early-return while dx<16 and hit_mask==0 that the port doesn't
  replicate, but adding it would make enemies live LONGER, i.e. spawn LESS often.)
- Small-food secret pounding (count<0x40 small-random): the spawn (random
  0x2080+num via level_get_random_bonus_spr_num), count decrement (count+1 drops
  then reveal), velocities and the diff<6 draw-counter throttle all match blues.
  The original level_collide_axe_bonuses/handle_bonuses_found lives in a
  data-interleaved region of the flat disasm that doesn't recover cleanly, so I
  could not diff against raw ASM; against blues it is faithful. Need the specific
  observed difference to pin down further.
