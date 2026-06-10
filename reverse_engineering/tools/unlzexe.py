#!/usr/bin/env python3
"""Correct LZEXE 0.91 (LZ91) unpacker for PRE2.EXE.

The previous PRE2_load.bin in disasm/ was produced by a flawed unpacker that
left stray bytes in the stream (mangled strings like "PRESENT.S Q..Z", data
tables diverging mid-way, undecodable code). This is a clean-room port of the
canonical unlzexe algorithm (Bellard's LZEXE bit-stream: literal/short-match/
long-match with segment markers).

Usage:
    python unlzexe.py PACKED.EXE OUT_LOAD_MODULE.BIN [OUT_UNPACKED.EXE]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


class BitReader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.buf = 0
        self.count = 0
        self._fill()

    def _fill(self) -> None:
        self.buf = self.data[self.pos] | (self.data[self.pos + 1] << 8)
        self.pos += 2
        self.count = 16

    def getbit(self) -> int:
        bit = self.buf & 1
        self.buf >>= 1
        self.count -= 1
        if self.count == 0:
            self._fill()
        return bit

    def getbyte(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b


def unpack_lzexe(packed: bytes) -> tuple[bytes, dict]:
    if packed[0x1C:0x20] not in (b"LZ91", b"LZ09"):
        raise ValueError(f"not an LZEXE file (sig={packed[0x1C:0x20]!r})")
    (sig, cblp, cp, crlc, cparhdr, minall, maxall,
     ss, sp, csum, ip, cs, lfarlc, ovno) = struct.unpack_from("<14H", packed, 0)
    header_size = cparhdr << 4
    stub_base = (cs << 4) + header_size
    # LZ91 info block at cs:0 (7 words before the entry at cs:ip):
    # real_ip, real_cs, real_sp, real_ss, compressed_size(paras),
    # inc_size(paras), decompressor_size(bytes)
    info = struct.unpack_from("<7H", packed, stub_base)
    real_ip, real_cs, real_sp, real_ss = info[0], info[1], info[2], info[3]

    br = BitReader(packed, header_size)
    out = bytearray()
    while True:
        if br.getbit():
            out.append(br.getbyte())
            continue
        if not br.getbit():
            length = ((br.getbit() << 1) | br.getbit()) + 2
            span = br.getbyte() | 0xFF00
        else:
            lo = br.getbyte()
            hi = br.getbyte()
            span = lo | ((hi & 0xF8) << 5) | 0xE000
            length = (hi & 7) + 2
            if length == 2:
                b = br.getbyte()
                if b == 0:
                    break  # end of compressed image
                if b == 1:
                    continue  # segment-change marker (no output)
                length = b + 1
        offset = span - 0x10000  # negative displacement
        for _ in range(length):
            out.append(out[offset])
    meta = {
        "real_ip": real_ip, "real_cs": real_cs,
        "real_sp": real_sp, "real_ss": real_ss,
        "load_size": len(out),
    }
    return bytes(out), meta


def main(argv: list[str]) -> int:
    packed = Path(argv[0]).read_bytes()
    out, meta = unpack_lzexe(packed)
    Path(argv[1]).write_bytes(out)
    print(f"load module: {meta['load_size']} bytes "
          f"(entry {meta['real_cs']:04X}:{meta['real_ip']:04X}, "
          f"ss:sp {meta['real_ss']:04X}:{meta['real_sp']:04X})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
