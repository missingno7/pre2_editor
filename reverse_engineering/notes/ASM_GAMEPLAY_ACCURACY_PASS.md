# ASM gameplay accuracy pass

Goal: stop adding new boss features for a moment and fix runtime behavior where
we can tie a discrepancy to the original control flow / data layout.

## 1. Tick-order correction

`blues/p2/level.c::do_level()` updates gameplay in this order:

1. `level_update_objects_hit_animation()`
2. `level_update_objects_axe()`
3. `level_update_objects_monsters()`
4. `level_update_objects_club_projectiles()`
5. `level_update_objects_bonuses()`
6. `level_update_objects_bonus_scores()`
7. `level_update_objects_decors()`
8. `level_update_player()`
9. `level_update_player_collision()`
10. `level_update_objects_items()`
11. `level_update_gates()`
12. `level_update_columns()`
13. camera/scroll/update helpers

`level_update_objects_monsters()` calls `boss_update()` before walking the normal
monster slots.  The runtime previously updated gorilla/tree/minotaur after the
player/items/gates, which made boss contact and special boss spawned hazards use
the wrong frame of player/projectile state.

Runtime change:

- boss updates now run before normal monsters and before player movement;
- axe/club collision uses object slot 0 from the previous tick;
- object slot 0 is hidden at the start of the player update, matching
  `level_update_player()`.

This is gameplay-visible, not just cleanup: one-tick differences matter for boss
hits, stomp windows, and minotaur hazard timing.

## 2. Landing hit spark restored

`level_update_tile_attr1_helper()` always calls
`level_init_object_hit_from_player_pos()` when `player_jumping_counter > 4`,
before deciding whether the impact is hard enough to bounce/shake.

Runtime change:

- `_apply_attr1_ground()` now emits the small landing spark for all landings past
  that threshold, then applies the existing hard-fall bounce/shake branch.

## 3. `level_update_columns()` ported

Column records were already parsed for editor overlays, but the runtime did not
execute the original column system.  Levels with active column records:

- runtime level 8 / `LEVEL9`: 4 columns
- runtime level 13 / `LEVELE`: 3 columns

Original behavior:

- `load_level_data_init_columns()` reconstructs `columns_tiles_buf` from the
  current level tilemap after hidden-secret tile initialization;
- once triggered, `level_update_columns()` shakes the screen;
- every fourth engine tick it scrolls the column upward by one tile row and
  restores the next hidden bottom row from `columns_tiles_buf`;
- once `y_target` reaches zero, the column disables itself.

Runtime change:

- added mutable `RuntimeColumnState`;
- reconstructed the same per-level `columns_tiles_buf` after secret bonus tiles;
- added `_update_columns()` after gates and before camera/off-screen culling.

## 4. Boss/static table ASM probe status

A reproducible probe script was added:

```text
reverse_engineering/tools/scan_boss_tables.py
```

Current result against `disasm/PRE2_load.bin`:

- `boss_minotaur_seq_data` first 8 bytes are visible at `0xB5BF`/`0xB5C0`, but
  the full sequence is not a clean contiguous byte-array hit;
- `boss_gorilla_data` and `boss_gorilla_spr_tbl` are not found as exact
  contiguous word-array hits;
- tree boss `boss_level5_pos*_data` arrays are not found as exact contiguous
  word-array hits;
- the area around `0x8650..0x86D0` contains fragments resembling tree position
  words, but also interleaved bytes/words, so it is not safe to replace the
  current blues-derived tree tables from this flat binary alone.

Conclusion: keep gorilla/tree/minotaur tables marked as **blues-derived pending a
segmented ASM data map**.  Do not “improve” those tables by eyeballing loose
hex fragments; that would likely make accuracy worse.

## 5. Camera/scroll state machine follow-up

See `ASM_CAMERA_SCROLL_PASS.md`.  The runtime now ports the original-shaped
`level_update_scrolling()` camera state instead of the previous broad dead-zone
approximation, including tilemap_w-based horizontal clamp and vertical easing.
The `vscroll_offsets_data` bytes remain blues-derived pending a segmented ASM
address; `tools/scan_scroll_tables.py` records the current flat-binary probe.
