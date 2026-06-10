# Enemy accuracy / intro / camera option pass

This pass keeps the project policy from the previous camera discussion:
regular gameplay uses the smoother modern follow camera by default, while the
DOS/original scroll state is available as an optional `vanilla` camera view and
special scripted camera modes remain accuracy-critical.

## Implemented

### Startup / main menu screens

Added the blues-style startup image sequence:

- `TITUS.SQZ`
- `PRESENT.SQZ`
- `MENU.SQZ`

These files unpack as a 768-byte VGA6 palette followed by a 320x200 chunky
framebuffer. The runtime now shows them before the existing BEGINNER/EXPERT
mode-select screen. Palette fades and the animated `MENU2` demo are still not
ported.

### Smooth vs vanilla camera option

Added `RuntimeWorld.camera_mode`:

- `smooth` is the default and remains the intentional modernization.
- `vanilla` routes ordinary camera follow through the original-shaped scroll
  helpers for comparison.
- LEVELA/minotaur remains fixed at `(0, 0)`.
- `scrolling_mask & 0x04` still performs the scripted 1 px/tick downward camera
  scroll used by the LEVEL6 tree-boss shaft.

The pygame backend exposes this through:

- View menu -> Camera: Smooth / Vanilla
- `F4`
- FPS overlay suffix: `smooth-cam` / `vanilla-cam`

### Three-utensil monster-hit powerup

The original has `player_utensils_mask` and `player_hit_monster_counter`. After
collecting utensil bits `0x08 | 0x10 | 0x20`, `player_hit_monster_counter` is set
to `660`. While active:

- player/monster contact kills the monster instead of hurting the player;
- `monster.hit_mask = anim_high_bits | (player_hit_monster_counter & 0xff)`;
- monster animation pointers stop advancing;
- hit replacement sprites from `monster_spr_hit_table` are shown;
- at counter value `7`, the screen shake is triggered.

Runtime now models this with `PlayerState.utensils_mask`,
`PlayerState.hit_monster_counter`, `_update_player_bonuses()`, and the updated
monster animation path. The counter decrement order was also aligned with blues:
`level_update_objects_anim()` decrements counters before
`level_update_player_bonuses()` can set a fresh value of 660.

Flat ASM landmarks seen while searching for this path include the `0x38` utensil
mask tests around unpacked offsets near `1c28`, `42fe`, and `4b89..4b8b`, but the
exact segmented function labels are still not safely assigned from the flat dump.
The gameplay behavior in this pass is therefore blues-grounded and ASM-guided,
not fully address-labeled.

### Monster culling / visibility height

The runtime was using full DOS screen height (`200`) in two places where blues
uses the active tilemap playfield height (`176`):

- vertical offscreen retention: `PLAY_H + 124`, not `DOS_H + 124`;
- monster visibility: `(PLAY_H >> 4) + 2`, not `(DOS_H >> 4) + 2`.

This changes when monsters despawn/reactivate at the bottom edge of the HUD.

### Tree-boss interaction guard for type-10 monsters

Blues has a LEVEL6 special case for type-10 enemies while the tree-boss arena is
shaking or in boss state 3. Runtime now mirrors this guard in both spawn and
update paths so those enemies do not keep behaving normally during the boss
phase.

### Spider web/orb trail pixels

Blues type-2 and type-4 monsters call `monster_add_orb()` and `level_draw_orbs()`
for one-frame white spider-web/orb trail pixels. Runtime now has an `orb_tbl[20]`
equivalent:

- type-2 vertical monsters add orbs from their vertical offset;
- type-4 swinging/rotating monsters add orbs while extending and rotating;
- Pillow and pygame renderers draw the particles without mutating them during
  interpolation;
- the logic clears and regenerates them per gameplay tick.

## Still inaccurate / next candidates

- `MENU2` animated menu/demo path is not ported yet.
- Startup/menu palette fades are not ported yet.
- Exact segmented ASM labels for `player_hit_monster_counter` and the related
  `0x38` utensil tests still need a better DS/data-map reconstruction.
- Enemy type-by-type audit remains open. Good candidates:
  - type 0/2/4/10/11 timing comparisons against recorded DOS footage;
  - exact death/hit replacement table mapping from ASM instead of blues;
  - edge cases where `monster.hit_mask` affects AI transitions;
  - visual order of orbs/flies/snow/front tiles in pygame vs DOS.
