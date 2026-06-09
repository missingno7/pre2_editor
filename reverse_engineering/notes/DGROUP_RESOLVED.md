# EXE investigation: DGROUP/DS base RESOLVED (the long-standing unlock)

## Result

**Data segment base = 0x9860 (DS = 0x986).** Any DS-relative reference `[off]` in
the disassembly maps to file offset **`0x9860 + off`** in `disasm/PRE2_load.bin`.
This was the blocker that made every data table (palettes, sprite/marker tables,
filename tables, gameplay constants) unreadable. It's now readable.

## How it was found / verified

- Header has SS = CS = 0x986; the data/stack group base is therefore 0x9860.
- Hard cross-check: the title-screen copper-bar palette-cycle routine at file
  **0x9B31** reads a colour table from `DS:0xa7a`, wrapping at `DS:0xfa8`, and
  initialises its pointer var `[0xa78]` to `0xa7a`. At base 0x9860 that table is at
  file **0xA2DA** — and indeed file 0xA2DA holds a 1326-byte (442-colour) run of
  bytes all < 0x40 (valid 6-bit RGB), length divisible by 3. The pointer var
  `[0xa78]` at file 0xA2D8 reads back as `0xa7a` exactly. Two independent matches.

## Correction to prior notes (important)

The old calibration anchor "PRNG 5-byte seed `05 22 86 8D E5` at file 0x79D1, use
random_get_number" is **BOGUS**. Those bytes at 0x79D1 are CODE
(`add ax,0x8622; lea sp,bp; mov ...`), not the PRNG initializer — a coincidental
byte match. Removed from the working assumptions.

## Other anchors recovered (file offsets)

- `recdis.py ranges/listing --scan` reaches **60.4%** of code (18404 insns). Always
  pass `--scan`.
- **0x9D4E** — VGA DAC "write one palette entry" primitive:
  `out 0x3c8,al; inc dx; outsb x3` (RGB from DS:SI). The fundamental palette poke.
- **0x9B31** — copper-bar palette-cycle (cycles DAC indices 8 and 0xF through the
  442-colour table at DS:0xa7a..0xfa8 = file 0xA2DA..0xA808).
- **0x9D54** — generic menu/keyboard handler: `cmp al` vs 0x48 up / 0x50 down /
  0x49 PgUp / 0x51 PgDn / 0x0d Enter / 0x1b Esc / 0x20 Space; moves cursor word
  `[0x3c9f]` by step `[0x3ca2]`, clamped 0..`[0x3ca6]`. Called from 0x99F1. (Confirms
  menu screens use up/down to move a cursor — matches the mode-select input model.)
- 0x9986–0x9F8E is the TITLE/menu module: CRTC smooth-scroll (port 0x3d4),
  attribute-controller effects (0x3c0), copper bars, text via 0xB800.

## Mode-select palette: still not pinned (but no longer blocked in principle)

The mode-select (MOTIF wallpaper) display routine is NOT in the title module above,
so its specific 16-colour palette table wasn't located yet. A DGROUP-region scan
for a blue 16-colour table found only gradient ramps / mixed-hue title palettes,
not the blue 4-colour MOTIF scheme. Finding it needs tracing the mode-select
screen's own routine (where MOTIF is loaded + its palette set). With DGROUP now
resolved, that's tractable follow-up work rather than a dead end. For now the
runtime keeps the screenshot-matched blue palette (variant "B").
