#!/usr/bin/env python3
"""Audit candidate gameplay constants against the unpacked PRE2 load module.

The blues p2 rewrite is useful as a map, but this project aims at the DOS ASM.
This script records which small tables/constants are byte-for-byte visible in
PRE2_load.bin and which still need a better segmented/data-flow confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOAD_BIN = ROOT / "disasm" / "PRE2_load.bin"


@dataclass(frozen=True)
class Pattern:
    name: str
    data: bytes
    note: str


PATTERNS = [
    Pattern("PRNG seed", bytes.fromhex("05 22 86 8D E5"), "Matches p2/resource.c random seed state."),
    Pattern("player_anim_lut", bytes.fromhex("00 03 05 07 02 06 00 00 01 03 04 07 02 06 01 00 01 03 04 07 02 06 00 00 00 00 00 00 00 00 00 00"), "Expected from p2/staticres.c; no direct flat match so far."),
    Pattern("jump y_tbl int8", bytes.fromhex("BF CD DD EC F6 FB FE FF 00"), "Expected signed bytes -65,-51,-35,-20,-10,-5,-2,-1,0."),
    Pattern("jump y_tbl int16", bytes.fromhex("BF FF CD FF DD FF EC FF F6 FF FB FF FE FF FF FF 00 00"), "Same values as little-endian int16 words."),
    Pattern("object_anim idle", bytes.fromhex("09 00 FE FF"), "object_anim_7d93 from p2/staticres.c."),
    Pattern("object_anim run head", bytes.fromhex("00 00 00 00 01 00 01 00 02 00 02 00"), "Run animation prefix from p2/staticres.c."),
    Pattern("club anim head", bytes.fromhex("22 00 3A 00 0B 00 10 00"), "club_anim_data prefix from p2/staticres.c."),
    Pattern("score_spr_lut head", bytes.fromhex("10 0F 0D 0E 0C 0C 0D 0E 00 00 00 00 00 00 00 00"), "Pickup score sprite lookup from p2/staticres.c."),
    Pattern("score_tbl head", bytes.fromhex("0A 00 14 00 1E 00 32 00 3C 00 46 00"), "Score values from p2/staticres.c."),
]


def find_all(blob: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        pos = blob.find(needle, start)
        if pos < 0:
            return out
        out.append(pos)
        start = pos + 1


def main() -> int:
    blob = LOAD_BIN.read_bytes()
    print(f"PRE2_load.bin size={len(blob)} bytes")
    for pat in PATTERNS:
        matches = find_all(blob, pat.data)
        if matches:
            locs = ", ".join(f"0x{x:05X}" for x in matches[:12])
            more = "" if len(matches) <= 12 else f" ... +{len(matches)-12} more"
            print(f"OK   {pat.name:22s} {locs}{more}  # {pat.note}")
        else:
            print(f"MISS {pat.name:22s} --  # {pat.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
