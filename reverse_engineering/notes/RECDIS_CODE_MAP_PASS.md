# Recursive-descent code map pass

## Why

Every prior physics/monster note ends with "still pending against ASM" because
the only disassembly artifact was `PRE2_unpacked_full.asm`, a flat objdump linear
sweep. On a DOS EXE with data interleaved, ~90% of that listing is data decoded
as instructions (telltales: MMX `por mm1`, `fsubr`, `enter`, `int3` in a 1992
game). It cannot be read as logic, and `asm_constant_audit.py` could not find the
gameplay tables as flat byte sequences. We needed real code/data separation.

## Tool

`reverse_engineering/tools/recdis.py` — capstone-based 16-bit recursive-descent
disassembler over `disasm/PRE2_load.bin`.

Key facts that make it work:
- The unpacked load module is 63216 bytes (< 64 KiB) → single code segment.
- NEAR call/jmp/jcc relative targets resolve as `(ip + rel) & 0xFFFF` and that
  value IS the file offset. Verified: startup at `0xF6A8` does `call 0x9960`
  which lands exactly on the real video module (`0x09960`).
- FAR call/jmp `seg:off` resolve as `(seg*16 + off) & 0xFFFFF`, also a direct
  file offset (startup seg `0x986`: `0x986*16 + 0x5e48 = 0xF6A8`).

Seeding: the entry's jump into `main` is an indirect far jump we cannot follow,
so `--scan` linearly collects every `E8`/`9A` target and seeds recursive descent
from all of them. False seeds die in short invalid runs; real function entries
recur consistently.

Commands:
```
python reverse_engineering/tools/recdis.py ranges  --scan   # code vs data map
python reverse_engineering/tools/recdis.py funcs   --scan   # call targets
python reverse_engineering/tools/recdis.py xref 0xNNNN       # callers
python reverse_engineering/tools/recdis.py listing --scan    # full listing
python reverse_engineering/tools/recdis.py listing 0xNNNN    # one function
```

## Result

- 60.4% of the blob is now reachable real code (vs the flat sweep's noise).
- Largest real code runs (likely the gameplay engine, to be labelled next):
  - `0x0E682-0x0F6EC` (4202 b)
  - `0x0A0D1-0x0AAC3` (2546 b)
  - `0x0D08C-0x0D64C` (1472 b)
  - `0x0C212-0x0C715` (1283 b)
  - `0x0975A-0x09F8E` (2100 b, contains the verified VGA/retrace video module)
  - `0x0DC80-0x0DEB9` (569 b)
- Full listing dumped to `reverse_engineering/notes/recdis_listing.txt`.

## Important correction to the table-audit story

`asm_constant_audit.py` reports MISS for jump/anim/score/club tables but OK for
the PRNG seed (found at file `0x079D1`). The PRNG seed proves initialized DATA is
present in the file. So the gameplay tables are either (a) stored with values that
DIFFER from the blues `staticres.c` transcription, or (b) computed at runtime.
**This is a live tick-correctness risk**: the blues jump-arc / anim / score
tables currently used by the Python runtime are NOT confirmed equal to the
original and at least the jump y-velocity sequence is not byte-present in any
form. These must be re-derived from the actual code, not trusted from blues.

## Next steps

1. DATA references in instructions are DS-relative (`[off]` where
   `off = file_offset - DS_base*16`), NOT linear file offsets — that's why
   grepping the listing for `0x79d1` finds nothing. Determine the DGROUP/DS base
   (from the C startup at `0xF6A8`, which loads DS from a relocated value) so we
   can correlate code memory operands to file data offsets. This unlocks
   "which function touches the player struct / jump table".
2. Calibrate by locating `random_get_number` (known byte/word arithmetic on the
   5-byte PRNG state) to lock the DS base, then walk outward to the player update.
3. Label the big code runs against the blues function list, starting with the
   player physics chain.
