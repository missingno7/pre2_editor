# Tick-correctness verification status (ASM = ground truth)

Policy: PRE2.EXE (`disasm/PRE2_load.bin`) is ground truth; the bundled `blues` C is a
guide and is NOT tick-accurate, so constants/tables/behaviour must be ASM-verified.

## Verification surface available now
- DGROUP/DS base RESOLVED = 0x9860: any DS-relative data table reads at file
  `0x9860 + off`. (See pre2-disasm-anchors memory; verified via the copper palette.)
- recdis `--scan` disassembles ~60% of code (the video/title module 0x9986-0x9F8E is
  fully covered). The gameplay engine is mostly in the unreached 40% (relocated far
  calls stop the recursive descent).

## Confirmed / fixed this and prior passes (logic verified against ASM behaviour)
- Earthquake column trigger: UNSIGNED `(trigger-player) <= 8` (blues' signed form was
  wrong — fired every column at level load).
- Auto-scroll-down kill: die when player.y < camera_top on scrolling_mask&4 levels.
- Gorilla assembly hdir-flip order; dormant-when-far; lighter-drop slot reservation.
- Platform landing: blues halved-width sprite-box overlap (not a wide hardcoded box).
- Sun/light palette fade matches level_update_light_palette exactly.
- Vanilla camera even-pixel snap removed (was a 1px walk-drag stutter).

## BLOCKED: literal const-segment data tables (the next unlock)
The literal tables blues inlines are in a CONST segment whose base is NOT 0x9860, so
they cannot be byte-verified yet:
- `PLAYER_JUMP_Y_DELTA16` (jump arc) — blues `-65,-51,-35,-20,-10,-5,-2,-1,0` is NOT
  in PRE2_load.bin as bytes or int16 words. blues' values are reconstructed.
- gorilla/tree boss position+offset tables — same (verified earlier).
- The physics immediates (gravity 16, max-fall 192, run-max 80, friction 12, etc.)
  live in the unreached gameplay code.

Until the const-segment base is resolved (find a code ref to a known const once the
gameplay code is disassembled), these stay blues-derived. They are likely close
(blues devs tuned them), but are NOT ASM-confirmed. This is the single highest-value
RE task remaining for tick-perfection — analogous to the DGROUP unlock.
