# Level-complete bonus screen, pre-level map screen, and screen wipes

## Level-completed bonuses animation (blues level_completed_bonuses_animation)

Ported as a per-tick state machine (one step per engine tick = blues `level_sync`
pacing, so it runs at the 21.8 TPS logic rate). Phases:
- `walk_in`: player moves toward (60,175) by ±2/axis (literal blues `dx>=2` test).
- `pot_in`: cauldron objects (slots 2/3/4 = spr 100/98/104) slide in -3/tick to x<=155.
- `throw`: if any food collected, player throw anim (object_anim_tbl[18]),
  x_velocity 64 decayed by `12>>x_friction` (friction 2 -> 3/tick) until 0.
- `tally`: anim 17; flying food (slots 55-74) falls (`+=8` to vy<128, `y+=vy>>4`),
  landing at y>=145 adds `score_tbl[score_spr_lut[di]]` + sound 8. Burst boundary
  every 8 ticks (`draw_counter&7==0`) runs the di/al outer loop that feeds the next
  `level_items_count_tbl[di]` item into a free slot; ends when all spawned and none
  still flying (`al==113 && bp==0`).
- `outro`: anim 1; cauldron slides off -2/tick, player walks off, until obj2.x<=-52,
  then load next level + begin its map intro.

Added bookkeeping incremented during gameplay (were untracked):
`level_items_count[128]`, `level_items_total`, `level_complete_secrets`
(= active hidden-tile records at load), `level_complete_bonuses` (ref'd food),
`level_current_secrets` (in `_update_found_bonus_tile`). Percentage =
`current_secrets*100/(complete_secrets+complete_bonuses)` (current_bonuses is
always 0 in blues too). Added `SCORE_TBL` (17 vals) to original_tables.

### Text positioning fix
The draw_score screen uses `video_draw_string2` -> `video_draw_character_spr` ->
`video_draw_sprite(241+chr, x, y, 0)` which blits the sprite at the **top-left**
(x,y): blues `render_add_sprite` stores x,y verbatim and the SDL blit draws the
frame there; the `spr_offs`/height anchor is only applied by `level_draw_objects`,
NOT by direct video_draw_sprite calls. My `_draw_sprite` always subtracts the
sprite origin, which shifted the letters out of line with the top-left-blitted
number font. Added `_blit_sprite_topleft` for the letters and the '%' glyph; digits
use the allfonts+0x1C70 16x11 number font pasted at top-left. Now reads
"SCORE 0028100" / "LEVEL COMPLETED 16%" correctly aligned.

Triggered by the exit semaphore (num 226/258) via the iris wipe (below), and by a
new Develop-menu item "Trigger level end (bonus screen)".

## Pre-level "you are here" map screen (DOS-original, not in blues)

Assets: MAP.SQZ + CARTE.TRK — neither referenced by blues, both present in the DOS
data. MAP.SQZ (EAT) decodes to 64000 bytes = **640x200 planar 4bpp** (a two-screen
world map the journey crosses left->right). No embedded palette; the DOS map screen
sets one before showing it — the forest **level-0 palette** reproduces the natural
greens/browns (exact map palette pending EXE RE: PRE2.EXE is DIET-packed so strings
and the marker table aren't recoverable from the flat disasm).

Shows before every level incl. the first (`_begin_map_intro` after each load_level
that starts a new level). One pan step/tick: the map pans in from the right
(`offset` DOS_W -> clamp(160-marker_x, -(640-320), 0)), a blinking marker is drawn
at the level's spot, holds `MAP_HOLD` ticks, then plays the level music and starts.
Any key (after a release) skips. `MAP_MARKER_X[16]` are estimated spots across the
map — exact positions await EXE RE.

## Screen wipes (DOS-original, not in blues gameplay)

blues only uses `transition_screen` for menu screens; level start/end wipes are
DOS-original. Both pause gameplay and run at the logic rate; rendered as overlays
after the HUD.
- **Level entry**: `_wipe = {open}` set in load_level — a black curtain receding
  from centre to the edges (cave-entrance style; same geometry as the gate-teleport
  phase-2 open but full-height and on every level load).
- **Level exit**: the exit semaphore starts `_start_exit_iris()` — a black iris
  (visible circle shrinking to 0) centred on the player's screen position; at full
  close it hands off to `_start_level_complete` (the bonus screen).

All 16 levels smoke-clean through map-intro -> open wipe -> gameplay -> exit iris
-> bonus screen -> next level.

## Follow-up corrections

- **Bonus screen draw order**: blues level_draw_objects draws slots high->low, so
  higher slots are BEHIND. Food (55-74) must fall *behind* the cauldron (2-4), and
  the player (slot 1) is *in front* of the cauldron. The first cut drew them in the
  wrong order (food in front). Now renders food -> cauldron -> player.
- **Bonus screen interpolation**: `_render_complete(alpha)` now interpolates the
  food fall / cauldron slide / player walk (ipx/ipy lerp with the iact snap guard),
  so it is smooth like gameplay instead of stepping at the tick rate.
- **Map marker**: now the player sprite (an idle frame captured at intro start)
  standing on the level's spot, not a drawn diamond.
- **Map scroll distance**: the pan now traverses the FULL 640px map (start at the
  end opposite the marker) ending centred on the marker, instead of only ~one screen
  for left-side markers. `_render_map_intro(alpha)` interpolates the pan.
- **Map scroll DIRECTION (user-confirmed)**: starts on a BLACK screen with the map
  fully off the right edge (`draw = +DOS_W`); the map appears from the RIGHT and
  scrolls LEFT (`draw` decreasing). (Earlier it started with the map already on
  screen and panned the wrong way.) Both renderers read the same `_map_intro`
  state, so the fix applies to both.
- **Map scroll DISTANCE (user-confirmed)**: now scrolls all the way to the END of
  the map (`target = -span`, the far right end of the 640px world on screen), so the
  whole journey pans past instead of stopping early at the marker.
- **Map marker sprite**: the gameplay idle frame is sprite 9; DOS uses a DIFFERENT
  dedicated player frame for the "you are here" marker (exact frame is in the
  unreadable EXE data). `MAP_MARKER_SPR` holds a best-guess standing frame (0)
  pending the user picking the exact frame from the rendered player-frame grid.
- **Develop "Trigger level end"** now runs the real iris-close (was calling
  `_start_level_complete` directly, skipping the curtain).

## Backend: pygame is now the only game backend (Tk removed)

The Tk/Pillow `GameApp` was removed from the game (`main()` now always launches
`run_pygame_app`; Tk lives on only in the separate editor project). This also fixed
a crash: when pygame raised, `main()` fell back to Tk which then crashed on
`world._last_cam` — two black windows.

IMPORTANT duplication caveat: the pygame backend (`PygameSurfaceRenderer`) is a
SEPARATE native renderer from `RuntimeWorld.render_frame` (the PIL renderer kept
for headless tests). The animation STATE lives in game.py (shared), but the DRAW
code is duplicated. The `'offset'` crash happened because the map-intro key was
renamed offset->draw in game.py but not in the pygame renderer, and the pygame
renderer was also missing the wipes / new marker / bonus draw-order+interp. Both
renderers are now in sync. When changing any render path, update BOTH and test the
pygame one headlessly: `SDL_VIDEODRIVER=dummy`, `pygame.display.set_mode`,
`PygameSurfaceRenderer(world, pygame).render(alpha)` -> `pygame.image.save`.
- **Fall-off-bottom death**: the ground probe in `_asm_update_tile0` defaulted an
  out-of-bounds tile to attr1=1 (solid), leaving an invisible floor at the map's
  bottom edge so the player never fell off to die. blues `level_get_tile` returns 0
  there, so the floor attr is tile 0's (empty). Now below-map (below_ty >= height,
  x in range) uses `tile_attributes1[0]`; the player falls off the bottom edge and
  the off-screen check kills them. Sides/top stay solid to keep the player in bounds.
