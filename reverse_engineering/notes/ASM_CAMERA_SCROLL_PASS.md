# ASM/gameplay accuracy pass: camera and scrolling

Goal: replace the remaining broad camera approximation with the original
`level_update_scrolling()` shape, because camera position is gameplay state in
PRE2: it affects enemy activation/despawning, gate/cave placement, off-screen
fall death, and the LEVEL6 scripted downward-scroll kill.

## What was inaccurate

The runtime used a generic pixel dead-zone:

- horizontal margin around `112px`;
- relaxed vertical margins around the player sprite centre;
- clamp based on the full decompressed 256-tile map width.

The original code does not do that.  The `blues/p2/level.c` reference mirrors a
more specific tilemap state machine:

- `camera_x/y = tilemap.{x,y} * 16 + scroll_d{x,y}`;
- normal horizontal smooth-scroll keeps the player between `x = 128` and `x = 192`;
- horizontal `scroll_dx` is forced even with `& ~1`;
- horizontal max is `tilemap_end_xpos()`, normally `level.header.tilemap_w`, not
  the whole 256-tile backing row;
- once the player is beyond `tilemap_w + 20` tiles, the max temporarily becomes
  `256 - 20`, matching the original gate/cave handling;
- vertical scrolling is driven by `level_yscroll_center_flag`,
  `tilemap_yscroll_diff`, `scrolling_top`, and the 132-byte easing table used by
  `level_adjust_vscroll_up/down()`;
- `scrolling_mask & 4` does a scripted 1px/tick downward scroll, but only after
  the level-start camera placement pass (`tilemap_adjust_player_pos_flag` false).

## Runtime changes

Implemented in `runtime/game.py`:

- added persistent original-style scroll state:
  - `_level_xscroll_center_flag`
  - `_level_yscroll_center_flag`
  - `_tilemap_yscroll_diff`
  - `_tilemap_adjust_player_pos_flag`
- added helper equivalents of:
  - `tilemap_end_xpos()`
  - `level_adjust_hscroll_left/right()`
  - `level_adjust_vscroll_up/down()`
  - `level_adjust_x_scroll()`
  - `level_adjust_y_scroll()`
  - `level_init_tilemap()` camera placement
- replaced full-world clamp with original `tilemap_w`-based clamp;
- kept LEVELA/minotaur fixed at tilemap offset `(0,0)`;
- gate transitions now reset scroll centre flags when applying the new tilemap
  position.

`runtime/original_tables.py` now includes `VSCROLL_OFFSETS_DATA`, copied from
`blues/p2/staticres.c` so the easing behavior is not an invented curve.

## Binary / ASM status

Added reproducible probe:

```text
reverse_engineering/tools/scan_scroll_tables.py
```

Current result against `disasm/PRE2_load.bin`:

- the full 132-byte `vscroll_offsets_data` sequence is **not** visible as an
  exact contiguous byte-array hit;
- shorter head/middle/tail signatures also do not hit;
- the small threshold-byte signature around the camera state machine also does
  not hit cleanly in the flat load image.

So this pass is **not claiming the vscroll table address is ASM-confirmed**.  The
control-flow shape is ported from the best available C reconstruction, and the
binary probe result is recorded so we do not falsely mark this table as verified.
The next step for direct ASM proof is a better segmented/code-data map around the
scrolling routine, not blind pattern replacement.

## Gameplay effect

This should make these cases closer to DOS:

- initial camera placement at level start;
- horizontal camera in long levels and caves;
- no accidental horizontal scroll over unused 256-tile map space;
- vertical catch-up speed when jumping/falling;
- off-screen death timing, because the tilemap origin is now closer to the
  original state machine.

## 2026-06-09 policy correction: keep smooth regular camera

After review, we intentionally **do not** want full DOS camera parity for normal
level traversal.  The broad, smoother runtime dead-zone camera is now treated as
the one deliberate gameplay modernization.

The original-shaped camera helpers remain in `runtime/game.py`, but regular
`_update_camera()` no longer uses them.  They are reserved for camera behavior
that is gameplay-critical and should stay accurate:

- LEVEL6 / tree-boss shaft `scrolling_mask & 0x04`: scripted downward camera
  movement at 1 px/tick;
- LEVELA / minotaur: fixed-screen tilemap boss arena at camera `(0, 0)`;
- gate `tilemap_noscroll_flag`: camera freeze after scripted teleports.

Normal horizontal/vertical follow now uses the previous smoother margin-based
logic again, including full decompressed-map clamping, because this is the one
known inaccuracy we want to keep for playability/readability.
