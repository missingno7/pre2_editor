# PRE2.EXE is double-packed; real ground truth recovered; password algorithm solved

## The discovery chain

Investigating why editor passwords don't work in DOS: the password hex-parse idiom
(`sub al,'0' .. sub al,7`) was found at load-module 0x6E30 but with junk bytes
interleaved — the same corruption pattern as the mangled filename strings and the
half-matching data tables. First hypothesis (bad LZEXE unpack) was DISPROVED by a
clean-room unlzexe (tools/unlzexe.py): byte-identical output. The truth: the
corruption pattern IS compression. PRE2.EXE is double-packed:

1. Outer: LZEXE 0.91 -> PRE2_load.bin (63216 bytes).
2. Inner: PRE2_load.bin bytes 0x0000..~0x98D0 are a RAW EAT bitstream (the same
   codec family as the *.SQZ data files). The 0x3A-byte bootstrap at offset 0
   relocates the blob high in memory and far-jumps to the decompressor, which
   lives (uncompressed) in the resident tail at 0x97D3. The resident tail
   0x975A..0xF6F0 (video module, DGROUP runtime data, startup) is plain code —
   exactly the only regions that ever disassembled cleanly.

`tools/unpack_inner_eat.py` (decompressor transcribed instruction-by-instruction
from 0x975A..0x9853; bit word at file 0x3A, stream at 0x3C) produces
**disasm/PRE2_main.bin (90768 bytes) — the real main program**.

## Validation (and retractions)

In PRE2_main.bin, previously "missing/divergent" items are present and EXACT:
- PLAYER_JUMP_Y_DELTA16 (-65,-51,-35,-20,-10,-5,-2,-1,0) as int16 @ 0x119FA —
  blues' jump arc is ASM-confirmed.
- boss_gorilla_spr_tbl first triple @ 0x14719 — gorilla offsets ASM-confirmed.
- tree pos1_data @ 0x144B1 — tree tables ASM-confirmed.
- Clean strings: PRESENT.SQZ @ 0x14324; CODE/CARTE/MAP/MOTIF @ ~0xAA17..0xAA52.
The earlier notes claiming blues' boss/jump tables diverge from the ASM were
artifacts of comparing against compressed bytes. Withdrawn.

## The password algorithm (PRE2_main.bin routine @ 0x9559)

```
machine_value (once, cached at DGROUP[0xA32F], flag [0xA331]):
    dx = 0
    for the 16 bytes at F000:FFF0..FFFF:  dl += b ; dh = dh - b - carry
    scan seg = C000; while high byte of seg < 0xF0:
        if word [seg:0] == 0xAA55:
            for first 0x80 bytes: dl += b ; dh ^= b
            seg += (byte [seg:2] * 256) >> 3      # ROM length, else +0x400 paras
    if dx == 0: dx = 0x20
password(seed) = ROL16((seed ^ 0x55A3) * machine_value, cs:[5])   # cs:[5] = 3
seed = level_num + (expert ? 10 : 0); rendered as 4 hex digits, high nibble first.
```

So passwords hash the EMULATED BIOS + option-ROM (video BIOS) bytes: every DOSBox
version/fork/machine= setting yields its own password set. blues' 0xB297 ("to match
dosbox") is stock DOSBox's machine value — on the user's emulator it differs, which
is exactly why the editor's list didn't work.

## Fix shipped

`pre2lib/passwords.py`: real algorithm documented, `multiplier=` parameter, and
`derive_machine_multiplier(observed_password)` — solves M by mod-inverse from ONE
observed password with an even seed (level-1 beginner; seed^0x55A3 is then odd and
uniquely invertible mod 2^16). Editor Passwords tab now has a calibrate box: type
the level-1 beginner password seen in YOUR game, the whole table recomputes for
your machine. Round-trip verified for 5 synthetic machine values.
