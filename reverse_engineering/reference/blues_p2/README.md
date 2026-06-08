# blues-master `p2/` reference snapshot

This folder is a local reverse-engineering reference copied from the uploaded
`blues-master.zip`.  The useful part is the `p2/` engine rewrite, which supports
Prehistorik 2 data files.

Use this as a comparative RE aid, not as final truth.  Anything moved into the
Python `run_game` runtime should either be:

1. confirmed directly in `PRE2.EXE` disassembly, or
2. marked as coming from the `blues-master/p2` rewrite until confirmed.

High-value files:

- `resource.h` — exact level/game structs used by this rewrite.
- `level.c` — player physics, tile collision, platform updates, items, secrets.
- `monsters.c` — monster state machines.
- `bosses.c` — boss state machines.
- `staticres.c` — sprite sizes, player animation LUT, object animations, trig tables.
- `screen.c` — original-ish tile/sprite rendering behavior.
- `unpack.c` — decompression reference.

Known immediately useful fragments already transcribed:

- `player_anim_lut[]`
- jump impulse table `{-65,-51,-35,-20,-10,-5,-2,-1,0}`
- player fixed-point velocity constants: accel `16`, max run `80`, friction `12`, gravity `16`, max fall `192`
- platform update state machine and movement vector table
- falling platform type `8` state machine
- level object table layout and sprite-number fixups
