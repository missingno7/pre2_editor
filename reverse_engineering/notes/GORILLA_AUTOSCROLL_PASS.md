# Gorilla boss + scripted auto-scroll pass

## Camera / level scrolling findings

The level records already carry the original scrolling mask parsed by `resource.c`:

- bit `0x01`: wider vertical scrolling behaviour
- bit `0x02`: no horizontal follow
- bit `0x04`: scripted downward scroll, one pixel per logic tick

The relevant control path is `level_update_scrolling()` -> `level_adjust_y_scroll()` in the bundled disassembly-backed `blues_p2` reference.  The key branch is:

```c
if (!g_vars.tilemap_adjust_player_pos_flag && (g_res.level.scrolling_mask & 4) != 0) {
    level_adjust_vscroll_down(1);
    return;
}
```

`level_update_scrolling()` also exits early while `tilemap_noscroll_flag` is set, and does not call the horizontal-follow routine when `scrolling_mask & 2` is set.  Gates copy their per-gate `scroll_flag` into `tilemap_noscroll_flag` after teleporting.

The shipped level headers show only two levels with `scrolling_mask == 6` (`0x02 | 0x04`):

- `LEVEL6` / zero-based runtime index 5: the tree-boss downward-scroll climb
- `LEVELA` / zero-based runtime index 9: same scripted downward-scroll class

The runtime now follows those mask bits: `0x04` advances `camera_y` by one pixel per tick, `0x02` freezes horizontal follow, and the off-screen death check runs after the scroll update so a player that falls behind the scripted camera dies like the DOS game.

## Gorilla boss findings

The gorilla is driven by the per-level `Boss` record rather than by regular monster records.  Active boss records are present in:

- `LEVEL3` / index 2: x-range `3184..4048`, speed `2`, energy `100`, start `(3616,736)`
- `LEVEL9` / index 8: x-range `1664..2240`, speed `3`, energy `50`, start `(2160,256)`

The boss body is assembled every tick into `objects_tbl[103..107]` from:

- `boss_gorilla_data`: 19 animation poses, each with 5 part sprites and 5 object-slot body-part addresses
- `boss_gorilla_spr_tbl`: packed signed `(dy, dx)` offsets between adjacent body-part sprites

The state machine port now includes the main original behaviours from `level_update_boss_gorilla()`:

- idle, watch, jump, close-range attack, slam, hit-stun, defeated, and evasive jump states
- gravity, tile-floor landing, x-range clamp, facing/mirroring
- boss body collision with the player
- stomp bounce from the lower/body part
- club/projectile damage through object slots `0, 2..5`
- guarded arm frames `0x195/0x196` blocking attacks
- boss-energy pips in slots `108..115`
- defeat cleanup and bonus/lighter drop scaffold

Remaining likely follow-up: compare the exact boss defeat reward shower and the tree/minotaur boss special cases against DOS captures once their arenas are exercised.


## Follow-up pass: tree boss scaffold implemented

The next gap after the Gorilla pass was the actual LEVEL6 tree-boss arena.  The
important trigger is not the level header boss record: the DOS gate handler for
level index 5 teleports the player into the bottom arena, clears
`scrolling_mask &= 1`, initializes `boss_level5`, and wipes decor/object slots
91..96.  The runtime now mirrors that in `_update_transition()` /
`_start_tree_boss()` so the scripted downward-scroll shaft ends when the boss
gate is entered.

Implemented runtime pieces:

- `TreeBossState` + five `TreeBossLeafState` records.
- object slots 98..102 for falling leaves.
- object slots 103..107 for the animated/vulnerable tree body parts.
- object slots 108..115 for tree boss energy pips using the original
  `(2 - state) << 1` display count.
- phase progression: 10 hits for phase 0, 7 hits for phase 1, 7 hits for phase 2,
  then reward shower + lighter/end trigger drop.
- falling leaf spawn/sway/despawn and leaf-player damage.
- trunk/branch collision bounce/damage and right-side arena pushback at x > 984.
- club/projectile hits on the current vulnerable object slot
  `103 + state * 2`, including hit flash and phase animation recoil
  (`unk1 = 6`, `spr106_pos = 3`).

Caveat: the tree body position tables are currently copied from bundled
`blues/p2/bosses.c` because they make the arena playable.  Earlier RE notes found
that some clean blues arrays may diverge from the original const segment, so this
should still be ASM-audited by following the real table accesses in
`level_update_boss_tree`.  The state counters and gate initialization are kept
close to blues and smoke-tested in the runtime.

## Follow-up pass: LEVELA minotaur boss implemented

The next boss gap after gorilla + LEVEL6 tree was the LEVELA minotaur.  In
`blues/p2/bosses.c`, this is `level_update_boss_minotaur()` and it is gated by
`level_num == 9` (runtime index 9, `LEVELA`).  This boss is structurally
separate from gorilla/tree:

- It does not assemble a multi-part sprite in slots 103..107.
- The visible boss is a 6x7 tilemap animation.  `level_update_boss_minotaur_set_frame(num)` copies rows 0..6 from hidden source columns `num * 6 + 20` into visible columns 12..17.
- `blues/p2/level.c` has a hard render special case for `level_num == 9`: draw from tilemap offset 0 at the original 320x176 playfield, ignoring normal tilemap camera state.  The runtime now clamps LEVELA camera to `(0, 0)` to match this fixed-screen boss arena.
- The boss bytecode comes from `boss_minotaur_seq_data` in the bundled reference `staticres.c` and uses opcodes:
  - `0x00..0x7F`: set tile frame and end this boss tick.
  - `0xFF`: spawn rock sprite `0x1CA` into bonus slots 23..54.
  - `0xFE`: spawn chandelier/drop sprite `0x1CB` into bonus slots 23..54.
  - `0xFD`: play sound 2.
  - `0x80..0xFF` other than the sentinels: signed relative loop branch using `seq_counter`.
- Projectile hits are fixed-screen tests, not object-vs-object collision: any active club projectile in slots 2..5 with `x < 235` and `y < 80` damages the boss, switches to the hit sequence at offset `0x19`, and every fourth hit uses offset `0x25`.
- Boss energy is 24 and the HUD pips display `energy >> 2`.
- On defeat it sets frame 2 and spawns four copies of sprite `0x2137`, which is the final/game-complete semaphore path in the generic item logic (`0x137 - 53 == 258`).

Implemented in `runtime/game.py`:

- `MinotaurBossState` mirroring `boss_level9`.
- `_minotaur_set_frame()`, `_minotaur_run_sequence()`, `_minotaur_add_rock()`, `_minotaur_add_chandelier()`, `_minotaur_add_defeat_bonus()`.
- fixed LEVELA camera clamp.
- raw minotaur rock/chandelier collision cleanup in `_pickup_bonus_or_item()` so these slot-23 objects do not stick to the player forever in this runtime (the DOS collision path clears the object before item dispatch).

Caveat: this pass is a direct port of the bundled `blues/p2` boss routine and table bytes.  The flat `PRE2_unpacked_full.asm` listing is still too data-interleaved to cleanly name the matching functions without deeper segment/DS reconstruction, so exact opcode/table addresses should be ASM-audited later once the data segment is mapped.

## Follow-up pass: shared boss-completion behavior

The boss defeat path has now been moved closer to the shared original code:

- Gorilla and tree both call the same reward-shower helper matching the
  `level_add_bonuses_4x()` pattern from `bosses.c`/`level.c`.  This fills the
  original 23..54 dynamic bonus slots instead of spawning a tiny four-object
  approximation.
- The lighter/end-trigger drop now uses zero velocity and the original final
  reward anchor.
- Boss contact bone bursts alternate left/right from `bonus_energy_counter & 1`.
- Gorilla aggression/change-counter increments now saturate like the original
  uint8 helper, and the state-1 club-hold branch uses the global tick/draw
  counter.
- Minotaur final semaphore `0x2137` / item num `258` now enters THEEND.SQZ rather
  than being treated as a normal level-complete semaphore.
