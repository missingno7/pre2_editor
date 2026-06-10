#!/usr/bin/env python3
"""Unpack the INNER compressed main program of PRE2.EXE.

PRE2.EXE is double-packed:
  1. Outer layer: LZEXE 0.91 (strip with tools/unlzexe.py -> PRE2_load.bin).
  2. Inner layer: the first ~0x98D0 bytes of the load module are an EAT-codec
     bitstream (same family as the *.SQZ data files, raw — no container
     header). A 0x3A-byte bootstrap at offset 0 moves the blob high in memory
     and jumps to the decompressor that lives in the resident tail at file
     offset 0x97D3 (the video module / DGROUP / startup at 0x975A..0xF6F0 are
     NOT compressed).

This tool reproduces that inner decompressor exactly (transcribed from the
disassembly at 0x975A..0x9853): bit word preloaded from file offset 0x3A,
stream from 0x3C, literals inline, spans built into BX (0xFFxx based), lengths
unary/3-bit/byte+17 coded; lo==0xFF + bit0 = end, +bit1 = segment-window slide
(linear-neutral, ignored here).

Output: PRE2_main.bin — the real, clean main program (~90 KB). THIS is the
ground-truth image for gameplay code/data (tables, password code, bosses...).
The old PRE2_load.bin offsets below 0x98D0 were compressed garbage.

Usage:
    python unpack_inner_eat.py PRE2_load.bin PRE2_main.bin
"""
from __future__ import annotations

import sys
from pathlib import Path

BITS_WORD_OFFSET = 0x3A  # initial 16-bit bit buffer; byte stream follows at 0x3C


def unpack_inner(data: bytes) -> bytes:
    pos = BITS_WORD_OFFSET + 2
    bits = data[BITS_WORD_OFFSET] | (data[BITS_WORD_OFFSET + 1] << 8)
    nbits = 16

    def getbit() -> int:
        nonlocal bits, nbits, pos
        b = bits & 1
        bits >>= 1
        nbits -= 1
        if nbits == 0:
            bits = data[pos] | (data[pos + 1] << 8)
            pos += 2
            nbits = 16
        return b

    def getbyte() -> int:
        nonlocal pos
        v = data[pos]
        pos += 1
        return v

    out = bytearray()

    def copy(count: int, hi: int, lo: int) -> None:
        span = (((hi & 0xFF) << 8) | lo) - 0x10000
        for _ in range(count):
            out.append(out[span])

    while True:
        while getbit():
            out.append(getbyte())
        if getbit():  # long match
            lo = getbyte()
            hi = ((0xFF << 1) | getbit()) & 0xFF
            if not getbit():
                i, dh = 1, 2
                while True:
                    if getbit():
                        break
                    hi = ((hi << 1) | getbit()) & 0xFF
                    dh <<= 1
                    i += 1
                    if i > 3:
                        break
                hi = (hi - dh) & 0xFF
            ln = None
            dh = 2
            for _ in range(4):
                dh += 1
                if getbit():
                    ln = dh
                    break
            if ln is None:
                if getbit():
                    dh += 1
                    if getbit():
                        dh += 1
                    ln = dh
                elif getbit():
                    ln = getbyte() + 0x11
                else:
                    v = 0
                    for _ in range(3):
                        v = (v << 1) | getbit()
                    ln = v + 9
            copy(ln, hi, lo)
        else:
            lo = getbyte()
            if getbit():  # 3 extra hi bits, length 2
                hi = 0xFF
                for _ in range(3):
                    hi = ((hi << 1) | getbit()) & 0xFF
                copy(2, (hi - 1) & 0xFF, lo)
            elif lo != 0xFF:
                copy(2, 0xFF, lo)
            elif getbit():
                continue  # segment-window slide: linear-neutral
            else:
                return bytes(out)  # end marker


def main(argv: list[str]) -> int:
    data = Path(argv[0]).read_bytes()
    out = unpack_inner(data)
    Path(argv[1]).write_bytes(out)
    print(f"decoded {len(out)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
