# Tick rate, food kick-up, interpolation slot-identity

## Tick rate -> 21.8

The original is vsync/work-limited (mode 13h ~70 Hz); the PIT reload isn't cleanly
recoverable from the flat disasm (the only reachable `out 0x40` is in a
garbage-decoded region, with no preceding `out 0x43` mode set). The effective DOS
logic rate measured from the real game is ~21.8 ticks/sec (not a clean retrace
divider, hence non-integer). Set `TICK_HZ = 21.8` (float; the loop uses
`1/TICK_HZ` and the interpolation smooths the non-integer divide). Removed the
unused TICK_MS.

## Big food kicked up off the player

blues food pickup (num<=64): food still FALLING FAST (data.t.y_velocity >= 128) is
NOT collected -- it bounces off the player's head (`y_velocity = -y_velocity`,
`x_velocity = +/-32`, 50% chance shake_screen_counter = 7). Only slower food gets
collected. Ported into `_pickup_bonus_or_item`: the num<=64 branch now bounces the
fast food (and this is the correct ASM source of the "food shake"). Verified: food
at vy=160 -> not removed, vy=-160, x_vel=-32; slow food collected.

## Interpolation blink/shake (slot reuse)

Object slots are reused between ticks: items (55-74) are reassigned every tick as
the camera scrolls, and bonuses/sparks spawn/despawn. The renderer was lerping a
slot's previous-tick position to its current-tick position even when the slot now
held a DIFFERENT object -> the new object slid out of the old one's place (the
blink/shake, worst with many items on screen, e.g. level 1).

Fix: snapshot each slot's identity (`iact` = was active, `iref` = ref_index) in
`snapshot_prev`; in render, interpolate only when the slot held the same object
last tick (`iact` and `iref == current ref_index`), otherwise snap to the current
position. Items/monsters key on ref_index; bonuses (ref None) still rely on the
48px teleport-snap guard. Player and platforms always interpolate (no reuse).
Verified: a slot changing ref 3->7 snaps instead of sliding; all 16 levels clean.
