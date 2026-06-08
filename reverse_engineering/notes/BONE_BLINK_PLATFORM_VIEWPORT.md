# Bone blink, platform riding, viewport active area

## Bone blink + disappear (blues level_update_objects_bonuses + draw)

The bonus update set `ttl=15` when the TTL counter hit 0 but never decremented it,
so bones with counter<0 lived forever. blues: at counter==0 sets hit_counter=15;
while counter<0 it stays until hit_counter==0; the draw loop decrements hit_counter
and skips drawing on 3/4 frames (the blink). Now `_update_runtime_bonuses`
decrements `ttl` while counter<0 and removes at 0; render skips bonus slots 23..54
on `(tick & 3)!=0` while ttl>0 -> flicker. Verified: 6 bones live ~198 frames,
blink ~15, gone at t=212.

## Platform riding

Static and vertical (up/down) riding already produced idle anim with the player
tracking the platform exactly (gap 0). The real-play fault was fast-fall
tunnelling: the landing used a tight 10px band, so a player falling faster than
10px/frame skipped past a thin platform (blues uses sprite-box overlap, ~32px).
`_apply_platform_landing` now uses a crossing test (feet were at/above the surface
last step, at/below it now) plus a small resting band. Verified: lands from a fast
fall and rides with no fall-animation frames.

## Viewport active area (the 1px gap)

blues TILEMAP_SCREEN_H = GAME_SCREEN_H - PANEL_H = 200 - 24 = 176: the playfield is
176 px, the bottom 24 px is the panel. The panel strip is 23 px (176..198), so the
24th line (199) is black, outside the active area. Added `PLAY_H = 176`:
- camera vertical clamp/follow use PLAY_H (max_y = world_h - PLAY_H),
- tiles render only up to PLAY_H,
- `_draw_hud` black-fills PLAY_H..200 before pasting the 23px panel, so y=199 is
  black.
Verified: y=199 is solid black; the panel renders at the boundary.

Pending: monsters/sprites are still drawn into the 176..198 band before the panel
covers it (panel is opaque so it's hidden) — could crop for cleanliness; camera X
unchanged.
