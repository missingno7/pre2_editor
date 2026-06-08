# Club table field swap + tick rate

User: game feels too fast; club should auto-swing like the original; hitting
enemies behaves wrong.

## Root cause (single bug for both attack issues)

blues `club_anim_t` is `{ anim, a, power, c }` where `a` = club_anim_duration
(recovery/cooldown between swings) and `power` = club damage. `CLUB_ANIM_SPECS`
in runtime/original_tables.py stores them in that order, e.g. club 0 = (0x00, 2,
0x19, 0): a=2, power=25.

`_update_player_state_attack_367` unpacked them SWAPPED:
`_off, power, duration, _flags = club_anim_spec(...)` → it took `a`(=2) as the
damage and `power`(=25) as the cooldown.

Consequences:
- club_anim_duration = 25 → ~25-tick lockout after each swing (mask forced to 0
  while club_anim_duration != 0), so no autoswing; one swing then a long pause.
- club_power = 2 → almost no damage / wrong knockback → "hitting enemies wrong".

Fix: unpack as `_off, duration, power, _flags`. Now: hold attack → continuous
autoswing (swing ~6 ticks, 1-tick recovery, repeat), club_power = 25/30.

This long lockout was also a big part of the earlier "controls clumsy /
unresponsive" complaint — attacking froze input for ~25 ticks.

## Tick rate

Reverted 30 -> 20 Hz. The original has NO fixed logic rate (vsync-capped mode 13h
~70 Hz, CPU-bound; all retrace-wait call sites are inside the video fade/scroll/
copy routines, not a per-frame logic cap), so the felt speed is reference-
dependent. 30 felt too fast; 19's unresponsiveness was largely the club bug above.
TICK_HZ is a single constant to tune against DOSBox side-by-side.

## Verified

Autoswing trace: continuous swings while fire held, club_power 25. Headless tick
400 frames levels 0/1/2/4/8 with move/jump/club: no errors.
