# Bosses: scope, blues reference, and ASM-driven implementation plan

## Current state

Bosses are **not implemented at all** in `runtime/game.py` (zero "boss" references).
blues `bosses.c` (≈853 lines) is the only reference, and per the user it is NOT
tick-accurate — so the port must be driven by the PRE2 ASM, using the now-resolved
DGROUP base (data tables at file `0x9860 + DS_offset`) to read the exact movement /
timing / sprite tables rather than copying blues' approximations.

## The three bosses (blues `boss_update`, bosses.c:843)

```
void boss_update() {
    if (level.boss_state != 0xFF)   level_update_boss_gorilla();   // gorilla
    if (level_num == 5 && (scrolling_mask & ~1)==0) level_update_boss_tree();  // L5 tree
    if (level_num == 9)             level_update_boss_minotaur();   // L9 minotaur
}
```

- **Gorilla** (bosses.c:215, ~286 lines): gated by `level.boss_state != 0xFF` (a
  per-level record). Throws objects; uses `level_update_boss_gorilla_init_objects`
  with a position table `p`, find-pos helper, projectile collision.
- **Tree** (level 5, bosses.c:501, ~340 lines): `g_vars.boss_level5` state machine
  (state 0..2, energy 10, leaf_tbl, spr103/spr106 positions, tick_counter,
  idle_counter). Triggered by the exit semaphore (level.c:2964 "exit to boss
  (tree)") which inits boss_level5. Uses data tables `pos1_data`/`pos2_data`/
  `pos3_data` (sprite positions) — these are the DS tables to read from ASM.
- **Minotaur** (level 9, `level_update_boss_minotaur`, `g_vars.boss_level9`):
  blues notes "expects original screen resolution". seq-driven.

## Why ASM, not blues

blues admits non-accuracy; the timing (tick_counter init values like 110, 64+rng,
idle_counter (rng&15)<<3) and the position tables drive the fight feel. With DGROUP
resolved we can now read the real `pos*_data` tables and the per-level boss records
directly from `PRE2_load.bin` (file = 0x9860 + DS offset) and match the exact
counters from the boss routines in the disassembly.

## Implementation plan (next focused session)

1. Locate the three boss routines in the disassembly (`recdis.py listing --scan`),
   starting from the main-loop `boss_update` dispatch; confirm the level gates
   (boss_state, level_num 5/9).
2. Read each boss's data tables from DGROUP (positions, timing, sprite lists) and
   transcribe them into `runtime/original_tables.py` (verified file offsets).
3. Port each boss as a per-tick state machine in `game.py` (one step/tick = DOS
   rate), hooked from `tick()` at the right level, reusing the object-slot model
   (the tree uses slots 103-106; gorilla/minotaur their own ranges — note our
   runtime_objects currently has 91 slots, so the slot table must be extended to
   cover boss slots ≥103).
4. Add boss energy bar (`level_update_objects_boss_energy`) + boss-projectile
   items (nums 458/459 already partially handled in `_pickup_bonus_or_item`).
5. Verify each boss tick-by-tick against DOSBox.

## Prereq note — DONE

`runtime_objects` was `range(91)`; now `range(OBJECTS_COUNT)` with
`OBJECTS_COUNT = 116` (matches blues). Verified all 16 levels smoke-clean. Boss
objects live in slots 98-115 and render for free via the existing object loop
(world-coord, camera-culled). Slot map in the OBJECTS_COUNT comment in game.py.

## CRITICAL finding: blues boss tables DIVERGE from the ASM

Verified against `PRE2_load.bin` (DGROUP/const data): the tree-boss position tables
in blues `bosses.c` are NOT all faithful to the original:
- `pos3_data` (0x3E3,0x773,0x1B0 ...) matches the ASM exactly at file **0x86BD**.
- `pos2_data` matches only the first 5 words at file 0x8690 then DIVERGES
  (word[5] is 0xFCAB in the ASM vs 0x1AB in blues).
- `pos1_data` first entry (0x3A9,0x7D4,0x1A4) is NOT found contiguously at all.

So the boss data is **interleaved in the const segment around 0x8670-0x86D0** and
blues' clean inline arrays are partly reconstructed/wrong. The tree-boss port MUST
extract the real tables by reading the boss routine's actual data indexing in the
disassembly (find `level_update_boss_tree` in the code via recdis, follow the
`mov si, <off>` / `[bx+off]` table accesses), NOT copy blues. This is the focused
RE step before porting the state machine. Timing constants (energy 10, tick 110,
idle 8, etc.) likewise need ASM confirmation.

## Trigger

The tree boss is started from the level-5 GATE handler (blues level.c:2964: on
entering the boss gate it sets boss_level5.{energy=10,state=0,tick=110,idle=8,...}
and clears objects 91-96). Our runtime's `_update_gates` is where this hooks in.
