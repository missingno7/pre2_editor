# Camera relax, platform jump-off, mid-air spawn, unified blink

## 1. Vertical camera too tight (our own scrolling, diverges from ASM)

The PLAY_H (176) change shrank the vertical dead zone to ~16px. Rewrote the
vertical follow with an explicit relaxed dead zone within PLAY_H:
`CAMERA_MARGIN_TOP=52`, `CAMERA_MARGIN_BOTTOM_PLAY=52` -> player moves freely in a
~72px band before the camera scrolls. (Intentionally not ASM-faithful.)

## 2. Platform: stuck floating, can't jump off (esp. falling platforms)

While riding, the player has no tile under their feet, so the decor's
`_update_player_jump_fall_from_decor` (port of level_update_player_jump) treated
them as airborne every frame and set `nojump_counter = 6`; the jump state then
falls back to idle (`if nojump_counter > 0`). The platform landing could only
decrement it 6->5, so it was stuck and jumps were blocked.

blues gates this with `force_x_scroll_flag` (set in the platform helper, which
also calls level_player_reset). Mirrored with `self._on_platform`:
- reset False at the top of `_apply_platform_landing`, set True when the player
  lands/rides,
- `_update_player_jump_fall_from_decor` returns early when `_on_platform` (no
  airborne fall update -> nojump not re-armed),
- landing also calls `_player_reset_after_ground_contact` (decrements nojump).
Verified on a type-8 falling platform: nojump reaches 0 and the player jumps off
(vy=-65); riding stays idle with perfect tracking.

## 3. Enemies spawning mid-air (type-10 ground-emerge)

`_spawn_monsters` type 10 activated the enemy even when the floor search found no
ground (y stayed at player.y -> mid-air -> falls). blues monster_func2_type10
returns false (no spawn) in that case. Now tracks `found` and only activates when
a floor is found.

## 4. Unified hit/expire blink (only black or white)

The game only ever blinks black or white. Replaced the bonus visibility-flicker
with a tint: hit enemies -> white (`_BLINK_WHITE`), expiring bonus drops (bones)
and the hit player -> black (`_BLINK_BLACK`), all on alternating frames. Bones now
blink black like the player before disappearing.

All 16 levels smoke + render clean.
