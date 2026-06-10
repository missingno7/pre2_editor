# Sun/light pickup + gorilla accuracy pass

## Sun ON/OFF light pickup (num 180/181) — IMPLEMENTED

blues: item num 181 = light OFF, num 180 = light ON (sprites 234/233; on levels idx
1, 2, 13). They set g_vars.light flags; level_update_light_palette() then fades the
active 16-colour level palette toward light_palette_data (OFF) or back to the level
palette (ON), one accelerating step per tick (`palette[i] = src[i] - counter`,
snapping when within counter), recomputed from the base palette each tick.

Implemented faithfully: LIGHT_PALETTE_DATA table, self._light state, the num 180/181
pickup, and _update_light_palette() (called after _update_player_bonuses, like blues).
The fade mutates self.palette/self.rgb and bumps self._palette_gen; both renderers
re-bake their colour caches on a palette-gen change (PIL clears tile/sprite/bg/panel
caches; pygame _sync_assets includes _palette_gen). Verified: palette brightness
306 -> 141 (OFF, ~60-tick smooth fade to light_palette_data) -> 306 (ON, back to
base). The sun toggle is fluent and modifies the current palette, exactly as the
user expected.

## Gorilla accuracy fixes

- **Dormant when far (was assembling at level start).** blues returns early from
  level_update_boss_gorilla when the player is outside the activation box (x_dist>400
  or |dy|>250) WITHOUT stepping the anim, so the gorilla stays invisible (objects
  103-107 = 0xFFFF) until the player approaches. Our far-away branch was calling
  set_anim + step_anim, assembling/animating the gorilla while the player was still
  far (e.g. visible at level load). Now it just returns. Verified: parts hidden when
  far, assemble (5 parts) when the player comes within range.
- **data_st1 anim fixed.** blues `data_st1 = {0,0,0, 0x40,0x40,0x40, 0xFA}` (three 0
  frames); ours had only two. Corrected.
- State machine, movement, collision parts (obj1/2/3 mapping), defeat + lighter drop
  all verified faithful to blues.

KNOWN remaining (documented): the exact gorilla part-OFFSET table (pose) is
blues-derived and NOT present in PRE2_load.bin (different sprite numbering AND data
layout; the find_pos loop is in the ~40% of code recdis --scan doesn't reach). The
assembly ALGORITHM matches the original and all 19 frames composite coherently, but
byte-exact pose offsets need a dedicated trace of the gorilla routine entry point.
