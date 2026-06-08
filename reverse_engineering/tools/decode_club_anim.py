#!/usr/bin/env python3
"""Decode the Prehistorik 2 club_anim_tbl data used by run_game.

This is a small inspection helper for the player action path.  It prints the
player-sprite -> visible-club-sprite mapping and the optional projectile header
for each club type.  The data currently comes from blues_p2/staticres.c and is
kept in runtime/original_tables.py so we can later mark each sequence as
ASM-confirmed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from runtime.original_tables import club_anim_spec, club_anim_words, club_projectile_start  # noqa: E402


def main() -> int:
    for club_type in range(4):
        off, power, duration, flags = club_anim_spec(club_type)
        words = club_anim_words(club_type)
        print(f"club_type={club_type} offset=0x{off:02X} power={power} duration={duration} flags=0x{flags:X}")
        i = 0
        while i + 3 < len(words) and (words[i] & 0xFFFF) != 0x55AA:
            print(f"  player_spr={words[i] & 0xFFFF:3d} -> club_spr={words[i+1] & 0xFFFF:3d} dx={words[i+2]:4d} dy={words[i+3]:4d}")
            i += 4
        start = club_projectile_start(club_type)
        if start is not None:
            x_vel, y_vel, anim = start
            print(f"  projectile: x_vel16={x_vel} y_vel16={y_vel} anim_words={','.join(str(w & 0xFFFF if w >= 0 else w) for w in anim)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
