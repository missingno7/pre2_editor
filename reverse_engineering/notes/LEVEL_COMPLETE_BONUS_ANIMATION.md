# Level-completed bonuses animation

Port of blues `level_completed_bonuses_animation()` (p2/level.c ~3476-3708): the
end-of-level showcase where the player tosses every food item collected during
the level into a cauldron, each landing adding its `score_tbl` bonus, with the
score and completion percentage shown.

## Trigger

Touching the exit semaphore (num 226 / 258) used to set `_pending_level`
immediately. It now calls `_start_level_complete()` instead; the animation runs
to completion and then `load_level(level_index + 1)`.

## Pacing (the user's priority)

blues drives the animation with blocking while-loops + `level_sync` (one tick per
iteration). The port converts this into a per-tick state machine: `tick()` checks
`self._complete` first and, while set, calls `_complete_tick(inp)` and returns.
**One animation step = one engine tick**, so the whole sequence plays at the DOS
logic rate (TICK_HZ = 21.8). No render interpolation is applied to this screen
(`_render_complete` ignores alpha) — DOS had none here either. A full run is ~296
ticks (~13.5 s).

## Phases (`self._complete["phase"]`)

1. `walk_in` — player moves toward screen (60,175) by ±2/axis (blues' literal
   `if (dx >= 2)` test; snaps the axis otherwise).
2. `pot_in` — cauldron objects spawn at x=360 (obj2 spr100 y175 = pot, obj3 spr98
   y148 = food showcase cabinet, obj4 spr104 y155 = bubbling hearts) and slide
   left 3px/tick until obj2.x ≤ 155.
3. `throw` — only if `level_items_total != 0`: player throw anim (18), object
   x_velocity 64 decayed by `12>>x_friction` (friction 2 → 3/tick) until it
   stops.
4. `tally` — the di/al/bp loop. Collected food (`level_items_count[]`, indexed by
   `food_sprite-110 = num-57`) is fed one item per ~8-tick burst into a free slot
   55-74 at (155,0); each falls under +8 gravity and, on reaching y≥145, adds
   `SCORE_TBL[SCORE_SPR_LUT[di]]` and plays sound 8. Burst boundary = every 8th
   `draw_counter`; ends when all items are spawned AND none are still falling
   (`bp == 0`).
5. `outro` — player idle anim (1); cauldron slides off −2/tick, player walks right
   (+2, or +3 once obj4.x<0) until obj2.x ≤ −52, then advance level.

obj4's heart/fork sprite cycles 104..109 every 4th frame
(`_complete_fixup_hearts`, blues `..._fixup_object4_spr_num`).

## Bookkeeping added

Counters reset in `_init_secret_bonus_tiles` and updated during play:
- `level_items_count[idx]`/`level_items_total` — incremented on food (num≤64,
  slow branch) and collectible (num≤166) pickup, `idx = num-57`.
- `level_complete_secrets` — count of active hidden-tile records at load.
- `level_complete_bonuses` — incremented on ref'd-food pickup.
- `level_current_secrets` — incremented in `_update_found_bonus_tile` (reveal).
- Percentage = `current_secrets * 100 / (complete_secrets + complete_bonuses)`
  (blues `current_bonuses` is never incremented, so it is omitted).

## Rendering (`_render_complete`)

Black screen + objects drawn in table order (player slot 1 behind, cauldron 2-4,
food 55-74) via `_draw_sprite(camera=(0,0))`, then `_complete_draw_score`:
- "SCORE" + 7 digits, "LEVEL COMPLETED" + percentage + '%' glyph.
- Text via blues offset→pixel math `(x=(off*8)%320, y=(off*8)//320)`.
- Letters = sprites `241+(ord-0x41)` (blues `video_draw_character_spr` /
  `video_draw_string2`); '%' = code 0x1A.
- Digits = a new ALLFONTS number font (`_number_glyphs`, allfonts+0x1C70, 16×11,
  num*88), built in `_build_panel_assets`.

`SCORE_TBL` (17 entries) added to original_tables.py.

Verified: all 16 levels run the full walk_in→pot_in→throw→tally→outro sequence in
296 ticks, consume every item, and advance to the next level; the screen renders
the cabinet, cauldron, falling food, player, score and "LEVEL COMPLETED N%".
