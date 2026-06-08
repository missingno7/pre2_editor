# Bones, energy, gates/teleports, platforms

## Bones (level_monster_die)

`_monster_die` dropped a single bone via the old single-spawn `_add_bonus_object`.
Now uses `_add_object23_bonus(0x2046, x, y, 48, -128, 6)` -> 6 bones that scatter
(alternating x velocity, decreasing y), TTL 198 so they disappear after a while.
Verified: 6 bones, velocities (48,-128)(-48,-128)(32,-144)(-32,-144)(16,-160)(-16,-160).

Bone sprite 0x2046 -> num 17 (<=20). Pickup branch now ports blues:
each bone/small-energy pickup increments `player.bonus_energy_counter`; every 6,
if energy != 3, `player.energy += 1` (heart) and the counter resets. Added
PlayerState.energy (0..3, default 3) and bonus_energy_counter. The monster hurt
branch now decrements energy (death when < 0), closing the loop.

## Gates / teleports (cave entrances) — were missing

Ported `level_update_gates`: when the player has done the down-action
(`action_counter` set by anim 4/5, already wired) and holds Down on a gate's
`enter_pos` tile, teleport to `dst_pos` and reframe the camera to the gate's
`tilemap_pos`. Called from tick after `_update_runtime_items`. Verified: level 0
gate enter (37,39) -> dst (76,46).

## Moving platforms — never started

blues only moves an oscillating platform when the player rides it: the move
condition is `velocity!=0 || max_velocity<0 || flags&0xC0`, and `flags|=0x40` is
set when the player stands on it (level.c:1830). The Python had the move
condition and the carry logic but never set the riding flag, so a freshly loaded
platform (velocity 0, flags 0x2) never moved. `_update_platforms` now sets/clears
`flags 0x40` from `_player_is_standing_on_platform`. Verified: a type-2 platform
moves once ridden.

## 1UP

num 174 is now explicitly handled (extra life + score 227) and removed on pickup
(verified via the handler). If it still doesn't collect in-game, the remaining
suspect is the 1UP sprite's size/offset in the collision box, not the handler.

All 16 levels smoke-test clean.

## Pending
- Energy/lives not shown on a panel; player.energy drives death only.
- 1UP in-game collision box to confirm.
- Performance/stutter pass still outstanding.
