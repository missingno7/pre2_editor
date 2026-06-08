# Render interpolation + FPS/TPS overlay (View menu)

These are pure presentation features; the fixed-timestep engine tick is
unchanged (still TICK_HZ logic ticks/sec).

## Interpolation

- `RuntimeWorld.snapshot_prev()` records each drawable's position (player,
  runtime_objects, camera) into `ipx/ipy` / `_icam_*` right before each tick.
  Platforms reuse their existing `prev_x/prev_y` (set at tick start).
- The main loop calls `snapshot_prev()` before every `world.tick()`, then passes
  `alpha = accumulator / step` to `render_frame(alpha=...)`.
- `render_frame` lerps the camera and every sprite position by `alpha`
  (`_lerp`), with a 48px snap guard so teleports/spawns/wraps don't slide.
- When interpolation is off, `alpha=None` and positions render at the current
  tick exactly as before.
- The render cadence is raised (after-interval 6ms) while interpolation is on so
  the extra smoothness is visible; logic still steps only on accumulator overflow.

## FPS / TPS overlay

- The main loop counts rendered frames and samples `world.tick_count` over a
  ~0.5s window to compute fps and ticks/sec.
- `_draw_fps_overlay` draws a small green "NNN fps  NN tps" box top-left on the
  320x200 frame (scales up with the view).

## View menu

Added a "View" cascade with two checkbuttons:
- Interpolation (smooth FPS) -> `GameApp.interpolate`.
- FPS / ticks overlay -> `GameApp.show_fps`.
Both default off.

Verified: lerp places sprites between prev/cur tick positions (182->187 @0.5 =
184); renders at alpha None/0/0.5/1; overlay draws; all 16 levels smoke-clean
with interpolation on.
