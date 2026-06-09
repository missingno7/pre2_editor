# DOS tick rate + fall-off-screen death

## Tick rate -> 21.8 ticks/sec

The original PRE2 is vsync/work-limited (mode 13h ~70 Hz), so the effective logic
rate is NOT a clean retrace divider. Attempted to recover a PIT reload from the
disasm: the only `out 0x40` in "reachable" code (0x1C31) is in a garbage-decoded
region (no `out 0x43` mode-set precedes it), so the rate isn't statically
recoverable. Set `TICK_HZ = 21.8` (measured from the real DOS game). The engine
steps at exactly this rate; render interpolation smooths it to the display FPS
even though 21.8 doesn't divide 60 evenly (the in-between render frames just
interpolate, never showing a "real" intermediate tick). Removed the now-unused
TICK_MS.

## Falling out of the screen kills the player

blues `level_update_player_decor` calls level_player_die when the player goes
off-screen:
- `abs(player_tile_y - camera_tile_y) > TILEMAP_SCREEN_H/16` (11),
- `abs(player_tile_x - camera_tile_x) > TILEMAP_SCREEN_W/16` (20),
- or `y_pos > (tilemap.h + 1) << 4` (below the map).

The port only killed the player below the WHOLE level (`y > world_h + 128`), so a
fall into a mid-level pit (camera clamped, player off the bottom of the screen)
never died. Replaced the trigger with the faithful off-screen check (PLAY_H//16,
DOS_W//16, below-map). `_respawn_player` now snaps the camera onto the respawn
point so the off-screen check doesn't immediately re-trigger.

Verified: an off-screen drop triggers death; respawn lands the camera within the
threshold; no false deaths during normal play on any of the 16 levels.
