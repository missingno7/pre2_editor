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


## Implementation status update

- Gorilla boss: implemented in `runtime/game.py` as `GorillaBossState` and
  `_update_gorilla_boss()`, using slots 103..107 and energy slots 108..115.
- LEVEL6/tree boss: first playable pass implemented as `TreeBossState` and
  `_update_tree_boss()`.  It is triggered from the level-5 gate transition,
  clears the auto-scroll mask like the DOS gate handler, and uses slots 98..107.
- Implemented after this note: the LEVELA minotaur tilemap boss now has a first direct runtime port from `blues/p2/bosses.c`. Still pending: ASM audit of tree position tables and exact minotaur table/function addresses in the original binary.

## Follow-up pass: boss reward showers + final semaphore

After the minotaur pass, the next highest-value correctness gap was not another
boss body table but the *shared* boss-completion path:

- `bosses.c` does **not** spawn only four reward objects after gorilla/tree.
  It calls `level_add_bonuses_4x()` four times.  Each call itself tries to
  spawn four groups of four objects, and the original bonus-slot range 23..54
  naturally caps the visible shower at 32 simultaneous objects.  Runtime now has
  `_add_bonuses_4x_at()` and `_drop_boss_reward_shower()` and both gorilla and
  tree use that shared path.
- The lighter drop after gorilla/tree now uses the same final anchor and zero
  velocity as the original `level_add_object23_bonus(0, 0, 1)` call.
- `level_update_objects_boss_hit_player()` alternates the bone-burst horizontal
  velocity based on `bonus_energy_counter & 1`; runtime now mirrors that.
- Gorilla `change_counter` increments now use a uint8-style saturating helper,
  and the state-1 `player_anim_0x40_flag` branch keys off the global draw/tick
  counter instead of the hit-cooldown `draw_counter`.
- The minotaur's final dropped sprite `0x2137` maps to item num `258`, which in
  blues sets `level_completed_flag = 0xFF` and goes to `do_theend_screen()`.
  Runtime now splits num 226 (normal exit -> level-completed tally) from num 258
  (iris -> THEEND.SQZ static final screen).

Still pending after this pass: full original `next_level_tbl` routing for normal
exit semaphores.  The table is recorded in `runtime/game.py` as `NEXT_LEVEL_TBL`,
but normal exits still mostly follow the runtime's previous +1 behavior.

## Follow-up pass: normal semaphore routing

While wiring the final semaphore, I also added the original num-226 pre-routing
special cases from `level_update_player_collision()`:

- level 2 -> rewrite to 12, completion loop increments -> 13
- level 13 -> rewrite to 2, completion loop increments -> 3
- level 6 -> rewrite to 14, completion loop increments -> 15
- level 15 -> rewrite to 6, completion loop increments -> 7

The runtime now computes this target at semaphore pickup time and stores it until
the level-completed tally/outro finishes.  This keeps normal exits from blindly
loading `level_index + 1` in the levels that branch into/return from compact
special arenas.  The separate `next_level_tbl` / `level_completed_flag & 0x80`
path still needs deeper review; the final minotaur item num 258 is already routed
to THEEND.SQZ.

## ASM gameplay accuracy follow-up

- Corrected runtime tick order to match `do_level()` / `level_update_objects_monsters()`:
  boss updates now happen before player movement, not after items/gates.
- Restored the landing hit spark from `level_update_tile_attr1_helper()` for
  `player_jumping_counter > 4`.
- Ported `level_update_columns()` because levels 9 and E contain active column
  records and the editor-only parsing was not enough for accurate traversal.
- Added `reverse_engineering/tools/scan_boss_tables.py` to reproduce the current
  table-search findings in `PRE2_load.bin`.  The boss body/sequence tables still
  need segmented ASM mapping before replacing the bundled blues-derived tables.

## Fixes (gorilla assembly + auto-scroll kill)

### Gorilla part assembly (was visibly wrong)
Root cause in `_gorilla_init_objects`: the GLOBAL hdir mirror (`| 0x8000` on every
part sprite) was applied BEFORE the part-offset lookup. blues
(`level_update_boss_gorilla_init_objects`) looks up offsets with the sprite numbers
AS STORED in boss_gorilla_data (some parts already carry 0x8000 for a given frame)
and applies the global mirror only AFTER positioning. With the early flip, for one
facing the find_pos lookup missed (e.g. 0x199 -> 0x8199 is not a table key),
returned offset 0, and all five parts collapsed onto the anchor. Fixed: look up
with data-stored sprites, apply the global hdir `|0x8000` at object-assignment.
Verified the 5 parts now spread (x≈31,y≈43) and mirror around the anchor for both
facings, forming a coherent gorilla.

Knock-on fix: the body hit-target object (obj3) was collapsed onto the anchor too,
so the gorilla couldn't be damaged -> never reached defeat state 6 -> never dropped
the lighter. With assembly fixed, obj3 sits on the body, the boss is hittable, and
the defeat path (`_gorilla_drop_rewards` -> `_drop_boss_reward_shower`) drops the
4x bonus shower + lighter (sprite 99 = item num 46), which activates the level's
exit semaphore (verified: level idx 2 has one runtime-278 item) so the level can
complete. Gorilla appears on level idx 2 (ID 3) and idx 8 (ID 9).

### Auto-scroll-down kill (scrolling_mask bit 0x04)
Levels idx 5 (ID 6, tree-boss descent) and idx 9 (ID A, minotaur) have
scrolling_mask 0x06 (bit2 no-x-scroll + bit4 auto-scroll-down). The camera
descends 1px/tick (matches blues level_adjust_vscroll_down(1)). Added the blues
`level_update_player_decor` rule: in a bit-0x04 level the player dies as soon as
their anchor goes above the scrolled camera top (`player.y < camera_y`), not just
at the generic 11-tile threshold. Verified: idle player on level idx 5 is overtaken
by the descent and dies exactly when camera_y passes player.y.

## Gorilla squished/collapsed — re-fixed after external revert

The hdir-flip assembly bug had reappeared (external changes reverted the prior fix
back to flipping all part sprites BEFORE the find_pos lookup, which collapses parts
onto the anchor for one facing -> the squished gorilla in the user screenshot).
Re-applied the fix: look up offsets with the data-stored sprites, apply the global
hdir mirror only at object assignment. Verified: all 19 animation frames composite
into a coherent, upright gorilla (head/body/arms/legs), both PIL and pygame
renderers agree, levels idx 2 and idx 8 assemble (x/y spread, not collapsed).

ASM-accuracy caveat: the gorilla part sprites (0x195-0x1A3 in blues / our sheet)
are NOT found in PRE2_load.bin as a contiguous pair/offset table, and the
boss_gorilla_data part-pointer signature (5 sprites + 5 {P,P+6..} pointers) isn't
present either — so blues uses different sprite numbering AND a different data
layout than the binary. The assembly ALGORITHM matches blues (= the original), and
the offset DATA is blues' (produces a coherent tall gorilla), but the exact ASM
offset table could not be located/verified; pinning it byte-exact needs a dedicated
trace of the gorilla routine (find spr_monsters_offset, then the table access).

## Lighter not dropping on gorilla defeat (level 2 uncompletable) — FIXED

`_drop_boss_reward_shower` issued four bonus showers (64 objects requested) into the
32-slot bonus range 23-54, filling them ALL, then spawned the level-completing
lighter (sprite 99 = item num 46) LAST — so it never got a free slot and never
appeared. blues has the identical flaw (spawns lighter last after 4x
level_add_bonuses_4x), so this is a blues inaccuracy that breaks completion. Fixed
by reserving the lighter's slot FIRST (at blues' final anchor start-(64,32)), then
letting the shower fill the rest. Verified: gorilla defeat on level idx 2 now spawns
the lighter, and collecting it flips the exit semaphore item 278 -> 279 (active),
so the level can be completed.

Note: the level-2 purple gorilla/player colours are CORRECT — DOS renders those
sprites with level 2's palette, which is purple/pink (user-confirmed). Not a bug.

Still open: exact gorilla part-offset pose vs DOS (arms-spread-wide upright pose).
The offset table is blues-derived and not locatable in PRE2_load.bin (different
sprite numbering + layout), so byte-exact pose matching needs a dedicated trace of
the gorilla routine. Current assembly is coherent and animates through the poses.

## Gorilla "looks different" — global DRAW ORDER was inverted (FIXED)

User DOS screenshots confirmed the suspicion: the body parts stacked wrong. Root
cause was not the part offsets but the GLOBAL object draw order. DOS
level_draw_objects iterates `for (i = OBJECTS_COUNT-1; i >= 0; --i)` — HIGHER slots
draw FIRST and end up BEHIND; slot 103 is the front-most gorilla part, the player
(slot 1) draws above all objects, and the club/wing overlay (slot 0) is top-most.
Both our renderers iterated ASCENDING, inverting every overlap (the gorilla body
covered the head/face; the player drew over the club overlay).

Fixed in BOTH renderers (game.py render_frame + pygame_backend.render): draw slots
N..2 descending, then the player, then slot 0 on top. Also synced the boss 0x4000
white-flash tint into the pygame renderer (it only existed in the PIL one).
Verified: gorilla now renders hood/face in front exactly like the DOS screenshots;
pygame vs PIL boss-region pixel diff = 0; all 16 levels smoke-clean.

Note this fix is global: score popups (75-90) now correctly draw behind items
(55-74), bonuses behind monsters, etc., matching the original engine everywhere.
