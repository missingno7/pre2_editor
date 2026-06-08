# Performance, solid platforms, hit-blink, HUD

## Performance (the choppiness)

render_frame redrew the 320x200 background (64000 px) and every tile per-pixel in
Python each frame, plus called img.load() once per tile and spr.convert("RGB")
per sprite. That exceeded the ~25 ms frame budget -> dropped frames.

- Background is static/viewport-fixed -> precomputed once per level as `_bg_image`
  (load_level); each frame is `_bg_image.copy()`.
- Tiles/front tiles cached as RGBA images (`_tile_image`/`_front_image`, colour 0
  transparent) and blitted with `frame.paste(img, pos, img)`.
- Sprites pasted as RGBA directly (dropped per-draw convert).
Result: render_frame ~5 ms/frame (~180 fps render-bound) vs the old per-pixel path.

## Solid platforms

Platforms moved (after the earlier flags-0x40 fix) but the player fell through:
the ground probe only checked tiles. Added `_apply_platform_landing` (blues
level_update_objects_decors_helper): a falling player whose feet reach a
platform's top surface lands on it (snap y, vy=0, on_ground), one-way (only from
above). With on_ground set, the riding flag/carry path works. Verified: player
lands on a level-0 platform and is carried along.

## Hit blink

- Monsters: a surviving club hit sets `obj.hit_flash = 6`; rendered solid WHITE
  while it counts down (keeps the silhouette via the alpha mask).
- Player: while `hit_counter > 0`, drawn solid BLACK on alternating frames (blink)
  during the invincibility window.
Done via a new `tint` arg on `_draw_sprite`. (The original gates a flicker on
obj->hit_counter; the white/black recolour matches the observed DOS flash.)

## HUD

`_draw_hud` (bottom 16 px bar) shows lives (x N), energy hearts, score, and the
BONUS letters (lit per `bonus_letters_mask`) + level number — mirrors what blues
level_draw_panel shows, not pixel-accurate (per request). Added PlayerState.lives
and RuntimeWorld.score (+10 per score object; placeholder value). 1UP gives a
life; death decrements lives and refills energy.

All 16 levels render + smoke-test clean.

## Pending
- Full player death animation sequence (currently the hit anim + black blink, then
  respawn; no dedicated death/spin sequence yet).
- Score value is a placeholder (+10/pickup), not the real score_tbl values.
- HUD is drawn over the bottom of the tilemap (no separate 176px play area yet).
