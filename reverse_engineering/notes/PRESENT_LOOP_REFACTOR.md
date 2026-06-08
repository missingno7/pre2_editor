# Present/loop refactor: overlays layer, FPS==TPS, vsync, perf

## Overlays are now a separate vector layer

FPS/TPS and the debug (F1) overlay are no longer drawn into the game pixels.
`_update_overlays` manages independent Tk canvas items (text/lines/rect) above
the game image (`canvas_image`):
- FPS/TPS: a green canvas text item, top-left.
- Debug: red anchor cross, cyan side-probe line, yellow tile rect, and a stats
  text line — all canvas vectors, positioned with the render's interpolated
  camera (`world._last_cam`) and the window scale.
render_frame no longer draws either overlay.

## FPS == TPS without interpolation

Old loop rendered on a fixed `after` interval regardless of ticks, so FPS (~27)
didn't match TPS (20). New loop:
- fixed-timestep accumulator drives ticks at TICK_HZ;
- interpolation OFF -> present ONLY when a tick happened this iteration, so FPS
  exactly equals TPS;
- interpolation ON -> present every iteration, blending frame-1 -> frame-2 by
  `accum/step`.

## VSYNC-like frame cap

View -> "Frame rate (interpolation)": 30/50/60/75/120/144/Uncapped -> `target_fps`.
The scheduler aims for that interval and COMPENSATES for the frame's work time
(`after(target_ms - work_ms)`), so the actual FPS tracks the target instead of
falling to ~1/(interval+work).

## Performance

Measured render+resize(3x) = ~4.5 ms (~220 fps ceiling); the 45-55 fps cap was
the Tk pipeline. Fixes:
- Reuse the PhotoImage buffer with `photo.paste(img)` instead of allocating a new
  `ImageTk.PhotoImage` every frame (the big win).
- Work-time-compensated scheduling (above) removes the implicit
  interval+work_time stall that limited effective FPS and caused uneven pacing
  (stutter).

## Interpolation stays simple

snapshot_prev() stores frame-1 positions before each tick; render lerps frame-1
-> frame-2 (current) by alpha with a 48px teleport-snap guard. Engine tick is
untouched (TPS overlay confirms it stays at TICK_HZ).

Verified headless: parse + render at various alpha on all 16 levels.
