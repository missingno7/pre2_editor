# Area-spawned enemies not respawning after being killed

User report (reproducible): killing the area-spawned (type 0/10) enemies stopped
them respawning entirely. Faithfulness target = DOS ASM (blues is only a hint).

## Root cause

blues `monster_reset(obj, m)` retires the record permanently only when the LIVE
monster flags lack bit 2:

    m->current_tick = 0;
    obj->spr_num = 0xFFFF;
    m->flags &= ~4;
    if ((m->flags & 2) == 0) m->spr_num = 0xFFFF;   // permanent

`m->flags` here is the live, AI-mutated flag byte. A killed area enemy is flung as
a corpse (state 0xFF) with live flags 0x07 / 0x36 (bit 1 set), so blues does NOT
retire the record -> it respawns.

The port computed "permanent" from `ms.record_flags & 2` -- the ORIGINAL level
record flags -- in all three despawn paths:
- `_monster_offscreen_helper` (corpse scrolls off),
- `_monster_update_y_velocity_or_reset` (state-0xFF corpse, fires right after a
  kill),
- `_monster_die` bones branch.
For type-10 records whose original flags lack bit 2, the first kill set
`ms.runtime_sprite = 0xFFFF`, permanently retiring the record -> never respawns.

## Fix

All three sites now use `obj.monster_flags & 2` (the live flags) for the permanent
decision, matching blues `monster_reset`. Verified: 6 kills over 500 ticks -> 6
respawns, record stays alive (was retired after the first kill). All 16 levels
smoke-clean.

(Separately: spawn cadence itself is frame-tied to the 20 tps tick; this fix is
about the record being permanently retired, which was the "don't respawn at all"
bug.)
