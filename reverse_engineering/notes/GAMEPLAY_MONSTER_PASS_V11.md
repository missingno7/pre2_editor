# Gameplay pass v11 — objects_tbl monster slots

Goal: move another large gameplay subsystem away from static editor preview data
and into the original engine-shaped runtime model.

## What changed

- Added runtime `objects_tbl[11..22]` live monster slots.
- Added `RuntimeMonsterState` records parallel to `level.monsters[]` so the
  shipped level records remain inspectable while runtime flags/counters mutate.
- Added parser for `monster_anim_tbl[]` and `monster_spr_tbl[]` from the bundled
  `reverse_engineering/reference/blues_p2/staticres.c` snapshot.
- Added original-style monster spawn gates from `monster_func2()`:
  - type 0 / 10 trigger rectangles,
  - type 1/2/3/4/5/6/7/8/9 visible-on-screen spawn,
  - type 11/12 delayed/special spawn checks.
- Added first-pass `monster_func1()` update states for all movement types 0..12.
- Added `level_update_monster_pos()` shaped tile collision for ground/side/slope.
- Monsters are no longer drawn statically from `level.monsters`; they are drawn
  through the generic object-table draw path.
- Added `level_update_objects_axe()` shaped collision from club/projectiles to
  monster slots.
- Extended player collision to handle live monster slots before bonus/item slots.
- Extended `trace_player.py --objects` with a `monsters` column.
- Added `reverse_engineering/tools/inspect_monsters.py` for repeated inspection
  of per-level monster records, runtime sprite fixups and animation heads.

## Important source status

- `monster_func1/2`, `level_update_objects_monsters`, `level_update_monster_pos`,
  `level_update_objects_axe`, and `level_update_player_collision` are currently
  **blues-confirmed**.
- The level record layout and sprite base fixups are also supported by the C#
  project and our existing parser.
- The exact `monster_anim_tbl[]` segment in `PRE2.EXE` is still **not ASM-mapped**.
  v11 intentionally parses it from the local blues reference so every dependency
  is visible and diffable.

## Why this matters

Earlier runtime versions rendered enemies as static context sprites. That made
player collision, club collision and moving enemy state impossible to verify.
The original engine routes enemies through `objects_tbl[11+i]`; v11 now follows
that shape, so future ASM work can replace individual constants/state branches
without changing architecture.

## Known approximations

- Monster animation stream selection follows the blues table search, but the
  Python slicer is a practical approximation of pointer movement through the C
  byte array.
- Several `monster_func1()` branches are first-pass source-port translations and
  still need tick-by-tick DOSBox/ASM confirmation.
- Player damage currently preserves the original bounce/counter shape but does
  not yet decrement the full panel energy/lives state.
- Stomp collision reconstructs `player_jump_monster_flag` from falling speed and
  vertical relation; the exact flag side effect inside `level_objects_collide()`
  still needs direct ASM confirmation.

## Useful commands

```bash
python reverse_engineering/tools/inspect_monsters.py --level 3
python reverse_engineering/tools/trace_player.py --level 3 --steps 80 --objects
python reverse_engineering/tools/trace_player.py --level 3 --steps 40 --inputs R:40 --objects
```

