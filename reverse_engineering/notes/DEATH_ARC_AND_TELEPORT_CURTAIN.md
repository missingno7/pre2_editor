# Death launch arc + teleport curtain

## Death animation = upward launch (blues level_player_death_animation)

The death sprite (33) is LAUNCHED UP, not dropped. blues:
```
y_velocity = 15
each frame: x_pos += x_velocity (+/-5, toward screen centre)
            y_velocity = max(y_velocity - 1, -16)
            y_pos -= y_velocity      # +y_velocity => moves UP
```
So y_velocity 15 -> ... -> 0 (apex) -> ... -> -16: the player rises, slows, then
falls. `x_velocity = +5 if player.x < camera_x+160 else -5`.

Runtime now uses pixel-velocity fields `death_vx/death_vy`:
`_start_death` sets death_vy=15, death_vx=+/-5; `_update_death_animation` does
`x += death_vx; yv = max(death_vy-1,-16); y -= yv; death_vy = yv`. Verified: the
player rises ~105px to an apex then falls (was previously just falling).

## Teleport curtain (DOS gate transition; not in blues)

The original level_update_gates wraps the teleport in `video_transition_close()`
... teleport ... `video_transition_open()`. blues stubs these. Observed DOS
behaviour: a black curtain rolls from top AND bottom to the centre (vertical
close), the teleport happens at the fully-black apex, then black recedes from the
centre out to left AND right (horizontal open).

Implemented as a world transition state machine (gameplay paused during it):
- `_update_gates` no longer teleports instantly; it sets `_trans_phase=1`,
  `_trans_pending=(dst_x,dst_y,cam_x,cam_y)`.
- tick early-returns to `_update_transition` while `_trans_phase != 0`.
- phase 1 (close, TRANS_CLOSE_FRAMES): at the apex apply the teleport +
  `snapshot_prev` (so interpolation doesn't slide across it), go to phase 2.
- phase 2 (open, TRANS_OPEN_FRAMES): finish -> phase 0.
- `_draw_transition(frame, alpha)` draws the curtains; phase 1 = two black bars
  top/bottom growing to centre (height = f*PLAY_H/2), phase 2 = two black bars
  left/right shrinking from the edges (revealed centre width = f*DOS_W). `alpha`
  smooths the motion between ticks.
Verified: gate -> close (top black, centre visible mid-close) -> teleport at apex
(player to dst) -> open -> done (~23 ticks).
